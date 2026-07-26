import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

import run_smartwin_gateway as bridge
from smartwin_gateway.protocol import Telemetry


def make_telemetry(seq: int = 1) -> Telemetry:
    return Telemetry(
        seq=seq,
        temperature_c=20.0,
        humidity_pct=50.0,
        brightness_pct=5,
        air_quality_mv=100,
        window_open=False,
        alarm=False,
    )


class TelemetryPayloadTest(unittest.TestCase):
    def test_uses_learned_node_id_over_fallback(self):
        args = bridge.parser().parse_args(["--serial-port", "/dev/fake", "--node-id", "fallback-01"])
        payload = bridge.telemetry_payload(args, "win-hello-01", make_telemetry(7))

        record = payload["records"][0]
        self.assertEqual(record["node_id"], "win-hello-01")
        self.assertEqual(record["seq"], 7)
        self.assertEqual(record["temperature"], 20.0)
        self.assertEqual(payload["batch_id"], "smartwin-7")

    def test_falls_back_to_cli_node_id_before_any_hello(self):
        args = bridge.parser().parse_args(["--serial-port", "/dev/fake", "--node-id", "fallback-01"])
        payload = bridge.telemetry_payload(args, None, make_telemetry())

        self.assertEqual(payload["records"][0]["node_id"], "fallback-01")


class OnceModeTest(unittest.TestCase):
    def test_posts_first_telemetry_frame_and_exits_zero(self):
        parsed = [(make_telemetry(3), "win-01")]
        with mock.patch.object(bridge, "serial_lines", return_value=iter(["unused"])), \
             mock.patch.object(bridge, "parse_stream", return_value=iter(parsed)), \
             mock.patch.object(bridge, "post_json", return_value={"data": {"ok": True}}) as post:
            code = bridge.main(["--serial-port", "/dev/fake", "--once"])

        self.assertEqual(code, 0)
        post.assert_called_once()
        endpoint, payload = post.call_args[0]
        self.assertTrue(endpoint.endswith("/api/readings"))
        self.assertEqual(payload["records"][0]["node_id"], "win-01")

    def test_non_telemetry_frames_are_skipped_before_the_first_telemetry(self):
        from smartwin_gateway.protocol import Hello

        parsed = [(Hello(fw_version="1.0.0", node_id="win-01"), "win-01"), (make_telemetry(1), "win-01")]
        with mock.patch.object(bridge, "serial_lines", return_value=iter(["unused"])), \
             mock.patch.object(bridge, "parse_stream", return_value=iter(parsed)), \
             mock.patch.object(bridge, "post_json", return_value={"data": {}}) as post:
            code = bridge.main(["--serial-port", "/dev/fake", "--once"])

        self.assertEqual(code, 0)
        post.assert_called_once()

    def test_backend_post_failure_exits_nonzero_without_retrying(self):
        parsed = [(make_telemetry(1), "win-01")]
        with mock.patch.object(bridge, "serial_lines", return_value=iter(["unused"])), \
             mock.patch.object(bridge, "parse_stream", return_value=iter(parsed)), \
             mock.patch.object(bridge, "post_json", side_effect=RuntimeError("backend unavailable: refused")) as post, \
             mock.patch.object(bridge.time, "sleep") as sleep:
            code = bridge.main(["--serial-port", "/dev/fake", "--once"])

        self.assertEqual(code, 1)
        post.assert_called_once()
        sleep.assert_not_called()

    def test_serial_open_failure_exits_nonzero_immediately(self):
        with mock.patch.object(bridge, "serial_lines", side_effect=OSError("could not open port")), \
             mock.patch.object(bridge.time, "sleep") as sleep:
            code = bridge.main(["--serial-port", "/dev/nonexistent", "--once"])

        self.assertEqual(code, 1)
        sleep.assert_not_called()


class LongRunningRetryTest(unittest.TestCase):
    def test_serial_failure_retries_after_a_backoff_instead_of_dying(self):
        """Long-running mode must not silently exit on a transient serial error;
        it should sleep(2) and retry. `except Exception` in main() would also
        swallow an exception raised from inside its own handler, so we can't
        break the `while True` loop with another regular exception from
        serial_lines — instead we raise the stop signal from the mocked
        time.sleep() call itself, which sits outside that except block's own
        try, and assert it was reached with the right backoff."""

        class StopTest(BaseException):
            pass

        with mock.patch.object(bridge, "serial_lines", side_effect=OSError("transient serial error")), \
             mock.patch.object(bridge.time, "sleep", side_effect=StopTest) as sleep:
            with self.assertRaises(StopTest):
                bridge.main(["--serial-port", "/dev/fake"])

        sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
