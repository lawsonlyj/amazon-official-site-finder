from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class AgentClientError(RuntimeError):
    pass


class AgentConfigurationError(AgentClientError):
    pass


@dataclass
class OpenAIJsonClient:
    api_key: str
    model: str
    timeout: int = 60
    endpoint: str = "https://api.openai.com/v1/chat/completions"

    @classmethod
    def from_env(
        cls,
        *,
        model_env: str,
        default_model: str = "gpt-4.1-mini",
        timeout_env: str = "FINDER_DEV_AGENT_TIMEOUT",
    ) -> "OpenAIJsonClient":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise AgentConfigurationError("OPENAI_API_KEY is not configured.")
        model = os.getenv(model_env, "").strip() or os.getenv("FINDER_DEV_AGENT_MODEL", "").strip() or default_model
        timeout = _to_int(os.getenv(timeout_env), default=60)
        return cls(api_key=api_key, model=model, timeout=timeout)

    def complete_json(self, *, system_prompt: str, user_payload: dict[str, Any], max_tokens: int = 1400) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _safe_error_detail(exc)
            raise AgentClientError(f"OpenAI API request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AgentClientError(f"OpenAI API request failed: {exc.reason}") from exc
        try:
            data = json.loads(body)
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AgentClientError("OpenAI API response did not contain valid JSON content.") from exc


def _safe_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    try:
        data = json.loads(body)
        message = data.get("error", {}).get("message", "")
        return str(message)[:500]
    except Exception:
        return body[:500]


def _to_int(value: object, *, default: int) -> int:
    try:
        parsed = int(float(value or 0))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
