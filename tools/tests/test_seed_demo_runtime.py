import unittest
from datetime import datetime, timezone

from seed_demo_runtime import auth_headers, build_seed_plan


class SeedDemoRuntimeTest(unittest.TestCase):
    def test_seed_plan_uses_backend_api_payloads_for_dashboard_vision_and_prediction(self):
        plan = build_seed_plan(
            node_id="sim-router",
            gateway_id="gw-sim-01",
            samples=60,
            captured_at=datetime(2026, 7, 4, 8, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(len(plan.reading_batches), 60)
        self.assertEqual(plan.reading_batches[0]["gateway_id"], "gw-sim-01")
        self.assertIn("20260704T080000Z", plan.reading_batches[0]["message_id"])
        self.assertEqual(plan.reading_batches[0]["source"], "simulated")
        self.assertEqual(plan.reading_batches[0]["records"][0]["node_id"], "sim-router")
        self.assertIn("temperature", plan.reading_batches[0]["records"][0])

        self.assertEqual(plan.vision_model["model_id"], "scene-demo-v1")
        self.assertEqual(plan.vision_model["task"], "scene-classification")
        self.assertEqual(plan.vision_model["runtime"], "dry-run")
        self.assertEqual(
            [(item["task"], item["runtime"]) for item in plan.extra_vision_models],
            [
                ("person-detection", "dry-run"),
                ("face-identification", "dry-run"),
                ("anomaly-detection", "dry-run"),
                ("scene-caption", "dry-run"),
            ],
        )

        self.assertEqual(plan.vision_prediction["task"], "scene-classification")
        self.assertEqual(plan.vision_prediction["input"]["camera_id"], "cam-demo-01")
        self.assertIn("image_uri", plan.vision_prediction["input"])

        self.assertEqual(plan.prediction_run["target_node_id"], "sim-router")
        self.assertEqual(plan.prediction_run["metric"], "temperature")
        self.assertEqual(plan.prediction_run["model_name"], "persistence")

    def test_seed_plan_can_register_pretrained_vision_runtime_models(self):
        plan = build_seed_plan(
            node_id="sim-router",
            gateway_id="gw-sim-01",
            captured_at=datetime(2026, 7, 4, 8, 0, tzinfo=timezone.utc),
            vision_runtime_mode="pretrained",
        )

        self.assertEqual(plan.vision_model["runtime"], "dry-run")
        self.assertEqual(
            [(item["model_id"], item["task"], item["runtime"]) for item in plan.extra_vision_models],
            [
                ("person-yolo-pretrained-v1", "person-detection", "ultralytics-yolo"),
                ("face-insightface-pretrained-v1", "face-identification", "insightface-onnx"),
                ("anomaly-dinov2-pretrained-v1", "anomaly-detection", "dinov2-knn"),
                ("caption-moondream-pretrained-v1", "scene-caption", "moondream-vlm"),
            ],
        )

    def test_auth_headers_include_bearer_token_when_configured(self):
        self.assertEqual(auth_headers(), {"Content-Type": "application/json"})
        self.assertEqual(
            auth_headers("dev-token"),
            {"Content-Type": "application/json", "Authorization": "Bearer dev-token"},
        )


if __name__ == "__main__":
    unittest.main()
