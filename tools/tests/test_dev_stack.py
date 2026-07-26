import unittest
from unittest import mock

import dev_stack
from dev_stack import ProcessSpec, build_preflight_specs, build_process_specs, parser, run_processes


class DevStackTest(unittest.TestCase):
    def test_default_specs_include_core_processes(self):
        args = parser().parse_args([])

        specs = build_process_specs(args)

        self.assertEqual([spec.name for spec in specs], ["inference", "backend", "frontend"])
        backend = next(spec for spec in specs if spec.name == "backend")
        self.assertIn("DATABASE_URL", backend.env)
        self.assertEqual(backend.env["API_TOKEN"], "breezio-dev-token")
        self.assertEqual(backend.env["BREEZIO_INFERENCE_URL"], "http://127.0.0.1:8100")
        self.assertIn("http://127.0.0.1:5173", backend.env["BREEZIO_CORS_ORIGINS"])
        frontend = next(spec for spec in specs if spec.name == "frontend")
        self.assertIn("tools/frontend_static_server.py", frontend.command)
        self.assertIn("http://127.0.0.1:8000", frontend.command)

    def test_backend_can_target_remote_inference_url(self):
        args = parser().parse_args(["--no-inference", "--inference-url", "http://192.0.2.40:8100"])

        specs = build_process_specs(args)

        self.assertEqual([spec.name for spec in specs], ["backend", "frontend"])
        backend = next(spec for spec in specs if spec.name == "backend")
        self.assertEqual(backend.env["BREEZIO_INFERENCE_URL"], "http://192.0.2.40:8100")

    def test_worker_specs_are_opt_in(self):
        args = parser().parse_args(["--with-workers", "--no-frontend"])

        specs = build_process_specs(args)

        self.assertIn("mqtt-ingestor", [spec.name for spec in specs])
        self.assertIn("control-dispatcher", [spec.name for spec in specs])

    def test_vision_runtime_specs_are_opt_in_for_inference_and_seed(self):
        args = parser().parse_args(["--with-vision-runtimes", "--seed-demo-runtime", "--no-frontend"])

        specs = build_process_specs(args)

        inference = next(spec for spec in specs if spec.name == "inference")
        self.assertIn("--with", inference.command)
        self.assertIn("ultralytics", inference.command)
        self.assertIn("insightface", inference.command)
        self.assertIn("torch", inference.command)
        self.assertLess(inference.command.index("torch"), inference.command.index("python"))

        seeder = next(spec for spec in specs if spec.name == "demo-runtime-seed")
        self.assertIn("--vision-runtime-mode", seeder.command)
        self.assertIn("pretrained", seeder.command)

    def test_simulated_gateway_is_opt_in_and_publishes_to_mqtt(self):
        args = parser().parse_args(["--with-simulated-gateway", "--no-frontend"])

        specs = build_process_specs(args)

        simulator = next(spec for spec in specs if spec.name == "simulated-gateway")
        self.assertIn("tools/run_simulated_gateway.py", simulator.command)
        self.assertEqual(simulator.env["MQTT_HOST"], "127.0.0.1")
        self.assertEqual(simulator.env["MQTT_PORT"], "1883")
        self.assertEqual(simulator.env["GATEWAY_READING_SOURCE"], "serial_simulated")

    def test_wifi_gateway_is_opt_in_and_listens_for_sensor_connections(self):
        args = parser().parse_args(["--with-wifi-gateway", "--no-frontend"])

        specs = build_process_specs(args)

        gateway = next(spec for spec in specs if spec.name == "wifi-gateway")
        self.assertIn("breezio_gateway.main", gateway.command)
        self.assertIn("--wifi-listen", gateway.command)
        self.assertIn("0.0.0.0:5568", gateway.command)
        self.assertEqual(gateway.env["PYTHONPATH"], "gateway")
        self.assertEqual(gateway.env["MQTT_HOST"], "127.0.0.1")
        self.assertEqual(gateway.env["MQTT_PORT"], "1883")
        self.assertEqual(gateway.env["GATEWAY_READING_SOURCE"], "wifi_tcp")

    def test_camera_gateway_is_opt_in_and_targets_backend_camera_source(self):
        args = parser().parse_args(
            [
                "--with-camera-gateway",
                "--camera-source-uri",
                "rtsp://192.0.2.72",
                "--camera-id",
                "cam-rtsp-01",
                "--no-frontend",
            ]
        )

        specs = build_process_specs(args)

        camera = next(spec for spec in specs if spec.name == "camera-gateway")
        self.assertIn("tools/run_camera_gateway.py", camera.command)
        self.assertIn("--source-uri", camera.command)
        self.assertIn("rtsp://192.0.2.72", camera.command)
        self.assertIn("--camera-id", camera.command)
        self.assertIn("cam-rtsp-01", camera.command)
        self.assertIn("--face-interval-seconds", camera.command)
        self.assertIn("1.0", camera.command)
        self.assertEqual(camera.env["PYTHONPATH"], "backend")

    def test_demo_runtime_seed_is_opt_in_and_targets_backend_api(self):
        args = parser().parse_args(["--seed-demo-runtime", "--no-frontend"])

        specs = build_process_specs(args)

        seeder = next(spec for spec in specs if spec.name == "demo-runtime-seed")
        self.assertIn("tools/seed_demo_runtime.py", seeder.command)
        self.assertIn("http://127.0.0.1:8000", seeder.command)
        self.assertIn("--api-token", seeder.command)
        self.assertIn("breezio-dev-token", seeder.command)
        self.assertEqual(seeder.env["UV_CACHE_DIR"], "/tmp/breezio-uv-cache")
        # The seed is a best-effort one-shot task; its failure must not stop the stack.
        self.assertTrue(seeder.oneshot)
        long_running = [spec for spec in specs if spec.name != "demo-runtime-seed"]
        self.assertTrue(all(not spec.oneshot for spec in long_running))

    def test_postgres_migration_preflight_runs_for_backend(self):
        args = parser().parse_args([])

        specs = build_preflight_specs(args)

        self.assertEqual(
            [spec.name for spec in specs],
            ["postgres-migrations", "frontend-dependencies", "frontend-build"],
        )
        self.assertIn("breezio_backend.migrate", specs[0].command)
        self.assertEqual(specs[0].env["PYTHONPATH"], "backend")
        self.assertEqual(specs[1].command, ["bun", "install", "--frozen-lockfile"])
        self.assertEqual(specs[2].command, ["bun", "run", "build"])

    def test_postgres_migration_preflight_can_be_skipped_without_skipping_frontend_build(self):
        args = parser().parse_args(["--skip-migrations"])

        specs = build_preflight_specs(args)

        self.assertEqual([spec.name for spec in specs], ["frontend-dependencies", "frontend-build"])


class FakeProcess:
    def __init__(self, poll_returns):
        self._returns = list(poll_returns)
        self._final = self._returns[-1] if self._returns else 0
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._returns.pop(0) if self._returns else self._final

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self._final


class RunProcessesOneshotTest(unittest.TestCase):
    def test_failing_oneshot_does_not_stop_long_running_stack(self):
        # backend stays alive for three polls then exits cleanly on its own.
        backend = FakeProcess([None, None, None, 0])
        # the one-shot seed exits non-zero (e.g. inference not ready) after one poll.
        seed = FakeProcess([None, 5])
        fakes = iter([backend, seed])

        specs = [
            ProcessSpec(name="backend", command=["backend"]),
            ProcessSpec(name="demo-runtime-seed", command=["seed"], oneshot=True),
        ]

        with mock.patch.object(dev_stack.subprocess, "Popen", side_effect=lambda *a, **k: next(fakes)), \
             mock.patch.object(dev_stack.time, "sleep", return_value=None):
            code = run_processes(specs)

        # The stack exits on the backend's own clean exit, not the seed's failure.
        self.assertEqual(code, 0)
        # The backend was never force-terminated because of the seed failure.
        self.assertFalse(backend.terminated)


if __name__ == "__main__":
    unittest.main()
