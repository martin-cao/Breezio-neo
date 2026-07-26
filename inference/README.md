# Breezio Edge Inference

This is the dependency-light Flask service for edge inference. It runs as an
independent process so camera/model execution does not block the backend API.
The dashboard should call it through the backend proxy (`/api/models`,
`/api/inference/predict`) rather than directly from the browser.

The first runtime is `dry-run`. It exercises model registration, activation,
request validation, timing, and response schemas without claiming real ML
predictions. Add PyTorch, ONNX Runtime, or camera dependencies later behind new
runtime adapters.

## Run

```sh
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference uv run python -m breezio_inference.main --host 127.0.0.1 --port 8100
```

Run with pretrained vision adapters available:

```sh
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference \
  uv run --with ultralytics --with insightface --with onnxruntime \
    --with opencv-python-headless --with numpy --with torch \
    --with torchvision --with pillow \
    python -m breezio_inference.main --host 127.0.0.1 --port 8100
```

This path starts the service with existing model runtimes only:

- `ultralytics-yolo`: pretrained `yolov8n.pt` person detection unless a local
  `artifact_uri` is registered.
- `insightface-onnx`: InsightFace `buffalo_l` face embeddings plus nearest
  neighbor matching against enrolled identities.
- `dinov2-knn`: DINOv2 embeddings plus a registered normal-frame bank. Without
  a bank it returns `no_normal_bank` instead of inventing anomaly scores.

No fine-tuning or training script is run by service startup.

## Smoke Test

```sh
curl http://127.0.0.1:8100/api/inference/health

curl -X POST http://127.0.0.1:8100/api/inference/models \
  -H 'Content-Type: application/json' \
  -d '{"model_id":"scene-v1","task":"scene-classification","version":"v1","runtime":"dry-run","labels":["vacant","normal","crowded","abnormal"]}'

curl -X POST http://127.0.0.1:8100/api/inference/models/scene-v1/activate

curl -X POST http://127.0.0.1:8100/api/inference/predict \
  -H 'Content-Type: application/json' \
  -d '{"task":"scene-classification","input":{"image_uri":"file:///tmp/frame.jpg"}}'
```

## Face Identification

Enrollment and identification follow the same register/activate/predict shape as
other tasks. Enrollment itself needs no runtime — it just records the five
pose-tagged reference images per person. See
[docs/vision-model-design.md](../docs/vision-model-design.md) §9 for the full
design (InsightFace `buffalo_l` + nearest-neighbor cosine matching, no training
loop).

```sh
curl -X POST http://127.0.0.1:8100/api/inference/identities \
  -H 'Content-Type: application/json' \
  -d '{"person_id":"p001","display_name":"Alice","images":[
        {"pose":"front","image_uri":"file:///tmp/alice-front.jpg"},
        {"pose":"up","image_uri":"file:///tmp/alice-up.jpg"},
        {"pose":"down","image_uri":"file:///tmp/alice-down.jpg"},
        {"pose":"left","image_uri":"file:///tmp/alice-left.jpg"},
        {"pose":"right","image_uri":"file:///tmp/alice-right.jpg"}
      ]}'

curl http://127.0.0.1:8100/api/inference/identities
```

Register a `face-identification` model with `"runtime":"dry-run"` to exercise
the API shape with no ML deps, or `"runtime":"insightface-onnx"` for the real
thing:

```sh
curl -X POST http://127.0.0.1:8100/api/inference/models \
  -H 'Content-Type: application/json' \
  -d '{"model_id":"face-id-v1","task":"face-identification","version":"v1","runtime":"insightface-onnx"}'

curl -X POST http://127.0.0.1:8100/api/inference/models/face-id-v1/activate

curl -X POST http://127.0.0.1:8100/api/inference/predict \
  -H 'Content-Type: application/json' \
  -d '{"task":"face-identification","input":{"image_uri":"file:///tmp/frame.jpg"}}'
```

`insightface-onnx` needs `insightface`, `onnxruntime` and
`opencv-python-headless` — not in `pyproject.toml`, install per-invocation:

```sh
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference \
  uv run --with insightface --with onnxruntime --with opencv-python-headless --with numpy \
  python -m breezio_inference.main --host 127.0.0.1 --port 8100
```

First use downloads the `buffalo_l` model pack (~280MB) from GitHub to
`~/.insightface/models/` — takes ~15s on a decent connection, confirmed
working end to end (download, init, detect-on-image call shape) as of this
session. `image_uri` values (both for enrollment photos and predict calls)
are local `file://` paths read with OpenCV; there's no upload endpoint yet.

Recognition is nearest-neighbor cosine similarity against each enrolled
person's 5-photo averaged embedding, with an **untuned** default threshold
(`FACE_MATCH_THRESHOLD = 0.35` in `service.py`) — calibrate it against real
enrollment photos before demoing; there was no real face data available to
tune against while building this.

## Tests

```sh
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference uv run python -m unittest discover inference/tests
```

The `InsightFaceRuntimeTest` case needs the same three extra deps and is
skipped without them; run the full suite with:

```sh
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference \
  uv run --with insightface --with onnxruntime --with opencv-python-headless --with numpy \
  python -m unittest discover inference/tests
```
