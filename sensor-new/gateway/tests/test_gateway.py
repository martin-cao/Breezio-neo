import json
import unittest

from smartwin_gateway.gateway import message_to_dict, parse_stream, run_monitor
from smartwin_gateway.protocol import Telemetry, frame


class ParseStreamTests(unittest.TestCase):
    def test_node_id_is_learned_from_hello(self):
        lines = [
            frame("BRZ2,HELLO,1.0.0,win-01"),
            frame("BRZ2,T,1,20.0,50.0,5,100,CLOSED,0"),
        ]
        results = list(parse_stream(lines))
        self.assertEqual(results[0][1], "win-01")  # hello sets node_id
        self.assertEqual(results[1][1], "win-01")  # telemetry inherits it

    def test_garbage_lines_are_skipped(self):
        lines = ["noise", "", frame("BRZ2,T,1,20.0,50.0,5,100,CLOSED,0")]
        results = list(parse_stream(lines))
        self.assertEqual(len(results), 1)


class MessageToDictTests(unittest.TestCase):
    def test_telemetry_record_shape(self):
        msg = Telemetry(
            seq=41,
            temperature_c=24.5,
            humidity_pct=48.0,
            brightness_pct=73,
            air_quality_mv=2100,
            window_open=True,
            alarm=True,
        )
        record = message_to_dict(msg, "win-01")
        self.assertEqual(record["type"], "telemetry")
        self.assertEqual(record["node_id"], "win-01")
        self.assertEqual(record["air_quality_band"], "POOR")
        self.assertTrue(record["window_open"])
        self.assertIn("captured_at", record)


class RunMonitorTests(unittest.TestCase):
    def test_emits_one_json_object_per_message(self):
        lines = [
            frame("BRZ2,HELLO,1.0.0,win-01"),
            frame("BRZ2,T,1,20.0,50.0,5,100,CLOSED,0"),
            "garbage",
            frame("BRZ2,ACK,7,WINDOW,OK,OPEN"),
        ]
        out: list[str] = []
        run_monitor(lines, emit=out.append)
        self.assertEqual(len(out), 3)
        kinds = [json.loads(line)["type"] for line in out]
        self.assertEqual(kinds, ["hello", "telemetry", "ack"])


if __name__ == "__main__":
    unittest.main()
