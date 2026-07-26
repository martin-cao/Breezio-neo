# lab_anomaly_v1 — ViT/DINOv2 anomaly detection dataset & workflow

Status: **capture workflow + runnable baseline only; no frames collected yet.**
No anomaly metrics exist and none are claimed. The runtime (`dinov2-knn` in
`inference/breezio_inference/service.py`) refuses to score until a real
normal-frame bank is built.

## Approach

One-class anomaly detection: DINOv2 ViT-S/14 embeddings of camera frames,
scored by mean cosine distance to the k=5 nearest neighbors in a memory bank
of **normal** frames. Threshold = 99th percentile of held-out normal frames'
scores (set by `inference/tools/build_anomaly_bank.py`, not hand-picked).
No network is trained; this is few-shot/one-class by design and works with
dozens of frames.

## Directory layout

```
datasets/lab_anomaly_v1/
  normal/            # vacant, normal usage, normal-crowded frames (bank source)
  anomalies/         # held-out evaluation-only frames, one subdir per type:
    occlusion/       #   camera covered/blocked
    after_hours/     #   person present outside opening hours
    overcrowded/     #   abnormal crowding
    lights_vacant/   #   lights/devices on while vacant
    chaos/           #   chaotic/blocked view
  manifest.csv       # image_path,label,captured_at,camera_id,session,split
```

Normal frames: target ≥ 100 across different times of day and occupancy.
Anomaly frames may be staged (e.g. deliberately cover the camera) — staged
frames are fine for evaluation but must be marked `staged=true` in the
manifest. Never put anomaly frames in `normal/`.

## Commands

```sh
# 1. build the bank from real normal frames (refuses if < 20 frames)
UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference \
  uv run --with torch --with torchvision --with pillow --with numpy \
  python inference/tools/build_anomaly_bank.py datasets/lab_anomaly_v1/normal \
  --out var/ml/vit_anomaly/normal_bank.npz

# 2. register + activate
curl -X POST http://127.0.0.1:8100/api/inference/models -H 'Content-Type: application/json' \
  -d '{"model_id":"vit-anomaly-v1","task":"anomaly-detection","version":"v1",
       "runtime":"dinov2-knn","artifact_uri":"var/ml/vit_anomaly/normal_bank.npz"}'
curl -X POST http://127.0.0.1:8100/api/inference/models/vit-anomaly-v1/activate

# 3. score frames through the backend proxy (persists into vision_events)
curl -X POST http://127.0.0.1:8000/api/inference/predict -H 'Content-Type: application/json' \
  -d '{"task":"anomaly-detection","input":{"camera_id":"cam-01","image_uri":"file:///path/frame.jpg"}}'
```

Output fields: `anomaly_score`, `threshold`, `anomaly_label`, `confidence`
(distance-to-threshold margin), `bank_size`. Once `anomalies/` has labeled
frames, report per-type detection rate + false-positive rate on held-out
normals — until then, no numbers.
