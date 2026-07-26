from __future__ import annotations

import json
import queue
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from breezio_gateway.protocol import (
    BRZ2_FLAG_ALARM,
    BRZ2_FLAG_WINDOW_OPEN,
    BatchFrame,
    CommandAck,
    NodeReading,
    brz2_command_short_id,
    brz2_frame,
    command_to_brz2_frame,
    parse_brz2_line,
)
from breezio_gateway.wifi import (
    SensorEndpoint,
    WifiListenerTransport,
    WifiTransport,
    fetch_node_endpoints,
    parse_endpoint_spec,
)


class FakeWifiSensor:
    """Minimal TCP sensor: serves canned telemetry lines and ACKs commands."""

    def __init__(self, lines: list[str]):
        self.lines = lines
        self.received: list[str] = []
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        conn, _ = self.server.accept()
        with conn:
            for line in self.lines:
                conn.sendall((line + "\r\n").encode())
            conn.settimeout(5.0)
            buffer = b""
            try:
                while b"\n" not in buffer:
                    chunk = conn.recv(1024)
                    if not chunk:
                        return
                    buffer += chunk
            except socket.timeout:
                return
            command_line = buffer.split(b"\n", 1)[0].decode().strip()
            self.received.append(command_line)
            # $BRZ2,CMD,<id>,WINDOW,OPEN -> matching ACK
            body = command_line.strip("$").split("*")[0]
            fields = body.split(",")
            conn.sendall(brz2_frame(f"BRZ2,ACK,{fields[2]},{fields[3]},OK,OPEN").encode())
            time.sleep(0.2)

    def close(self) -> None:
        self.server.close()


class FakePushWifiSensor:
    """TCP sensor in the desired topology: connects to the gateway listener."""

    def __init__(self, host: str, port: int, lines: list[str]):
        self.lines = lines
        self.received: list[str] = []
        self.sock = socket.create_connection((host, port), timeout=5)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        with self.sock:
            for line in self.lines:
                self.sock.sendall((line + "\r\n").encode())
            self.sock.settimeout(5.0)
            buffer = b""
            try:
                while b"\n" not in buffer:
                    chunk = self.sock.recv(1024)
                    if not chunk:
                        return
                    buffer += chunk
            except socket.timeout:
                return
            command_line = buffer.split(b"\n", 1)[0].decode().strip()
            self.received.append(command_line)
            body = command_line.strip("$").split("*")[0]
            fields = body.split(",")
            self.sock.sendall(brz2_frame(f"BRZ2,ACK,{fields[2]},{fields[3]},OK,OPEN").encode())
            time.sleep(0.2)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Brz2ProtocolTest(unittest.TestCase):
    def test_parse_telemetry(self):
        line = brz2_frame("BRZ2,T,7,24.5,48.0,73,612,OPEN,1").strip()
        frame = parse_brz2_line(line, gateway_id="gw-01", node_id="win-01")
        self.assertIsInstance(frame, NodeReading)
        self.assertEqual(frame.node_id, "win-01")
        self.assertEqual(frame.seq, 7)
        self.assertEqual(frame.temperature, 24.5)
        self.assertEqual(frame.humidity, 48.0)
        self.assertEqual(frame.light, 73)
        self.assertEqual(frame.flags, BRZ2_FLAG_WINDOW_OPEN | BRZ2_FLAG_ALARM)
        self.assertEqual(frame.source, "wifi_tcp")

    def test_parse_telemetry_with_missing_dht(self):
        line = brz2_frame("BRZ2,T,8,NA,NA,10,300,CLOSED,0").strip()
        frame = parse_brz2_line(line)
        self.assertIsNone(frame.temperature)
        self.assertIsNone(frame.humidity)
        self.assertEqual(frame.flags, 0)

    def test_bad_checksum_and_hello_return_none(self):
        good = brz2_frame("BRZ2,T,7,24.5,48.0,73,612,OPEN,1").strip()
        bad = good[:-2] + ("01" if good.endswith("00") else "00")
        self.assertIsNone(parse_brz2_line(bad))
        self.assertIsNone(parse_brz2_line(brz2_frame("BRZ2,HELLO,1.0.0,win-01").strip()))

    def test_command_encoding_round_trip(self):
        command = {"command_id": "cmd-uuid-123", "target_node_id": "win-01", "target_device": "window", "action": "open"}
        line = command_to_brz2_frame(command).strip()
        short_id = brz2_command_short_id("cmd-uuid-123")
        self.assertIn(f"BRZ2,CMD,{short_id},WINDOW,OPEN", line)


class EndpointSpecTest(unittest.TestCase):
    def test_specs(self):
        self.assertEqual(parse_endpoint_spec("win-01@192.0.2.57:7000"), SensorEndpoint("win-01", "192.0.2.57", 7000))
        self.assertEqual(parse_endpoint_spec("192.0.2.57", 5568), SensorEndpoint("192.0.2.57", "192.0.2.57", 5568))
        with self.assertRaises(ValueError):
            parse_endpoint_spec("win-01@")


class WifiTransportTest(unittest.TestCase):
    def test_telemetry_command_and_ack_flow(self):
        sensor = FakeWifiSensor([
            brz2_frame("BRZ2,HELLO,1.0.0,win-01").strip(),
            brz2_frame("BRZ2,T,1,24.5,48.0,73,612,CLOSED,0").strip(),
        ])
        self.addCleanup(sensor.close)

        frames: queue.Queue = queue.Queue()
        transport = WifiTransport(
            [SensorEndpoint("win-01", "127.0.0.1", sensor.port)],
            frames.put,
            gateway_id="gw-01",
        )
        transport.start()
        self.addCleanup(transport.stop)

        reading = frames.get(timeout=5)
        self.assertIsInstance(reading, NodeReading)
        self.assertEqual(reading.node_id, "win-01")
        self.assertEqual(reading.gateway_id, "gw-01")
        self.assertEqual(reading.temperature, 24.5)

        command = {"command_id": "cmd-uuid-9", "target_node_id": "win-01", "target_device": "window", "action": "open"}
        deadline = time.time() + 5
        while True:  # session may still be dialing when we send
            try:
                transport.send_command(command)
                break
            except ConnectionError:
                if time.time() > deadline:
                    raise
                time.sleep(0.05)

        ack = frames.get(timeout=5)
        self.assertIsInstance(ack, CommandAck)
        self.assertEqual(ack.command_id, "cmd-uuid-9")  # short id translated back
        self.assertEqual(ack.node_id, "win-01")
        self.assertEqual(ack.result, "OK")
        self.assertEqual(ack.state, "OPEN")
        self.assertEqual(len(sensor.received), 1)

    def test_breezio_dialect_node_lines(self):
        sensor = FakeWifiSensor(["@NODE,n7,12,215,480,300,3100,-60,0"])
        self.addCleanup(sensor.close)
        frames: queue.Queue = queue.Queue()
        transport = WifiTransport(
            [SensorEndpoint("n7", "127.0.0.1", sensor.port)],
            frames.put,
            gateway_id="gw-01",
            dialect="breezio",
        )
        transport.start()
        self.addCleanup(transport.stop)
        reading = frames.get(timeout=5)
        self.assertIsInstance(reading, NodeReading)
        self.assertEqual(reading.node_id, "n7")
        self.assertEqual(reading.temperature, 21.5)

    def test_unknown_target_raises(self):
        transport = WifiTransport([], lambda frame: None)
        with self.assertRaises(KeyError):
            transport.send_command({"command_id": "x", "target_node_id": "ghost", "target_device": "window", "action": "open"})


class WifiListenerTransportTest(unittest.TestCase):
    def test_sensor_initiated_telemetry_command_and_ack_flow(self):
        frames: queue.Queue = queue.Queue()
        transport = WifiListenerTransport(
            "127.0.0.1",
            0,
            frames.put,
            gateway_id="gw-01",
        )
        transport.start()
        self.addCleanup(transport.stop)

        sensor = FakePushWifiSensor(
            transport.host,
            transport.port,
            [
                brz2_frame("BRZ2,HELLO,1.0.0,win-01").strip(),
                brz2_frame("BRZ2,T,1,24.5,48.0,73,612,CLOSED,0").strip(),
            ],
        )
        self.addCleanup(sensor.close)

        reading = frames.get(timeout=5)
        self.assertIsInstance(reading, NodeReading)
        self.assertEqual(reading.node_id, "win-01")
        self.assertEqual(reading.gateway_id, "gw-01")
        self.assertEqual(reading.temperature, 24.5)

        command = {"command_id": "cmd-uuid-10", "target_node_id": "win-01", "target_device": "window", "action": "open"}
        deadline = time.time() + 5
        while True:
            try:
                transport.send_command(command)
                break
            except ConnectionError:
                if time.time() > deadline:
                    raise
                time.sleep(0.05)

        ack = frames.get(timeout=5)
        self.assertIsInstance(ack, CommandAck)
        self.assertEqual(ack.command_id, "cmd-uuid-10")
        self.assertEqual(ack.node_id, "win-01")
        self.assertEqual(ack.result, "OK")
        self.assertEqual(ack.state, "OPEN")
        self.assertEqual(len(sensor.received), 1)


class _NodesHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = {
            "success": True,
            "data": [
                {"node_id": "win-01", "ip_address": "192.0.2.57", "gateway_id": "gw-01"},
                {"node_id": "win-02", "ip_address": "192.0.2.58:7001", "gateway_id": "gw-01"},
                {"node_id": "serial-node", "ip_address": None, "gateway_id": "gw-01"},
            ],
        }
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class FetchEndpointsTest(unittest.TestCase):
    def test_fetch_node_endpoints_from_backend(self):
        server = HTTPServer(("127.0.0.1", 0), _NodesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        endpoints = fetch_node_endpoints(url, gateway_id="gw-01", default_port=5568)
        self.assertEqual(
            endpoints,
            [
                SensorEndpoint("win-01", "192.0.2.57", 5568),
                SensorEndpoint("win-02", "192.0.2.58", 7001),
            ],
        )


if __name__ == "__main__":
    unittest.main()
