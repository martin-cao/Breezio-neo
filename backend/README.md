# Breezio Backend

Flask backend, following `AgriNex`'s `api -> services -> db` layering. Implements
the parts of the main loop that are already verifiable offline:

- Gateway 心跳与节点状态。
- MQTT/HTTP 批量 telemetry 入库。
- 最新读数、历史读数和 dashboard summary 查询。
- 控制命令创建、outbox 记录和 ACK 推进。
- 告警创建、确认和解除。
- 温度/湿度预测：训练、评估、模型对比、在线预测（见下方 Prediction）。

`flask` and the PostgreSQL driver (`psycopg`) are core dependencies in
`pyproject.toml`, so `uv run` pulls them in automatically. The canonical data
plane is PostgreSQL + Redis + RustFS, matching `docs/database-design.md`,
`docs/object-storage-design.md`, and `deploy/docker-compose.yml`. Do not add new
persistent storage to SQLite; any SQLite-only path in tests is a legacy offline
harness while the remaining modules are migrated.

## Run

```sh
set -a
. ./.env
set +a
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build

PYTHONPATH=backend uv run python -m breezio_backend.migrate

MQTT_HOST=127.0.0.1 \
BREEZIO_INFERENCE_URL=http://127.0.0.1:8100 \
RUSTFS_PUBLIC_URL=http://127.0.0.1:9000 \
PYTHONPATH=backend uv run python -m breezio_backend.main --host 127.0.0.1 --port 8000
```

The compose image runs the same SQL on first database initialization. The
explicit migration command is still required for existing local volumes because
PostgreSQL does not rerun `/docker-entrypoint-initdb.d` after the data directory
already exists.

Set `BREEZIO_AUTH_SECRET` for stable dashboard user tokens. Without a stable
secret, tokens are signed with a per-process development secret and become
invalid after backend restart. The seeded admin account (`operator@lab-317`) has
no committed password hash; set `BREEZIO_BOOTSTRAP_PASSWORD` once and log in
with that value to initialize its password hash.

```sh
export BREEZIO_AUTH_SECRET="$(openssl rand -base64 32)"
export BREEZIO_BOOTSTRAP_PASSWORD="replace-with-local-admin-password"

curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"operator@lab-317","password":"replace-with-local-admin-password"}'
```

Dashboard requests use the returned token:

```text
Authorization: Bearer <access_token>
```

`API_TOKEN` is still supported as a service token for scripts, gateways and
smoke checks that do not act as a dashboard user.

## Manual Ingest

```sh
curl -X POST http://127.0.0.1:8000/api/readings \
  -H 'Content-Type: application/json' \
  -d '{"message_id":"demo","gateway_id":"gw-01","batch_id":"1","records":[{"node_id":"n1","seq":1,"temperature":26.4,"humidity":58.2,"light":734}]}'
```

## Accounts / Devices

User accounts, signed-in profile settings and logical devices are backed by
PostgreSQL tables (`users`, `logical_devices`):

```sh
curl http://127.0.0.1:8000/api/me
curl http://127.0.0.1:8000/api/users
curl -X POST http://127.0.0.1:8000/api/devices \
  -H 'Content-Type: application/json' \
  -d '{"name":"Fan Controller","device_type":"fan","location":"Equipment","client_id":"fan-1"}'
curl -X POST http://127.0.0.1:8000/api/devices \
  -H 'Content-Type: application/json' \
  -d '{"name":"Lab RTSP Camera","device_type":"camera","location":"Lab Overview",
       "client_id":"cam-rtsp-01","protocol":"rtsp","ip_address":"192.0.2.72"}'
curl -X POST http://127.0.0.1:8000/api/devices/1/probe
curl http://127.0.0.1:8000/api/devices
```

## Prediction

Implements [docs/prediction-model-design.md](../docs/prediction-model-design.md): four
models (`persistence`, `linear_regression`, `random_forest`,
`hist_gradient_boosting`), time-based 70/15/15 split, MAE/RMSE/R2, and an
online forecast endpoint. `numpy` and `scikit-learn` are declared in the root
`pyproject.toml`, so the standard project environment includes all four model
implementations:

```sh
PYTHONPATH=backend uv run python -m breezio_backend.main
```

Seed a couple of days of simulated readings first if there's no real sensor
data yet (rows must be tagged `source=simulated`, per the "don't pass off
simulated data as real" rule in the design doc):

```sh
PYTHONPATH=backend uv run python backend/tools/seed_demo_readings.py \
  --node-ids n1 --days 2
```

Train all four models for a node/metric in one call (or pass a single
`model_name` instead of `models` to train just one, per the documented
contract):

```sh
curl -X POST http://127.0.0.1:8000/api/prediction/runs \
  -H 'Content-Type: application/json' \
  -d '{"target_node_id":"n1","metric":"temperature","horizon_minutes":15,
       "models":["persistence","linear_regression","random_forest","hist_gradient_boosting"]}'
```

The run with the lowest MAE is auto-activated as the recommended model for
that node/metric; override with
`POST /api/prediction/runs/{run_id}/activate`. Then:

```sh
curl "http://127.0.0.1:8000/api/prediction/metrics?node_id=n1&metric=temperature"
curl "http://127.0.0.1:8000/api/prediction/latest?node_id=n1&metric=temperature"
curl "http://127.0.0.1:8000/api/prediction/features"
```

`/latest` re-derives the forecast on demand from the active run's pickled
model (stored as a DB blob) and the most recent readings — there is no
background scheduler yet running the "pull window → featurize → predict →
write `predictions`" loop from the design doc's §6.2; that's the next piece
if a live-updating dashboard chart is needed. `horizon_minutes` is converted
to a step count via the median gap between a node's own readings rather than
a true fixed-grid resample — documented in `breezio_backend/prediction.py`.

## Inference Link

The backend is the frontend-facing entrypoint for model and inference calls:

```sh
curl http://127.0.0.1:8000/api/inference/health
curl "http://127.0.0.1:8000/api/models?task=scene-classification"
curl -X POST http://127.0.0.1:8000/api/inference/predict \
  -H 'Content-Type: application/json' \
  -d '{"task":"scene-classification","input":{"camera_id":"cam-01","frame_id":"latest","image_uri":"file:///tmp/frame.jpg"}}'
```

Vision prediction events are persisted through PostgreSQL `vision_events`.
`DATABASE_URL` must be set; otherwise `/api/vision/events` and vision prediction
persistence return `vision_store_unavailable` instead of silently using an
incorrect data store.

## Artifacts / RustFS

Model files, snapshots, datasets and benchmark JSON live in RustFS/S3-compatible
object storage. The backend records object metadata in PostgreSQL
`object_artifacts` and uses `RUSTFS_PUBLIC_URL` to generate browser-facing object
URLs:

```sh
curl -X POST http://127.0.0.1:8000/api/artifacts \
  -H 'Content-Type: application/json' \
  -d '{"bucket":"breezio-models","object_key":"models/demo/model.onnx","artifact_type":"onnx",
       "content_type":"application/octet-stream","size_bytes":123,"sha256":"..."}'

curl http://127.0.0.1:8000/api/artifacts
curl -X POST http://127.0.0.1:8000/api/artifacts/1/presign
```

## MQTT Workers

`mqtt_ingestor` 消费 telemetry 和 ACK topic，写入 PostgreSQL：

```sh
PYTHONPATH=backend uv run --with paho-mqtt python backend/workers/mqtt_ingestor.py --host 127.0.0.1
```

订阅：

- `breezio/gateways/+/batch`
- `breezio/nodes/+/command_ack`

`control_dispatcher` 扫描 PostgreSQL `outbox_messages`，发布控制命令到
`breezio/nodes/{node_id}/command`，发布成功后将 outbox 和对应
`control_commands.status` 推进到 `sent`：

```sh
PYTHONPATH=backend uv run --with paho-mqtt python backend/workers/control_dispatcher.py --host 127.0.0.1
```

本地验证单批次派发时可以加 `--once`。

## Tests

```sh
PYTHONPATH=backend uv run python -m unittest discover backend/tests
```

This command runs the full prediction suite because the project dependencies
already include `numpy` and `scikit-learn`.
