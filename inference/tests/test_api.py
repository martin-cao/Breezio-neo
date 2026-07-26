import unittest

from flask import Flask

from breezio_inference.api import dispatch_request, make_app
from breezio_inference.service import InferenceService


class InferenceApiTest(unittest.TestCase):
    def setUp(self):
        self.service = InferenceService()

    def request(self, method, path, payload=None):
        return dispatch_request(self.service, method, path, payload or {})

    def test_health_models_activate_and_predict_routes(self):
        status, data = self.request("GET", "/api/inference/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")

        status, data = self.request(
            "POST",
            "/api/inference/models",
            {
                "model_id": "people-detector-v1",
                "task": "person-detection",
                "version": "v1",
                "runtime": "dry-run",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(data["data"]["model_id"], "people-detector-v1")

        status, data = self.request("POST", "/api/inference/models/people-detector-v1/activate")
        self.assertEqual(status, 200)
        self.assertEqual(data["data"]["status"], "active")

        status, data = self.request(
            "POST",
            "/api/inference/predict",
            {"task": "person-detection", "input": {"image_uri": "file:///tmp/frame.jpg"}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["success"], True)
        self.assertEqual(data["data"]["outputs"]["detections"], [])

    def test_identity_enroll_and_list_routes(self):
        status, data = self.request(
            "POST",
            "/api/inference/identities",
            {
                "person_id": "p001",
                "display_name": "Alice",
                "images": [{"pose": "front", "image_uri": "file:///tmp/alice-front.jpg"}],
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(data["data"]["person_id"], "p001")
        self.assertFalse(data["data"]["enrolled"])

        status, data = self.request("GET", "/api/inference/identities")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["data"]), 1)
        self.assertEqual(data["data"][0]["display_name"], "Alice")

    def test_make_app_exposes_flask_test_client_routes(self):
        app = make_app(InferenceService())
        self.assertIsInstance(app, Flask)
        client = app.test_client()

        response = client.get("/api/inference/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["service"], "breezio-inference")

        response = client.post(
            "/api/inference/models",
            json={
                "model_id": "scene-v1",
                "task": "scene-classification",
                "version": "v1",
                "runtime": "dry-run",
                "labels": ["vacant", "normal", "crowded", "abnormal"],
            },
        )
        self.assertEqual(response.status_code, 201)

        response = client.post("/api/inference/models/scene-v1/activate")
        self.assertEqual(response.status_code, 200)

        response = client.post(
            "/api/inference/predict",
            json={"task": "scene-classification", "input": {"image_uri": "file:///tmp/frame.jpg"}},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["task"], "scene-classification")

    def test_flask_app_returns_json_bad_request_for_invalid_json(self):
        client = make_app(InferenceService()).test_client()
        response = client.post("/api/inference/models", data="[", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "bad_request")


if __name__ == "__main__":
    unittest.main()
