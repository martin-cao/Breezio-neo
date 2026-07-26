import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import run_camera_gateway as gateway


class CameraGatewayTest(unittest.TestCase):
    def test_rtsp_snapshot_command_uses_udp_transport(self):
        command = gateway.ffmpeg_snapshot_command("rtsp://192.0.2.72", Path("frame.jpg"))

        self.assertEqual(command[:4], ["ffmpeg", "-y", "-rtsp_transport", "udp"])
        self.assertIn("rtsp://192.0.2.72", command)
        self.assertEqual(command[-1], "frame.jpg")

    def test_resolves_registered_camera_device_source(self):
        args = gateway.parser().parse_args(["--device-id", "7"])

        source_uri, camera_id = gateway.resolve_camera_source(
            args,
            fetch_json=lambda *a, **k: {
                "id": 7,
                "device_type": "camera",
                "client_id": "cam-rtsp-01",
                "protocol": "rtsp",
                "ip_address": "192.0.2.72",
                "port": 554,
            },
        )

        self.assertEqual(source_uri, "rtsp://192.0.2.72")
        self.assertEqual(camera_id, "cam-rtsp-01")

    def test_run_once_captures_frame_and_posts_person_and_face_predictions(self):
        posts = []

        def fake_post(_backend_url, path, payload=None, **_kwargs):
            posts.append((path, payload))
            return {"trace_id": f"trace-{payload['task']}"}

        def fake_run(command, **_kwargs):
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"jpg")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as tmpdir:
            args = gateway.parser().parse_args(
                [
                    "--source-uri",
                    "rtsp://192.0.2.72",
                    "--camera-id",
                    "cam-rtsp-01",
                    "--snapshot-dir",
                    tmpdir,
                ]
            )
            result = gateway.run_once(
                args,
                post_json=fake_post,
                run_command=fake_run,
                captured_at=datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(result["camera_id"], "cam-rtsp-01")
        self.assertEqual({item[0] for item in posts}, {"/api/inference/predict"})
        self.assertEqual(
            [item[1]["task"] for item in posts],
            ["person-detection", "face-identification", "anomaly-detection", "scene-caption"],
        )
        self.assertTrue(posts[0][1]["input"]["image_uri"].startswith("file://"))
        self.assertEqual(posts[0][1]["input"]["image_b64"], "anBn")
        self.assertEqual(posts[0][1]["input"]["image_format"], "jpg")
        self.assertEqual(posts[0][1]["input"]["source_uri"], "rtsp://192.0.2.72")
        self.assertEqual(posts[0][1]["input"]["conf"], 0.25)
        self.assertNotIn("conf", posts[1][1]["input"])
        self.assertEqual(posts[1][1]["input"]["image_b64"], "anBn")

    def test_anomalous_frame_triggers_immediate_caption(self):
        posts = []

        def fake_post(_backend_url, path, payload=None, **_kwargs):
            posts.append((path, payload))
            if payload["task"] == "anomaly-detection":
                return {"task": "anomaly-detection", "outputs": {"anomaly_label": "anomaly", "anomaly_score": 0.9}}
            return {"task": payload["task"], "outputs": {}}

        def fake_run(command, **_kwargs):
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"jpg")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as tmpdir:
            args = gateway.parser().parse_args(["--source-uri", "rtsp://192.0.2.72", "--camera-id", "cam-rtsp-01", "--snapshot-dir", tmpdir])
            result = gateway.run_once(
                args,
                post_json=fake_post,
                run_command=fake_run,
                tasks=["anomaly-detection"],
                caption_on_anomaly=True,
            )

        self.assertEqual([item[1]["task"] for item in posts], ["anomaly-detection", "scene-caption"])
        caption = next(p for p in result["predictions"] if p["task"] == "scene-caption")
        self.assertEqual(caption.get("trigger"), "anomaly")

    def test_normal_frame_does_not_trigger_extra_caption(self):
        posts = []

        def fake_post(_backend_url, path, payload=None, **_kwargs):
            posts.append((path, payload))
            return {"task": payload["task"], "outputs": {"anomaly_label": "normal"}}

        def fake_run(command, **_kwargs):
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"jpg")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as tmpdir:
            args = gateway.parser().parse_args(["--source-uri", "rtsp://192.0.2.72", "--camera-id", "cam-rtsp-01", "--snapshot-dir", tmpdir])
            gateway.run_once(
                args,
                post_json=fake_post,
                run_command=fake_run,
                tasks=["anomaly-detection"],
                caption_on_anomaly=True,
            )

        self.assertEqual([item[1]["task"] for item in posts], ["anomaly-detection"])

    def test_runtime_settings_reads_vision_inference_rate(self):
        args = gateway.parser().parse_args(["--source-uri", "rtsp://192.0.2.72"])

        settings = gateway.runtime_settings(
            args,
            fetch_json=lambda *a, **k: {
                "vision": {
                    "inference_hz": 5,
                    "face_inference_hz": 1,
                    "person_confidence": 0.3,
                    "anomaly_inference_hz": 0.5,
                    "caption_periodic_seconds": 120,
                    "caption_on_anomaly": False,
                }
            },
        )

        self.assertAlmostEqual(settings["interval_seconds"], 0.2)
        self.assertAlmostEqual(settings["face_interval_seconds"], 1.0)
        self.assertEqual(settings["person_conf"], 0.3)
        self.assertAlmostEqual(settings["anomaly_interval_seconds"], 2.0)
        self.assertAlmostEqual(settings["caption_interval_seconds"], 120.0)
        self.assertFalse(settings["caption_on_anomaly"])

    def test_once_mode_returns_nonzero_when_ffmpeg_fails(self):
        with mock.patch.object(gateway, "run_once", side_effect=RuntimeError("ffmpeg failed")):
            code = gateway.main(["--source-uri", "rtsp://192.0.2.72", "--once"])

        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
