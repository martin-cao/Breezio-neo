from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Callable, TextIO

from .protocol import command_to_serial_frame


DEFAULT_BAUDRATE = 38400


def command_payload_to_serial_frame(payload: str | bytes) -> str:
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    command = json.loads(text)
    return command_to_serial_frame(command)


def stdout_writer(stream: TextIO = sys.stdout) -> Callable[[str], None]:
    def write(frame: str) -> None:
        stream.write(frame)
        stream.flush()

    return write


def serial_writer(port: str, baudrate: int) -> Callable[[str], None]:
    try:
        import serial
    except ModuleNotFoundError as exc:
        raise SystemExit("pyserial is required for serial command writing; use --stdout for dry-run mode") from exc

    ser = serial.Serial(port, baudrate=baudrate, timeout=1)

    def write(frame: str) -> None:
        ser.write(frame.encode("utf-8"))
        ser.flush()

    return write


def run_listener(*, mqtt_host: str, mqtt_port: int, topic: str, writer: Callable[[str], None]) -> int:
    try:
        import paho.mqtt.client as mqtt
    except ModuleNotFoundError as exc:
        raise SystemExit("paho-mqtt is required for MQTT command listening") from exc

    def on_message(client, userdata, message):
        writer(command_payload_to_serial_frame(message.payload))

    client = mqtt.Client()
    client.on_message = on_message
    client.connect(mqtt_host, mqtt_port, keepalive=30)
    client.subscribe(topic, qos=1)
    client.loop_forever()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Breezio MQTT-command to serial bridge")
    parser.add_argument("--mqtt-host", default=os.getenv("MQTT_HOST", "127.0.0.1"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    parser.add_argument("--node-id", default=os.getenv("NODE_ID", "+"))
    parser.add_argument("--serial-port")
    parser.add_argument("--baudrate", type=int, default=int(os.getenv("BAUDRATE", str(DEFAULT_BAUDRATE))))
    parser.add_argument("--stdout", action="store_true", help="write command frames to stdout instead of a serial port")
    args = parser.parse_args()
    if not args.stdout and not args.serial_port:
        raise SystemExit("--serial-port is required unless --stdout is set")

    topic = f"breezio/nodes/{args.node_id}/command"
    writer = stdout_writer() if args.stdout else serial_writer(args.serial_port, args.baudrate)
    return run_listener(mqtt_host=args.mqtt_host, mqtt_port=args.mqtt_port, topic=topic, writer=writer)


if __name__ == "__main__":
    raise SystemExit(main())
