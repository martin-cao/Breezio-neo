from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SMARTWIN_GATEWAY = ROOT / "sensor-new" / "gateway"
sys.path.insert(0, str(SMARTWIN_GATEWAY))

from smartwin_gateway.gateway import parse_stream, serial_lines  # noqa: E402
from smartwin_gateway.protocol import Telemetry  # noqa: E402


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Bridge sensor-new $BRZ2 serial telemetry into the backend /api/readings endpoint")
    p.add_argument("--serial-port", required=True)
    p.add_argument("--baudrate", type=int, default=115200)
    p.add_argument("--backend-url", default="http://127.0.0.1:8000")
    p.add_argument("--gateway-id", default="gw-smartwin-01")
    p.add_argument("--node-id", default="win-01", help="fallback node id until a HELLO frame is seen")
    p.add_argument("--source", default="serial_hardware")
    p.add_argument("--once", action="store_true", help="ingest one telemetry frame and exit")
    return p


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"backend POST failed: {exc.code} {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"backend unavailable: {exc.reason}") from exc


def telemetry_payload(args: argparse.Namespace, node_id: Optional[str], telemetry: Telemetry) -> dict:
    resolved_node_id = node_id or args.node_id
    return {
        "gateway_id": args.gateway_id,
        "batch_id": f"smartwin-{telemetry.seq}",
        "source": args.source,
        "records": [
            {
                "node_id": resolved_node_id,
                "role": "sensor",
                "seq": telemetry.seq,
                "temperature": telemetry.temperature_c,
                "humidity": telemetry.humidity_pct,
                "light": telemetry.brightness_pct,
                "flags": 1 if telemetry.alarm else 0,
                "source": args.source,
            }
        ],
    }


def main(argv: Optional[list[str]] = None) -> int:
    args = parser().parse_args(argv)
    endpoint = f"{args.backend_url.rstrip('/')}/api/readings"
    count = 0
    while True:
        try:
            lines = serial_lines(args.serial_port, args.baudrate)
            for message, node_id in parse_stream(lines):
                if not isinstance(message, Telemetry):
                    continue
                payload = telemetry_payload(args, node_id, message)
                try:
                    result = post_json(endpoint, payload)
                except RuntimeError as exc:
                    print(
                        json.dumps(
                            {
                                "seq": message.seq,
                                "node_id": payload["records"][0]["node_id"],
                                "error": str(exc),
                            },
                            ensure_ascii=False,
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
                    if args.once:
                        return 1
                    continue
                count += 1
                print(
                    json.dumps(
                        {
                            "seq": message.seq,
                            "node_id": payload["records"][0]["node_id"],
                            "temperature": message.temperature_c,
                            "humidity": message.humidity_pct,
                            "brightness": message.brightness_pct,
                            "air_quality_mv": message.air_quality_mv,
                            "backend": result.get("data", result),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if args.once:
                    return 0
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "serial_port": args.serial_port,
                        "error": f"serial read failed: {exc}",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )
            if args.once:
                return 1
            time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
