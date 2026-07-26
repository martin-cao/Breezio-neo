"""Prepare reviewable pseudo-label material from a lab video.

This script intentionally writes to a staging directory, not the canonical
YOLO train/val/test folders. Human review should happen before these labels are
promoted into the training dataset.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PERSON_CLASS_ID = 0


@dataclass(frozen=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    label: str

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def contains_center_of(self, other: "Box") -> bool:
        cx, cy = other.center
        return self.x1 <= cx <= self.x2 and self.y1 <= cy <= self.y2

    def clipped(self, width: int, height: int, pad: int = 0) -> "Box":
        return Box(
            x1=max(0.0, self.x1 - pad),
            y1=max(0.0, self.y1 - pad),
            x2=min(float(width), self.x2 + pad),
            y2=min(float(height), self.y2 + pad),
            confidence=self.confidence,
            label=self.label,
        )

    def yolo_line(self, image_width: int, image_height: int) -> str:
        cx = ((self.x1 + self.x2) / 2.0) / image_width
        cy = ((self.y1 + self.y2) / 2.0) / image_height
        w = self.width / image_width
        h = self.height / image_height
        return f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "box": [round(self.x1, 1), round(self.y1, 1), round(self.x2, 1), round(self.y2, 1)],
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract frames and pseudo-label people/faces for review.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--camera-id", default="cam-iphone-se2")
    parser.add_argument("--session", default=None)
    parser.add_argument("--sample-every-seconds", type=float, default=2.0)
    parser.add_argument("--sample-every-frames", type=int, default=0, help="override seconds-based sampling with an exact frame stride")
    parser.add_argument("--max-frames", type=int, default=0, help="0 means no limit")
    parser.add_argument("--person-conf", type=float, default=0.25)
    parser.add_argument("--face-conf", type=float, default=0.35)
    parser.add_argument("--weights", default="yolov8n.pt")
    parser.add_argument("--face-detector", choices=("insightface", "haar", "none"), default="insightface")
    parser.add_argument("--imgsz", type=int, default=960)
    return parser.parse_args()


def ensure_dirs(output_dir: Path) -> dict[str, Path]:
    paths = {
        "frames": output_dir / "frames",
        "review": output_dir / "review",
        "person_crops": output_dir / "crops" / "persons",
        "face_crops": output_dir / "crops" / "faces",
        "pseudo_labels": output_dir / "pseudo_labels_pending",
        "metadata": output_dir / "metadata",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def load_face_detector(mode: str):
    if mode == "none":
        return None
    if mode == "haar":
        import cv2

        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        return cv2.CascadeClassifier(str(cascade_path))

    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return app


def detect_people(model, frame, conf: float, imgsz: int) -> list[Box]:
    results = model.predict(frame, classes=[PERSON_CLASS_ID], conf=conf, imgsz=imgsz, verbose=False)
    boxes: list[Box] = []
    for result in results:
        for raw_box in result.boxes:
            x1, y1, x2, y2 = [float(value) for value in raw_box.xyxy[0].tolist()]
            boxes.append(Box(x1=x1, y1=y1, x2=x2, y2=y2, confidence=float(raw_box.conf[0]), label="person"))
    return boxes


def detect_faces(detector, frame, mode: str, conf: float) -> list[Box]:
    if detector is None:
        return []
    if mode == "haar":
        import cv2

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
        return [Box(float(x), float(y), float(x + w), float(y + h), 1.0, "face") for x, y, w, h in faces]

    faces = detector.get(frame)
    boxes = []
    for face in faces:
        score = float(getattr(face, "det_score", 0.0))
        if score < conf:
            continue
        x1, y1, x2, y2 = [float(value) for value in face.bbox.tolist()]
        boxes.append(Box(x1=x1, y1=y1, x2=x2, y2=y2, confidence=score, label="face"))
    return boxes


def write_crop(frame, box: Box, path: Path, pad: int = 8) -> bool:
    height, width = frame.shape[:2]
    clipped = box.clipped(width, height, pad=pad)
    x1, y1, x2, y2 = [int(round(value)) for value in (clipped.x1, clipped.y1, clipped.x2, clipped.y2)]
    if x2 <= x1 or y2 <= y1:
        return False
    import cv2

    cv2.imwrite(str(path), frame[y1:y2, x1:x2])
    return True


def draw_review(frame, people: list[Box], faces: list[Box]) -> Any:
    import cv2

    canvas = frame.copy()
    for person in people:
        x1, y1, x2, y2 = [int(round(v)) for v in (person.x1, person.y1, person.x2, person.y2)]
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (26, 176, 82), 3)
        cv2.putText(
            canvas,
            f"person {person.confidence:.2f}",
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (26, 176, 82),
            2,
            cv2.LINE_AA,
        )
    for face in faces:
        x1, y1, x2, y2 = [int(round(v)) for v in (face.x1, face.y1, face.x2, face.y2)]
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (38, 127, 255), 2)
        cv2.putText(
            canvas,
            f"face {face.confidence:.2f}",
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (38, 127, 255),
            2,
            cv2.LINE_AA,
        )
    return canvas


def frame_indices(total_frames: int, fps: float, sample_every_seconds: float, sample_every_frames: int, max_frames: int) -> list[int]:
    stride = max(1, sample_every_frames or int(round(fps * sample_every_seconds)))
    indices = list(range(0, total_frames, stride))
    if max_frames > 0:
        indices = indices[:max_frames]
    return indices


def main() -> int:
    args = parse_args()
    if not args.video.exists():
        raise FileNotFoundError(args.video)

    import cv2
    from ultralytics import YOLO

    session = args.session or args.video.stem.replace("-", "_")
    paths = ensure_dirs(args.output_dir)
    model = YOLO(args.weights)
    face_detector = load_face_detector(args.face_detector)

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open video: {args.video}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if fps <= 0 or total_frames <= 0:
        raise RuntimeError(f"invalid video metadata: fps={fps}, frames={total_frames}")

    selected = frame_indices(total_frames, fps, args.sample_every_seconds, args.sample_every_frames, args.max_frames)
    manifest_rows: list[dict[str, Any]] = []
    face_rows: list[dict[str, Any]] = []

    for ordinal, frame_index in enumerate(selected, start=1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
        if not ok:
            continue

        timestamp_seconds = frame_index / fps
        stem = f"{session}_{ordinal:04d}"
        frame_path = paths["frames"] / f"{stem}.jpg"
        review_path = paths["review"] / f"{stem}.jpg"
        label_path = paths["pseudo_labels"] / f"{stem}.txt"

        cv2.imwrite(str(frame_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        people = detect_people(model, frame, args.person_conf, args.imgsz)
        faces = detect_faces(face_detector, frame, args.face_detector, args.face_conf)
        cv2.imwrite(str(review_path), draw_review(frame, people, faces), [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        label_path.write_text("\n".join(person.yolo_line(frame.shape[1], frame.shape[0]) for person in people) + ("\n" if people else ""))

        person_crop_paths = []
        face_crop_paths = []
        for person_index, person in enumerate(people, start=1):
            crop_path = paths["person_crops"] / f"{stem}_person_{person_index:02d}.jpg"
            if write_crop(frame, person, crop_path, pad=12):
                person_crop_paths.append(str(crop_path.relative_to(args.output_dir)))
        for face_index, face in enumerate(faces, start=1):
            crop_path = paths["face_crops"] / f"{stem}_face_{face_index:02d}.jpg"
            if write_crop(frame, face, crop_path, pad=10):
                face_crop_paths.append(str(crop_path.relative_to(args.output_dir)))
            matched_person = next((idx for idx, person in enumerate(people, start=1) if person.contains_center_of(face)), None)
            face_rows.append(
                {
                    "frame_id": stem,
                    "camera_id": args.camera_id,
                    "timestamp_seconds": f"{timestamp_seconds:.3f}",
                    "face_index": face_index,
                    "matched_person_index": matched_person or "",
                    "confidence": f"{face.confidence:.4f}",
                    "box_xyxy": json.dumps(face.to_json()["box"]),
                    "crop_path": str(crop_path.relative_to(args.output_dir)),
                }
            )

        manifest_rows.append(
            {
                "frame_id": stem,
                "camera_id": args.camera_id,
                "session": session,
                "source_video": str(args.video),
                "source_frame_index": frame_index,
                "timestamp_seconds": f"{timestamp_seconds:.3f}",
                "image_path": str(frame_path.relative_to(args.output_dir)),
                "review_image_path": str(review_path.relative_to(args.output_dir)),
                "pseudo_label_path": str(label_path.relative_to(args.output_dir)),
                "people_count": len(people),
                "face_count": len(faces),
                "person_detections_json": json.dumps([person.to_json() for person in people], separators=(",", ":")),
                "face_detections_json": json.dumps([face.to_json() for face in faces], separators=(",", ":")),
                "person_crop_paths_json": json.dumps(person_crop_paths, separators=(",", ":")),
                "face_crop_paths_json": json.dumps(face_crop_paths, separators=(",", ":")),
                "review_status": "pending",
            }
        )

    cap.release()

    manifest_path = args.output_dir / "manifest_review.csv"
    with manifest_path.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(manifest_rows[0].keys()) if manifest_rows else [])
        writer.writeheader()
        writer.writerows(manifest_rows)

    faces_path = args.output_dir / "face_crops_manifest.csv"
    face_fieldnames = [
        "frame_id",
        "camera_id",
        "timestamp_seconds",
        "face_index",
        "matched_person_index",
        "confidence",
        "box_xyxy",
        "crop_path",
    ]
    with faces_path.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=face_fieldnames)
        writer.writeheader()
        writer.writerows(face_rows)

    summary = {
        "video": str(args.video),
        "output_dir": str(args.output_dir),
        "camera_id": args.camera_id,
        "session": session,
        "fps": fps,
        "total_frames": total_frames,
        "sample_every_seconds": args.sample_every_seconds,
        "sample_every_frames": args.sample_every_frames,
        "selected_frames": len(selected),
        "processed_frames": len(manifest_rows),
        "people_boxes": sum(int(row["people_count"]) for row in manifest_rows),
        "face_boxes": sum(int(row["face_count"]) for row in manifest_rows),
        "weights": args.weights,
        "face_detector": args.face_detector,
        "person_conf": args.person_conf,
        "face_conf": args.face_conf,
    }
    (paths["metadata"] / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if manifest_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
