from __future__ import annotations

import argparse
import json
import os
import queue
import select
import sys
import termios
import threading
from pathlib import Path
from typing import Iterable, Optional

from .discovery import GatewayServicePublisher
from .protocol import MqttMessageFactory, SerialProtocolParser
from .queue import JsonlQueue
from .wifi import DEFAULT_SENSOR_PORT, WifiListenerTransport, WifiTransport, fetch_node_endpoints, parse_endpoint_spec, parse_listen_spec


BAUD_RATES = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


class GatewayApplication:
    def __init__(
        self,
        *,
        gateway_id: str,
        node_id: str,
        topology: str,
        reading_source: Optional[str],
        mqtt_host: Optional[str],
        mqtt_port: int,
        queue_path: Path,
    ):
        self.parser = SerialProtocolParser(
            gateway_id=gateway_id,
            node_id=node_id,
            topology=topology,
            router_node_id=node_id,
            reading_source=reading_source,
        )
        self.message_factory = MqttMessageFactory()
        self.publisher = _mqtt_publisher(mqtt_host, mqtt_port) if mqtt_host else None
        self.queue = JsonlQueue(queue_path)

    def run(self, lines: Iterable[str]) -> int:
        for frame in self.parser.parse_stream(lines):
            for topic, payload in self.message_factory.messages_for(frame):
                decoded = json.loads(payload)
                if self.publisher is None:
                    print(json.dumps({"topic": topic, "payload": decoded}, ensure_ascii=False))
                    self.queue.append({"topic": topic, "payload": decoded})
                else:
                    self.publisher(topic, payload)
        return 0


def run(
    lines: Iterable[str],
    *,
    gateway_id: str,
    node_id: str,
    topology: str,
    reading_source: Optional[str],
    mqtt_host: Optional[str],
    mqtt_port: int,
    queue_path: Path,
) -> int:
    app = GatewayApplication(
        gateway_id=gateway_id,
        node_id=node_id,
        topology=topology,
        reading_source=reading_source,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        queue_path=queue_path,
    )
    return app.run(lines)


def run_wifi(args: argparse.Namespace) -> int:
    """Compatibility Wi-Fi/TCP mode: dial each registered sensor IP.

    Prefer run_wifi_listener() for the ESP32 flow where sensors actively connect
    to the gateway listener.
    """
    endpoints = [parse_endpoint_spec(spec, args.sensor_port) for spec in (args.wifi_node or [])]
    if args.backend_url:
        endpoints.extend(fetch_node_endpoints(args.backend_url, gateway_id=args.gateway_id, token=os.getenv("BREEZIO_API_TOKEN"), default_port=args.sensor_port))
    seen: set[str] = set()
    endpoints = [ep for ep in endpoints if ep.node_id not in seen and not seen.add(ep.node_id)]
    if not endpoints:
        raise SystemExit("Wi-Fi mode needs at least one sensor endpoint (--wifi-node or --backend-url with registered node IPs)")

    factory = MqttMessageFactory()
    publisher = _mqtt_publisher(args.mqtt_host, args.mqtt_port) if args.mqtt_host else None
    offline_queue = JsonlQueue(args.queue_path)

    def sink(frame) -> None:
        for topic, payload in factory.messages_for(frame):
            decoded = json.loads(payload)
            if publisher is None:
                print(json.dumps({"topic": topic, "payload": decoded}, ensure_ascii=False), flush=True)
                offline_queue.append({"topic": topic, "payload": decoded})
            else:
                publisher(topic, payload)

    transport = WifiTransport(
        endpoints,
        sink,
        gateway_id=args.gateway_id,
        dialect=args.sensor_dialect,
        reading_source=args.reading_source or "wifi_tcp",
    )
    transport.start()

    if args.mqtt_host:
        _run_wifi_command_listener(args.mqtt_host, args.mqtt_port, transport)
    transport.join()
    return 0


def run_wifi_listener(args: argparse.Namespace) -> int:
    """Wi-Fi/TCP mode: listen for sensors that actively connect to the gateway."""
    host, port = parse_listen_spec(args.wifi_listen, args.sensor_port)
    factory = MqttMessageFactory()
    publisher = _mqtt_publisher(args.mqtt_host, args.mqtt_port) if args.mqtt_host else None
    offline_queue = JsonlQueue(args.queue_path)

    def sink(frame) -> None:
        for topic, payload in factory.messages_for(frame):
            decoded = json.loads(payload)
            if publisher is None:
                print(json.dumps({"topic": topic, "payload": decoded}, ensure_ascii=False), flush=True)
                offline_queue.append({"topic": topic, "payload": decoded})
            else:
                publisher(topic, payload)

    transport = WifiListenerTransport(
        host,
        port,
        sink,
        gateway_id=args.gateway_id,
        dialect=args.sensor_dialect,
        reading_source=args.reading_source or "wifi_tcp",
    )
    transport.start()
    service_publisher = GatewayServicePublisher(gateway_id=args.gateway_id, port=transport.port, protocol=args.sensor_dialect)
    service_publisher.start()
    try:
        if args.mqtt_host:
            _run_wifi_command_listener(args.mqtt_host, args.mqtt_port, transport)
        transport.join()
    finally:
        service_publisher.stop()
    return 0


def _run_wifi_command_listener(host: str, port: int, transport: WifiTransport) -> None:
    """Subscribe to node command topics and forward them over the Wi-Fi links."""
    import paho.mqtt.client as mqtt

    def on_message(client, userdata, message):
        try:
            transport.send_command(json.loads(message.payload.decode("utf-8")))
        except (ValueError, KeyError, ConnectionError, OSError) as exc:
            print(f"failed to forward command from {message.topic}: {exc}", file=sys.stderr)

    client = mqtt.Client()
    client.on_message = on_message
    client.connect(host, port, keepalive=30)
    client.subscribe("breezio/nodes/+/command", qos=1)
    client.loop_forever()


def _mqtt_publisher(host: str, port: int):
    try:
        import paho.mqtt.client as mqtt
    except ModuleNotFoundError as exc:
        raise SystemExit("paho-mqtt is required for MQTT publishing; omit --mqtt-host for dry-run mode") from exc

    client = mqtt.Client()
    client.connect(host, port, keepalive=30)
    client.loop_start()

    def publish(topic: str, payload: str) -> None:
        result = client.publish(topic, payload, qos=1)
        result.wait_for_publish()
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed for {topic}: rc={result.rc}")

    return publish


def _serial_lines(port: str, baudrate: int):
    try:
        import serial
    except ModuleNotFoundError:
        yield from _posix_serial_lines(port, baudrate)
        return
    with serial.Serial(port, baudrate, timeout=1) as handle:
        while True:
            line = handle.readline()
            if line:
                yield line.decode("utf-8", errors="replace")


def _posix_serial_lines(port: str, baudrate: int):
    if baudrate not in BAUD_RATES:
        raise SystemExit(f"unsupported baudrate without pyserial: {baudrate}")
    fd = os.open(port, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    original = termios.tcgetattr(fd)
    buffer = b""
    try:
        configured = list(original)
        configured[0] = 0
        configured[1] = 0
        configured[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        configured[3] = 0
        configured[4] = BAUD_RATES[baudrate]
        configured[5] = BAUD_RATES[baudrate]
        configured[6] = list(configured[6])
        configured[6][termios.VMIN] = 0
        configured[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, configured)
        while True:
            readable, _, _ = select.select([fd], [], [], 1.0)
            if not readable:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                continue
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                decoded = line.decode("utf-8", errors="replace").rstrip("\r")
                if decoded:
                    yield decoded
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, original)
        os.close(fd)


def _serial_lines_from_ports(ports: list[str], baudrate: int):
    if len(ports) == 1:
        yield from _serial_lines(ports[0], baudrate)
        return

    line_queue: queue.Queue[str] = queue.Queue()

    def pump(port: str) -> None:
        for line in _serial_lines(port, baudrate):
            line_queue.put(line)

    for port in ports:
        threading.Thread(target=pump, args=(port,), daemon=True).start()

    while True:
        yield line_queue.get()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Breezio serial-to-MQTT gateway")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--stdin", action="store_true", help="read frames from stdin for simulator tests")
    source.add_argument("--serial-port", action="append", help="read frames from a hardware serial port; repeat for direct-serial multi-node mode")
    source.add_argument("--wifi-node", action="append", help="legacy Wi-Fi/TCP mode: gateway dials sensor endpoint as node_id@ip[:port] (or bare ip); repeatable")
    source.add_argument("--wifi-listen", help="Wi-Fi/TCP mode: listen for sensor-initiated connections on host:port, :port, or bare port")
    parser.add_argument("--backend-url", default=os.getenv("BREEZIO_BACKEND_URL"), help="legacy Wi-Fi/TCP mode: fetch registered node IPs from this backend (/api/nodes); combinable with --wifi-node")
    parser.add_argument("--sensor-port", type=int, default=int(os.getenv("SENSOR_TCP_PORT", str(DEFAULT_SENSOR_PORT))), help="default TCP port for Wi-Fi sensors")
    parser.add_argument("--sensor-dialect", choices=("brz2", "breezio"), default=os.getenv("SENSOR_DIALECT", "brz2"), help="line protocol spoken by Wi-Fi sensors")
    parser.add_argument("--baudrate", type=int, default=int(os.getenv("SERIAL_BAUDRATE", "38400")))
    parser.add_argument("--gateway-id", default=os.getenv("GATEWAY_ID", "gw-01"))
    parser.add_argument("--node-id", default=os.getenv("NODE_ID", "n1"))
    parser.add_argument("--topology", choices=("direct", "router"), default=os.getenv("GATEWAY_TOPOLOGY", "router"))
    parser.add_argument("--reading-source", default=os.getenv("GATEWAY_READING_SOURCE"))
    parser.add_argument("--mqtt-host", default=os.getenv("MQTT_HOST"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    parser.add_argument("--queue-path", type=Path, default=Path(os.getenv("GATEWAY_QUEUE_PATH", "var/gateway-offline.jsonl")))
    args = parser.parse_args(argv)
    if not (args.stdin or args.serial_port or args.wifi_node or args.backend_url or args.wifi_listen):
        parser.error("one of --stdin, --serial-port, --wifi-node, --wifi-listen or --backend-url is required")
    if args.backend_url and (args.stdin or args.serial_port):
        parser.error("--backend-url is only valid in Wi-Fi mode (not with --stdin/--serial-port)")
    if args.backend_url and args.wifi_listen:
        parser.error("--backend-url is only valid for gateway-dials-sensor mode, not --wifi-listen")
    if args.topology == "router" and args.serial_port and len(args.serial_port) > 1:
        parser.error("router topology expects exactly one serial port; use --topology direct for multi-port serial")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.wifi_listen:
        return run_wifi_listener(args)
    if args.wifi_node or args.backend_url:
        return run_wifi(args)
    lines = sys.stdin if args.stdin else _serial_lines_from_ports(args.serial_port, args.baudrate)
    return run(
        lines,
        gateway_id=args.gateway_id,
        node_id=args.node_id,
        topology=args.topology,
        reading_source=args.reading_source,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        queue_path=args.queue_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
