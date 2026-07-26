# Breezio Local Deployment

This runbook starts the local stack that can be integrated without the real
sensor firmware or physical gateway:

- PostgreSQL: metadata, telemetry, commands, alerts and outbox.
- Redis: cache/queue coordination placeholder.
- EMQX: MQTT broker for simulated telemetry, command dispatch and ACK topics.
- RustFS: S3-compatible object storage endpoint.
- Inference service: dry-run model runtime on port `8100`.
- Backend API: Flask API and PostgreSQL store on port `8000`.
- Frontend: built React SPA served from `frontend/` on port `5173`, with
  `/api/*` proxied to the backend.
- Optional workers: MQTT telemetry ingestor and control outbox dispatcher.

## Prerequisites

- Docker or OrbStack with the Docker CLI available.
- `uv` for Python app commands.
- Bun for the frontend build.

## One-command local app stack

From the repository root:

```sh
cp .env.example .env
set -a
. ./.env
set +a
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=tools \
  uv run python tools/dev_stack.py --with-workers
```

The shell export is required: it makes Docker Compose and the Python runner
use the same database password and service credentials. Edit `.env` before
using the stack outside a local development machine.

To start the same stack with pretrained vision runtime dependencies available
to the inference service, add `--with-vision-runtimes`. This starts the
existing YOLO, InsightFace and DINOv2 adapters; it does not run fine-tuning:

```sh
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=tools \
  uv run python tools/dev_stack.py --with-workers --with-vision-runtimes --seed-demo-runtime
```

To include a real RTSP camera gateway, add `--with-camera-gateway` and either
point it at a registered camera device or pass the source URI directly:

```sh
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=tools \
  uv run python tools/dev_stack.py --with-workers --with-vision-runtimes \
    --with-camera-gateway --camera-source-uri rtsp://192.0.2.72 \
    --camera-id cam-rtsp-01
```

Open:

- Frontend: `http://127.0.0.1:5173`
- Backend health: `http://127.0.0.1:8000/api/health`
- Inference health through backend: `http://127.0.0.1:8000/api/inference/health`
- EMQX dashboard: `http://127.0.0.1:18083`
- RustFS endpoint: `http://127.0.0.1:9000`

The frontend server started by `tools/dev_stack.py` proxies relative `/api/*`
requests to the backend, so the dashboard works without manually setting a
browser API base URL.

## What the runner does

`tools/dev_stack.py --with-workers` performs these steps:

1. Applies `backend/migrations/*.sql` to the local PostgreSQL database.
2. Installs the locked Bun dependencies and runs `bun run build` in `frontend/`.
3. Starts the inference Flask service.
4. Starts the backend Flask API with `BREEZIO_INFERENCE_URL=http://127.0.0.1:8100`.
5. Starts the MQTT ingestor and control dispatcher workers.
6. Optionally starts serial, Wi-Fi or camera gateway processes when requested.
7. Serves the compiled frontend and proxies `/api/*` to the backend.

For the local Postgres stack, the runner sets `API_TOKEN=breezio-dev-token` by
default and passes the same token to `tools/seed_demo_runtime.py`. Override it
with `--api-token` or the `API_TOKEN` environment variable if needed.

With `--with-vision-runtimes`, step 3 uses per-invocation `uv run --with ...`
dependencies for `ultralytics`, `insightface`, `onnxruntime`, OpenCV, Torch,
TorchVision, NumPy and Pillow. The first run may download packages and model
weights; later runs reuse the `UV_CACHE_DIR` and model caches.

Use `--print` to inspect the commands without starting long-running processes:

```sh
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=tools \
  uv run python tools/dev_stack.py --with-workers --print
```

Stop the app stack with `Ctrl-C`. Stop containers with:

```sh
docker compose -f deploy/docker-compose.yml down
```

## Manual startup

Use this when debugging one layer at a time.

```sh
set -a
. ./.env
set +a
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build

UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=backend \
  uv run python -m breezio_backend.migrate

UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference \
  uv run python -m breezio_inference.main --host 127.0.0.1 --port 8100

# Optional pretrained vision runtime path; no training runs here.
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference \
  uv run --with ultralytics --with insightface --with onnxruntime \
    --with opencv-python-headless --with numpy --with torch \
    --with torchvision --with pillow \
    python -m breezio_inference.main --host 127.0.0.1 --port 8100

BREEZIO_INFERENCE_URL=http://127.0.0.1:8100 \
BREEZIO_CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173 \
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=backend \
  uv run python -m breezio_backend.main --host 127.0.0.1 --port 8000

MQTT_HOST=127.0.0.1 MQTT_PORT=1883 \
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=backend \
  uv run --with paho-mqtt python backend/workers/mqtt_ingestor.py

MQTT_HOST=127.0.0.1 MQTT_PORT=1883 \
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=backend \
  uv run --with paho-mqtt python backend/workers/control_dispatcher.py

# Optional real camera gateway. It captures one frame per interval with FFmpeg
# and posts the snapshot to /api/inference/predict for person and face tasks.
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=backend \
  uv run python tools/run_camera_gateway.py \
    --backend-url http://127.0.0.1:8000 \
    --source-uri rtsp://192.0.2.72 \
    --camera-id cam-rtsp-01

cd frontend
bun install --frozen-lockfile
bun run build
cd ..
python3 tools/frontend_static_server.py \
  --host 127.0.0.1 \
  --port 5173 \
  --backend-url http://127.0.0.1:8000 \
  --directory frontend
```

Do not use `bun --hot ./index.html` for this app. The frontend `index.html`
loads compiled files from `dist/`, so the stable local path is build first,
then serve `frontend/` as static files.

## Smoke checks

After the stack is running:

```sh
curl -sS http://127.0.0.1:8000/api/health
curl -sS http://127.0.0.1:8000/api/inference/health
curl -sS http://127.0.0.1:8000/api/dashboard/summary
curl -sS http://127.0.0.1:5173/
curl -sS http://127.0.0.1:5173/api/health
```

Expected results:

- Backend health returns `{"service":"breezio-backend","status":"ok","success":true}`.
- Inference health returns dry-run runtime metadata.
- Dashboard summary returns counts from PostgreSQL.
- Frontend HTML includes `dist/main.js` and `dist/styles.css`.
- `http://127.0.0.1:5173/api/health` returns the backend health payload through
  the local frontend proxy.

## Default endpoints

- PostgreSQL: the `DATABASE_URL` loaded from `.env`
- Redis: `redis://127.0.0.1:6379/0`
- MQTT broker: `mqtt://127.0.0.1:1883`
- EMQX dashboard: `http://127.0.0.1:18083`
- RustFS S3 endpoint: `http://127.0.0.1:9000`

The RustFS image name is centralized as the `RUSTFS_IMAGE` build arg in
`deploy/docker-compose.yml`; EMQX can be overridden with `EMQX_IMAGE`.
