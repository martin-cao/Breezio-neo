from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import urlencode
from urllib.request import urlopen


@dataclass(frozen=True)
class SerialControl:
    path: str
    baud: int | None = None
    dtr: bool | None = None
    rts: bool | None = None


class XzgBridgeClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def discover(self, service_types: str = "_xzg-mt._tcp.local", timeout_ms: int = 2000) -> dict:
        query = urlencode({"types": service_types, "timeout": timeout_ms})
        return self._get_json(f"/mdns?{query}")

    def serial_control(self, control: SerialControl) -> dict:
        params: dict[str, str | int] = {"path": control.path}
        if control.baud is not None:
            params["baud"] = control.baud
        if control.dtr is not None:
            params["dtr"] = int(control.dtr)
        if control.rts is not None:
            params["rts"] = int(control.rts)
        return self._get_json(f"/sc?{urlencode(params)}")

    def _get_json(self, path: str) -> dict:
        with urlopen(self.base_url + path, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

