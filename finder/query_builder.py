from __future__ import annotations

from .text import compact_space, slug


def build_queries(provider: dict, *, include_github: bool = True) -> list[str]:
    name = compact_space(provider.get("provider_name", ""))
    locations = provider.get("provider_locations") or []
    services = provider.get("service_apis") or []
    service_hint = services[0] if services else "Amazon service provider"
    queries = [
        f'"{name}" official website',
        f'"{name}" Amazon service provider',
        f'"{name}" "{service_hint}"',
        f'"{name}" Seller Central',
    ]
    if locations:
        queries.append(f'"{name}" "{locations[0]}" website')
    if slug(name):
        queries.append(f'{slug(name)} website')
    if include_github:
        queries.extend(
            [
                f'site:github.com "{name}"',
                f'site:github.com "{name}" website',
            ]
        )
    out = []
    for query in queries:
        if query not in out:
            out.append(query)
    return out
