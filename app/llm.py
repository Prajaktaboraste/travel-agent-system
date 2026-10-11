"""Optional LLM used to turn retrieved passages into a fluent answer.

If no API key is configured (or the call fails) callers fall back to an
extractive answer, so the system never depends on the network.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)
API_URL = "https://api.anthropic.com/v1/messages"


class LLMClient:
    def __init__(self, api_key: str | None, model: str, timeout: float = 30.0):
        self.api_key, self.model, self.timeout = api_key, model, timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, user: str, max_tokens: int = 500) -> str | None:
        if not self.available:
            return None
        try:
            resp = httpx.post(
                API_URL,
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": self.model, "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            blocks = resp.json().get("content", [])
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            return text or None
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM call failed, using extractive fallback: %s", exc)
            return None
