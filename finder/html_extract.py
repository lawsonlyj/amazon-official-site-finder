from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin


VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class BasicHTMLExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.meta: list[str] = []
        self.links: list[str] = []
        self.mailto_links: list[str] = []
        self.tel_links: list[str] = []
        self.headings: dict[str, list[str]] = {"h1": [], "h2": []}
        self.nav_parts: list[str] = []
        self.footer_parts: list[str] = []
        self.json_ld_parts: list[str] = []
        self.text_parts: list[str] = []
        self._in_title = False
        self._heading_tag = ""
        self._in_json_ld = False
        self._skip_depth = 0
        self._context_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k.lower(): v or "" for k, v in attrs}
        tag = tag.lower()
        context = self._context_for(tag, attrs_dict)
        if tag not in VOID_TAGS:
            self._context_stack.append(context)
        if tag == "script" and "ld+json" in attrs_dict.get("type", "").lower():
            self._in_json_ld = True
            return
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in {"h1", "h2"}:
            self._heading_tag = tag
        if tag == "meta":
            name = (attrs_dict.get("name") or attrs_dict.get("property") or "").lower()
            if name in {"description", "og:description", "og:title", "twitter:description"}:
                self.meta.append(attrs_dict.get("content", ""))
        if tag == "a" and attrs_dict.get("href"):
            href = attrs_dict["href"]
            full_url = urljoin(self.base_url, href)
            self.links.append(full_url)
            if href.lower().startswith("mailto:"):
                self.mailto_links.append(href)
            elif href.lower().startswith("tel:"):
                self.tel_links.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "script" and self._in_json_ld:
            self._in_json_ld = False
        if tag in {"h1", "h2"} and self._heading_tag == tag:
            self._heading_tag = ""
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if self._context_stack:
            self._context_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self.json_ld_parts.append(data)
            return
        if self._skip_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        if self._in_title:
            self.title_parts.append(value)
        if self._heading_tag:
            self.headings.setdefault(self._heading_tag, []).append(value)
        if "nav" in self._context_stack:
            self.nav_parts.append(value)
        if "footer" in self._context_stack:
            self.footer_parts.append(value)
        self.text_parts.append(value)

    @staticmethod
    def _context_for(tag: str, attrs: dict[str, str]) -> str:
        role = attrs.get("role", "").lower()
        class_id = " ".join([attrs.get("class", ""), attrs.get("id", "")]).lower()
        if tag == "nav" or role == "navigation" or "nav" in class_id or "menu" in class_id:
            return "nav"
        if tag == "footer" or role == "contentinfo" or "footer" in class_id:
            return "footer"
        return ""


def extract_html(html: str, base_url: str) -> dict[str, object]:
    parser = BasicHTMLExtractor(base_url)
    parser.feed(html or "")
    text = " ".join(parser.text_parts)
    json_ld = _parse_json_ld(parser.json_ld_parts)
    return {
        "title": " ".join(parser.title_parts)[:300],
        "meta": " ".join(parser.meta)[:600],
        "h1": " ".join(parser.headings.get("h1", []))[:500],
        "h2": " ".join(parser.headings.get("h2", []))[:800],
        "nav": " ".join(parser.nav_parts)[:1200],
        "footer": " ".join(parser.footer_parts)[:2000],
        "mailto_links": _dedupe(parser.mailto_links)[:50],
        "tel_links": _dedupe(parser.tel_links)[:50],
        "json_ld": json_ld,
        "organizations": _json_ld_organizations(json_ld),
        "text": text[:80_000],
        "links": parser.links[:500],
    }


def _parse_json_ld(parts: list[str]) -> list[object]:
    out = []
    for raw in parts:
        value = raw.strip()
        if not value:
            continue
        for candidate in _json_candidates(value):
            try:
                out.append(json.loads(candidate))
            except json.JSONDecodeError:
                continue
    return out


def _json_candidates(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("{") or value.startswith("["):
        return [value]
    matches = re.findall(r"(\{.*?\}|\[.*?\])", value, flags=re.DOTALL)
    return matches or [value]


def _json_ld_organizations(values: list[object]) -> list[dict[str, str]]:
    out = []
    for node in _walk_json_ld(values):
        if not isinstance(node, dict) or not _is_org_type(node.get("@type")):
            continue
        item = {
            "name": _string_value(node.get("name")),
            "legalName": _string_value(node.get("legalName")),
            "url": _string_value(node.get("url")),
            "logo": _string_value(node.get("logo")),
            "address": _address_value(node.get("address")),
            "contactPoint": _contact_value(node.get("contactPoint")),
        }
        out.append(item)
    return out[:20]


def _walk_json_ld(value: object):
    if isinstance(value, list):
        for item in value:
            yield from _walk_json_ld(item)
    elif isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _walk_json_ld(item)
        for item in value.values():
            if isinstance(item, (dict, list)):
                yield from _walk_json_ld(item)


def _is_org_type(value: object) -> bool:
    org_types = {"organization", "localbusiness", "corporation", "professionalservice"}
    if isinstance(value, list):
        return any(_is_org_type(item) for item in value)
    return str(value or "").strip().lower() in org_types


def _string_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ["url", "@id", "name"]:
            if isinstance(value.get(key), str):
                return value[key].strip()
    if isinstance(value, list):
        return "; ".join(_string_value(item) for item in value if _string_value(item))[:1000]
    return ""


def _address_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "; ".join(_address_value(item) for item in value if _address_value(item))[:1000]
    if not isinstance(value, dict):
        return ""
    parts = []
    for key in ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"]:
        if value.get(key):
            parts.append(str(value[key]))
    return ", ".join(parts)


def _contact_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "; ".join(_contact_value(item) for item in value if _contact_value(item))[:1000]
    if not isinstance(value, dict):
        return ""
    parts = []
    for key in ["telephone", "email", "contactType", "areaServed"]:
        if value.get(key):
            parts.append(str(value[key]))
    return ", ".join(parts)


def _dedupe(values: list[str]) -> list[str]:
    out = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out
