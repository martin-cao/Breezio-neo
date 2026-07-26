"""Calibrate FACE_MATCH_THRESHOLD from real enrollment photos.

Point this at a directory of enrollment photos laid out as
    <root>/<person_id>/{front,up,down,left,right}.jpg
It computes, per person, the averaged reference embedding from all-but-one
photo and scores the held-out photo against every reference (leave-one-out):
genuine scores come from the same person, impostor scores from everyone else.
It then prints score distributions and the threshold that separates them.

Refuses to output a threshold when fewer than two enrolled people exist —
one person gives no impostor scores, so any threshold would be unfounded.

Usage:
    UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference \
      uv run --with insightface --with onnxruntime --with opencv-python-headless --with numpy \
      python inference/tools/calibrate_face_threshold.py var/face_enrollment/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from breezio_inference.service import InsightFaceRuntime, cosine_similarity

POSES = ("front", "up", "down", "left", "right")


def load_embeddings(runtime: InsightFaceRuntime, person_dir: Path) -> dict[str, list[float]]:
    embeddings = {}
    for pose in POSES:
        for ext in (".jpg", ".jpeg", ".png"):
            path = person_dir / f"{pose}{ext}"
            if path.exists():
                embedding = runtime._extract_embedding(str(path))
                if embedding is not None:
                    embeddings[pose] = embedding
                break
    return embeddings


def average(vectors: list[list[float]]) -> list[float]:
    return [sum(vec[i] for vec in vectors) / len(vectors) for i in range(len(vectors[0]))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enrollment_root", type=Path, help="dir with <person_id>/<pose>.jpg photos")
    args = parser.parse_args()

    people_dirs = [p for p in sorted(args.enrollment_root.iterdir()) if p.is_dir()] if args.enrollment_root.is_dir() else []
    if len(people_dirs) < 2:
        print(f"Found {len(people_dirs)} enrolled people under {args.enrollment_root}; need >= 2 "
              "(one person yields no impostor pairs). Collect real 5-pose enrollment photos first.")
        return 2

    runtime = InsightFaceRuntime()
    per_person = {d.name: load_embeddings(runtime, d) for d in people_dirs}
    per_person = {pid: embs for pid, embs in per_person.items() if len(embs) >= 2}
    if len(per_person) < 2:
        print("Fewer than 2 people had >= 2 usable face embeddings; check photo quality.")
        return 2

    genuine, impostor = [], []
    for pid, embs in per_person.items():
        for held_out_pose, held_out in embs.items():
            rest = [e for pose, e in embs.items() if pose != held_out_pose]
            genuine.append(cosine_similarity(held_out, average(rest)))
            for other_pid, other_embs in per_person.items():
                if other_pid != pid:
                    impostor.append(cosine_similarity(held_out, average(list(other_embs.values()))))

    genuine.sort()
    impostor.sort()
    suggested = (genuine[0] + impostor[-1]) / 2
    report = {
        "people": len(per_person),
        "genuine_scores": {"n": len(genuine), "min": round(genuine[0], 4), "median": round(genuine[len(genuine) // 2], 4)},
        "impostor_scores": {"n": len(impostor), "max": round(impostor[-1], 4), "median": round(impostor[len(impostor) // 2], 4)},
        "separable": genuine[0] > impostor[-1],
        "suggested_threshold": round(suggested, 4),
        "note": "Set FACE_MATCH_THRESHOLD in inference/breezio_inference/service.py; "
                "if not separable, prefer a threshold above impostor max and accept extra false rejects.",
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
