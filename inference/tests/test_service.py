import unittest

from breezio_inference.service import (
    IdentityRegistry,
    InferenceService,
    Dinov2AnomalyRuntime,
    InsightFaceRuntime,
    MoondreamCaptionRuntime,
    UltralyticsYoloRuntime,
    ModelRecord,
    ModelRegistry,
    SUPPORTED_RUNTIMES,
    SUPPORTED_TASKS,
    best_identity_match,
    cosine_similarity,
)

try:
    import cv2  # noqa: F401
    import insightface  # noqa: F401
    import onnxruntime  # noqa: F401

    HAS_FACE_DEPS = True
except ImportError:
    HAS_FACE_DEPS = False

try:
    import ultralytics  # noqa: F401

    HAS_YOLO_DEPS = True
except ImportError:
    HAS_YOLO_DEPS = False


class InferenceServiceTest(unittest.TestCase):
    def test_register_activate_and_predict_with_dry_run_runtime(self):
        registry = ModelRegistry()
        service = InferenceService(registry)

        model = service.register_model(
            {
                "model_id": "scene-v1",
                "task": "scene-classification",
                "version": "v1",
                "runtime": "dry-run",
                "labels": ["vacant", "normal", "crowded", "abnormal"],
            }
        )
        self.assertEqual(model["status"], "registered")

        activated = service.activate_model("scene-v1")
        self.assertEqual(activated["status"], "active")

        prediction = service.predict(
            {
                "task": "scene-classification",
                "input": {"image_uri": "file:///tmp/frame.jpg"},
                "trace_id": "trace-001",
            }
        )

        self.assertEqual(prediction["trace_id"], "trace-001")
        self.assertEqual(prediction["model_id"], "scene-v1")
        self.assertEqual(prediction["runtime"], "dry-run")
        self.assertEqual(prediction["outputs"]["mode"], "dry_run")
        self.assertIsNone(prediction["outputs"]["scene_label"])
        self.assertGreaterEqual(prediction["latency_ms"], 0)

    def test_predict_requires_active_model_for_task(self):
        service = InferenceService(ModelRegistry())

        with self.assertRaisesRegex(ValueError, "no active model"):
            service.predict({"task": "scene-classification", "input": {}})

    def test_enroll_identity_requires_all_five_poses_to_be_enrolled(self):
        registry = IdentityRegistry()

        partial = registry.enroll(
            {
                "person_id": "p001",
                "display_name": "Alice",
                "images": [{"pose": "front", "image_uri": "file:///tmp/alice-front.jpg"}],
            }
        )
        self.assertFalse(partial["enrolled"])

        complete = registry.enroll(
            {
                "person_id": "p001",
                "display_name": "Alice",
                "images": [
                    {"pose": "up", "image_uri": "file:///tmp/alice-up.jpg"},
                    {"pose": "down", "image_uri": "file:///tmp/alice-down.jpg"},
                    {"pose": "left", "image_uri": "file:///tmp/alice-left.jpg"},
                    {"pose": "right", "image_uri": "file:///tmp/alice-right.jpg"},
                ],
            }
        )
        self.assertTrue(complete["enrolled"])
        self.assertEqual(complete["poses"], ["down", "front", "left", "right", "up"])

    def test_enroll_identity_rejects_unsupported_pose(self):
        registry = IdentityRegistry()

        with self.assertRaisesRegex(ValueError, "unsupported pose"):
            registry.enroll(
                {
                    "person_id": "p002",
                    "display_name": "Bob",
                    "images": [{"pose": "back", "image_uri": "file:///tmp/bob-back.jpg"}],
                }
            )

    def test_face_identification_predict_reports_enrolled_count_in_dry_run(self):
        service = InferenceService()
        service.enroll_identity(
            {
                "person_id": "p001",
                "display_name": "Alice",
                "images": [{"pose": "front", "image_uri": "file:///tmp/alice-front.jpg"}],
            }
        )
        service.register_model(
            {
                "model_id": "face-id-v1",
                "task": "face-identification",
                "version": "v1",
                "runtime": "dry-run",
            }
        )
        service.activate_model("face-id-v1")

        prediction = service.predict(
            {"task": "face-identification", "input": {"image_uri": "file:///tmp/frame.jpg"}}
        )

        outputs = prediction["outputs"]
        self.assertFalse(outputs["identified"])
        self.assertIsNone(outputs["person_id"])
        self.assertEqual(outputs["enrolled_count"], 1)


class CosineSimilarityTest(unittest.TestCase):
    def test_identical_vectors_score_one(self):
        self.assertAlmostEqual(cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 1.0)

    def test_opposite_vectors_score_negative_one(self):
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [-1.0, 0.0]), -1.0)

    def test_orthogonal_vectors_score_zero(self):
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_zero_vector_is_safe(self):
        self.assertEqual(cosine_similarity([0.0, 0.0], [1.0, 1.0]), 0.0)


class BestIdentityMatchTest(unittest.TestCase):
    def test_returns_closest_candidate_above_threshold(self):
        query = [1.0, 0.0, 0.0]
        candidates = [
            {"person_id": "p001", "display_name": "Alice", "reference_embedding": [0.9, 0.1, 0.0]},
            {"person_id": "p002", "display_name": "Bob", "reference_embedding": [0.0, 1.0, 0.0]},
        ]
        match = best_identity_match(query, candidates, threshold=0.5)
        self.assertTrue(match["identified"])
        self.assertEqual(match["person_id"], "p001")

    def test_rejects_match_below_threshold_as_unidentified(self):
        query = [1.0, 0.0, 0.0]
        candidates = [{"person_id": "p001", "display_name": "Alice", "reference_embedding": [0.1, 0.9, 0.0]}]
        match = best_identity_match(query, candidates, threshold=0.5)
        self.assertFalse(match["identified"])
        self.assertIsNone(match["person_id"])

    def test_skips_candidates_without_reference_embedding(self):
        query = [1.0, 0.0]
        candidates = [
            {"person_id": "p001", "display_name": "Alice", "reference_embedding": None},
            {"person_id": "p002", "display_name": "Bob", "reference_embedding": [1.0, 0.0]},
        ]
        match = best_identity_match(query, candidates, threshold=0.5)
        self.assertEqual(match["person_id"], "p002")

    def test_no_enrolled_candidates_is_unidentified(self):
        match = best_identity_match([1.0, 0.0], [], threshold=0.5)
        self.assertFalse(match["identified"])


@unittest.skipUnless(HAS_FACE_DEPS, "insightface/onnxruntime/opencv not installed")
class InsightFaceRuntimeTest(unittest.TestCase):
    def test_handles_image_with_no_detectable_face_without_crashing(self):
        import numpy as np
        import cv2

        blank_path = "/tmp/breezio-insightface-blank-test.jpg"
        cv2.imwrite(blank_path, np.zeros((480, 640, 3), dtype="uint8"))

        runtime = InsightFaceRuntime()
        model = ModelRecord(model_id="face-id-v1", task="face-identification", version="v1", runtime="insightface-onnx")
        result = runtime.predict(model, {"image_uri": blank_path}, identities=[])

        self.assertEqual(result["mode"], "insightface-onnx")
        self.assertFalse(result["identified"])
        self.assertEqual(result["reason"], "no_face_detected")


class UltralyticsYoloRuntimeShapeTest(unittest.TestCase):
    def test_rejects_non_person_detection_task(self):
        runtime = UltralyticsYoloRuntime()
        model = ModelRecord(model_id="scene-v1", task="scene-classification", version="v1", runtime="ultralytics-yolo")
        with self.assertRaises(ValueError):
            runtime.predict(model, {"image_uri": "/tmp/frame.jpg"})

    def test_requires_image_uri(self):
        runtime = UltralyticsYoloRuntime()
        model = ModelRecord(model_id="yolo-v1", task="person-detection", version="v1", runtime="ultralytics-yolo")
        with self.assertRaises(ValueError):
            runtime.predict(model, {})


@unittest.skipUnless(HAS_YOLO_DEPS, "ultralytics not installed")
class UltralyticsYoloRuntimeTest(unittest.TestCase):
    def test_empty_image_yields_zero_people(self):
        import numpy as np
        import cv2

        blank_path = "/tmp/breezio-yolo-blank-test.jpg"
        cv2.imwrite(blank_path, np.zeros((480, 640, 3), dtype="uint8"))

        runtime = UltralyticsYoloRuntime()
        model = ModelRecord(model_id="yolo-v1", task="person-detection", version="v1", runtime="ultralytics-yolo")
        result = runtime.predict(model, {"image_uri": blank_path})

        self.assertEqual(result["mode"], "ultralytics-yolo")
        self.assertEqual(result["people_count"], 0)
        self.assertEqual(result["detections"], [])
        self.assertIn("inference", result["speed_ms"])


class Dinov2AnomalyRuntimeShapeTest(unittest.TestCase):
    def test_refuses_to_score_without_normal_bank(self):
        runtime = Dinov2AnomalyRuntime()
        model = ModelRecord(model_id="vit-anomaly-v1", task="anomaly-detection", version="v1", runtime="dinov2-knn")
        result = runtime.predict(model, {"image_uri": "/tmp/frame.jpg"})
        self.assertIsNone(result["anomaly_score"])
        self.assertEqual(result["reason"], "no_normal_bank")
        self.assertIn("not scored", result["scene_summary"])

    def test_rejects_other_tasks(self):
        runtime = Dinov2AnomalyRuntime()
        model = ModelRecord(model_id="vit-anomaly-v1", task="scene-classification", version="v1", runtime="dinov2-knn")
        with self.assertRaises(ValueError):
            runtime.predict(model, {"image_uri": "/tmp/frame.jpg"})


class MoondreamCaptionRuntimeShapeTest(unittest.TestCase):
    def test_scene_caption_task_and_runtime_are_registered(self):
        self.assertIn("scene-caption", SUPPORTED_TASKS)
        self.assertIn("moondream-vlm", SUPPORTED_RUNTIMES)
        self.assertIn(MoondreamCaptionRuntime.name, InferenceService()._runtimes)

    def test_dry_run_handles_scene_caption(self):
        service = InferenceService()
        service.register_model({"model_id": "caption-demo", "task": "scene-caption", "version": "demo", "runtime": "dry-run"})
        service.activate_model("caption-demo")
        prediction = service.predict({"task": "scene-caption", "input": {"image_uri": "file:///tmp/frame.jpg"}})
        self.assertEqual(prediction["task"], "scene-caption")
        self.assertIn("scene_summary", prediction["outputs"])

    def test_rejects_other_tasks(self):
        runtime = MoondreamCaptionRuntime()
        model = ModelRecord(model_id="caption-v1", task="person-detection", version="v1", runtime="moondream-vlm")
        with self.assertRaises(ValueError):
            runtime.predict(model, {"image_uri": "/tmp/frame.jpg"})

    def test_caption_failure_refuses_with_reason_instead_of_raising(self):
        # No transformers installed / bogus path: predict must degrade to a
        # structured refusal, mirroring Dinov2AnomalyRuntime's no-bank path.
        runtime = MoondreamCaptionRuntime()
        model = ModelRecord(model_id="caption-v1", task="scene-caption", version="v1", runtime="moondream-vlm")
        result = runtime.predict(model, {"image_uri": "/tmp/definitely-missing-frame.jpg"})
        self.assertIsNone(result["scene_summary"])
        self.assertEqual(result["reason"], "caption_failed")
        self.assertTrue(result["detail"])


if __name__ == "__main__":
    unittest.main()
