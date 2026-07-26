from __future__ import annotations

import base64
from dataclasses import dataclass, field
import os
import tempfile
from time import perf_counter
from typing import Any, Optional
from uuid import uuid4


SUPPORTED_TASKS = {"scene-classification", "person-detection", "sensor-forecast", "face-identification", "anomaly-detection", "scene-caption"}
SUPPORTED_RUNTIMES = {"dry-run", "insightface-onnx", "ultralytics-yolo", "dinov2-knn", "moondream-vlm", "ollama-vlm"}
VALID_ENROLLMENT_POSES = {"front", "up", "down", "left", "right"}
FACE_MATCH_THRESHOLD = 0.35


def payload_image_path(payload: dict[str, Any]) -> tuple[str, bool]:
    image_b64 = payload.get("image_b64") or payload.get("image_base64")
    if image_b64:
        encoded = str(image_b64)
        if "," in encoded and encoded.split(",", 1)[0].startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        suffix = f".{str(payload.get('image_format') or 'jpg').lstrip('.')}"
        tmp = tempfile.NamedTemporaryFile(prefix="breezio-infer-", suffix=suffix, delete=False)
        try:
            tmp.write(base64.b64decode(encoded))
            return tmp.name, True
        finally:
            tmp.close()

    image_uri = payload.get("image_uri")
    if not image_uri:
        raise ValueError("image_uri or image_b64 is required")
    return (image_uri[len("file://") :] if image_uri.startswith("file://") else image_uri), False


def cleanup_payload_image(path: str, temporary: bool) -> None:
    if temporary:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


@dataclass
class ModelRecord:
    model_id: str
    task: str
    version: str
    runtime: str = "dry-run"
    labels: list[str] = field(default_factory=list)
    artifact_uri: Optional[str] = None
    status: str = "registered"

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "task": self.task,
            "version": self.version,
            "runtime": self.runtime,
            "labels": list(self.labels),
            "artifact_uri": self.artifact_uri,
            "status": self.status,
        }


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, ModelRecord] = {}

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        model_id = _required_str(payload, "model_id")
        task = _required_str(payload, "task")
        version = _required_str(payload, "version")
        runtime = str(payload.get("runtime") or "dry-run")
        if task not in SUPPORTED_TASKS:
            raise ValueError(f"unsupported task: {task}")
        if runtime not in SUPPORTED_RUNTIMES:
            raise ValueError(f"unsupported runtime: {runtime}")

        labels = payload.get("labels") or []
        if not isinstance(labels, list):
            raise ValueError("labels must be a list")

        previous = self._models.get(model_id)
        status = previous.status if previous else "registered"
        record = ModelRecord(
            model_id=model_id,
            task=task,
            version=version,
            runtime=runtime,
            labels=[str(label) for label in labels],
            artifact_uri=payload.get("artifact_uri"),
            status=status,
        )
        self._models[model_id] = record
        return record.to_dict()

    def activate(self, model_id: str) -> dict[str, Any]:
        record = self._models.get(model_id)
        if record is None:
            raise KeyError(f"model not found: {model_id}")
        for candidate in self._models.values():
            if candidate.task == record.task and candidate.status == "active":
                candidate.status = "registered"
        record.status = "active"
        return record.to_dict()

    def list_models(self, task: Optional[str] = None) -> list[dict[str, Any]]:
        records = self._models.values()
        if task:
            records = [record for record in records if record.task == task]
        return [record.to_dict() for record in sorted(records, key=lambda item: item.model_id)]

    def get_active(self, task: str) -> ModelRecord:
        for record in self._models.values():
            if record.task == task and record.status == "active":
                return record
        raise ValueError(f"no active model for task: {task}")

    def active_models(self) -> dict[str, str]:
        return {record.task: record.model_id for record in self._models.values() if record.status == "active"}


@dataclass
class Identity:
    person_id: str
    display_name: str
    images: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "display_name": self.display_name,
            "poses": sorted(self.images),
            "images": dict(self.images),
            "enrolled": VALID_ENROLLMENT_POSES.issubset(self.images),
        }


class IdentityRegistry:
    """Stores enrollment reference images per known lab member.

    No embeddings are computed here; this is the dry-run bookkeeping layer.
    A real runtime adapter extracts and averages embeddings from the same
    pose-tagged image_uris to build the nearest-neighbor reference vectors.
    """

    def __init__(self) -> None:
        self._identities: dict[str, Identity] = {}

    def enroll(self, payload: dict[str, Any]) -> dict[str, Any]:
        person_id = _required_str(payload, "person_id")
        display_name = _required_str(payload, "display_name")
        images = payload.get("images")
        if not isinstance(images, list) or not images:
            raise ValueError("images must be a non-empty list")

        identity = self._identities.get(person_id) or Identity(person_id=person_id, display_name=display_name)
        identity.display_name = display_name
        for item in images:
            if not isinstance(item, dict):
                raise ValueError("each image entry must be an object")
            pose = str(item.get("pose") or "").strip().lower()
            image_uri = item.get("image_uri")
            if pose not in VALID_ENROLLMENT_POSES:
                raise ValueError(f"unsupported pose: {pose}")
            if not image_uri:
                raise ValueError("image_uri is required for each pose")
            identity.images[pose] = image_uri

        self._identities[person_id] = identity
        return identity.to_dict()

    def list_identities(self) -> list[dict[str, Any]]:
        return [identity.to_dict() for identity in sorted(self._identities.values(), key=lambda item: item.person_id)]


class DryRunRuntime:
    """Runtime adapter that exercises the service path without real ML deps."""

    name = "dry-run"

    def predict(self, model: ModelRecord, payload: dict[str, Any], **context: Any) -> dict[str, Any]:
        if model.task == "scene-classification":
            return {
                "mode": "dry_run",
                "scene_label": None,
                "scene_confidence": None,
                "labels": list(model.labels),
                "input_ref": payload.get("image_uri") or payload.get("frame_id"),
            }
        if model.task == "person-detection":
            return {
                "mode": "dry_run",
                "detections": [],
                "people_count": None,
                "input_ref": payload.get("image_uri") or payload.get("frame_id"),
            }
        if model.task == "sensor-forecast":
            return {
                "mode": "dry_run",
                "predictions": [],
                "horizon": payload.get("horizon"),
                "metric": payload.get("metric"),
            }
        if model.task == "anomaly-detection":
            return {
                "mode": "dry_run",
                "anomaly_score": None,
                "anomaly_label": None,
                "input_ref": payload.get("image_uri") or payload.get("frame_id"),
            }
        if model.task == "face-identification":
            enrolled = context.get("identities") or []
            return {
                "mode": "dry_run",
                "identified": False,
                "person_id": None,
                "display_name": None,
                "confidence": None,
                "enrolled_count": len(enrolled),
                "input_ref": payload.get("image_uri") or payload.get("frame_id"),
            }
        if model.task == "scene-caption":
            return {
                "mode": "dry_run",
                "scene_summary": None,
                "input_ref": payload.get("image_uri") or payload.get("frame_id"),
            }
        raise ValueError(f"unsupported task: {model.task}")


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def best_identity_match(
    embedding: list[float],
    candidates: list[dict[str, Any]],
    threshold: float = FACE_MATCH_THRESHOLD,
) -> dict[str, Any]:
    """candidates: [{"person_id", "display_name", "reference_embedding"}, ...].

    Nearest-neighbor cosine match, no classifier. Candidates missing a
    reference_embedding (not enrolled yet, or embedding extraction failed)
    are skipped. `threshold` is an untuned default — calibrate against real
    enrollment photos before relying on it (docs/vision-model-design.md §9).
    """
    best: Optional[dict[str, Any]] = None
    best_score = -1.0
    for candidate in candidates:
        reference = candidate.get("reference_embedding")
        if not reference:
            continue
        score = cosine_similarity(embedding, reference)
        if score > best_score:
            best_score, best = score, candidate
    if best is None or best_score < threshold:
        return {
            "identified": False,
            "person_id": None,
            "display_name": None,
            "confidence": round(max(best_score, 0.0), 4),
        }
    return {
        "identified": True,
        "person_id": best["person_id"],
        "display_name": best["display_name"],
        "confidence": round(best_score, 4),
    }


class InsightFaceRuntime:
    """Real face-identification runtime: InsightFace `buffalo_l` (ArcFace) on ONNX Runtime.

    No training loop: enrollment averages embeddings extracted from each
    person's pose-tagged reference photos, recognition is nearest-neighbor
    cosine similarity against those averages. Requires `insightface`,
    `onnxruntime` and `opencv-python-headless`, none of which are installed
    by default — this class only imports them lazily on first use so the
    rest of the service keeps working without them. See
    docs/vision-model-design.md §9.
    """

    name = "insightface-onnx"
    DEFAULT_PACK = "buffalo_l"
    KNOWN_PACKS = ("buffalo_l", "buffalo_m", "buffalo_s", "buffalo_sc", "antelopev2")

    def __init__(self) -> None:
        self._apps: dict[str, Any] = {}
        self._pack = self.DEFAULT_PACK

    def _face_app(self, pack: Optional[str] = None):
        pack = pack or self._pack
        if pack not in self._apps:
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(name=pack, providers=["CPUExecutionProvider"])
            app.prepare(ctx_id=0, det_size=(640, 640))
            self._apps[pack] = app
        return self._apps[pack]

    def _select_pack(self, model: "ModelRecord") -> str:
        # the model pack rides in the record's version (registry entries use
        # e.g. version="buffalo_l"), so registering a second face model is
        # pure registration — no code change per pack
        if model.version in self.KNOWN_PACKS:
            return model.version
        return self.DEFAULT_PACK

    def _extract_face(self, image_uri: str) -> Optional[dict[str, Any]]:
        import cv2

        path = image_uri[len("file://") :] if image_uri.startswith("file://") else image_uri
        image = cv2.imread(path)
        if image is None:
            return None
        faces = self._face_app().get(image)
        if not faces:
            return None
        best_face = max(faces, key=lambda face: face.det_score)
        return {
            "embedding": [float(x) for x in best_face.embedding],
            "box": [round(float(v), 1) for v in best_face.bbox.tolist()],
            "det_score": round(float(best_face.det_score), 4),
        }

    def _extract_embedding(self, image_uri: str) -> Optional[list[float]]:
        face = self._extract_face(image_uri)
        return face["embedding"] if face else None

    def _reference_embedding(self, identity: dict[str, Any]) -> Optional[list[float]]:
        embeddings = [
            embedding
            for embedding in (self._extract_embedding(uri) for uri in identity.get("images", {}).values())
            if embedding is not None
        ]
        if not embeddings:
            return None
        length = len(embeddings[0])
        return [sum(vec[i] for vec in embeddings) / len(embeddings) for i in range(length)]

    def predict(self, model: ModelRecord, payload: dict[str, Any], **context: Any) -> dict[str, Any]:
        if model.task != "face-identification":
            raise ValueError(f"InsightFaceRuntime only supports face-identification, got: {model.task}")
        self._pack = self._select_pack(model)
        image_uri = payload.get("image_uri")

        identities = context.get("identities") or []
        path, temporary = payload_image_path(payload)
        try:
            face = self._extract_face(path)
            if face is None:
                return {
                    "mode": "insightface-onnx",
                    "identified": False,
                    "person_id": None,
                    "display_name": None,
                    "confidence": None,
                    "reason": "no_face_detected",
                    "enrolled_count": len(identities),
                    "detections": [],
                    "input_ref": image_uri or payload.get("frame_id"),
                }
            embedding = face["embedding"]

            candidates = [
                {
                    "person_id": identity["person_id"],
                    "display_name": identity["display_name"],
                    "reference_embedding": self._reference_embedding(identity),
                }
                for identity in identities
                if identity.get("enrolled")
            ]
            match = best_identity_match(embedding, candidates)
            label = match["display_name"] or "unknown face"
            confidence = match["confidence"] if match["confidence"] is not None else face["det_score"]
            return {
                "mode": "insightface-onnx",
                "enrolled_count": len(identities),
                "detections": [
                    {
                        "label": label,
                        "confidence": confidence,
                        "box": face["box"],
                    }
                ],
                "face_confidence": face["det_score"],
                "input_ref": image_uri or payload.get("frame_id"),
                **match,
            }
        finally:
            cleanup_payload_image(path, temporary)


class UltralyticsYoloRuntime:
    """Person-detection runtime backed by Ultralytics YOLO.

    Loads weights from the model's `artifact_uri` (a local .pt/.onnx path,
    e.g. the fine-tuned lab-person checkpoint) or falls back to the
    pretrained `yolov8n.pt` when no artifact is registered. Detection is
    restricted to the COCO `person` class. Requires `ultralytics` (and its
    torch dependency), imported lazily so the rest of the service works
    without them — mirror of InsightFaceRuntime's dependency policy.
    """

    name = "ultralytics-yolo"
    DEFAULT_WEIGHTS = "yolov8n.pt"
    PERSON_CLASS_ID = 0

    def __init__(self) -> None:
        self._models: dict[str, Any] = {}

    def _yolo(self, artifact_uri: Optional[str]):
        weights = artifact_uri or self.DEFAULT_WEIGHTS
        if weights.startswith("file://"):
            weights = weights[len("file://") :]
        if weights not in self._models:
            from ultralytics import YOLO

            self._models[weights] = YOLO(weights)
        return self._models[weights]

    def predict(self, model: ModelRecord, payload: dict[str, Any], **context: Any) -> dict[str, Any]:
        if model.task != "person-detection":
            raise ValueError(f"UltralyticsYoloRuntime only supports person-detection, got: {model.task}")
        image_uri = payload.get("image_uri")
        path, temporary = payload_image_path(payload)

        yolo = self._yolo(model.artifact_uri)
        conf = float(payload.get("conf", 0.25))
        try:
            results = yolo.predict(path, classes=[self.PERSON_CLASS_ID], conf=conf, verbose=False)
        finally:
            cleanup_payload_image(path, temporary)
        result = results[0]
        detections = []
        for box in result.boxes:
            xyxy = [round(float(v), 1) for v in box.xyxy[0].tolist()]
            detections.append(
                {
                    "label": result.names.get(int(box.cls[0]), "person"),
                    "confidence": round(float(box.conf[0]), 4),
                    "box": xyxy,
                }
            )
        speed = {key: round(float(value), 3) for key, value in (result.speed or {}).items()}
        return {
            "mode": self.name,
            "weights": model.artifact_uri or self.DEFAULT_WEIGHTS,
            "people_count": len(detections),
            "detections": detections,
            "speed_ms": speed,
            "input_ref": image_uri or payload.get("frame_id"),
            "snapshot_object_key": payload.get("snapshot_object_key"),
        }


class Dinov2AnomalyRuntime:
    """ViT-based anomaly detection: DINOv2 embeddings + kNN distance to a
    memory bank of *real* normal lab frames.

    The bank is a .npz built by inference/tools/build_anomaly_bank.py from
    self-collected normal frames (vacant / normal / crowded-normal). The
    model's `artifact_uri` points at that .npz. anomaly_score is the mean
    cosine distance to the k nearest normal embeddings; the threshold is
    stored in the bank file (from held-out normal frames' score quantile).
    Without a bank this runtime refuses to score instead of inventing
    numbers. Requires torch + torchvision (lazy import).
    """

    name = "dinov2-knn"
    DEFAULT_K = 5
    DEFAULT_ARCH = "dinov2_vits14"
    KNOWN_ARCHS = ("dinov2_vits14", "dinov2_vitb14", "dinov2_vitl14")

    def __init__(self) -> None:
        self._models: dict[str, Any] = {}
        self._arch = self.DEFAULT_ARCH
        self._banks: dict[str, Any] = {}

    def _dinov2(self, arch: Optional[str] = None):
        # arch rides in the model record's version (e.g. "dinov2_vitb14"),
        # so a second anomaly model is registration + its own bank .npz —
        # bank embeddings are arch-specific and must match
        arch = arch or self._arch
        if arch not in self._models:
            import torch

            # skip_validation avoids a torch.hub GitHub-API call that crashes
            # with KeyError('Authorization') on rate-limited networks
            model = torch.hub.load("facebookresearch/dinov2", arch, skip_validation=True)
            model.eval()
            self._models[arch] = model
        return self._models[arch]

    def embed(self, path: str) -> "Any":
        import numpy as np
        import torch
        from PIL import Image

        image = Image.open(path).convert("RGB").resize((224, 224))
        array = np.asarray(image, dtype="float32") / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype="float32")
        std = np.array([0.229, 0.224, 0.225], dtype="float32")
        tensor = torch.from_numpy(((array - mean) / std).transpose(2, 0, 1)).unsqueeze(0)
        with torch.no_grad():
            embedding = self._dinov2()(tensor)[0].numpy()
        return embedding / (np.linalg.norm(embedding) + 1e-12)

    def _bank(self, artifact_uri: str):
        if artifact_uri not in self._banks:
            import numpy as np

            path = artifact_uri[len("file://") :] if artifact_uri.startswith("file://") else artifact_uri
            data = np.load(path, allow_pickle=False)
            self._banks[artifact_uri] = {
                "embeddings": data["embeddings"],
                "threshold": float(data["threshold"]),
            }
        return self._banks[artifact_uri]

    def score(self, embedding, bank_embeddings, k: int = DEFAULT_K) -> float:
        import numpy as np

        similarities = bank_embeddings @ embedding
        top_k = np.sort(similarities)[-min(k, len(similarities)) :]
        return float(np.mean(1.0 - top_k))

    def predict(self, model: ModelRecord, payload: dict[str, Any], **context: Any) -> dict[str, Any]:
        if model.task != "anomaly-detection":
            raise ValueError(f"Dinov2AnomalyRuntime only supports anomaly-detection, got: {model.task}")
        if model.version in self.KNOWN_ARCHS:
            self._arch = model.version
        image_uri = payload.get("image_uri")
        if not model.artifact_uri:
            return {
                "mode": self.name,
                "anomaly_score": None,
                "anomaly_label": None,
                "scene_summary": "No ViT normal-frame bank is registered, so this frame was not scored.",
                "reason": "no_normal_bank",
                "detail": "register the model with artifact_uri pointing at a normal-frame bank "
                          "(inference/tools/build_anomaly_bank.py); scoring without real normal data would be fabricated",
                "input_ref": image_uri,
            }

        path, temporary = payload_image_path(payload)
        bank = self._bank(model.artifact_uri)
        try:
            embedding = self.embed(path)
        finally:
            cleanup_payload_image(path, temporary)
        score = self.score(embedding, bank["embeddings"])
        is_anomaly = score > bank["threshold"]
        label = "anomaly" if is_anomaly else "normal"
        summary = (
            "Scene differs from the normal lab reference bank; inspect camera view."
            if is_anomaly
            else "Scene is consistent with the normal lab reference bank."
        )
        return {
            "mode": self.name,
            "anomaly_score": round(score, 4),
            "threshold": round(bank["threshold"], 4),
            "anomaly_label": label,
            "scene_summary": f"{summary} score={score:.4f}, threshold={bank['threshold']:.4f}.",
            "confidence": round(abs(score - bank["threshold"]) / max(bank["threshold"], 1e-6), 4),
            "bank_size": int(len(bank["embeddings"])),
            "input_ref": image_uri or payload.get("frame_id"),
            "snapshot_object_key": payload.get("snapshot_object_key"),
        }


class MoondreamCaptionRuntime:
    """Generative scene captioning: Moondream2 (a ~1.9B-param VLM) produces a
    free-text description of a camera frame — unlike Dinov2AnomalyRuntime's
    templated scene_summary, this text is actually generated by the model.

    Requires transformers==4.51.3 + torch + einops + accelerate (lazy import,
    mirror of the other runtimes' dependency policy). The model revision is
    pinned because its call surface (.caption()/.query()) changed across Hub
    revisions, and transformers is pinned because this revision's custom code
    breaks on newer transformers internals ('all_tied_weights_keys').
    trust_remote_code executes the repo's own modeling code — same accepted
    risk class as Dinov2AnomalyRuntime's torch.hub.load.

    BREEZIO_VLM_MODE=eager|compile selects whether to wrap the model with
    torch.compile. Default is eager: per the Admit-or-Avoid admission
    methodology (tools/runtime_admission_bench.py), optimization startup cost
    is only worth paying when a per-device benchmark says so.

    BREEZIO_VLM_PLACEMENT=cuda|cpu|swap selects where the model lives, same
    measure-first policy (tools/runtime_admission_bench.py --workload
    gpu-offload). Default: cuda when available. "cpu" keeps the model on CPU
    permanently; "swap" parks it in CPU RAM and moves it to the GPU only for
    the duration of a caption call, trading per-call PCIe transfer time for
    idle VRAM (the caption cadence is minutes, so it is the cold model of the
    stack). swap loads without device_map so .to() stays legal (no accelerate
    hooks), and is incompatible with BREEZIO_VLM_MODE=compile.
    """

    name = "moondream-vlm"
    MODEL_ID = "vikhyatk/moondream2"
    REVISION = "2025-06-21"

    def __init__(self) -> None:
        self._model = None
        self._placement = "cpu"
        import threading

        self._swap_lock = threading.Lock()

    def _moondream(self):
        if self._model is None:
            import torch
            from transformers import AutoModelForCausalLM

            placement = os.getenv("BREEZIO_VLM_PLACEMENT", "").lower()
            if placement not in {"cuda", "cpu", "swap"}:
                placement = "cuda" if torch.cuda.is_available() else "cpu"
            if placement in {"cuda", "swap"} and not torch.cuda.is_available():
                placement = "cpu"
            kwargs: dict[str, Any] = {}
            if placement == "cuda":
                kwargs["device_map"] = {"": "cuda"}
            # BREEZIO_VLM_DTYPE=float16|int8 — default is unset (fp32), per the
            # measure-first policy; fp16 halves VRAM and PCIe swap time, int8
            # (bitsandbytes) quarters VRAM but changes numerics more. Both
            # alter generated text vs fp32, so the admission bench gates them
            # on recorded-caption review, not silent equality.
            dtype = os.getenv("BREEZIO_VLM_DTYPE", "").lower()
            if dtype == "float16":
                kwargs["torch_dtype"] = torch.float16
            elif dtype == "int8":
                from transformers import BitsAndBytesConfig

                kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
                kwargs.setdefault("device_map", {"": "cuda"})
                if placement != "cuda":
                    raise ValueError("BREEZIO_VLM_DTYPE=int8 requires BREEZIO_VLM_PLACEMENT=cuda (bitsandbytes models cannot .to() across devices)")
            # cpu/swap: plain CPU load, no device_map, so the model has no
            # accelerate hooks and .to(device) is legal
            model = AutoModelForCausalLM.from_pretrained(
                self.MODEL_ID,
                revision=self.REVISION,
                trust_remote_code=True,
                **kwargs,
            )
            if placement != "swap" and os.getenv("BREEZIO_VLM_MODE", "eager").lower() == "compile":
                model = torch.compile(model)
            self._placement = placement
            self._model = model
        return self._model

    def _caption(self, image: "Any", length: str) -> str:
        model = self._moondream()
        if self._placement != "swap":
            return model.caption(image, length=length)["caption"]
        import torch

        with self._swap_lock:
            model.to("cuda")
            try:
                return model.caption(image, length=length)["caption"]
            finally:
                model.to("cpu")
                torch.cuda.empty_cache()

    def predict(self, model: ModelRecord, payload: dict[str, Any], **context: Any) -> dict[str, Any]:
        if model.task != "scene-caption":
            raise ValueError(f"MoondreamCaptionRuntime only supports scene-caption, got: {model.task}")
        image_uri = payload.get("image_uri")
        path, temporary = payload_image_path(payload)
        try:
            from PIL import Image

            image = Image.open(path).convert("RGB")
            length = str(payload.get("length") or "normal")
            if length not in {"short", "normal"}:
                length = "normal"
            caption = self._caption(image, length)
        except Exception as exc:  # noqa: BLE001 — refuse with a reason instead of fabricating a caption
            return {
                "mode": self.name,
                "scene_summary": None,
                "reason": "caption_failed",
                "detail": str(exc),
                "input_ref": image_uri or payload.get("frame_id"),
            }
        finally:
            cleanup_payload_image(path, temporary)
        return {
            "mode": self.name,
            "scene_summary": str(caption).strip(),
            "caption_length": length,
            "input_ref": image_uri or payload.get("frame_id"),
            "snapshot_object_key": payload.get("snapshot_object_key"),
        }


class OllamaCaptionRuntime:
    """Second scene-caption model family: any Ollama-served VLM (e.g.
    qwen2.5vl:3b). The Ollama model name rides in the record's version, so
    switching caption models is registration + activate, mirroring the other
    runtimes' policy. Ollama loads/unloads models on demand, so this holds
    no VRAM while idle — same cold-model placement philosophy as
    BREEZIO_VLM_PLACEMENT=swap for Moondream.

    BREEZIO_OLLAMA_URL overrides the server (default http://127.0.0.1:11434).
    Refuses with a reason on any failure instead of fabricating a caption.
    """

    name = "ollama-vlm"
    PROMPT = "Describe this camera scene in one or two short sentences."

    def predict(self, model: ModelRecord, payload: dict[str, Any], **context: Any) -> dict[str, Any]:
        if model.task != "scene-caption":
            raise ValueError(f"OllamaCaptionRuntime only supports scene-caption, got: {model.task}")
        import base64
        import json as _json
        import urllib.request

        image_uri = payload.get("image_uri")
        path, temporary = payload_image_path(payload)
        try:
            with open(path, "rb") as fh:
                image_b64 = base64.b64encode(fh.read()).decode("ascii")
            base_url = os.getenv("BREEZIO_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
            request = urllib.request.Request(
                f"{base_url}/api/generate",
                data=_json.dumps(
                    {"model": model.version, "prompt": self.PROMPT, "images": [image_b64], "stream": False}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=180) as response:
                body = _json.loads(response.read().decode("utf-8"))
            caption = str(body.get("response") or "").strip()
            if not caption:
                raise RuntimeError(f"empty response from ollama for model {model.version}")
        except Exception as exc:  # noqa: BLE001 — refuse with a reason instead of fabricating a caption
            return {
                "mode": self.name,
                "scene_summary": None,
                "reason": "caption_failed",
                "detail": str(exc),
                "input_ref": image_uri or payload.get("frame_id"),
            }
        finally:
            cleanup_payload_image(path, temporary)
        return {
            "mode": self.name,
            "scene_summary": caption,
            "caption_length": "normal",
            "input_ref": image_uri or payload.get("frame_id"),
            "snapshot_object_key": payload.get("snapshot_object_key"),
        }


class InferenceService:
    def __init__(
        self,
        registry: Optional[ModelRegistry] = None,
        identities: Optional[IdentityRegistry] = None,
    ) -> None:
        self.registry = registry or ModelRegistry()
        self.identities = identities or IdentityRegistry()
        self._runtimes = {
            DryRunRuntime.name: DryRunRuntime(),
            InsightFaceRuntime.name: InsightFaceRuntime(),
            UltralyticsYoloRuntime.name: UltralyticsYoloRuntime(),
            Dinov2AnomalyRuntime.name: Dinov2AnomalyRuntime(),
            MoondreamCaptionRuntime.name: MoondreamCaptionRuntime(),
            OllamaCaptionRuntime.name: OllamaCaptionRuntime(),
        }

    def health(self) -> dict[str, Any]:
        return {
            "service": "breezio-inference",
            "status": "ok",
            "active_models": self.registry.active_models(),
            "runtimes": sorted(self._runtimes),
        }

    def register_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.registry.register(payload)

    def activate_model(self, model_id: str) -> dict[str, Any]:
        return self.registry.activate(model_id)

    def list_models(self, task: Optional[str] = None) -> list[dict[str, Any]]:
        return self.registry.list_models(task)

    def enroll_identity(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.identities.enroll(payload)

    def list_identities(self) -> list[dict[str, Any]]:
        return self.identities.list_identities()

    def predict(self, payload: dict[str, Any]) -> dict[str, Any]:
        task = _required_str(payload, "task")
        model = self.registry.get_active(task)
        runtime = self._runtimes[model.runtime]
        trace_id = str(payload.get("trace_id") or uuid4())
        input_payload = payload.get("input") or {}
        if not isinstance(input_payload, dict):
            raise ValueError("input must be an object")

        context: dict[str, Any] = {}
        if task == "face-identification":
            context["identities"] = self.identities.list_identities()

        started = perf_counter()
        outputs = runtime.predict(model, input_payload, **context)
        latency_ms = round((perf_counter() - started) * 1000, 3)
        return {
            "trace_id": trace_id,
            "task": task,
            "model_id": model.model_id,
            "model_version": model.version,
            "runtime": model.runtime,
            "latency_ms": latency_ms,
            "timings": {
                "preprocess_ms": 0.0,
                "inference_ms": latency_ms,
                "postprocess_ms": 0.0,
            },
            "outputs": outputs,
        }


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing required field: {key}")
    return value.strip()
