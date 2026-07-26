import contextlib
import io
import json
import unittest

from breezio_gateway.main import parse_args
from breezio_gateway.protocol import (
    HEX_MSG_COMMAND_ACK,
    HEX_MSG_TELEMETRY,
    SerialProtocolParser,
    command_to_brz2_frame,
    command_to_serial_frame,
    crc16_ccitt_false,
    encode_hex_frame,
    frames_from_stream,
    mqtt_messages,
    parse_batch_lines,
    parse_hex_frame,
    parse_legacy_frame,
)


def _telemetry_payload(node_id: str, seq: int, *, temperature_x10=0x7FFF, humidity_x10=0x7FFF, light=0xFFFF, battery_mv=0xFFFF, rssi=-128, flags=0, batch_id=1) -> bytes:
    body = bytearray()
    body += len(node_id).to_bytes(1, "little")
    body += node_id.encode("ascii")
    body += seq.to_bytes(2, "little")
    body += (temperature_x10 & 0xFFFF).to_bytes(2, "little")
    body += (humidity_x10 & 0xFFFF).to_bytes(2, "little")
    body += light.to_bytes(2, "little")
    body += battery_mv.to_bytes(2, "little")
    body += (rssi & 0xFF).to_bytes(1, "little")
    body += flags.to_bytes(1, "little")
    header = bytes([1, HEX_MSG_TELEMETRY]) + batch_id.to_bytes(2, "little") + bytes([1])
    return bytes(header + body)


def _command_ack_payload(command_short_id: int, node_id: str, *, result_code: int = 0, state: str = "ON") -> bytes:
    body = bytearray()
    body += len(node_id).to_bytes(1, "little")
    body += node_id.encode("ascii")
    body += bytes([result_code])
    body += len(state).to_bytes(1, "little")
    body += state.encode("ascii")
    header = bytes([1, HEX_MSG_COMMAND_ACK]) + command_short_id.to_bytes(2, "little")
    return bytes(header + body)


class GatewayProtocolTest(unittest.TestCase):
    def test_parse_legacy_frame_maps_a0_humidity_a1_temperature(self):
        reading = parse_legacy_frame("{A0=58,A1=27}", gateway_id="gw-x", node_id="n9", seq=7)
        self.assertEqual(reading.gateway_id, "gw-x")
        self.assertEqual(reading.node_id, "n9")
        self.assertEqual(reading.seq, 7)
        self.assertEqual(reading.humidity, 58.0)
        self.assertEqual(reading.temperature, 27.0)
        self.assertEqual(reading.source, "serial_legacy")

    def test_parse_batch_frame_with_missing_values(self):
        frame = parse_batch_lines(
            [
                "@BATCH,gw-01,1042,2",
                "@NODE,n1,201,264,582,734,3120,-67,0",
                "@NODE,n2,88,,,821,2990,-61,1",
                "@END,00",
            ]
        )
        self.assertEqual(frame.batch_id, "1042")
        self.assertEqual(len(frame.records), 2)
        self.assertEqual(frame.records[0].temperature, 26.4)
        self.assertEqual(frame.records[0].humidity, 58.2)
        self.assertIsNone(frame.records[1].temperature)
        self.assertIsNone(frame.records[1].humidity)
        self.assertEqual(frame.records[1].light, 821)

    def test_stream_and_mqtt_messages(self):
        frames = list(
            frames_from_stream(
                [
                    "{A0=60,A1=26}",
                    "@ACK,cmd-1,n1,OK,ON",
                ],
                gateway_id="gw-01",
                node_id="n1",
            )
        )
        self.assertEqual(len(frames), 2)
        messages = mqtt_messages(frames[0])
        self.assertEqual(messages[0][0], "breezio/gateways/gw-01/batch")
        payload = json.loads(messages[0][1])
        self.assertEqual(payload["records"][0]["temperature"], 26.0)
        ack_messages = mqtt_messages(frames[1])
        self.assertEqual(ack_messages[0][0], "breezio/nodes/n1/command_ack")

    def test_direct_serial_single_node_line_without_router_batch(self):
        parser = SerialProtocolParser(gateway_id="gw-direct", topology="direct")

        frame = parser.feed_line("@NODE,n7,42,265,590,734,3120,-60,0")

        self.assertEqual(frame.gateway_id, "gw-direct")
        self.assertEqual(frame.node_id, "n7")
        self.assertEqual(frame.temperature, 26.5)
        self.assertEqual(frame.humidity, 59.0)
        self.assertEqual(frame.source, "serial_direct")
        self.assertEqual(frame.role, "sensor")
        self.assertIsNone(frame.parent_node_id)

    def test_parser_can_override_reading_source_for_simulated_serial(self):
        parser = SerialProtocolParser(gateway_id="gw-demo", topology="direct", reading_source="simulated")

        frame = parser.feed_line("@NODE,n7,42,265,590,734,3120,-60,0")

        self.assertEqual(frame.source, "simulated")

    def test_router_topology_marks_remote_nodes_under_router_parent(self):
        parser = SerialProtocolParser(gateway_id="gw-cli", topology="router", router_node_id="n1")
        frames = list(
            parser.parse_stream(
                [
                    "@BATCH,gw-01,1042,2",
                    "@NODE,n1,201,264,582,734,3120,-67,0",
                    "@NODE,n2,88,,,821,2990,-61,1",
                    "@END,00",
                ]
            )
        )

        self.assertEqual(len(frames), 1)
        local, remote = frames[0].records
        self.assertEqual(local.source, "serial_router")
        self.assertEqual(local.role, "router")
        self.assertIsNone(local.parent_node_id)
        self.assertEqual(remote.source, "serial_router")
        self.assertEqual(remote.role, "sensor")
        self.assertEqual(remote.parent_node_id, "n1")

    def test_cli_accepts_multiple_serial_ports_for_direct_serial_gateway(self):
        args = parse_args(
            [
                "--serial-port",
                "/dev/cu.node1",
                "--serial-port",
                "/dev/cu.node2",
                "--topology",
                "direct",
                "--reading-source",
                "simulated",
            ]
        )

        self.assertEqual(args.serial_port, ["/dev/cu.node1", "/dev/cu.node2"])
        self.assertEqual(args.topology, "direct")
        self.assertEqual(args.reading_source, "simulated")

    def test_cli_rejects_multiple_serial_ports_for_router_gateway(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_args(["--serial-port", "/dev/cu.router", "--serial-port", "/dev/cu.extra", "--topology", "router"])


class CommandEncodingTest(unittest.TestCase):
    def command(self, **overrides):
        command = {
            "command_id": "123e4567-e89b-12d3-a456-426614174000",
            "target_node_id": "n1",
            "target_device": "lamp",
            "action": "toggle",
        }
        command.update(overrides)
        return command

    def test_cc2530_command_accepts_complete_36_character_uuid(self):
        frame = command_to_serial_frame(self.command())

        self.assertEqual(
            frame,
            "@CMD,123e4567-e89b-12d3-a456-426614174000,n1,LAMP,TOGGLE\n",
        )

    def test_cc2530_command_rejects_id_longer_than_36_characters(self):
        with self.assertRaisesRegex(ValueError, "command_id must contain 1-36"):
            command_to_serial_frame(self.command(command_id="x" * 37))

    def test_cc2530_command_rejects_csv_delimiters_and_line_breaks(self):
        for command_id in ("bad,id", "bad\rid", "bad\nid"):
            with self.subTest(command_id=command_id):
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    command_to_serial_frame(self.command(command_id=command_id))

    def test_cc2530_command_validates_other_bounded_csv_fields(self):
        invalid_fields = (
            ("target_node_id", "n" * 15),
            ("target_node_id", "n,1"),
            ("target_node_id", "n*"),
            ("target_device", "fan"),
            ("target_device", "lamp\n"),
            ("action", "raise"),
            ("action", "on\r"),
        )
        for key, value in invalid_fields:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    command_to_serial_frame(self.command(**{key: value}))

    def test_cc2530_longest_valid_command_fills_70_character_uart_body(self):
        frame = command_to_serial_frame(
            self.command(
                target_node_id="node1234567890",
                target_device="window",
                action="toggle",
            )
        )

        self.assertEqual(len(frame.removesuffix("\n")), 70)

    def test_cc2530_broadcast_target_is_accepted(self):
        frame = command_to_serial_frame(self.command(target_node_id="*"))

        self.assertIn(",*,LAMP,TOGGLE\n", frame)

    def test_brz2_command_id_is_not_limited_by_cc2530_csv_rules(self):
        frame = command_to_brz2_frame(
            self.command(
                command_id="x" * 64,
                target_node_id="win-01",
                target_device="window",
                action="open",
            )
        )

        self.assertIn("$BRZ2,CMD,", frame)


class HexProtocolTest(unittest.TestCase):
    def test_crc16_ccitt_false_matches_known_check_value(self):
        # Standard CRC-16/CCITT-FALSE check value for ASCII "123456789".
        self.assertEqual(crc16_ccitt_false(b"123456789"), 0x29B1)

    def test_valid_hex_telemetry_frame_decodes_like_text_batch(self):
        payload = _telemetry_payload("n1", 53, temperature_x10=280, humidity_x10=120, rssi=-67, flags=0)
        line = encode_hex_frame(payload)

        parser = SerialProtocolParser(gateway_id="gw-01", topology="router", router_node_id="n1")
        frame = parser.feed_line(line)

        self.assertEqual(len(frame.records), 1)
        record = frame.records[0]
        self.assertEqual(record.node_id, "n1")
        self.assertEqual(record.seq, 53)
        self.assertEqual(record.temperature, 28.0)
        self.assertEqual(record.humidity, 12.0)
        self.assertIsNone(record.light)
        self.assertIsNone(record.battery_mv)
        self.assertEqual(record.rssi, -67)
        self.assertEqual(record.role, "router")
        self.assertIsNone(record.parent_node_id)
        self.assertEqual(parser.hex_stats.as_dict(), {"accepted": 1, "dropped_malformed": 0, "dropped_crc": 0})

    def test_hex_telemetry_marks_remote_node_under_router_parent(self):
        payload = _telemetry_payload("n2", 357, light=549, rssi=0, flags=0)
        line = encode_hex_frame(payload)

        parser = SerialProtocolParser(gateway_id="gw-01", topology="router", router_node_id="n1")
        frame = parser.feed_line(line)

        record = frame.records[0]
        self.assertEqual(record.node_id, "n2")
        self.assertEqual(record.light, 549)
        self.assertEqual(record.role, "sensor")
        self.assertEqual(record.parent_node_id, "n1")

    def test_hex_command_ack_decodes_to_command_ack(self):
        payload = _command_ack_payload(301, "n2", result_code=0, state="ON")
        line = encode_hex_frame(payload)

        parser = SerialProtocolParser(gateway_id="gw-01", topology="router", router_node_id="n1")
        ack = parser.feed_line(line)

        self.assertEqual(ack.command_id, "301")
        self.assertEqual(ack.node_id, "n2")
        self.assertEqual(ack.result, "OK")
        self.assertEqual(ack.state, "ON")

    def test_crc_mismatch_is_rejected_and_counted(self):
        payload = _telemetry_payload("n1", 1, temperature_x10=280, humidity_x10=120)
        line = encode_hex_frame(payload)
        corrupted = line[:-1] + ("0" if line[-1] != "0" else "1")  # flip the last CRC hex digit

        parser = SerialProtocolParser(gateway_id="gw-01", topology="router", router_node_id="n1")
        frame = parser.feed_line(corrupted)

        self.assertIsNone(frame)
        self.assertEqual(parser.hex_stats.dropped_crc, 1)
        self.assertEqual(parser.hex_stats.accepted, 0)

    def test_garbage_before_inside_and_after_frame_does_not_block_later_frames(self):
        payload = _telemetry_payload("n1", 42, temperature_x10=280, humidity_x10=120)
        good_line = encode_hex_frame(payload)

        parser = SerialProtocolParser(gateway_id="gw-01", topology="router", router_node_id="n1")
        lines = [
            "@NDCFn,128012,,0,P=0002}{YE=01002}garbage-before" + good_line,  # garbage prepended to a valid frame
            "~BZ1,ZZZZ,DEADBEEF,0000",  # invalid length hex
            "~BZ1,0004,DEAD,0000",  # payload shorter than declared length
            good_line + "trailing-garbage-from-other-transmitter",  # garbage appended
        ]

        frames = [parser.feed_line(line) for line in lines]

        self.assertIsNotNone(frames[0])
        self.assertEqual(frames[0].records[0].seq, 42)
        self.assertIsNone(frames[1])
        self.assertIsNone(frames[2])
        self.assertIsNotNone(frames[3])
        self.assertEqual(frames[3].records[0].seq, 42)
        self.assertEqual(parser.hex_stats.accepted, 2)
        self.assertEqual(parser.hex_stats.dropped_malformed, 2)

    def test_malformed_hex_frames_never_raise(self):
        parser = SerialProtocolParser(gateway_id="gw-01", topology="router", router_node_id="n1")
        malformed_lines = [
            "~BZ1,",
            "~BZ1,0001,",
            "~BZ1,0001,ZZ,0000",
            "~BZ1,FFFF,00,0000",  # oversized length
            "~BZ1,0002,0102,0000",  # wrong CRC, and payload too short to be a valid telemetry/ack header anyway
            "not a frame at all",
        ]
        for line in malformed_lines:
            with self.subTest(line=line):
                self.assertIsNone(parser.feed_line(line))
        self.assertEqual(parser.hex_stats.accepted, 0)

    def test_malformed_legacy_batch_does_not_crash_and_later_frames_still_parse(self):
        parser = SerialProtocolParser(gateway_id="gw-01", topology="router", router_node_id="n1")

        self.assertIsNone(parser.feed_line("@BATCH,gw-01,1,1"))
        # Corrupted @NODE line: wrong field count, would previously raise ValueError
        # and crash the whole gateway process instead of just dropping this frame.
        self.assertIsNone(parser.feed_line("@NOE,n1,378,,,577,00"))
        self.assertIsNone(parser.feed_line("@END,00"))

        frame = parser.feed_line("@BATCH,gw-01,2,1")
        self.assertIsNone(frame)
        frame = parser.feed_line("@NODE,n1,53,280,120,,,0,0")
        self.assertIsNone(frame)
        frame = parser.feed_line("@END,00")
        self.assertIsNotNone(frame)
        self.assertEqual(frame.records[0].node_id, "n1")

    def test_malformed_direct_node_line_does_not_crash_and_later_lines_still_parse(self):
        parser = SerialProtocolParser(gateway_id="gw-direct", topology="direct")

        self.assertIsNone(parser.feed_line("@NODE,n,378,,,577,00"))
        frame = parser.feed_line("@NODE,n7,42,265,590,734,3120,-60,0")

        self.assertIsNotNone(frame)
        self.assertEqual(frame.node_id, "n7")

    def test_old_batch_and_legacy_frames_still_parse_unchanged(self):
        parser = SerialProtocolParser(gateway_id="gw-01", topology="router", router_node_id="n1")

        self.assertIsNone(parser.feed_line("@BATCH,gw-01,1042,2"))
        self.assertIsNone(parser.feed_line("@NODE,n1,201,264,582,734,3120,-67,0"))
        self.assertIsNone(parser.feed_line("@NODE,n2,88,,,821,2990,-61,1"))
        frame = parser.feed_line("@END,00")

        self.assertEqual(len(frame.records), 2)
        self.assertEqual(frame.records[0].temperature, 26.4)
        self.assertEqual(frame.records[1].light, 821)

        direct_parser = SerialProtocolParser(gateway_id="gw-01", topology="direct")
        legacy = direct_parser.feed_line("{A0=58,A1=27}")
        self.assertEqual(legacy.humidity, 58.0)
        self.assertEqual(legacy.temperature, 27.0)

    def test_hex_and_legacy_frames_interleave_on_the_same_stream(self):
        parser = SerialProtocolParser(gateway_id="gw-01", topology="router", router_node_id="n1")
        hex_line = encode_hex_frame(_telemetry_payload("n2", 357, light=549))

        frames = [
            parser.feed_line("@BATCH,gw-01,1,1"),
            parser.feed_line("@NODE,n1,53,280,120,,,0,0"),
            parser.feed_line("@END,00"),
            parser.feed_line(hex_line),
        ]

        self.assertIsNone(frames[0])
        self.assertIsNone(frames[1])
        self.assertEqual(frames[2].records[0].node_id, "n1")
        self.assertEqual(frames[3].records[0].node_id, "n2")


if __name__ == "__main__":
    unittest.main()
