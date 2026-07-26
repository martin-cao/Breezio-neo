"""Build the DINOv2 normal-frame memory bank for the anomaly runtime.

Input: a directory of *real, self-collected* normal lab frames (vacant,
normal usage, normal-crowded). Frames are embedded with DINOv2 ViT-S/14;
a held-out fraction of the normal frames sets the score threshold (their
kNN-distance quantile), so the threshold comes from data, not from a guess.

Refuses to run with fewer than 20 frames — a smaller bank gives a
meaningless threshold. See docs/vision-anomaly-dataset.md for the capture
manifest/workflow, and do not put staged anomaly frames in this directory.

Usage:
    UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference \
      uv run --with torch --with torchvision --with pillow --with numpy \
      python inference/tools/build_anomaly_bank.py datasets/lab_anomaly_v1/normal \
      --out var/ml/vit_anomaly/normal_bank.npz
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from breezio_inference.service import Dinov2AnomalyRuntime

MIN_FRAMES = 20
HOLDOUT_FRACTION = 0.2
THRESHOLD_QUANTILE = 0.99


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("normal_dir", type=Path)
    parser.add_argument("--out", type=Path, default=Path("var/ml/vit_anomaly/normal_bank.npz"))
    parser.add_argument("--arch", default="dinov2_vits14", choices=("dinov2_vits14", "dinov2_vitb14", "dinov2_vitl14"), help="bank embeddings are arch-specific; register the bank on a model whose version matches")
    args = parser.parse_args()

    frames = sorted(
        p for p in args.normal_dir.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    ) if args.normal_dir.is_dir() else []
    if len(frames) < MIN_FRAMES:
        print(f"Found {len(frames)} normal frames under {args.normal_dir}; need >= {MIN_FRAMES}.")
        print("Collect real normal lab frames first (docs/vision-anomaly-dataset.md). "
              "Refusing to build a bank that would produce meaningless anomaly scores.")
        return 2

    import numpy as np

    runtime = Dinov2AnomalyRuntime()
    runtime._arch = args.arch
    embeddings = np.stack([runtime.embed(str(p)) for p in frames])

    rng = np.random.default_rng(42)
    order = rng.permutation(len(embeddings))
    holdout_n = max(1, int(len(embeddings) * HOLDOUT_FRACTION))
    holdout, bank = embeddings[order[:holdout_n]], embeddings[order[holdout_n:]]

    holdout_scores = [runtime.score(e, bank) for e in holdout]
    threshold = float(np.quantile(holdout_scores, THRESHOLD_QUANTILE))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.out, embeddings=bank, threshold=threshold)
    print(f"bank: {len(bank)} embeddings, holdout: {holdout_n}, "
          f"threshold (q{THRESHOLD_QUANTILE} of held-out normal scores): {threshold:.4f}")
    print(f"wrote {args.out} — register with artifact_uri={args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
