import unittest

from smartwin_gateway.protocol import (
    Ack,
    Hello,
    Telemetry,
    checksum,
    encode_command,
    frame,
    parse_line,
)


class ChecksumTests(unittest.TestCase):
    def test_xor_checksum_known_vector(self):
        # XOR of the ASCII bytes of the body, NMEA-style.
        self.assertEqual(checksum("BRZ2,CMD,7,WINDOW,OPEN"), 0x1D)

    def test_frame_wraps_with_checksum(self):
        line = frame("BRZ2,HELLO,1.0.0,win-01")
        self.assertTrue(line.startswith("$BRZ2,HELLO,1.0.0,win-01*"))
        self.assertTrue(line.endswith("\r\n"))


class ParseTelemetryTests(unittest.TestCase):
    def test_round_trip_valid_telemetry(self):
        line = frame("BRZ2,T,41,24.5,48.0,73,612,CLOSED,0")
        msg = parse_line(line)
        self.assertEqual(
            msg,
            Telemetry(
                seq=41,
                temperature_c=24.5,
                humidity_pct=48.0,
                brightness_pct=73,
                air_quality_mv=612,
                window_open=False,
                alarm=False,
            ),
        )
        self.assertEqual(msg.air_quality_band, "GOOD")

    def test_na_sensor_fields_become_none(self):
        msg = parse_line(frame("BRZ2,T,5,NA,NA,10,2100,OPEN,1"))
        assert isinstance(msg, Telemetry)
        self.assertIsNone(msg.temperature_c)
        self.assertIsNone(msg.humidity_pct)
        self.assertTrue(msg.window_open)
        self.assertTrue(msg.alarm)
        self.assertEqual(msg.air_quality_band, "POOR")

    def test_leading_garbage_is_tolerated(self):
        noisy = "\x00\xff junk $BRZ2,T,1,20.0,50.0,5,100,CLOSED,0"
        # rebuild with a correct checksum appended
        body = "BRZ2,T,1,20.0,50.0,5,100,CLOSED,0"
        line = "garbage-prefix " + frame(body)
        msg = parse_line(line)
        self.assertIsInstance(msg, Telemetry)

    def test_bad_checksum_is_rejected(self):
        good = frame("BRZ2,T,1,20.0,50.0,5,100,CLOSED,0").strip()
        corrupted = good[:-2] + "00"  # clobber the checksum digits
        self.assertIsNone(parse_line(corrupted))

    def test_wrong_field_count_is_rejected(self):
        self.assertIsNone(parse_line(frame("BRZ2,T,1,20.0")))

    def test_non_frame_returns_none(self):
        self.assertIsNone(parse_line("hello world"))
        self.assertIsNone(parse_line(""))


class ParseHelloAckTests(unittest.TestCase):
    def test_parse_hello(self):
        self.assertEqual(
            parse_line(frame("BRZ2,HELLO,1.0.0,win-01")),
            Hello(fw_version="1.0.0", node_id="win-01"),
        )

    def test_parse_ack(self):
        self.assertEqual(
            parse_line(frame("BRZ2,ACK,7,WINDOW,OK,OPEN")),
            Ack(id=7, device="WINDOW", result="OK", state="OPEN"),
        )


class EncodeCommandTests(unittest.TestCase):
    def test_encode_round_trips_through_parser_shape(self):
        line = encode_command(7, "WINDOW", "OPEN")
        self.assertEqual(line, "$BRZ2,CMD,7,WINDOW,OPEN*1D\r\n")

    def test_encode_lowercase_is_normalized(self):
        self.assertEqual(
            encode_command(3, "buzzer", "mute"),
            frame("BRZ2,CMD,3,BUZZER,MUTE"),
        )

    def test_invalid_action_rejected(self):
        with self.assertRaises(ValueError):
            encode_command(1, "WINDOW", "MUTE")

    def test_invalid_device_rejected(self):
        with self.assertRaises(ValueError):
            encode_command(1, "DOOR", "OPEN")


if __name__ == "__main__":
    unittest.main()
