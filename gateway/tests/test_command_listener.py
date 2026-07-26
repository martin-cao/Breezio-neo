import io
import unittest

from breezio_gateway.command_listener import DEFAULT_BAUDRATE, command_payload_to_serial_frame, stdout_writer


class CommandListenerTest(unittest.TestCase):
    def test_default_baudrate_matches_sensor_protocol(self):
        self.assertEqual(DEFAULT_BAUDRATE, 38400)

    def test_command_payload_to_serial_frame(self):
        frame = command_payload_to_serial_frame(
            '{"command_id":"cmd-1","target_node_id":"n1","target_device":"lamp","action":"on"}'
        )

        self.assertEqual(frame, "@CMD,cmd-1,n1,LAMP,ON\n")

    def test_command_payload_rejects_invalid_cc2530_command_id(self):
        invalid_payloads = (
            '{"command_id":"' + ("x" * 37) + '","target_node_id":"n1","target_device":"lamp","action":"on"}',
            '{"command_id":"bad,id","target_node_id":"n1","target_device":"lamp","action":"on"}',
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    command_payload_to_serial_frame(payload)

    def test_stdout_writer_writes_frame(self):
        stream = io.StringIO()

        stdout_writer(stream)("@CMD,cmd-1,n1,FAN,ON\n")

        self.assertEqual(stream.getvalue(), "@CMD,cmd-1,n1,FAN,ON\n")


if __name__ == "__main__":
    unittest.main()
