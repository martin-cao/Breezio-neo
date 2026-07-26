import os
import queue
import select
import threading
import time
import unittest

from breezio_gateway.protocol import parse_batch_lines
from breezio_gateway.main import _posix_serial_lines
from breezio_gateway.simulated_serial import (
    SensorSample,
    VirtualSerialDevice,
    build_batch_lines,
    default_samples,
)


class SimulatedSerialTest(unittest.TestCase):
    def test_default_samples_match_demo_sensor_values(self):
        samples = default_samples()

        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0].node_id, "n1")
        self.assertEqual(samples[0].temperature, 28.0)
        self.assertEqual(samples[0].humidity, 12.0)
        self.assertEqual(samples[1].node_id, "n2")
        self.assertEqual(samples[1].light, 734)

    def test_batch_lines_are_gateway_parseable_with_crc(self):
        lines = build_batch_lines(
            "gw-demo",
            7,
            [
                SensorSample(node_id="n1", temperature=28.0, humidity=12.0),
                SensorSample(node_id="n2", light=734),
            ],
        )

        frame = parse_batch_lines(lines, verify_crc=True, topology="direct", source="serial_direct")

        self.assertEqual(frame.gateway_id, "gw-demo")
        self.assertEqual(frame.batch_id, "7")
        self.assertEqual(frame.records[0].temperature, 28.0)
        self.assertEqual(frame.records[0].humidity, 12.0)
        self.assertEqual(frame.records[1].light, 734)

    def test_virtual_serial_device_exposes_readable_slave_path(self):
        with VirtualSerialDevice() as device:
            self.assertTrue(os.path.exists(device.slave_path))

            device.write_lines(["@BATCH,gw-01,1,0", "@END,00"])
            fd = os.open(device.slave_path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
            try:
                readable, _, _ = select.select([fd], [], [], 1.0)
                self.assertTrue(readable)
                deadline = time.monotonic() + 1.0
                data = b""
                while time.monotonic() < deadline and b"@END,00" not in data:
                    try:
                        data += os.read(fd, 4096)
                    except BlockingIOError:
                        time.sleep(0.01)
                self.assertIn(b"@BATCH,gw-01,1,0", data)
                self.assertIn(b"@END,00", data)
            finally:
                os.close(fd)

    def test_gateway_posix_serial_reader_can_read_virtual_serial_device_without_pyserial(self):
        with VirtualSerialDevice() as device:
            reader = _posix_serial_lines(device.slave_path, 38400)
            line_queue = queue.Queue()

            def read_lines():
                for line in reader:
                    line_queue.put(line.strip())
                    if line.strip().startswith("@END,"):
                        break

            thread = threading.Thread(target=read_lines, daemon=True)
            thread.start()
            device.write_lines(build_batch_lines("gw-demo", 1, default_samples()))

            lines = []
            while len(lines) < 4:
                try:
                    lines.append(line_queue.get(timeout=1.0))
                except queue.Empty:
                    self.fail(f"timed out waiting for virtual serial lines, got {lines!r}")

            self.assertEqual(lines[0], "@BATCH,gw-demo,1,2")
            self.assertIn("@NODE,n1,1,280,120,,,0,0", lines)
            self.assertIn("@NODE,n2,1,,,734,,0,0", lines)
            self.assertTrue(lines[-1].startswith("@END,"))


if __name__ == "__main__":
    unittest.main()
