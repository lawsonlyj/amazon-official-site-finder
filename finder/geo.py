from __future__ import annotations

from .text import normalize_text


GENERIC_TLDS = {
    "com",
    "co",
    "io",
    "net",
    "org",
    "biz",
    "info",
    "agency",
    "digital",
    "marketing",
    "consulting",
    "solutions",
    "services",
    "top",
    "xyz",
    "ai",
}

COUNTRY_PROFILES = [
    {
        "code": "US",
        "names": ["united states", "united states of america", "usa", "u.s.", "u.s.a.", "america"],
        "tlds": ["us"],
        "official_terms": ["official website", "contact us", "about us"],
        "service_terms": ["amazon agency", "amazon account management", "amazon advertising"],
    },
    {
        "code": "GB",
        "names": ["united kingdom", "uk", "great britain", "england"],
        "tlds": ["uk", "co.uk"],
        "official_terms": ["official website", "contact us", "companies house"],
        "service_terms": ["amazon agency", "seller central agency"],
    },
    {
        "code": "DE",
        "names": ["germany", "deutschland"],
        "tlds": ["de"],
        "official_terms": ["offizielle website", "offizielle seite", "kontakt", "impressum"],
        "service_terms": ["amazon agentur", "amazon seller central", "marktplatz agentur"],
    },
    {
        "code": "IT",
        "names": ["italy", "italia"],
        "tlds": ["it"],
        "official_terms": ["sito ufficiale", "contatti", "chi siamo", "partita iva"],
        "service_terms": ["agenzia amazon", "gestione account amazon", "marketplace amazon"],
    },
    {
        "code": "BR",
        "names": ["brazil", "brasil"],
        "tlds": ["br", "com.br"],
        "official_terms": ["site oficial", "contato", "sobre", "cnpj"],
        "service_terms": ["agencia amazon", "gestao de marketplace", "gestao de conta amazon"],
    },
    {
        "code": "PT",
        "names": ["portugal"],
        "tlds": ["pt"],
        "official_terms": ["site oficial", "contacto", "sobre"],
        "service_terms": ["agencia amazon", "gestao de marketplace"],
    },
    {
        "code": "FR",
        "names": ["france"],
        "tlds": ["fr"],
        "official_terms": ["site officiel", "contact", "a propos", "mentions legales", "siren"],
        "service_terms": ["agence amazon", "gestion compte amazon", "marketplace amazon"],
    },
    {
        "code": "ES",
        "names": ["spain", "espana", "españa"],
        "tlds": ["es"],
        "official_terms": ["sitio web oficial", "contacto", "sobre nosotros", "cif"],
        "service_terms": ["agencia amazon", "gestion cuenta amazon", "marketplace amazon"],
    },
    {
        "code": "SK",
        "names": ["slovakia", "slovak republic", "slovensko"],
        "tlds": ["sk"],
        "official_terms": ["oficialna stranka", "kontakt", "o nas"],
        "service_terms": ["amazon agentura", "sprava amazon uctu"],
    },
    {
        "code": "UA",
        "names": ["ukraine", "ukraina"],
        "tlds": ["ua", "com.ua"],
        "official_terms": ["official site", "kontakt", "oficiinyi sait"],
        "service_terms": ["amazon agency", "amazon marketplace"],
    },
    {
        "code": "PL",
        "names": ["poland", "polska"],
        "tlds": ["pl"],
        "official_terms": ["oficjalna strona", "kontakt", "o nas"],
        "service_terms": ["agencja amazon", "obsluga konta amazon"],
    },
    {
        "code": "IN",
        "names": ["india", "bharat"],
        "tlds": ["in", "co.in"],
        "official_terms": ["official website", "contact us", "mca", "gst"],
        "service_terms": ["amazon seller services", "marketplace management"],
    },
    {
        "code": "MX",
        "names": ["mexico", "méxico"],
        "tlds": ["mx", "com.mx"],
        "official_terms": ["sitio oficial", "contacto", "acerca de"],
        "service_terms": ["agencia amazon", "gestion marketplace"],
    },
    {
        "code": "NL",
        "names": ["netherlands", "nederland", "holland"],
        "tlds": ["nl"],
        "official_terms": ["officiele website", "contact", "over ons"],
        "service_terms": ["amazon bureau", "marketplace management"],
    },
]


def profiles_for_locations(locations: list[str] | str | None) -> list[dict]:
    if isinstance(locations, str):
        location_values = [locations]
    else:
        location_values = list(locations or [])
    text = normalize_text(" ".join(location_values))
    out = []
    for profile in COUNTRY_PROFILES:
        if any(normalize_text(name) in text for name in profile["names"]):
            out.append(profile)
    return out


def local_search_terms(locations: list[str] | str | None) -> list[str]:
    terms: list[str] = []
    for profile in profiles_for_locations(locations):
        terms.extend(profile["official_terms"][:3])
        terms.extend(profile["service_terms"][:2])
    return _dedupe(terms)


def location_text_markers(locations: list[str] | str | None) -> list[str]:
    markers: list[str] = []
    for profile in profiles_for_locations(locations):
        markers.extend(profile["names"])
    if isinstance(locations, str):
        markers.append(locations)
    else:
        markers.extend(str(item) for item in locations or [])
    return _dedupe([item for item in markers if item])


def domain_country_signal(domain: str, locations: list[str] | str | None) -> str:
    suffix = _domain_suffix(domain)
    if not suffix or suffix in GENERIC_TLDS:
        return "neutral"
    expected = {tld for profile in profiles_for_locations(locations) for tld in profile["tlds"]}
    if expected and suffix in expected:
        return "match"
    known = {tld for profile in COUNTRY_PROFILES for tld in profile["tlds"]}
    if suffix in known and expected:
        return "conflict"
    return "neutral"


def _domain_suffix(domain: str) -> str:
    parts = (domain or "").lower().strip(".").split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "net", "org"}:
        return ".".join(parts[-2:])
    if len(parts) >= 2:
        return parts[-1]
    return ""


def _dedupe(values: list[str]) -> list[str]:
    out = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out
