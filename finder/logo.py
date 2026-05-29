from __future__ import annotations

import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from .http import USER_AGENT, cache_dir


class LogoHTMLParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "meta":
            prop = (attrs_dict.get("property") or attrs_dict.get("name") or "").lower()
            if prop in {"og:image", "twitter:image", "twitter:image:src"} and attrs_dict.get("content"):
                self.urls.append(urljoin(self.base_url, attrs_dict["content"]))
        if tag == "link":
            rel = attrs_dict.get("rel", "").lower()
            if any(marker in rel for marker in ["icon", "apple-touch-icon"]) and attrs_dict.get("href"):
                self.urls.append(urljoin(self.base_url, attrs_dict["href"]))
        if tag == "img" and attrs_dict.get("src"):
            haystack = " ".join(
                [
                    attrs_dict.get("alt", ""),
                    attrs_dict.get("class", ""),
                    attrs_dict.get("id", ""),
                    attrs_dict.get("src", ""),
                ]
            ).casefold()
            if any(marker in haystack for marker in ["logo", "brand", "company"]):
                self.urls.append(urljoin(self.base_url, attrs_dict["src"]))


def logo_evidence(listing_logo_url: str, html: str, base_url: str, *, max_candidates: int = 6) -> dict:
    if os.getenv("FINDER_LOGO_MATCH_ENABLED", "1").strip().casefold() in {"0", "false", "no"}:
        return _empty("disabled")
    if not listing_logo_url or not html:
        return _empty("missing_listing_logo_or_html")
    candidate_urls = extract_logo_urls(html, base_url)[:max_candidates]
    if not candidate_urls:
        return _empty("no_candidate_logo_urls")
    listing_hash = image_average_hash(listing_logo_url)
    if not listing_hash:
        return {"matched": False, "score": 0, "reason": "listing_logo_unavailable", "candidate_logo_urls": candidate_urls}
    best: dict = {"matched": False, "score": 0, "reason": "no_logo_match", "candidate_logo_urls": candidate_urls}
    for candidate_url in candidate_urls:
        candidate_hash = image_average_hash(candidate_url)
        if not candidate_hash:
            continue
        similarity = hash_similarity(listing_hash, candidate_hash)
        if similarity > best["score"]:
            best = {
                "matched": similarity >= 0.88,
                "score": round(similarity, 3),
                "reason": "logo_visual_match" if similarity >= 0.88 else "logo_visual_near_match",
                "candidate_logo_url": candidate_url,
                "candidate_logo_urls": candidate_urls,
            }
    return best


def extract_logo_urls(html: str, base_url: str) -> list[str]:
    parser = LogoHTMLParser(base_url)
    parser.feed(html or "")
    out = []
    for url in parser.urls:
        if url and url not in out:
            out.append(url)
    return out


def image_average_hash(url: str) -> str:
    try:
        from PIL import Image
    except ImportError:
        return ""
    data = _fetch_binary(url)
    if not data:
        return ""
    try:
        image = Image.open(io.BytesIO(data)).convert("L").resize((8, 8))
    except Exception:
        return ""
    pixels = list(image.getdata())
    avg = sum(pixels) / len(pixels)
    return "".join("1" if pixel >= avg else "0" for pixel in pixels)


def hash_similarity(left: str, right: str) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    matches = sum(1 for a, b in zip(left, right) if a == b)
    return matches / len(left)


def _fetch_binary(url: str, *, use_cache: bool = True) -> bytes:
    url = _safe_url(url)
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    path = cache_dir() / "images" / f"{key}.json"
    if use_cache and path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return bytes.fromhex(payload.get("data_hex", ""))
    timeout = float(os.getenv("FINDER_HTTP_TIMEOUT", "12"))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.2"})
    started = time.time()
    data = b""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status and 200 <= status < 400:
                data = resp.read(500_000)
    except Exception:
        data = b""
    if use_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"url": url, "ok": bool(data), "elapsed_ms": int((time.time() - started) * 1000), "data_hex": data.hex()}),
            encoding="utf-8",
        )
    return data


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    path = urllib.parse.quote(parsed.path, safe="/:%")
    query = urllib.parse.quote(parsed.query, safe="=&?/%:+")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, parsed.fragment))


def _empty(reason: str) -> dict:
    return {"matched": False, "score": 0, "reason": reason, "candidate_logo_urls": []}
