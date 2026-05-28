import re
import unicodedata
from urllib.parse import urlparse


LEGAL_SUFFIX_RE = re.compile(
    r"\b(private limited|pvt ltd|pvt\. ltd\.|limited|ltd|llc|llp|inc|corp|corporation|gmbh|srl|sl|sas|ag|bv|plc|co\.?|company)\b",
    re.I,
)


def compact_space(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = LEGAL_SUFFIX_RE.sub(" ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return compact_space(value)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value))


def tokens(value: str) -> list[str]:
    return [t for t in normalize_text(value).split() if len(t) >= 3]


def domain_from_url(url: str) -> str:
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return ""
    host = (parsed.netloc or parsed.path).lower()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    host = host.split(":", 1)[0].strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def base_domain_label(domain: str) -> str:
    parts = domain_from_url(domain).split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "net", "org"}:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else ""


def url_like_candidates(text: str) -> list[str]:
    pattern = re.compile(
        r"(https?://[^\s\"'<>]+|www\.[^\s\"'<>]+|[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.(?:com|co|io|net|org|de|fr|it|es|in|pk|uk|nl|pl|ae|ca|au|us|cn|jp|sg|br|mx|tr|eu)\b)",
        re.I,
    )
    out = []
    for match in pattern.findall(text or ""):
        out.append(match.rstrip(".,);]"))
    return out
