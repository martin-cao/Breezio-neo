from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_URL = "postgresql://breezio:change-me@127.0.0.1:5432/breezio"
VISION_RUNTIME_DEPS = [
    "ultralytics",
    "insightface",
    "onnxruntime",
    "opencv-python-headless",
    "numpy",
    "torch",
    "torchvision",
    "pillow",
    # Moondream2 scene-caption runtime: its Hub modeling code needs einops
    # at import time and device_map loading needs accelerate. transformers is
    # pinned because the pinned model revision's custom code breaks on newer
    # transformers internals ('all_tied_weights_keys', verified 2026-07-08).
    "transformers==4.51.3",
    "einops",
    "accelerate",
]


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    command: list[str]
    cwd: Path = ROOT
    env: dict[str, str] | None = None
    # A one-shot task (e.g. demo seeding) that is expected to run to completion.
    # Its exit — success or failure — must not tear down the long-running stack.
    oneshot: bool = False


def build_process_specs(args: argparse.Namespace) -> list[ProcessSpec]:
    database_url = args.database_url
    api_token = args.api_token
    inference_url = args.inference_url or f"http://127.0.0.1:{args.inference_port}"
    frontend_origins = f"http://{args.host}:{args.frontend_port},http://localhost:{args.frontend_port}"
    specs: list[ProcessSpec] = []

    if not args.no_inference:
        inference_command = ["uv", "run"]
        if args.with_vision_runtimes:
            for dependency in VISION_RUNTIME_DEPS:
                inference_command.extend(["--with", dependency])
        inference_command.extend(
            [
                "python",
                "-m",
                "breezio_inference.main",
                "--host",
                args.host,
                "--port",
                str(args.inference_port),
            ]
        )
        specs.append(
            ProcessSpec(
                name="inference",
                command=inference_command,
                env={"PYTHONPATH": "inference", "UV_CACHE_DIR": args.uv_cache_dir},
            )
        )

    if not args.no_backend:
        specs.append(
            ProcessSpec(
                name="backend",
                command=[
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "breezio_backend.main",
                    "--host",
                    args.host,
                    "--port",
                    str(args.backend_port),
                ],
                env={
                    "PYTHONPATH": "backend",
                    "UV_CACHE_DIR": args.uv_cache_dir,
                    "DATABASE_URL": database_url,
                    "API_TOKEN": api_token,
                    "BREEZIO_AUTH_SECRET": args.auth_secret,
                    "BREEZIO_INFERENCE_URL": inference_url,
                    "RUSTFS_PUBLIC_URL": args.rustfs_public_url,
                    "MQTT_HOST": args.mqtt_host,
                    "MQTT_PORT": str(args.mqtt_port),
                    "BREEZIO_CORS_ORIGINS": frontend_origins,
                },
            )
        )

    if args.with_workers:
        worker_env = {
            "PYTHONPATH": "backend",
            "UV_CACHE_DIR": args.uv_cache_dir,
            "DATABASE_URL": database_url,
            "MQTT_HOST": args.mqtt_host,
            "MQTT_PORT": str(args.mqtt_port),
        }
        specs.extend(
            [
                ProcessSpec(
                    name="mqtt-ingestor",
                    command=["uv", "run", "--with", "paho-mqtt", "python", "backend/workers/mqtt_ingestor.py"],
                    env=worker_env,
                ),
                ProcessSpec(
                    name="control-dispatcher",
                    command=["uv", "run", "--with", "paho-mqtt", "python", "backend/workers/control_dispatcher.py"],
                    env=worker_env,
                ),
            ]
        )

    if args.with_simulated_gateway:
        specs.append(
            ProcessSpec(
                name="simulated-gateway",
                command=[
                    "uv",
                    "run",
                    "--with",
                    "paho-mqtt",
                    "python",
                    "tools/run_simulated_gateway.py",
                    "--gateway-id",
                    args.simulated_gateway_id,
                    "--node-id",
                    args.simulated_router_node_id,
                    "--interval-seconds",
                    str(args.simulated_interval_seconds),
                ],
                env={
                    "PYTHONPATH": "gateway",
                    "UV_CACHE_DIR": args.uv_cache_dir,
                    "MQTT_HOST": args.mqtt_host,
                    "MQTT_PORT": str(args.mqtt_port),
                    "GATEWAY_READING_SOURCE": "serial_simulated",
                },
            )
        )

    if args.with_wifi_gateway:
        specs.append(
            ProcessSpec(
                name="wifi-gateway",
                command=[
                    "uv",
                    "run",
                    "--with",
                    "paho-mqtt",
                    "--with",
                    "zeroconf",
                    "python",
                    "-m",
                    "breezio_gateway.main",
                    "--wifi-listen",
                    args.wifi_gateway_listen,
                    "--gateway-id",
                    args.wifi_gateway_id,
                    "--sensor-dialect",
                    args.wifi_sensor_dialect,
                ],
                env={
                    "PYTHONPATH": "gateway",
                    "UV_CACHE_DIR": args.uv_cache_dir,
                    "MQTT_HOST": args.mqtt_host,
                    "MQTT_PORT": str(args.mqtt_port),
                    "GATEWAY_READING_SOURCE": "wifi_tcp",
                },
            )
        )

    if args.with_camera_gateway:
        camera_command = [
            "uv",
            "run",
            "python",
            "tools/run_camera_gateway.py",
            "--backend-url",
            f"http://127.0.0.1:{args.backend_port}",
            "--api-token",
            api_token,
            "--interval-seconds",
            str(args.camera_interval_seconds),
            "--person-conf",
            str(args.camera_person_conf),
            "--face-interval-seconds",
            str(args.camera_face_interval_seconds),
        ]
        if args.camera_device_id is not None:
            camera_command.extend(["--device-id", str(args.camera_device_id)])
        if args.camera_source_uri:
            camera_command.extend(["--source-uri", args.camera_source_uri])
        if args.camera_id:
            camera_command.extend(["--camera-id", args.camera_id])
        specs.append(
            ProcessSpec(
                name="camera-gateway",
                command=camera_command,
                env={"PYTHONPATH": "backend", "UV_CACHE_DIR": args.uv_cache_dir},
            )
        )

    if args.seed_demo_runtime:
        seed_command = [
            "uv",
            "run",
            "python",
            "tools/seed_demo_runtime.py",
            "--backend-url",
            f"http://127.0.0.1:{args.backend_port}",
            "--gateway-id",
            args.simulated_gateway_id,
            "--node-id",
            args.simulated_router_node_id,
            "--api-token",
            api_token,
        ]
        if args.with_vision_runtimes:
            seed_command.extend(["--vision-runtime-mode", "pretrained"])
        specs.append(
            ProcessSpec(
                name="demo-runtime-seed",
                command=seed_command,
                env={"UV_CACHE_DIR": args.uv_cache_dir},
                oneshot=True,
            )
        )

    if not args.no_frontend:
        specs.append(
            ProcessSpec(
                name="frontend",
                command=[
                    sys.executable,
                    "tools/frontend_static_server.py",
                    "--host",
                    args.host,
                    "--port",
                    str(args.frontend_port),
                    "--backend-url",
                    f"http://127.0.0.1:{args.backend_port}",
                    "--directory",
                    "frontend",
                ],
            )
        )

    return specs


def build_preflight_specs(args: argparse.Namespace) -> list[ProcessSpec]:
    specs: list[ProcessSpec] = []
    if args.skip_migrations or (args.no_backend and not args.with_workers):
        pass
    else:
        specs.append(
            ProcessSpec(
                name="postgres-migrations",
                command=[
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "breezio_backend.migrate",
                    "--database-url",
                    args.database_url,
                ],
                env={"PYTHONPATH": "backend", "UV_CACHE_DIR": args.uv_cache_dir},
            )
        )
    if not args.no_frontend:
        specs.append(
            ProcessSpec(
                name="frontend-dependencies",
                command=["bun", "install", "--frozen-lockfile"],
                cwd=ROOT / "frontend",
            )
        )
        specs.append(ProcessSpec(name="frontend-build", command=["bun", "run", "build"], cwd=ROOT / "frontend"))
    return specs


def run_preflight(specs: Iterable[ProcessSpec]) -> int:
    for spec in specs:
        env = os.environ.copy()
        env.update(spec.env or {})
        print(f"[dev-stack] preflight {spec.name}: {' '.join(spec.command)}", flush=True)
        result = subprocess.run(spec.command, cwd=spec.cwd, env=env)
        if result.returncode != 0:
            print(f"[dev-stack] preflight {spec.name} exited with {result.returncode}", file=sys.stderr, flush=True)
            return result.returncode
    return 0


def run_processes(specs: Iterable[ProcessSpec]) -> int:
    processes: list[tuple[ProcessSpec, subprocess.Popen]] = []
    stopping = False

    def stop_all(signum: int | None = None, frame=None) -> None:
        nonlocal stopping
        stopping = True
        for _, process in processes:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)

    try:
        for spec in specs:
            env = os.environ.copy()
            env.update(spec.env or {})
            print(f"[dev-stack] starting {spec.name}: {' '.join(spec.command)}", flush=True)
            processes.append((spec, subprocess.Popen(spec.command, cwd=spec.cwd, env=env)))

        while processes:
            for spec, process in list(processes):
                code = process.poll()
                if code is None:
                    continue
                processes.remove((spec, process))
                if spec.oneshot:
                    if code != 0:
                        print(f"[dev-stack] one-shot {spec.name} exited with {code}; continuing stack", file=sys.stderr, flush=True)
                    else:
                        print(f"[dev-stack] one-shot {spec.name} completed", flush=True)
                    continue
                if not stopping and code != 0:
                    print(f"[dev-stack] {spec.name} exited with {code}; stopping stack", file=sys.stderr, flush=True)
                    stop_all()
                    return code
            time.sleep(0.5)
    finally:
        stop_all()
        for _, process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run Breezio local application processes after docker compose data plane is up")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--database-url", default=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
    p.add_argument("--mqtt-host", default=os.getenv("MQTT_HOST", "127.0.0.1"))
    p.add_argument("--mqtt-port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    p.add_argument("--rustfs-public-url", default=os.getenv("RUSTFS_PUBLIC_URL", "http://127.0.0.1:9000"))
    p.add_argument("--inference-url", default=os.getenv("BREEZIO_INFERENCE_URL"), help="backend-facing inference service URL; use with --no-inference for a remote GPU host")
    p.add_argument("--inference-port", type=int, default=8100)
    p.add_argument("--backend-port", type=int, default=8000)
    p.add_argument("--frontend-port", type=int, default=5173)
    p.add_argument("--api-token", default=os.getenv("API_TOKEN", "breezio-dev-token"))
    p.add_argument("--auth-secret", default=os.getenv("BREEZIO_AUTH_SECRET", "breezio-dev-auth-secret"))
    p.add_argument("--uv-cache-dir", default=os.getenv("UV_CACHE_DIR", "/tmp/breezio-uv-cache"))
    p.add_argument("--with-workers", action="store_true", help="also run MQTT ingestion and control dispatcher workers")
    p.add_argument(
        "--with-vision-runtimes",
        action="store_true",
        help="start inference with pretrained vision runtime dependencies; no training is run",
    )
    p.add_argument("--with-simulated-gateway", action="store_true", help="also run a PTY-backed simulated serial sensor gateway")
    p.add_argument("--with-wifi-gateway", action="store_true", help="also run the real Wi-Fi gateway listener for ESP32 sensor connections")
    p.add_argument("--with-camera-gateway", action="store_true", help="also run the FFmpeg camera frame gateway")
    p.add_argument("--wifi-gateway-listen", default=os.getenv("WIFI_GATEWAY_LISTEN", "0.0.0.0:5568"))
    p.add_argument("--wifi-gateway-id", default=os.getenv("WIFI_GATEWAY_ID", "gw-smartwin-01"))
    p.add_argument("--wifi-sensor-dialect", choices=("brz2", "breezio"), default=os.getenv("WIFI_SENSOR_DIALECT", "brz2"))
    p.add_argument("--camera-device-id", type=int, default=int(os.getenv("CAMERA_DEVICE_ID")) if os.getenv("CAMERA_DEVICE_ID") else None)
    p.add_argument("--camera-source-uri", default=os.getenv("CAMERA_SOURCE_URI"))
    p.add_argument("--camera-id", default=os.getenv("CAMERA_ID"))
    p.add_argument("--camera-interval-seconds", type=float, default=float(os.getenv("CAMERA_INTERVAL_SECONDS", "0.2")))
    p.add_argument("--camera-face-interval-seconds", type=float, default=float(os.getenv("CAMERA_FACE_INTERVAL_SECONDS", "1.0")))
    p.add_argument("--camera-person-conf", type=float, default=float(os.getenv("CAMERA_PERSON_CONF", "0.25")))
    p.add_argument("--simulated-gateway-id", default=os.getenv("SIMULATED_GATEWAY_ID", "gw-sim-01"))
    p.add_argument("--simulated-router-node-id", default=os.getenv("SIMULATED_ROUTER_NODE_ID", "sim-router"))
    p.add_argument("--simulated-interval-seconds", type=float, default=float(os.getenv("SIMULATED_INTERVAL_SECONDS", "1.0")))
    p.add_argument("--seed-demo-runtime", action="store_true", help="seed backend API data for non-hardware demo pages")
    p.add_argument("--no-inference", action="store_true")
    p.add_argument("--no-backend", action="store_true")
    p.add_argument("--no-frontend", action="store_true")
    p.add_argument("--skip-migrations", action="store_true", help="do not apply PostgreSQL migrations before starting backend/workers")
    p.add_argument("--print", action="store_true", help="print process commands without starting them")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    preflight_specs = build_preflight_specs(args)
    specs = build_process_specs(args)
    if args.print:
        for spec in [*preflight_specs, *specs]:
            env = " ".join(f"{key}={value}" for key, value in sorted((spec.env or {}).items()))
            prefix = f"{env} " if env else ""
            cwd = "" if spec.cwd == ROOT else f"(cd {spec.cwd.relative_to(ROOT)} && "
            suffix = ")" if cwd else ""
            print(f"{spec.name}: {cwd}{prefix}{' '.join(spec.command)}{suffix}")
        return 0
    preflight_code = run_preflight(preflight_specs)
    if preflight_code != 0:
        return preflight_code
    return run_processes(specs)


if __name__ == "__main__":
    raise SystemExit(main())
