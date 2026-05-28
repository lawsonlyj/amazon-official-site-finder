from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .http import cache_dir


def render_dynamic_page(url: str, *, timeout_ms: int = 8000, use_cache: bool = True) -> dict[str, Any]:
    path = cache_dir() / "dynamic_pages" / f"{_cache_key(url)}.json"
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    result: dict[str, Any] = {"url": url, "ok": False, "status": None, "final_url": url, "text": "", "error": ""}
    started = time.time()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result["error"] = "playwright_not_installed"
        _write_cache(path, result, use_cache)
        return result

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=os.getenv("FINDER_USER_AGENT", "official-site-finder/0.1"))
            response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_timeout(500)
            result.update(
                {
                    "ok": True,
                    "status": response.status if response else None,
                    "final_url": page.url,
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "text": page.content(),
                }
            )
            browser.close()
    except Exception as exc:
        result.update({"error": f"{type(exc).__name__}: {exc}"})

    _write_cache(path, result, use_cache)
    return result


def _cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _write_cache(path: Path, result: dict[str, Any], use_cache: bool) -> None:
    if not use_cache:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
