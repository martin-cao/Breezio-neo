"""Minimal OpenAI-compatible chat-completions client.

One client for every provider: Ollama, LM Studio, llama.cpp server and hosted
OpenAI-shaped APIs all speak POST {base_url}/chat/completions, so provider
choice is pure configuration (settings.chatbot.base_url + model), not code.
The API key comes from the BREEZIO_LLM_API_KEY env var only — never from the
user-settings blob, which round-trips to the browser.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMClientError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: Optional[str] = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key if api_key is not None else os.getenv("BREEZIO_LLM_API_KEY")
        self.timeout_seconds = timeout_seconds

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """One chat-completions round trip; returns choices[0].message.

        If the server rejects the request specifically because `tools` is
        present (some local servers/models don't support tool calling), retry
        once without tools so the assistant degrades to plain chat instead of
        hard-failing.
        """
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            return self._completions(payload)
        except LLMClientError as exc:
            if tools and exc.status is not None and 400 <= exc.status < 500:
                fallback = {"model": self.model, "messages": messages}
                return self._completions(fallback)
            raise

    def _completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            with urlopen(Request(url, data=data, headers=headers, method="POST"), timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMClientError(f"LLM backend returned {exc.code}: {detail[:500]}", status=exc.code) from exc
        except URLError as exc:
            raise LLMClientError(f"LLM backend unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMClientError("LLM backend timed out") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMClientError("LLM backend returned invalid JSON") from exc
        choices = parsed.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise LLMClientError("LLM backend response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise LLMClientError("LLM backend response has no message")
        return message
