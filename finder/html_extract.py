from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin


class BasicHTMLExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.meta: list[str] = []
        self.links: list[str] = []
        self.text_parts: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k.lower(): v or "" for k, v in attrs}
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            name = (attrs_dict.get("name") or attrs_dict.get("property") or "").lower()
            if name in {"description", "og:description", "og:title", "twitter:description"}:
                self.meta.append(attrs_dict.get("content", ""))
        if tag == "a" and attrs_dict.get("href"):
            self.links.append(urljoin(self.base_url, attrs_dict["href"]))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        if self._in_title:
            self.title_parts.append(value)
        self.text_parts.append(value)


def extract_html(html: str, base_url: str) -> dict[str, object]:
    parser = BasicHTMLExtractor(base_url)
    parser.feed(html or "")
    text = " ".join(parser.text_parts)
    return {
        "title": " ".join(parser.title_parts)[:300],
        "meta": " ".join(parser.meta)[:600],
        "text": text[:80_000],
        "links": parser.links[:500],
    }
