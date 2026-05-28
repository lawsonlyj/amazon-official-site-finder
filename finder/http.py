from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


USER_AGENT = "official-site-finder/0.1 (+https://github.com/)"


def cache_dir() -> Path:
    return Path(os.getenv("FINDER_CACHE_DIR", ".cache"))


def _cache_key(method: str, url: str, body: bytes | None = None) -> str:
    h = hashlib.sha1()
    h.update(method.encode())
    h.update(b"\0")
    h.update(url.encode())
    if body:
        h.update(b"\0")
        h.update(body)
    return h.hexdigest()


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    cache_namespace: str = "json",
    use_cache: bool = True,
) -> dict[str, Any]:
    body = None
    final_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        final_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        final_headers["Content-Type"] = "application/json"
    key = _cache_key(method, url, body)
    path = cache_dir() / cache_namespace / f"{key}.json"
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    timeout = float(os.getenv("FINDER_HTTP_TIMEOUT", "12"))
    req = urllib.request.Request(url, data=body, method=method, headers=final_headers)
    attempts = _json_request_attempts()
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            break
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts or not _should_retry_json_error(exc):
                raise
            time.sleep(_json_retry_delay(attempt))
    else:
        assert last_exc is not None
        raise last_exc
    parsed = json.loads(data.decode("utf-8", errors="replace"))
    if use_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    return parsed


def _json_request_attempts() -> int:
    retries = int(os.getenv("FINDER_SEARCH_RETRIES", "2"))
    return max(1, retries + 1)


def _json_retry_delay(attempt: int) -> float:
    base = float(os.getenv("FINDER_SEARCH_RETRY_DELAY", "1"))
    return max(0.0, base * attempt)


def _should_retry_json_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    return isinstance(exc, urllib.error.URLError)


def fetch_text(url: str, *, use_cache: bool = True) -> dict[str, Any]:
    key = _cache_key("GET", url)
    path = cache_dir() / "pages" / f"{key}.json"
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    timeout = float(os.getenv("FINDER_HTTP_TIMEOUT", "12"))
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.2",
        },
    )
    started = time.time()
    result: dict[str, Any] = {"url": url, "ok": False, "status": None, "final_url": url, "text": "", "error": ""}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("content-type", "")
            data = resp.read(750_000)
            result.update(
                {
                    "ok": 200 <= resp.status < 400,
                    "status": resp.status,
                    "final_url": resp.url,
                    "content_type": content_type,
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "text": data.decode(_charset(content_type), errors="replace"),
                }
            )
    except urllib.error.HTTPError as exc:
        result.update({"status": exc.code, "error": str(exc)})
    except Exception as exc:  # Network failures should not stop the batch.
        result.update({"error": f"{type(exc).__name__}: {exc}"})
    if use_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def _charset(content_type: str) -> str:
    parsed = urllib.parse.parse_qs(content_type.replace(";", "&"))
    charset = parsed.get("charset", ["utf-8"])[0]
    return charset or "utf-8"
