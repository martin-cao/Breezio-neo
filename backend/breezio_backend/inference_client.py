from __future__ import annotations

import json
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class InferenceClientError(RuntimeError):
    pass


class InferenceClient:
    def __init__(self, base_url: str, timeout_seconds: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/inference/health")

    def list_models(self, task: Optional[str] = None) -> list[dict[str, Any]]:
        query = {"task": task} if task else None
        return self._request("GET", "/api/inference/models", query=query)

    def register_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/inference/models", payload=payload)

    def activate_model(self, model_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/inference/models/{model_id}/activate")

    def predict(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/inference/predict", payload=payload)

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[dict[str, Any]] = None,
        query: Optional[dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode({key: value for key, value in query.items() if value is not None})}"

        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        try:
            with urlopen(Request(url, data=data, headers=headers, method=method), timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"message": raw}
            message = parsed.get("message") or parsed.get("error") or str(exc)
            raise InferenceClientError(str(message)) from exc
        except URLError as exc:
            raise InferenceClientError(str(exc.reason)) from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InferenceClientError("inference service returned invalid JSON") from exc

        if isinstance(parsed, dict) and parsed.get("success") is False:
            raise InferenceClientError(str(parsed.get("message") or parsed.get("error") or "inference request failed"))
        return parsed.get("data") if isinstance(parsed, dict) and "data" in parsed else parsed
