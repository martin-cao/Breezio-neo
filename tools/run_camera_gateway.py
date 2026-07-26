"""Capture camera frames with FFmpeg and submit them to backend inference."""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from breezio_backend.camera_sources import normalize_camera_payload, safe_snapshot_name  # noqa: E402


DEFAULT_TASKS = ["person-detection", "face-identification", "anomaly-detection", "scene-caption"]
DEFAULT_INFERENCE_HZ = 5.0
DEFAULT_FACE_INFERENCE_HZ = 1.0
DEFAULT_PERSON_CONF = 0.25
DEFAULT_ANOMALY_INTERVAL_SECONDS = 10.0
DEFAULT_CAPTION_INTERVAL_SECONDS = 300.0
# Generative caption calls pay a cold model load on first use; needs far more
# than the 10s default the fast detection tasks use.
DEFAULT_CAPTION_TIMEOUT_SECONDS = 120.0


def encode_image_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def auth_headers(api_token: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    return headers


def request_json(
    backend_url: str,
    path: str,
    payload: dict[str, Any] | None = None,
    api_token: str | None = None,
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        urljoin(backend_url.rstrip("/") + "/", path.lstrip("/")),
        data=body,
        headers=auth_headers(api_token),
        method="POST" if payload is not None else "GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"backend returned {exc.code}: {message}") from exc
    except URLError as exc:
        raise RuntimeError(f"backend unavailable: {exc.reason}") from exc
    if data.get("success") is False:
        raise RuntimeError(data.get("message") or data.get("error") or "backend request failed")
    return data.get("data", data)


def resolve_camera_source(
    args: argparse.Namespace,
    fetch_json: Callable[..., dict[str, Any]] = request_json,
) -> tuple[str, str]:
    if args.device_id is not None:
        device = fetch_json(args.backend_url, f"/api/devices/{args.device_id}", api_token=args.api_token)
        if device.get("device_type") != "camera":
            raise ValueError(f"device {args.device_id} is not a camera")
        normalized = normalize_camera_payload({}, existing=device)
        source_uri = normalized.get("source_uri")
        camera_id = args.camera_id or device.get("client_id") or f"device-{args.device_id}"
    else:
        normalized = normalize_camera_payload(
            {
                "protocol": args.protocol,
                "ip_address": args.ip_address,
                "port": args.port,
                "source_uri": args.source_uri,
            }
        )
        source_uri = normalized.get("source_uri")
        camera_id = args.camera_id or "camera"
    if not source_uri:
        raise ValueError("camera source_uri or ip_address is required")
    return str(source_uri), str(camera_id)


def ffmpeg_snapshot_command(source_uri: str, output_path: Path, rtsp_transport: str = "udp") -> list[str]:
    command = ["ffmpeg", "-y"]
    if source_uri.startswith("rtsp://") and rtsp_transport and rtsp_transport != "default":
        command.extend(["-rtsp_transport", rtsp_transport])
    command.extend(["-i", source_uri, "-frames:v", "1", "-q:v", "2", str(output_path)])
    return command


def capture_frame(
    source_uri: str,
    output_path: Path,
    timeout_seconds: float,
    rtsp_transport: str = "udp",
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = ffmpeg_snapshot_command(source_uri, output_path, rtsp_transport=rtsp_transport)
    try:
        result = run_command(command, capture_output=True, text=True, timeout=timeout_seconds)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is not installed or not available on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg snapshot timed out after {timeout_seconds}s") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg snapshot failed").strip()
        raise RuntimeError(detail)
    return output_path


def prediction_payload(
    task: str,
    camera_id: str,
    frame_id: str,
    image_uri: str,
    source_uri: str,
    person_conf: float = 0.15,
    image_b64: str | None = None,
) -> dict[str, Any]:
    input_payload: dict[str, Any] = {
        "camera_id": camera_id,
        "frame_id": frame_id,
        "image_uri": image_uri,
        "source_uri": source_uri,
    }
    if image_b64:
        input_payload["image_b64"] = image_b64
        input_payload["image_format"] = "jpg"
    if task == "person-detection":
        input_payload["conf"] = person_conf
    return {
        "task": task,
        "input": input_payload,
    }


def runtime_settings(
    args: argparse.Namespace,
    fetch_json: Callable[..., dict[str, Any]] = request_json,
) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "interval_seconds": float(args.interval_seconds),
        "face_interval_seconds": float(args.face_interval_seconds),
        "anomaly_interval_seconds": float(args.anomaly_interval_seconds),
        "caption_interval_seconds": float(args.caption_interval_seconds),
        "caption_on_anomaly": bool(args.caption_on_anomaly),
        "person_conf": float(args.person_conf),
    }
    try:
        data = fetch_json(args.backend_url, "/api/me/settings", api_token=args.api_token, timeout_seconds=3)
    except Exception:
        return settings
    vision = data.get("vision") if isinstance(data, dict) else None
    if not isinstance(vision, dict):
        return settings
    try:
        inference_hz = float(vision.get("inference_hz", 0))
        if inference_hz > 0:
            settings["interval_seconds"] = 1.0 / max(0.1, min(30.0, inference_hz))
    except (TypeError, ValueError):
        pass
    try:
        face_inference_hz = float(vision.get("face_inference_hz", 0))
        if face_inference_hz > 0:
            settings["face_interval_seconds"] = 1.0 / max(0.1, min(30.0, face_inference_hz))
    except (TypeError, ValueError):
        pass
    try:
        anomaly_inference_hz = float(vision.get("anomaly_inference_hz", 0))
        if anomaly_inference_hz > 0:
            settings["anomaly_interval_seconds"] = 1.0 / max(0.01, min(30.0, anomaly_inference_hz))
    except (TypeError, ValueError):
        pass
    try:
        caption_periodic_seconds = float(vision.get("caption_periodic_seconds", 0))
        if caption_periodic_seconds > 0:
            settings["caption_interval_seconds"] = max(10.0, min(86_400.0, caption_periodic_seconds))
    except (TypeError, ValueError):
        pass
    if isinstance(vision.get("caption_on_anomaly"), bool):
        settings["caption_on_anomaly"] = vision["caption_on_anomaly"]
    try:
        settings["person_conf"] = max(0.01, min(0.99, float(vision.get("person_confidence", settings["person_conf"]))))
    except (TypeError, ValueError):
        pass
    return settings


def run_once(
    args: argparse.Namespace,
    fetch_json: Callable[..., dict[str, Any]] = request_json,
    post_json: Callable[..., dict[str, Any]] = request_json,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    captured_at: datetime | None = None,
    person_conf: float | None = None,
    tasks: list[str] | None = None,
    caption_on_anomaly: bool = False,
) -> dict[str, Any]:
    source_uri, camera_id = resolve_camera_source(args, fetch_json=fetch_json)
    timestamp = captured_at or datetime.now(timezone.utc)
    frame_id = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"{safe_snapshot_name({'client_id': camera_id})}-{frame_id}.jpg"
    frame_path = capture_frame(source_uri, Path(args.snapshot_dir) / filename, args.timeout_seconds, rtsp_transport=args.rtsp_transport, run_command=run_command)
    image_uri = frame_path.resolve().as_uri()
    image_b64 = encode_image_base64(frame_path)
    predictions = []
    resolved_tasks = list(tasks or args.tasks)
    for task in resolved_tasks:
        payload = prediction_payload(task, camera_id, frame_id, image_uri, source_uri, person_conf=person_conf if person_conf is not None else args.person_conf, image_b64=image_b64)
        timeout = args.caption_timeout_seconds if task == "scene-caption" else 10
        try:
            data = post_json(args.backend_url, "/api/inference/predict", payload=payload, api_token=args.api_token, timeout_seconds=timeout)
            predictions.append({"task": task, "ok": True, "data": data})
        except Exception as exc:
            predictions.append({"task": task, "ok": False, "error": str(exc)})
    # An anomalous frame gets an immediate VLM description of the same frame,
    # regardless of the periodic caption timer — the whole point of the
    # caption is to explain what changed, right when it changed.
    if caption_on_anomaly and "scene-caption" not in resolved_tasks:
        anomaly = next((p for p in predictions if p["task"] == "anomaly-detection" and p.get("ok")), None)
        outputs = (anomaly or {}).get("data", {}).get("outputs") or {}
        if outputs.get("anomaly_label") == "anomaly":
            payload = prediction_payload("scene-caption", camera_id, frame_id, image_uri, source_uri, image_b64=image_b64)
            try:
                data = post_json(args.backend_url, "/api/inference/predict", payload=payload, api_token=args.api_token, timeout_seconds=args.caption_timeout_seconds)
                predictions.append({"task": "scene-caption", "ok": True, "trigger": "anomaly", "data": data})
            except Exception as exc:
                predictions.append({"task": "scene-caption", "ok": False, "trigger": "anomaly", "error": str(exc)})
    return {
        "camera_id": camera_id,
        "frame_id": frame_id,
        "frame_path": str(frame_path),
        "image_uri": image_uri,
        "predictions": predictions,
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run an FFmpeg-backed camera gateway")
    p.add_argument("--backend-url", default=os.getenv("BACKEND_URL", "http://127.0.0.1:8000"))
    p.add_argument("--api-token", default=os.getenv("API_TOKEN"))
    p.add_argument("--device-id", type=int, default=int(os.getenv("CAMERA_DEVICE_ID")) if os.getenv("CAMERA_DEVICE_ID") else None)
    p.add_argument("--source-uri", default=os.getenv("CAMERA_SOURCE_URI"))
    p.add_argument("--protocol", choices=("rtsp", "http", "https", "file"), default=os.getenv("CAMERA_PROTOCOL", "rtsp"))
    p.add_argument("--ip-address", default=os.getenv("CAMERA_IP_ADDRESS"))
    p.add_argument("--port", type=int, default=int(os.getenv("CAMERA_PORT")) if os.getenv("CAMERA_PORT") else None)
    p.add_argument("--camera-id", default=os.getenv("CAMERA_ID"))
    p.add_argument("--snapshot-dir", default=os.getenv("CAMERA_SNAPSHOT_DIR", "var/camera_gateway"))
    p.add_argument("--interval-seconds", type=float, default=float(os.getenv("CAMERA_INTERVAL_SECONDS", str(1.0 / DEFAULT_INFERENCE_HZ))))
    p.add_argument("--face-interval-seconds", type=float, default=float(os.getenv("CAMERA_FACE_INTERVAL_SECONDS", str(1.0 / DEFAULT_FACE_INFERENCE_HZ))))
    p.add_argument("--anomaly-interval-seconds", type=float, default=float(os.getenv("CAMERA_ANOMALY_INTERVAL_SECONDS", str(DEFAULT_ANOMALY_INTERVAL_SECONDS))))
    p.add_argument("--caption-interval-seconds", type=float, default=float(os.getenv("CAMERA_CAPTION_INTERVAL_SECONDS", str(DEFAULT_CAPTION_INTERVAL_SECONDS))))
    p.add_argument("--caption-timeout-seconds", type=float, default=float(os.getenv("CAMERA_CAPTION_TIMEOUT_SECONDS", str(DEFAULT_CAPTION_TIMEOUT_SECONDS))))
    p.add_argument("--caption-on-anomaly", action=argparse.BooleanOptionalAction, default=os.getenv("CAMERA_CAPTION_ON_ANOMALY", "1") not in {"0", "false", "no"})
    p.add_argument("--person-conf", type=float, default=float(os.getenv("CAMERA_PERSON_CONF", str(DEFAULT_PERSON_CONF))))
    p.add_argument("--timeout-seconds", type=float, default=float(os.getenv("CAMERA_TIMEOUT_SECONDS", "8.0")))
    p.add_argument("--rtsp-transport", choices=("udp", "tcp", "default"), default=os.getenv("CAMERA_RTSP_TRANSPORT", "udp"))
    p.add_argument("--tasks", nargs="+", default=DEFAULT_TASKS)
    p.add_argument("--max-inflight", type=int, default=int(os.getenv("CAMERA_MAX_INFLIGHT", "4")))
    p.add_argument("--once", action="store_true")
    p.add_argument("--max-frames", type=int, default=None)
    return p


def print_completed(pending: set[Future[dict[str, Any]]]) -> None:
    completed = [future for future in pending if future.done()]
    for future in completed:
        pending.remove(future)
        try:
            result = future.result()
            print(json.dumps(result, separators=(",", ":"), sort_keys=True), flush=True)
        except Exception as exc:
            print(f"[camera-gateway] {exc}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.once:
        try:
            settings = runtime_settings(args)
            result = run_once(args, person_conf=settings["person_conf"], caption_on_anomaly=settings["caption_on_anomaly"])
            print(json.dumps(result, separators=(",", ":"), sort_keys=True), flush=True)
            return 0
        except Exception as exc:
            print(f"[camera-gateway] {exc}", file=sys.stderr, flush=True)
            return 1

    captured = 0
    max_inflight = max(1, int(args.max_inflight))
    settings = runtime_settings(args)
    settings_checked_at = 0.0
    last_face_at = 0.0
    last_anomaly_at = 0.0
    last_caption_at = 0.0
    pending: set[Future[dict[str, Any]]] = set()
    # face/anomaly/scene-caption run on their own slower cadences than the
    # per-tick person detection; each is stripped from the per-tick list and
    # re-added when its interval elapses.
    gated_tasks = {"face-identification", "anomaly-detection", "scene-caption"}
    with ThreadPoolExecutor(max_workers=max_inflight) as executor:
        while True:
            print_completed(pending)
            now = time.monotonic()
            if now - settings_checked_at >= 2.0:
                settings = runtime_settings(args)
                settings_checked_at = now
            if len(pending) >= max_inflight:
                time.sleep(min(settings["interval_seconds"], 0.05))
                continue
            tasks = [task for task in args.tasks if task not in gated_tasks]
            if "face-identification" in args.tasks and now - last_face_at >= settings["face_interval_seconds"]:
                tasks.append("face-identification")
                last_face_at = now
            if "anomaly-detection" in args.tasks and now - last_anomaly_at >= settings["anomaly_interval_seconds"]:
                tasks.append("anomaly-detection")
                last_anomaly_at = now
            if "scene-caption" in args.tasks and now - last_caption_at >= settings["caption_interval_seconds"]:
                tasks.append("scene-caption")
                last_caption_at = now
            pending.add(executor.submit(run_once, args, person_conf=settings["person_conf"], tasks=tasks, caption_on_anomaly=settings["caption_on_anomaly"]))
            captured += 1
            if args.max_frames is not None and captured >= args.max_frames:
                while pending:
                    print_completed(pending)
                    if pending:
                        time.sleep(0.05)
                return 0
            time.sleep(settings["interval_seconds"])


if __name__ == "__main__":
    raise SystemExit(main())
