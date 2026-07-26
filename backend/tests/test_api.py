import math
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from breezio_backend.api import make_app
from breezio_backend.vision_events import InMemoryVisionEventSink


class FakeInferenceClient:
    def __init__(self):
        self.models = [
            {
                "model_id": "scene-v1",
                "task": "scene-classification",
                "version": "v1",
                "runtime": "dry-run",
                "status": "registered",
            }
        ]
        self.calls = []

    def health(self):
        self.calls.append(("health", None))
        return {"service": "breezio-inference", "status": "ok", "active_models": {}, "runtimes": ["dry-run"]}

    def list_models(self, task=None):
        self.calls.append(("list_models", task))
        if task:
            return [item for item in self.models if item["task"] == task]
        return list(self.models)

    def register_model(self, payload):
        self.calls.append(("register_model", payload))
        item = {**payload, "status": "registered"}
        self.models.append(item)
        return item

    def activate_model(self, model_id):
        self.calls.append(("activate_model", model_id))
        for item in self.models:
            item["status"] = "active" if item["model_id"] == model_id else item["status"]
            if item["model_id"] == model_id:
                return item
        raise KeyError(model_id)

    def predict(self, payload):
        self.calls.append(("predict", payload))
        return {
            "trace_id": "trace-1",
            "task": payload["task"],
            "model_id": "scene-v1",
            "model_version": "v1",
            "runtime": "dry-run",
            "latency_ms": 1.25,
            "outputs": {
                "mode": "dry_run",
                "scene_label": "crowded",
                "scene_confidence": 0.91,
                "people_count": 3,
                "input_ref": payload.get("input", {}).get("frame_id"),
            },
        }


class FakeArtifactStore:
    def __init__(self):
        self.items = [
            {
                "id": 1,
                "bucket": "breezio-snapshots",
                "object_key": "snapshots/cam-01/frame.jpg",
                "artifact_type": "snapshot",
                "content_type": "image/jpeg",
                "size_bytes": 128,
                "sha256": "abc",
                "etag": '"etag"',
                "meta": {"camera_id": "cam-01"},
                "presigned_url": "http://127.0.0.1:9000/breezio-snapshots/snapshots/cam-01/frame.jpg",
            }
        ]

    def list_artifacts(self, artifact_type=None, limit=100):
        if artifact_type:
            return [item for item in self.items if item["artifact_type"] == artifact_type]
        return list(self.items)

    def register_artifact(self, payload):
        item = {"id": len(self.items) + 1, "presigned_url": None, "meta": {}, **payload}
        self.items.append(item)
        return item

    def presign_artifact_url(self, artifact_id):
        for item in self.items:
            if item["id"] == artifact_id:
                return {"artifact_id": artifact_id, "url": f"http://127.0.0.1:9000/{item['bucket']}/{item['object_key']}"}
        raise KeyError(artifact_id)


class BackendApiTest(unittest.TestCase):
    def setUp(self):
        self.app = make_app(":memory:")
        self.client = self.app.test_client()

    def test_health_does_not_require_auth(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_make_app_requires_database_url_without_explicit_test_harness(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                make_app()

    def test_unknown_route_returns_json_not_found(self):
        response = self.client.get("/api/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"success": False, "error": "not_found"})

    def test_configured_cors_origin_gets_dev_headers(self):
        with patch.dict(os.environ, {"BREEZIO_CORS_ORIGINS": "http://127.0.0.1:5173"}, clear=False):
            client = make_app(":memory:").test_client()

        response = client.get("/api/health", headers={"Origin": "http://127.0.0.1:5173"})

        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://127.0.0.1:5173")
        self.assertIn("Content-Type", response.headers["Access-Control-Allow-Headers"])

    def test_user_routes_and_settings(self):
        response = self.client.get("/api/me")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["role"], "admin")
        self.assertNotIn("password_hash", response.get_json()["data"])

        response = self.client.put(
            "/api/me/settings",
            json={"theme_mode": "dark", "notifications": {"warning_alerts": False}, "vision": {"preview_fps": 30, "inference_hz": 5}},
        )
        self.assertEqual(response.status_code, 200)
        settings = response.get_json()["data"]
        self.assertEqual(settings["theme_mode"], "dark")
        self.assertFalse(settings["notifications"]["warning_alerts"])
        self.assertTrue(settings["notifications"]["critical_alerts"])
        self.assertEqual(settings["vision"]["preview_fps"], 30)
        self.assertEqual(settings["vision"]["inference_hz"], 5)
        self.assertEqual(settings["vision"]["face_inference_hz"], 1)
        self.assertEqual(settings["vision"]["preview_width"], 960)

        response = self.client.post("/api/users", json={"name": "Viewer", "email": "viewer@lab-317", "role": "viewer"})
        self.assertEqual(response.status_code, 201)
        user = response.get_json()["data"]
        self.assertEqual(user["status"], "invited")

        response = self.client.put(f"/api/users/{user['id']}", json={"status": "active", "name": "Viewer One"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["name"], "Viewer One")

        response = self.client.get("/api/users?role=viewer&status=active")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["data"]), 1)

        response = self.client.delete(f"/api/users/{user['id']}")
        self.assertEqual(response.status_code, 204)

        response = self.client.get(f"/api/users/{user['id']}")
        self.assertEqual(response.get_json()["data"]["status"], "disabled")

    def test_user_registration_login_and_bearer_identity(self):
        app = make_app(":memory:", auth_secret="test-secret", auth_required=True)
        client = app.test_client()

        response = client.post(
            "/api/auth/register",
            json={"name": "Viewer", "email": "viewer@lab-317", "password": "password-123", "role": "viewer"},
        )
        self.assertEqual(response.status_code, 201)
        auth = response.get_json()["data"]
        self.assertEqual(auth["token_type"], "Bearer")
        self.assertEqual(auth["user"]["email"], "viewer@lab-317")
        self.assertNotIn("password_hash", auth["user"])

        response = client.get("/api/me", headers={"Authorization": f"Bearer {auth['access_token']}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["email"], "viewer@lab-317")

        response = client.post("/api/auth/login", json={"email": "viewer@lab-317", "password": "wrong-password"})
        self.assertEqual(response.status_code, 401)

        response = client.post("/api/auth/login", json={"email": "viewer@lab-317", "password": "password-123"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.get_json()["data"])

    def test_bearer_user_role_controls_user_management(self):
        with patch.dict(os.environ, {"BREEZIO_BOOTSTRAP_PASSWORD": "admin-password"}, clear=False):
            app = make_app(":memory:", auth_secret="test-secret", auth_required=True)
            client = app.test_client()

            admin_login = client.post("/api/auth/login", json={"email": "operator@lab-317", "password": "admin-password"})
            self.assertEqual(admin_login.status_code, 200)
            admin_token = admin_login.get_json()["data"]["access_token"]

            response = client.get("/api/users", headers={"Authorization": f"Bearer {admin_token}"})
            self.assertEqual(response.status_code, 200)

            viewer = client.post(
                "/api/auth/register",
                json={"name": "Viewer", "email": "viewer@lab-317", "password": "password-123", "role": "viewer"},
            ).get_json()["data"]
            response = client.get("/api/users", headers={"Authorization": f"Bearer {viewer['access_token']}"})
            self.assertEqual(response.status_code, 403)

    def test_device_routes(self):
        response = self.client.post(
            "/api/devices",
            json={"name": "Fan Controller", "device_type": "fan", "location": "Equipment", "client_id": "fan-1"},
        )
        self.assertEqual(response.status_code, 201)
        device = response.get_json()["data"]
        self.assertEqual(device["status"], "offline")

        response = self.client.get("/api/devices?status=offline")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["data"]), 1)

        response = self.client.put(f"/api/devices/{device['id']}", json={"name": "Fan Controller A", "status": "offline"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["name"], "Fan Controller A")

        response = self.client.delete(f"/api/devices/{device['id']}")
        self.assertEqual(response.status_code, 204)

        response = self.client.get(f"/api/devices/{device['id']}")
        self.assertEqual(response.get_json()["data"]["status"], "offline")

        response = self.client.delete(f"/api/devices/{device['id']}/hard")
        self.assertEqual(response.status_code, 204)

        response = self.client.get(f"/api/devices/{device['id']}")
        self.assertEqual(response.status_code, 404)

    def test_camera_device_normalizes_rtsp_source_and_probe(self):
        response = self.client.post(
            "/api/devices",
            json={
                "name": "Lab RTSP Camera",
                "device_type": "camera",
                "location": "Lab Overview",
                "client_id": "cam-rtsp-01",
                "protocol": "rtsp",
                "ip_address": "192.0.2.72",
            },
        )
        self.assertEqual(response.status_code, 201)
        camera = response.get_json()["data"]
        self.assertEqual(camera["protocol"], "rtsp")
        self.assertEqual(camera["port"], 554)
        self.assertEqual(camera["source_uri"], "rtsp://192.0.2.72")

        from unittest.mock import patch

        with patch("breezio_backend.api.probe_camera_source", return_value={"ok": True, "snapshot_path": "var/camera_snapshots/cam.jpg"}):
            probe = self.client.post(f"/api/devices/{camera['id']}/probe")
        self.assertEqual(probe.status_code, 200)
        self.assertTrue(probe.get_json()["data"]["ok"])

        with patch("breezio_backend.api.stream_camera_mjpeg", return_value=iter([b"--frame\r\nContent-Type: image/jpeg\r\n\r\njpg\r\n"])):
            preview = self.client.get(f"/api/devices/{camera['id']}/preview.mjpg")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, "multipart/x-mixed-replace")
        self.assertIn(b"Content-Type: image/jpeg", preview.data)

    def test_readings_ingest_and_query_round_trip(self):
        response = self.client.post(
            "/api/readings",
            json={
                "message_id": "msg-1",
                "gateway_id": "gw-01",
                "batch_id": "1",
                "captured_at": "2026-05-30T00:00:00+00:00",
                "records": [{"node_id": "n1", "seq": 1, "temperature": 26.4, "humidity": 58.2, "light": 734}],
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["data"]["inserted_metrics"], 3)

        response = self.client.get("/api/readings?node_id=n1&metric=temperature")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["value"], 26.4)

        self.client.post(
            "/api/readings",
            json={
                "message_id": "window-old",
                "gateway_id": "gw-01",
                "batch_id": "window",
                "captured_at": "2026-06-01T00:00:00+00:00",
                "records": [{"node_id": "n-window", "seq": 1, "temperature": 21.0}],
            },
        )
        self.client.post(
            "/api/readings",
            json={
                "message_id": "window-new",
                "gateway_id": "gw-01",
                "batch_id": "window",
                "captured_at": "2026-06-02T00:00:00+00:00",
                "records": [{"node_id": "n-window", "seq": 2, "temperature": 22.0}],
            },
        )
        response = self.client.get(
            "/api/readings?node_id=n-window&metric=temperature&start=2026-06-01T12:00:00+00:00&end=2026-06-02T12:00:00+00:00"
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual([item["value"] for item in data], [22.0])

        response = self.client.get("/api/nodes/n1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["node_id"], "n1")

        response = self.client.get("/api/nodes/missing")
        self.assertEqual(response.status_code, 404)

        response = self.client.get("/api/readings/latest")
        self.assertEqual(response.status_code, 200)
        latest = response.get_json()["data"]
        self.assertGreaterEqual(len(latest), 1)
        self.assertEqual(latest[0]["node_id"], "n-window")
        self.assertEqual(latest[0]["value"], 22.0)

    def test_gateway_and_node_registration_routes(self):
        response = self.client.post(
            "/api/gateways",
            json={"gateway_id": "gw-manual", "name": "Manual Gateway", "location": "Lab"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["data"]["gateway_id"], "gw-manual")

        response = self.client.post(
            "/api/nodes",
            json={"node_id": "node-manual", "gateway_id": "gw-manual", "role": "router", "area": "Bench", "ip_address": "192.0.2.57"},
        )
        self.assertEqual(response.status_code, 201)
        node = response.get_json()["data"]
        self.assertEqual(node["node_id"], "node-manual")
        self.assertEqual(node["gateway_id"], "gw-manual")
        self.assertEqual(node["ip_address"], "192.0.2.57")

        # Re-registering without an ip_address must not clobber the stored one.
        response = self.client.post(
            "/api/nodes",
            json={"node_id": "node-manual", "gateway_id": "gw-manual", "role": "router", "area": "Bench"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["data"]["ip_address"], "192.0.2.57")

        response = self.client.get("/api/nodes?gateway_id=gw-manual")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["data"]), 1)

    def test_readings_missing_gateway_id_is_bad_request(self):
        response = self.client.post("/api/readings", json={"records": []})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "bad_request")

    def test_control_command_create_and_ack(self):
        response = self.client.post(
            "/api/control/commands",
            json={
                "target_node_id": "n1",
                "target_device": "fan",
                "action": "on",
                "desired_state": {"power": True, "speed": 2},
            },
        )
        self.assertEqual(response.status_code, 201)
        command = response.get_json()["data"]
        self.assertEqual(command["desired_state"], {"power": True, "speed": 2})
        command_id = command["command_id"]

        response = self.client.post(
            "/api/control/acks",
            json={"command_id": command_id, "node_id": "n1", "result": "OK"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "acked")

    def test_alert_create_and_ack(self):
        response = self.client.post(
            "/api/alerts", json={"alert_type": "high_temperature", "message": "34.2C"}
        )
        self.assertEqual(response.status_code, 201)
        alert_id = response.get_json()["data"]["id"]

        response = self.client.post(f"/api/alerts/{alert_id}/ack")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "acked")

        response = self.client.post("/api/alerts/999999/ack")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()["success"])

        response = self.client.post(f"/api/alerts/{alert_id}/resolve")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "resolved")

    def test_prediction_run_train_activate_and_latest_with_persistence_model(self):
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        for i in range(60):
            t = start + timedelta(seconds=i * 60)
            value = 24.0 + 2.0 * math.sin(i / 5)
            self.client.post(
                "/api/readings",
                json={
                    "message_id": f"seed-{i}",
                    "gateway_id": "gw-01",
                    "batch_id": "seed",
                    "captured_at": t.isoformat(),
                    "records": [{"node_id": "n1", "seq": i, "temperature": value}],
                },
            )

        response = self.client.post(
            "/api/prediction/runs",
            json={"target_node_id": "n1", "metric": "temperature", "model_name": "persistence", "horizon_minutes": 15},
        )
        self.assertEqual(response.status_code, 202)
        run = response.get_json()["data"]
        self.assertEqual(run["status"], "completed")
        self.assertTrue(run["is_active"])

        response = self.client.get("/api/prediction/metrics?node_id=n1&metric=temperature")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["data"]), 1)

        response = self.client.get("/api/prediction/latest?node_id=n1&metric=temperature")
        self.assertEqual(response.status_code, 200)
        self.assertIn("yhat", response.get_json()["data"])

        response = self.client.post(f"/api/prediction/runs/{run['run_id']}/activate")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["data"]["is_active"])

    def test_prediction_latest_requires_node_id_and_metric(self):
        response = self.client.get("/api/prediction/latest")
        self.assertEqual(response.status_code, 400)

    def test_bearer_token_required_when_configured(self):
        app = make_app(":memory:", token="secret")
        client = app.test_client()

        response = client.get("/api/dashboard/summary")
        self.assertEqual(response.status_code, 401)

        response = client.get("/api/dashboard/summary", headers={"Authorization": "Bearer secret"})
        self.assertEqual(response.status_code, 200)

        response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)

    def test_models_routes_proxy_to_inference_service(self):
        inference = FakeInferenceClient()
        app = make_app(":memory:", inference_client=inference)
        client = app.test_client()

        response = client.get("/api/inference/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["service"], "breezio-inference")

        response = client.get("/api/models?task=scene-classification")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"][0]["model_id"], "scene-v1")
        self.assertIn(("list_models", "scene-classification"), inference.calls)

        response = client.post(
            "/api/models",
            json={"model_id": "people-v1", "task": "person-detection", "version": "v1", "runtime": "dry-run"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["data"]["model_id"], "people-v1")

        response = client.post("/api/models/people-v1/activate")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "active")

    def test_inference_predict_persists_vision_events(self):
        app = make_app(":memory:", inference_client=FakeInferenceClient(), vision_event_sink=InMemoryVisionEventSink())
        client = app.test_client()

        response = client.post(
            "/api/inference/predict",
            json={"task": "scene-classification", "input": {"camera_id": "cam-01", "frame_id": "frame-001"}},
        )
        self.assertEqual(response.status_code, 200)
        prediction = response.get_json()["data"]
        self.assertEqual(prediction["outputs"]["scene_label"], "crowded")

        response = client.get("/api/vision/events?camera_id=cam-01&task=scene-classification")
        self.assertEqual(response.status_code, 200)
        events = response.get_json()["data"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["trace_id"], "trace-1")
        self.assertEqual(events[0]["camera_id"], "cam-01")
        self.assertEqual(events[0]["scene_label"], "crowded")
        self.assertEqual(events[0]["people_count"], 3)
        self.assertEqual(events[0]["outputs"]["scene_confidence"], 0.91)

    def test_inference_predict_persists_anomaly_and_caption_events(self):
        # anomaly-detection was silently dropped before VISION_TASKS included
        # it; scene-caption is the Moondream VLM task — both must persist.
        app = make_app(":memory:", inference_client=FakeInferenceClient(), vision_event_sink=InMemoryVisionEventSink())
        client = app.test_client()

        for task in ("anomaly-detection", "scene-caption"):
            response = client.post(
                "/api/inference/predict",
                json={"task": task, "input": {"camera_id": "cam-01", "frame_id": f"frame-{task}"}},
            )
            self.assertEqual(response.status_code, 200)
            response = client.get(f"/api/vision/events?camera_id=cam-01&task={task}")
            self.assertEqual(response.status_code, 200)
            events = response.get_json()["data"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["task"], task)

    def test_artifact_routes_use_backend_store_and_rustfs_url(self):
        app = make_app(store=FakeArtifactStore(), inference_client=FakeInferenceClient())
        client = app.test_client()

        response = client.get("/api/artifacts?artifact_type=snapshot")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"][0]["bucket"], "breezio-snapshots")

        response = client.post(
            "/api/artifacts",
            json={
                "bucket": "breezio-models",
                "object_key": "models/demo/model.onnx",
                "artifact_type": "onnx",
                "content_type": "application/octet-stream",
                "size_bytes": 42,
                "sha256": "def",
                "meta": {"task": "scene-classification"},
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["data"]["object_key"], "models/demo/model.onnx")

        response = client.post("/api/artifacts/2/presign")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["url"], "http://127.0.0.1:9000/breezio-models/models/demo/model.onnx")


if __name__ == "__main__":
    unittest.main()
