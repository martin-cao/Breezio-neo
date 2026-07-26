"""Seed a running local backend through its public API for a hardware-free demo.

The seed data is tagged source="simulated". It is intended only to make the
frontend exercise real backend routes while no physical camera/sensor hardware
is attached.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SeedPlan:
    reading_batches: list[dict[str, Any]]
    vision_model: dict[str, Any]
    extra_vision_models: list[dict[str, Any]]
    vision_prediction: dict[str, Any]
    prediction_run: dict[str, Any]


def build_seed_plan(
    node_id: str,
    gateway_id: str,
    samples: int = 60,
    captured_at: datetime | None = None,
    interval_seconds: int = 60,
    vision_runtime_mode: str = "dry-run",
) -> SeedPlan:
    if vision_runtime_mode not in {"dry-run", "pretrained"}:
        raise ValueError("vision_runtime_mode must be dry-run or pretrained")

    end = captured_at or datetime.now(timezone.utc)
    seed_id = end.strftime("%Y%m%dT%H%M%SZ")
    start = end - timedelta(seconds=(samples - 1) * interval_seconds)
    reading_batches = []
    for index in range(samples):
        ts = start + timedelta(seconds=index * interval_seconds)
        minute_of_day = ts.hour * 60 + ts.minute
        temperature = 24.0 + 2.5 * math.sin(2 * math.pi * minute_of_day / 1440) + 0.1 * math.sin(index / 3)
        humidity = 54.0 - 7.0 * math.sin(2 * math.pi * minute_of_day / 1440) + 0.2 * math.cos(index / 5)
        light = max(0.0, 360.0 * math.sin(math.pi * max(0, min(14, ts.hour - 7)) / 14)) if 7 <= ts.hour <= 21 else 2.0
        reading_batches.append(
            {
                "message_id": f"demo-seed-{seed_id}-{gateway_id}-{node_id}-{index}",
                "gateway_id": gateway_id,
                "batch_id": f"demo-seed-{seed_id}-batch-{index}",
                "captured_at": ts.isoformat(),
                "source": "simulated",
                "records": [
                    {
                        "node_id": node_id,
                        "seq": index,
                        "temperature": round(temperature, 2),
                        "humidity": round(humidity, 2),
                        "light": round(light, 1),
                        "battery_mv": 3010 - (index % 11),
                        "rssi": -58 - (index % 6),
                        "source": "simulated",
                    }
                ],
            }
        )

    scene_model = {
        "model_id": "scene-demo-v1",
        "task": "scene-classification",
        "version": "demo",
        "runtime": "dry-run",
        "labels": ["vacant", "normal", "crowded", "abnormal"],
    }
    if vision_runtime_mode == "pretrained":
        extra_vision_models = [
            {
                "model_id": "person-yolo-pretrained-v1",
                "task": "person-detection",
                "version": "yolov8n",
                "runtime": "ultralytics-yolo",
                "labels": ["person"],
            },
            {
                "model_id": "face-insightface-pretrained-v1",
                "task": "face-identification",
                "version": "buffalo_l",
                "runtime": "insightface-onnx",
            },
            {
                "model_id": "anomaly-dinov2-pretrained-v1",
                "task": "anomaly-detection",
                "version": "dinov2_vits14",
                "runtime": "dinov2-knn",
            },
            {
                "model_id": "caption-moondream-pretrained-v1",
                "task": "scene-caption",
                "version": "moondream2-2025-06-21",
                "runtime": "moondream-vlm",
            },
        ]
    else:
        extra_vision_models = [
            {
                "model_id": "person-demo-v1",
                "task": "person-detection",
                "version": "demo",
                "runtime": "dry-run",
                "labels": ["person"],
            },
            {
                "model_id": "face-demo-v1",
                "task": "face-identification",
                "version": "demo",
                "runtime": "dry-run",
            },
            {
                "model_id": "anomaly-demo-v1",
                "task": "anomaly-detection",
                "version": "demo",
                "runtime": "dry-run",
            },
            {
                "model_id": "caption-demo-v1",
                "task": "scene-caption",
                "version": "demo",
                "runtime": "dry-run",
            },
        ]

    return SeedPlan(
        reading_batches=reading_batches,
        vision_model=scene_model,
        extra_vision_models=extra_vision_models,
        vision_prediction={
            "task": "scene-classification",
            "input": {
                "camera_id": "cam-demo-01",
                "frame_id": f"demo-frame-{int(end.timestamp())}",
                "image_uri": "file:///tmp/breezio-demo-frame.jpg",
            },
            "source": "simulated",
        },
        prediction_run={
            "target_node_id": node_id,
            "metric": "temperature",
            "model_name": "persistence",
            "horizon_minutes": 15,
            "data_source": "simulated",
        },
    )


def auth_headers(api_token: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    return headers


def post_json(
    backend_url: str,
    path: str,
    payload: dict[str, Any],
    timeout: float = 10.0,
    api_token: str | None = None,
) -> dict[str, Any]:
    request = Request(
        urljoin(backend_url.rstrip("/") + "/", path.lstrip("/")),
        data=json.dumps(payload).encode("utf-8"),
        headers=auth_headers(api_token),
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_backend(backend_url: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    health_url = urljoin(backend_url.rstrip("/") + "/", "api/health")
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(health_url, timeout=2.0) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"backend did not become healthy at {health_url}: {last_error}")


def seed_backend(backend_url: str, plan: SeedPlan, wait_seconds: float, api_token: str | None = None) -> dict[str, Any]:
    wait_for_backend(backend_url, wait_seconds)
    inserted_metrics = 0
    for batch in plan.reading_batches:
        result = post_json(backend_url, "/api/readings", batch, api_token=api_token)
        inserted_metrics += int(result.get("data", {}).get("inserted_metrics", 0))
    registered_models = []
    for vision_model in [plan.vision_model, *plan.extra_vision_models]:
        model = post_json(backend_url, "/api/models", vision_model, api_token=api_token)
        post_json(backend_url, f"/api/models/{vision_model['model_id']}/activate", {}, api_token=api_token)
        registered_models.append(model.get("data", {}).get("model_id"))
    vision = post_json(backend_url, "/api/inference/predict", plan.vision_prediction, api_token=api_token)
    prediction = post_json(backend_url, "/api/prediction/runs", plan.prediction_run, api_token=api_token)
    return {
        "inserted_metrics": inserted_metrics,
        "vision_models": registered_models,
        "vision_trace": vision.get("data", {}).get("trace_id"),
        "prediction_status": prediction.get("data", {}).get("status"),
        "prediction_run_id": prediction.get("data", {}).get("run_id"),
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend-url", default="http://127.0.0.1:8000")
    p.add_argument("--gateway-id", default="gw-sim-01")
    p.add_argument("--node-id", default="sim-router")
    p.add_argument("--samples", type=int, default=60)
    p.add_argument("--wait-seconds", type=float, default=30.0)
    p.add_argument("--api-token", default=None)
    p.add_argument(
        "--vision-runtime-mode",
        choices=["dry-run", "pretrained"],
        default="dry-run",
        help="dry-run registers dependency-free models; pretrained registers existing YOLO/InsightFace/DINOv2 runtimes",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    plan = build_seed_plan(
        node_id=args.node_id,
        gateway_id=args.gateway_id,
        samples=args.samples,
        vision_runtime_mode=args.vision_runtime_mode,
    )
    summary = seed_backend(args.backend_url, plan, args.wait_seconds, args.api_token)
    print(
        "Seeded backend demo runtime: "
        f"{summary['inserted_metrics']} reading metrics, "
        f"vision models={','.join(summary['vision_models'])}, "
        f"vision trace={summary['vision_trace']}, "
        f"prediction {summary['prediction_status']} ({summary['prediction_run_id']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
