"""Promote manually reviewed pseudo-labels into the YOLO dataset.

Approval is represented by files still present in each staging `review/`
directory. Deleted review images are treated as rejected.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "datasets" / "lab_person_v1"
SPLITS = ("train", "val", "test")


@dataclass(frozen=True)
class ReviewedRow:
    source_dir: Path
    row: dict[str, str]
    split: str

    @property
    def frame_id(self) -> str:
        return self.row["frame_id"]

    @property
    def image_source(self) -> Path:
        return self.source_dir / self.row["image_path"]

    @property
    def label_source(self) -> Path:
        return self.source_dir / self.row["pseudo_label_path"]

    @property
    def image_dest(self) -> Path:
        return DATASET_DIR / "images" / self.split / f"{self.frame_id}.jpg"

    @property
    def label_dest(self) -> Path:
        return DATASET_DIR / "labels" / self.split / f"{self.frame_id}.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote reviewed staging labels into datasets/lab_person_v1.")
    parser.add_argument("staging_dirs", nargs="+", type=Path)
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--test-ratio", type=float, default=0.15)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def assign_time_ordered_splits(rows: list[dict[str, str]], train_ratio: float, val_ratio: float) -> list[tuple[dict[str, str], str]]:
    assigned: list[tuple[dict[str, str], str]] = []
    for subset in (
        [row for row in rows if int(row.get("people_count") or 0) == 0],
        [row for row in rows if int(row.get("people_count") or 0) > 0],
    ):
        subset.sort(key=lambda row: int(row.get("source_frame_index") or 0))
        train_end = int(round(len(subset) * train_ratio))
        val_end = train_end + int(round(len(subset) * val_ratio))
        for index, row in enumerate(subset):
            split = "train" if index < train_end else "val" if index < val_end else "test"
            assigned.append((row, split))
    assigned.sort(key=lambda item: (item[1], item[0].get("session", ""), int(item[0].get("source_frame_index") or 0)))
    return assigned


def reviewed_rows_for_dir(source_dir: Path, train_ratio: float, val_ratio: float) -> list[ReviewedRow]:
    manifest = source_dir / "manifest_review.csv"
    review_dir = source_dir / "review"
    if not manifest.exists():
        raise FileNotFoundError(manifest)
    if not review_dir.exists():
        raise FileNotFoundError(review_dir)

    approved = {path.stem for path in review_dir.glob("*.jpg")}
    rows = [row for row in csv.DictReader(manifest.open()) if row["frame_id"] in approved]
    return [ReviewedRow(source_dir=source_dir, row=row, split=split) for row, split in assign_time_ordered_splits(rows, train_ratio, val_ratio)]


def ensure_empty_targets(rows: list[ReviewedRow]) -> None:
    existing = [path for item in rows for path in (item.image_dest, item.label_dest) if path.exists()]
    if existing:
        sample = "\n".join(str(path) for path in existing[:10])
        raise FileExistsError(f"refusing to overwrite existing dataset files:\n{sample}")


def copy_rows(rows: list[ReviewedRow], dry_run: bool) -> None:
    for split in SPLITS:
        (DATASET_DIR / "images" / split).mkdir(parents=True, exist_ok=True)
        (DATASET_DIR / "labels" / split).mkdir(parents=True, exist_ok=True)
    if dry_run:
        return
    for item in rows:
        if not item.image_source.exists():
            raise FileNotFoundError(item.image_source)
        if not item.label_source.exists():
            raise FileNotFoundError(item.label_source)
        shutil.copy2(item.image_source, item.image_dest)
        shutil.copy2(item.label_source, item.label_dest)


def write_manifest(rows: list[ReviewedRow], dry_run: bool) -> None:
    manifest_path = DATASET_DIR / "manifest.csv"
    fieldnames = [
        "image_path",
        "label_path",
        "captured_at",
        "camera_id",
        "session",
        "source_video",
        "source_frame_index",
        "timestamp_seconds",
        "people_count",
        "face_count",
        "split",
        "review_source_dir",
    ]
    if dry_run:
        return
    with manifest_path.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for item in rows:
            row = item.row
            writer.writerow(
                {
                    "image_path": str(item.image_dest.relative_to(DATASET_DIR)),
                    "label_path": str(item.label_dest.relative_to(DATASET_DIR)),
                    "captured_at": "",
                    "camera_id": row.get("camera_id", ""),
                    "session": row.get("session", ""),
                    "source_video": row.get("source_video", ""),
                    "source_frame_index": row.get("source_frame_index", ""),
                    "timestamp_seconds": row.get("timestamp_seconds", ""),
                    "people_count": row.get("people_count", ""),
                    "face_count": row.get("face_count", ""),
                    "split": item.split,
                    "review_source_dir": str(item.source_dir),
                }
            )


def summarize(rows: list[ReviewedRow]) -> dict[str, dict[str, int]]:
    summary = {split: {"images": 0, "empty": 0, "boxes": 0} for split in SPLITS}
    for item in rows:
        people_count = int(item.row.get("people_count") or 0)
        summary[item.split]["images"] += 1
        summary[item.split]["boxes"] += people_count
        if people_count == 0:
            summary[item.split]["empty"] += 1
    return summary


def main() -> int:
    args = parse_args()
    total_ratio = args.train_ratio + args.val_ratio + args.test_ratio
    if abs(total_ratio - 1.0) > 1e-6:
        raise ValueError("--train-ratio + --val-ratio + --test-ratio must be 1.0")

    rows: list[ReviewedRow] = []
    for source_dir in args.staging_dirs:
        rows.extend(reviewed_rows_for_dir(source_dir, args.train_ratio, args.val_ratio))
    if not rows:
        raise RuntimeError("no approved review images found")
    ensure_empty_targets(rows)
    copy_rows(rows, args.dry_run)
    write_manifest(rows, args.dry_run)

    print("promoted" if not args.dry_run else "dry-run")
    print(f"dataset_dir={DATASET_DIR}")
    for split, stats in summarize(rows).items():
        print(f"{split}: images={stats['images']} empty={stats['empty']} boxes={stats['boxes']}")
    print(f"total: images={len(rows)} boxes={sum(int(item.row.get('people_count') or 0) for item in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
