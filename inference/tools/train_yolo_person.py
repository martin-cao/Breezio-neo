"""Fine-tune a pretrained YOLO on the self-collected lab-person dataset.

Refuses to run when the dataset is empty so nobody accidentally "trains" on
nothing and reports fake metrics. See datasets/lab_person_v1/README.md for the
capture/annotation workflow.

Usage:
    UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference \
      uv run --with ultralytics --with opencv-python-headless \
      python inference/tools/train_yolo_person.py --epochs 50 --imgsz 640
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "datasets" / "lab_person_v1"
OUTPUT_DIR = REPO_ROOT / "var" / "ml" / "yolo_lab_person"


def count_images(split: str) -> int:
    split_dir = DATASET_DIR / "images" / split
    if not split_dir.is_dir():
        return 0
    return sum(1 for p in split_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})


def write_resolved_dataset_yaml() -> Path:
    """Write a training config with absolute dataset root for remote runs."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config_path = OUTPUT_DIR / "dataset.resolved.yaml"
    config_path.write_text(
        "\n".join(
            [
                f"path: {DATASET_DIR}",
                "train: images/train",
                "val: images/val",
                "test: images/test",
                "names:",
                "  0: person",
                "",
            ]
        )
    )
    return config_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-weights", default="yolov8n.pt", help="pretrained checkpoint to fine-tune from")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--export-onnx", action="store_true", help="export best.pt to ONNX after training")
    args = parser.parse_args()

    counts = {split: count_images(split) for split in ("train", "val", "test")}
    if counts["train"] == 0 or counts["val"] == 0:
        print(f"Dataset not populated yet: {counts} images under {DATASET_DIR}/images/.")
        print("Collect and annotate real lab frames first (datasets/lab_person_v1/README.md).")
        print("Refusing to train on an empty dataset — no metrics will be fabricated.")
        return 2

    from ultralytics import YOLO

    dataset_config = write_resolved_dataset_yaml()
    model = YOLO(args.base_weights)  # fine-tune from pretrained, never from scratch
    results = model.train(
        data=str(dataset_config),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        project=str(OUTPUT_DIR),
        name="train",
        exist_ok=True,
    )

    metrics = model.val(data=str(dataset_config), split="test")
    summary = {
        "base_weights": args.base_weights,
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "dataset_counts": counts,
        "test_map50": float(metrics.box.map50),
        "test_map50_95": float(metrics.box.map),
        "save_dir": str(results.save_dir),
    }
    summary_path = OUTPUT_DIR / "train" / "test_metrics.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))

    if args.export_onnx:
        best = OUTPUT_DIR / "train" / "weights" / "best.pt"
        YOLO(str(best)).export(format="onnx", imgsz=args.imgsz)
    return 0


if __name__ == "__main__":
    sys.exit(main())
