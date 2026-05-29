from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from .dynamic import render_dynamic_page
from .geo import domain_country_signal, location_text_markers
from .html_extract import extract_html
from .http import fetch_text
from .logo import logo_evidence
from .search_sources import SearchCandidate
from .text import base_domain_label, domain_from_url, normalize_text, slug, tokens


def load_config(path: str | Path = "config/scoring.json") -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def is_excluded_domain(domain: str, config: dict) -> bool:
    domain = domain_from_url(domain)
    if not domain:
        return True
    labels = set(domain.split("."))
    for raw_pattern in config.get("excluded_domains", []):
        pattern = str(raw_pattern or "").strip().lower()
        if not pattern:
            continue
        if pattern.endswith("."):
            marker = pattern.rstrip(".")
            if marker in labels or domain.startswith(pattern) or f".{pattern}" in domain:
                return True
            continue
        if domain == pattern or domain.endswith(f".{pattern}"):
            return True
    return False


def pre_score_candidate(provider: dict, candidate: SearchCandidate, config: dict) -> dict:
    return _score_candidate(provider, candidate, config, inspect_site=False)


def score_candidate(provider: dict, candidate: SearchCandidate, config: dict) -> dict:
    return _score_candidate(provider, candidate, config, inspect_site=True)


def _score_candidate(provider: dict, candidate: SearchCandidate, config: dict, *, inspect_site: bool) -> dict:
    domain = domain_from_url(candidate.url)
    if is_excluded_domain(domain, config):
        return {
            "url": candidate.url,
            "domain": domain,
            "score": -100,
            "reject": True,
            "reasons": ["excluded_domain"],
            "source": candidate.source,
            "query": candidate.query,
            "rank": candidate.rank,
        }

    score = 0
    reasons: list[str] = []
    name = provider.get("provider_name", "")
    name_norm = normalize_text(name)
    name_slug = slug(name)
    domain_label = base_domain_label(domain)
    domain_slug = slug(domain_label)
    provider_tokens = tokens(name)
    title_snippet_norm = normalize_text(f"{candidate.title} {candidate.snippet}")
    title_similarity = _text_similarity(name, f"{candidate.title} {candidate.snippet}")

    if name_slug and name_slug == domain_slug:
        score += 35
        reasons.append("domain_exact_provider_slug")
    elif name_slug and (name_slug in domain_slug or domain_slug in name_slug):
        score += 25
        reasons.append("domain_contains_provider_slug")
    elif _text_similarity(name, domain_label) >= 92:
        score += 24
        reasons.append("domain_fuzzy_provider_match")
    else:
        matching_tokens = [t for t in provider_tokens if t in domain_slug]
        if matching_tokens:
            score += min(22, 8 * len(matching_tokens))
            reasons.append(f"domain_token_match:{','.join(matching_tokens[:4])}")

    if name_norm and name_norm in title_snippet_norm:
        score += 15
        reasons.append("search_result_contains_exact_name")
    elif title_similarity >= 90:
        score += 12
        reasons.append("search_result_fuzzy_name_match")
    elif provider_tokens and sum(1 for t in provider_tokens if t in title_snippet_norm) >= min(2, len(provider_tokens)):
        score += 8
        reasons.append("search_result_contains_name_tokens")

    non_search_sources = {
        "domain_guess",
        "second_pass_domain_variant",
        "second_pass_input_url",
        "second_pass_top_candidate",
    }
    if candidate.rank and candidate.rank <= 3 and candidate.source not in non_search_sources:
        score += 8
        reasons.append("top_search_result")

    if "official website" in candidate.query.lower() and candidate.rank and candidate.rank <= 5:
        score += 6
        reasons.append("official_website_query_hit")

    snippet_service_hits = [kw for kw in config.get("service_keywords", []) if normalize_text(kw) in title_snippet_norm]
    if len(snippet_service_hits) >= 3:
        score += 8
        reasons.append("search_snippet_contains_amazon_service_keywords")
    elif snippet_service_hits:
        score += 3
        reasons.append("search_snippet_contains_some_service_keywords")

    country_signal = domain_country_signal(domain, provider.get("provider_locations") or [])
    if country_signal == "match":
        score += 6
        reasons.append("domain_tld_matches_provider_country")
    elif country_signal == "conflict":
        score -= 8
        reasons.append("domain_tld_conflicts_provider_country")
    if _text_mentions_provider_location(title_snippet_norm, provider):
        score += 4
        reasons.append("search_result_contains_provider_country")

    page_evidence = {}
    if inspect_site:
        page_evidence = inspect_candidate_site(candidate.url, provider, config)
        score += page_evidence["score"]
        reasons.extend(page_evidence["reasons"])
        if "page_requires_javascript" in page_evidence["reasons"] and not any(
            reason in page_evidence["reasons"]
            for reason in ["page_contains_amazon_service_keywords", "page_mentions_amazon_spn"]
        ):
            score -= 25
            reasons.append("javascript_page_requires_dynamic_review")
    else:
        reasons.append("not_fetched_preliminary_score")

    if inspect_site:
        score, cap_reasons = _apply_identity_caps(score, reasons, provider)
        reasons.extend(cap_reasons)

    return {
        "url": page_evidence.get("final_url") or candidate.url,
        "domain": domain_from_url(page_evidence.get("final_url") or candidate.url),
        "score": score,
        "reject": False,
        "reasons": reasons,
        "source": candidate.source,
        "query": candidate.query,
        "rank": candidate.rank,
        "title": candidate.title,
        "snippet": candidate.snippet,
        "page_title": page_evidence.get("title", ""),
        "status": page_evidence.get("status"),
        "evidence_url": candidate.evidence_url,
    }


def inspect_candidate_site(url: str, provider: dict, config: dict) -> dict:
    page_scores = []
    home = {}
    root = ""
    for candidate_root in _candidate_roots(url):
        fetched = fetch_text(candidate_root + "/")
        if fetched.get("ok") and fetched.get("text"):
            home = fetched
            root = candidate_root
            break
        if not home:
            home = fetched
            root = candidate_root
    final_url = home.get("final_url") or root
    home_score = _score_page(home, provider, config, is_home=True)
    page_scores.append(home_score)
    logo = {}
    if home.get("ok") and home.get("text") and provider.get("listing_logo_url"):
        logo = logo_evidence(provider.get("listing_logo_url", ""), home.get("text", ""), final_url)
    if _should_dynamic_render(home_score, config):
        rendered = render_dynamic_page(final_url, timeout_ms=_dynamic_timeout_ms(config))
        rendered_score = _score_page(rendered, provider, config, is_home=True)
        if rendered.get("ok"):
            rendered_score["reasons"].insert(0, "dynamic_rendered_page")
        else:
            rendered_score["reasons"].append(_dynamic_error_reason(rendered))
        page_scores.append(rendered_score)
    if home.get("ok") and home.get("text"):
        max_supporting_paths = int(config.get("max_supporting_paths", 8) or 0)
        for path in [p for p in config.get("site_paths", ["/"]) if p != "/"][:max_supporting_paths]:
            target_root = _root_from_final_url(final_url)
            target = target_root + (path if path.startswith("/") else f"/{path}")
            fetched = fetch_text(target)
            page_scores.append(_score_page(fetched, provider, config, is_home=False))
    best = max(page_scores, key=lambda x: x["score"], default={"score": 0, "reasons": []})
    combined_reasons = []
    for item in page_scores:
        for reason in item["reasons"]:
            if reason not in combined_reasons:
                combined_reasons.append(reason)
    score = _combined_site_score(combined_reasons)
    if logo.get("matched"):
        score += 18
        combined_reasons.append("listing_logo_visual_match")
    elif float(logo.get("score") or 0) >= 0.78:
        score += 8
        combined_reasons.append("listing_logo_visual_near_match")
    return {
        "score": score,
        "reasons": combined_reasons[:12],
        "final_url": final_url,
        "title": best.get("title", ""),
        "status": best.get("status"),
        "logo": logo,
    }


def _root_from_final_url(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return f"{parsed.scheme or 'https'}://{parsed.netloc or parsed.path}".rstrip("/")


def _candidate_roots(url: str) -> list[str]:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or parsed.path).split("/")[0]
    if not host:
        return []
    scheme = parsed.scheme or "https"
    hosts = [host]
    if host.startswith("www."):
        hosts.append(host[4:])
    else:
        hosts.append(f"www.{host}")
    schemes = [scheme]
    schemes.extend(item for item in ["https", "http"] if item not in schemes)
    roots = []
    for candidate_scheme in schemes:
        for candidate_host in hosts:
            root = f"{candidate_scheme}://{candidate_host}".rstrip("/")
            if root not in roots:
                roots.append(root)
    return roots


def _score_page(fetched: dict, provider: dict, config: dict, *, is_home: bool) -> dict:
    if not fetched.get("ok") or not fetched.get("text"):
        return {"score": 0, "reasons": [], "title": "", "status": fetched.get("status")}
    extracted = _extract_page(fetched["text"], fetched.get("final_url") or fetched.get("url") or "")
    title = str(extracted.get("title") or "")
    text = normalize_text(" ".join([title, str(extracted.get("meta") or ""), str(extracted.get("text") or "")]))
    score = 4 if is_home else 2
    reasons = ["http_ok_home" if is_home else "http_ok_supporting_page"]
    name_norm = normalize_text(provider.get("provider_name", ""))
    provider_tokens = tokens(provider.get("provider_name", ""))
    page_similarity = _text_similarity(provider.get("provider_name", ""), text[:5000])
    if name_norm and name_norm in text:
        score += 25
        reasons.append("page_contains_exact_provider_name")
    elif page_similarity >= 92:
        score += 18
        reasons.append("page_fuzzy_provider_name_match")
    elif provider_tokens and sum(1 for t in provider_tokens if t in text) >= min(2, len(provider_tokens)):
        score += 12
        reasons.append("page_contains_provider_name_tokens")
    if "javascript is required" in text or "enable javascript" in text:
        reasons.append("page_requires_javascript")
    service_hits = [kw for kw in config.get("service_keywords", []) if normalize_text(kw) in text]
    if len(service_hits) >= 3:
        score += 12
        reasons.append("page_contains_amazon_service_keywords")
    elif service_hits:
        score += 5
        reasons.append("page_contains_some_service_keywords")
    for location in provider.get("provider_locations") or []:
        if normalize_text(location) and normalize_text(location) in text:
            score += 5
            reasons.append(f"page_contains_location:{location}")
            break
    if _text_mentions_provider_location(text, provider) and not any(r.startswith("page_contains_location:") for r in reasons):
        score += 4
        reasons.append("page_mentions_provider_country")
    if any(term in text for term in ["amazon service provider network", "amazon spn", "seller central partner"]):
        score += 18
        reasons.append("page_mentions_amazon_spn")
    if any(term in text for term in ["contact us", "about us", "privacy policy"]):
        score += 3
        reasons.append("site_has_standard_company_pages")
    for mismatch in _industry_mismatch_reasons(text, service_hits):
        score -= 18
        reasons.append(mismatch)
    return {"score": score, "reasons": reasons, "title": title, "status": fetched.get("status")}


def _should_dynamic_render(page_score: dict, config: dict) -> bool:
    dynamic = config.get("dynamic_rendering", {})
    if not dynamic.get("enabled"):
        return False
    trigger_reasons = set(dynamic.get("trigger_reasons") or ["page_requires_javascript"])
    return any(reason in trigger_reasons for reason in page_score.get("reasons", []))


def _dynamic_timeout_ms(config: dict) -> int:
    try:
        return int((config.get("dynamic_rendering") or {}).get("timeout_ms") or 8000)
    except (TypeError, ValueError):
        return 8000


def _dynamic_error_reason(rendered: dict) -> str:
    error = str(rendered.get("error") or "unknown")
    if "playwright_not_installed" in error:
        return "dynamic_render_unavailable"
    return "dynamic_render_failed"


def _extract_page(html: str, url: str) -> dict[str, object]:
    extracted = extract_html(html, url)
    trafilatura_text = _extract_with_trafilatura(html, url)
    if trafilatura_text and len(trafilatura_text) > len(str(extracted.get("text") or "")) * 0.4:
        extracted["text"] = trafilatura_text
        extracted["meta"] = " ".join([str(extracted.get("meta") or ""), "trafilatura_text"]).strip()
    return extracted


def _extract_with_trafilatura(html: str, url: str) -> str:
    try:
        import trafilatura
    except ImportError:
        return ""
    try:
        return trafilatura.extract(html, url=url, include_links=False, include_tables=False) or ""
    except Exception:
        return ""


def _text_similarity(a: str, b: str) -> float:
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm in b_norm or b_norm in a_norm:
        return 100.0
    try:
        from rapidfuzz.fuzz import token_set_ratio
    except ImportError:
        a_tokens = set(a_norm.split())
        b_tokens = set(b_norm.split())
        if not a_tokens or not b_tokens:
            return 0.0
        return 100.0 * len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
    return float(token_set_ratio(a_norm, b_norm))


def _combined_site_score(reasons: list[str]) -> int:
    score = 0
    if "http_ok_home" in reasons:
        score += 4
    elif "http_ok_supporting_page" in reasons:
        score += 2

    if "page_contains_exact_provider_name" in reasons:
        score += 25
    elif "page_contains_provider_name_tokens" in reasons:
        score += 12

    if "page_contains_amazon_service_keywords" in reasons:
        score += 12
    elif "page_contains_some_service_keywords" in reasons:
        score += 5

    if any(r.startswith("page_contains_location:") for r in reasons):
        score += 5
    if "page_mentions_provider_country" in reasons:
        score += 4
    if "page_mentions_amazon_spn" in reasons:
        score += 18
    if "site_has_standard_company_pages" in reasons:
        score += 3
    if "dynamic_rendered_page" in reasons:
        score += 2
    return min(55, score)


def _text_mentions_provider_location(text: str, provider: dict) -> bool:
    for marker in location_text_markers(provider.get("provider_locations") or []):
        normalized = normalize_text(marker)
        if normalized and normalized in text:
            return True
    return False


def _industry_mismatch_reasons(text: str, service_hits: list[str]) -> list[str]:
    if service_hits:
        return []
    categories = {
        "government_medical": [
            "federal institute",
            "government agency",
            "medical devices",
            "medicines",
            "health authority",
            "regulatory authority",
            "pharmaceutical",
        ],
        "financial_accounting": [
            "accounting",
            "tax advisory",
            "internal audit",
            "bookkeeping",
            "financial consulting",
            "audit firm",
        ],
        "fuel_shipping": [
            "marine fuel",
            "bunker fuel",
            "oil trading",
            "shipping fuel",
            "fuel supplier",
        ],
        "offline_retail": [
            "physical stores",
            "retail stores",
            "offline retail",
            "multibrand stores",
            "lojas fisicas",
        ],
    }
    reasons = []
    for category, markers in categories.items():
        hits = [marker for marker in markers if normalize_text(marker) in text]
        if len(hits) >= 1:
            reasons.append(f"page_industry_mismatch:{category}")
    return reasons[:2]


def _apply_identity_caps(score: int, reasons: list[str], provider: dict) -> tuple[int, list[str]]:
    caps: list[tuple[int, str]] = []
    page_identity = _has_any_reason(
        reasons,
        {
            "page_contains_exact_provider_name",
            "page_contains_provider_name_tokens",
            "page_fuzzy_provider_name_match",
        },
    )
    search_identity = _has_any_reason(
        reasons,
        {
            "search_result_contains_exact_name",
            "search_result_contains_name_tokens",
            "search_result_fuzzy_name_match",
        },
    )
    service_identity = _has_any_reason(
        reasons,
        {
            "page_contains_amazon_service_keywords",
            "page_mentions_amazon_spn",
            "search_snippet_contains_amazon_service_keywords",
        },
    )
    country_identity = any(
        reason == "domain_tld_matches_provider_country"
        or reason == "search_result_contains_provider_country"
        or reason == "page_mentions_provider_country"
        or reason.startswith("page_contains_location:")
        for reason in reasons
    )
    country_conflict = "domain_tld_conflicts_provider_country" in reasons
    industry_mismatch = any(reason.startswith("page_industry_mismatch:") for reason in reasons)
    ambiguous_name = _ambiguous_provider_name(provider.get("provider_name", ""))
    logo_identity = _has_any_reason(reasons, {"listing_logo_visual_match", "listing_logo_visual_near_match"})
    weak_page_service = _has_any_reason(
        reasons,
        {
            "page_contains_amazon_service_keywords",
            "page_contains_some_service_keywords",
            "page_mentions_amazon_spn",
        },
    )
    exact_domain_identity = "domain_exact_provider_slug" in reasons
    localized_domain_identity = "domain_contains_provider_slug" in reasons and country_identity
    strong_ambiguous_identity = (
        page_identity
        and weak_page_service
        and (exact_domain_identity or localized_domain_identity or logo_identity)
        and not country_conflict
        and not industry_mismatch
    )

    if industry_mismatch and not service_identity:
        caps.append((49, "identity_cap_industry_mismatch_without_service"))
    if country_conflict and not service_identity:
        caps.append((49, "identity_cap_country_conflict_without_service"))
    elif country_conflict and not (page_identity and service_identity and country_identity):
        caps.append((74, "identity_cap_country_conflict_needs_review"))
    if ambiguous_name and not (((page_identity or logo_identity) and service_identity) or strong_ambiguous_identity):
        caps.append((69, "identity_cap_ambiguous_name_requires_page_and_service"))
    if logo_identity and not (page_identity or service_identity):
        caps.append((69, "identity_cap_logo_only_evidence"))
    if score >= 75 and not service_identity and not country_identity and not (page_identity and search_identity):
        caps.append((69, "identity_cap_missing_service_country_corroboration"))

    if not caps:
        return score, []
    cap_value = min(value for value, _ in caps)
    cap_reasons = [reason for _, reason in caps]
    return min(score, cap_value), cap_reasons


def _has_any_reason(reasons: list[str], targets: set[str]) -> bool:
    return any(reason in targets for reason in reasons)


def _ambiguous_provider_name(name: str) -> bool:
    provider_tokens = tokens(name)
    if not provider_tokens:
        return False
    generic = {
        "amazon",
        "account",
        "agency",
        "consulting",
        "consultancy",
        "digital",
        "ecom",
        "ecommerce",
        "global",
        "growth",
        "management",
        "marketplace",
        "media",
        "seller",
        "service",
        "services",
        "solution",
        "solutions",
        "brand",
        "brands",
    }
    meaningful = [token for token in provider_tokens if token not in generic]
    return len(meaningful) <= 1 or len("".join(provider_tokens)) <= 4


def choose_best(provider: dict, candidates: list[SearchCandidate], config: dict) -> dict:
    max_fetch_candidates = int(config.get("max_fetch_candidates", 0) or 0)
    if max_fetch_candidates > 0 and len(candidates) > max_fetch_candidates:
        preliminary = [pre_score_candidate(provider, c, config) for c in candidates]
        fetch_urls = _urls_to_fetch(preliminary, max_fetch_candidates)
        fetched_scored = {
            c.url.rstrip("/"): score_candidate(provider, c, config)
            for c in candidates
            if c.url.rstrip("/") in fetch_urls
        }
        scored = []
        for item in preliminary:
            fetched = fetched_scored.get(item["url"].rstrip("/"))
            if fetched:
                scored.append(fetched)
            else:
                scored.append(item)
    else:
        scored = [score_candidate(provider, c, config) for c in candidates]
    viable = [s for s in scored if not s.get("reject")]
    viable.sort(key=lambda x: x["score"], reverse=True)
    best = viable[0] if viable else None
    if not best:
        return {
            "official_url": "",
            "official_domain": "",
            "confidence": 0,
            "status": "not_found",
            "evidence_summary": "No non-excluded candidate domains found.",
            "candidates": scored,
        }
    confidence = max(0, min(100, int(best["score"])))
    if confidence >= config.get("auto_match_threshold", 75):
        status = "matched"
    elif confidence >= config.get("review_threshold", 45):
        status = "needs_review"
    else:
        status = "low_confidence"
    return {
        "official_url": best["url"] if status != "low_confidence" else "",
        "official_domain": best["domain"] if status != "low_confidence" else "",
        "confidence": confidence,
        "status": status,
        "evidence_summary": "; ".join(_summary_reasons(best["reasons"])),
        "candidates": scored[: config.get("max_candidates_per_provider", 60)],
    }


def _urls_to_fetch(preliminary: list[dict], max_fetch_candidates: int) -> set[str]:
    viable = [item for item in preliminary if not item.get("reject")]
    viable.sort(key=lambda x: x["score"], reverse=True)
    return {item["url"].rstrip("/") for item in viable[:max_fetch_candidates]}


def _summary_reasons(reasons: list[str]) -> list[str]:
    priority = [
        r
        for r in reasons
        if r
        in {
            "javascript_page_requires_dynamic_review",
            "page_requires_javascript",
            "dynamic_rendered_page",
            "dynamic_render_unavailable",
            "dynamic_render_failed",
            "page_contains_amazon_service_keywords",
            "page_contains_some_service_keywords",
            "search_snippet_contains_amazon_service_keywords",
            "search_snippet_contains_some_service_keywords",
            "page_mentions_amazon_spn",
            "page_contains_exact_provider_name",
            "page_contains_provider_name_tokens",
            "page_fuzzy_provider_name_match",
            "listing_logo_visual_match",
            "listing_logo_visual_near_match",
            "identity_cap_industry_mismatch_without_service",
            "identity_cap_country_conflict_without_service",
            "identity_cap_country_conflict_needs_review",
            "identity_cap_ambiguous_name_requires_page_and_service",
            "identity_cap_logo_only_evidence",
            "identity_cap_missing_service_country_corroboration",
        }
        or r.startswith("page_industry_mismatch:")
    ]
    out = []
    for reason in priority + reasons:
        if reason not in out:
            out.append(reason)
        if len(out) >= 6:
            break
    return out
