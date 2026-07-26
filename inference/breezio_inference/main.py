from __future__ import annotations

import argparse
import os
from pathlib import Path

from .api import make_app
from .service import InferenceService, VALID_ENROLLMENT_POSES


def display_name_from_person_id(person_id: str) -> str:
    return " ".join(part.capitalize() for part in person_id.replace("-", "_").split("_") if part) or person_id


def preload_face_enrollments(service: InferenceService, enrollment_dir: Path) -> list[dict]:
    if not enrollment_dir.is_dir():
        raise ValueError(f"face enrollment dir does not exist: {enrollment_dir}")

    enrolled = []
    for person_dir in sorted(path for path in enrollment_dir.iterdir() if path.is_dir()):
        images = []
        for pose in sorted(VALID_ENROLLMENT_POSES):
            for suffix in (".jpg", ".jpeg", ".png"):
                image_path = person_dir / f"{pose}{suffix}"
                if image_path.exists():
                    images.append({"pose": pose, "image_uri": image_path.resolve().as_uri()})
                    break
        if not images:
            continue
        enrolled.append(
            service.enroll_identity(
                {
                    "person_id": person_dir.name,
                    "display_name": display_name_from_person_id(person_dir.name),
                    "images": images,
                }
            )
        )
    return enrolled


def main() -> int:
    parser = argparse.ArgumentParser(description="Breezio edge inference HTTP service")
    parser.add_argument("--host", default=os.getenv("INFERENCE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("INFERENCE_PORT", "8100")))
    parser.add_argument("--face-enrollment-dir", default=os.getenv("BREEZIO_FACE_ENROLLMENT_DIR"))
    args = parser.parse_args()

    service = InferenceService()
    if args.face_enrollment_dir:
        enrolled = preload_face_enrollments(service, Path(args.face_enrollment_dir))
        print(f"preloaded {len(enrolled)} face identities from {args.face_enrollment_dir}")

    app = make_app(service)
    print(f"breezio-inference listening on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
