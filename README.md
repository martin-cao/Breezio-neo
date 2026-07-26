# Breezio Neo

> [!NOTE]
>
> This is an course-design project for **2026 Spring** _Communication Technology of the Internet of Things_ by student ID: 0055713db0a7edac6c4d1743a6ca0fcd9aebe8b6d2656fb03cb4dfadc731ed5f (SHA256)

Breezio Neo is a smart laboratory IoT system that connects environmental
sensors, camera workloads, device control, data storage, AI inference, and a
Web dashboard.

The primary device path uses an ESP32-S3 node with Wi-Fi, mDNS/DNS-SD, TCP,
and the `$BRZ2` application protocol. An optional CC2530 implementation
provides a compact IEEE 802.15.4 star-network design and an SDCC-based firmware
toolchain.

## Architecture

```text
ESP32-S3 sensors ─ Wi-Fi / TCP ─ Python Gateway ─ MQTT ─ Backend workers
                                                               │
Camera ─ Camera Gateway ─ HTTP ─ Inference Service ────────────┤
                                                               ├─ PostgreSQL
                                                               └─ React dashboard
```

The browser talks only to the backend. The backend exposes REST and SSE
interfaces, persists application state in PostgreSQL, and proxies AI requests
to the independent inference service.

The CC2530 directory is an optional firmware and toolchain deliverable. It is
kept separate from the active ESP32-S3 end-to-end runtime shown above.

## Repository layout

| Directory | Contents |
| --- | --- |
| `frontend/` | React and TypeScript dashboard |
| `backend/` | Flask API, PostgreSQL store, migrations, and MQTT workers |
| `inference/` | Model registry, prediction API, and model preparation tools |
| `gateway/` | Serial and Wi-Fi/TCP protocol gateway |
| `sensor-new/` | ESP32-S3 firmware in embedded Rust |
| `sensor/` | CC2530F256 firmware, SDCC build, and flashing tools |
| `deploy/` | PostgreSQL, Redis, EMQX, and RustFS containers |
| `tools/` | Local stack runner, camera gateway, benchmarks, and CC2530 flasher |
| `docs/` | API, database, inference, and protocol documentation |

## Quick start without hardware

Prerequisites:

- Docker or a Docker-compatible runtime
- Python 3.11 or newer
- `uv`
- Bun

Create local configuration:

```sh
cp .env.example .env
set -a
. ./.env
set +a
```

Replace every `change-me` value before exposing any service outside a local
development machine. Loading `.env` into the shell keeps the credentials used
by Docker Compose and `tools/dev_stack.py` consistent.

Start infrastructure:

```sh
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
```

Start the backend, dry-run inference service, MQTT workers, and frontend:

```sh
UV_CACHE_DIR=/tmp/breezio-uv-cache \
PYTHONPATH=tools \
uv run python tools/dev_stack.py --with-workers
```

Open:

- Dashboard: `http://127.0.0.1:5173`
- Backend health: `http://127.0.0.1:8000/api/health`
- Inference health: `http://127.0.0.1:8000/api/inference/health`
- EMQX dashboard: `http://127.0.0.1:18083`

Detailed startup and smoke checks are in
[`deploy/README.md`](deploy/README.md).

## ESP32-S3 firmware

Install the Espressif Rust toolchain, `ldproxy`, and `espflash`, then build:

```sh
cd sensor-new
export BREEZIO_WIFI_SSID=<ssid>
export BREEZIO_WIFI_FALLBACK_SSID=<fallback-ssid>
export BREEZIO_WIFI_PASS=<password>
export BREEZIO_GATEWAY_HOST=<gateway-ip>
cargo build --release --features wifi
```

Flash and monitor:

```sh
cargo run --release --features wifi
```

Wiring and protocol details are in
[`sensor-new/docs/WIRING.md`](sensor-new/docs/WIRING.md) and
[`sensor-new/docs/PROTOCOL.md`](sensor-new/docs/PROTOCOL.md).

## CC2530 firmware

The CC2530 source supports standalone, router, and wireless-sensor builds with
either temperature/humidity or illumination sensing. The public source has
been checked with SDCC and produces validated Intel HEX images.

```sh
cd sensor
make check
```

Build a router or wireless sensor:

```sh
make clean
make NODE_ROLE=ROUTER NODE_KIND=TEMPHUMI NODE_ID=n1

make clean
make NODE_ROLE=SENSOR NODE_KIND=ILLU
```

Probe and program a CC2530 from the repository root:

```sh
PYTHONPATH=tools python3 -m xzg_cli.main probe-ti
PYTHONPATH=tools python3 -m xzg_cli.main flash-ti sensor/build/breezio-sensor.hex
```

See [`sensor/README.md`](sensor/README.md) for the complete workflow.

## Tests

Python services and tools:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend uv run python -m unittest discover -s backend/tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=gateway uv run python -m unittest discover -s gateway/tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=inference uv run python -m unittest discover -s inference/tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools uv run python -m unittest discover -s tools/tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=sensor-new/gateway \
  uv run python -m unittest discover -s sensor-new/gateway/tests
```

Frontend:

```sh
cd frontend
bun install --frozen-lockfile
bun run typecheck
bun run test
```

CC2530:

```sh
cd sensor
make check
```

## Models and data

Training datasets, face-enrollment material, camera recordings, runtime state,
and pretrained model weights are intentionally excluded. Model tools accept
local paths and environment variables so these assets can remain outside the
repository.

## Security

Values in `.env.example` and `deploy/docker-compose.yml` are development
placeholders. A networked deployment must use independent credentials, a
stable authentication secret, MQTT TLS and topic ACLs, restricted CORS, and a
private inference endpoint.

Do not commit Wi-Fi credentials, camera URLs with passwords, personal images,
model-provider API keys, or signing keys.

## License

Project-authored code is available under Apache-2.0. Some CC2530 Basic RF and
HAL files retain Texas Instruments device-specific terms, and the XZG-derived
programming workflow retains MIT attribution. Read
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) before redistribution.
