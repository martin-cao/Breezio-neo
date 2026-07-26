# Smart Laboratory IoT Dashboard Frontend

This is a Bun + TypeScript React static SPA for the course project frontend.

The application is implemented as React components and built as a static SPA.

## Run

```sh
set -a
. ./.env
set +a
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
PYTHONPATH=tools uv run python tools/dev_stack.py --with-workers
```

Open `http://127.0.0.1:5173`.

The dev stack runner starts the Flask inference service, Flask backend, optional
MQTT workers and Bun frontend. It also applies PostgreSQL migrations before the
backend/workers start. Use `--print` to inspect commands without starting
long-running processes.

Manual process startup remains useful when debugging one layer:

```sh
set -a
. ./.env
set +a
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build

UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=backend \
  uv run python -m breezio_backend.migrate

UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference \
  uv run python -m breezio_inference.main --host 127.0.0.1 --port 8100

BREEZIO_INFERENCE_URL=http://127.0.0.1:8100 \
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=backend \
  uv run python -m breezio_backend.main --host 127.0.0.1 --port 8000

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

If port `5173` is occupied, use another frontend port while keeping the backend
proxy:

```sh
python3 tools/frontend_static_server.py \
  --host 127.0.0.1 \
  --port 5199 \
  --backend-url http://127.0.0.1:8000 \
  --directory frontend
```

from the `frontend/` directory, then open `http://127.0.0.1:5199`.

React and React DOM are bundled from local Bun dependencies. The app entrypoint is `src/main.tsx`.
The frontend calls the Flask backend under `/api/*`; the backend proxies model
and inference calls to the separate Flask inference service. Use the backend as
the browser-facing API boundary, not the inference service directly.

`tools/dev_stack.py` and `tools/frontend_static_server.py` proxy `/api/*` to the
backend, so the app works with its default relative API routes. If you serve the
frontend with a plain static server instead, set the backend base URL once in the
browser console:

```js
localStorage.setItem("BREEZIO_API_BASE_URL", "http://127.0.0.1:8000")
```

Without that override the app uses relative `/api/*`, which is appropriate
behind a reverse proxy. The local dev stack also enables CORS for `5173`.

## Build and test

```sh
bun install --frozen-lockfile
bun run build
bun run test
bunx tsc --noEmit
```

## Implemented pages

- Dashboard
- Device Management
- History
- Sensor Network
- Environment Monitoring
- Vision Monitoring
- Prediction Center
- Alert Center
- Remote Control
- Model Selection
- Artifacts
- User Management
- User Settings modal
- Light/dark/system appearance mode

OpenAPI route helpers and request-shape helpers live in `src/api.ts`. The frontend contract copy is `openapi.yaml`; it is kept in sync with the aggregate project contract at `../docs/openapi.yaml`.

Generated files under `dist/` are ignored. Run `bun run build` before serving `index.html` with a static HTTP server.
