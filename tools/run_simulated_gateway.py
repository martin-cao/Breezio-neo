from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from breezio_gateway.main import GatewayApplication, _serial_lines_from_ports
from breezio_gateway.simulated_serial import VirtualSerialDevice, build_batch_lines, default_samples


ROOT = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run a PTY-backed simulated serial sensor and gateway")
    p.add_argument("--gateway-id", default=os.getenv("GATEWAY_ID", "gw-sim-01"))
    p.add_argument("--node-id", default=os.getenv("NODE_ID", "sim-router"))
    p.add_argument("--topology", choices=("direct", "router"), default=os.getenv("GATEWAY_TOPOLOGY", "router"))
    p.add_argument("--reading-source", default=os.getenv("GATEWAY_READING_SOURCE", "serial_simulated"))
    p.add_argument("--mqtt-host", default=os.getenv("MQTT_HOST"))
    p.add_argument("--mqtt-port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    p.add_argument("--queue-path", type=Path, default=Path(os.getenv("GATEWAY_QUEUE_PATH", "var/gateway-simulated.jsonl")))
    p.add_argument("--baudrate", type=int, default=int(os.getenv("SERIAL_BAUDRATE", "38400")))
    p.add_argument("--light", type=int, default=734)
    p.add_argument("--interval-seconds", type=float, default=1.0)
    p.add_argument("--once", action="store_true", help="emit one batch and exit after the gateway has read it")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = parser().parse_args(argv)
    if args.interval_seconds <= 0:
        raise SystemExit("--interval-seconds must be positive")

    stop = threading.Event()
    errors: list[BaseException] = []

    def request_stop(signum: int, frame=None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    with VirtualSerialDevice() as device:
        print(f"[simulated-gateway] serial={device.slave_path}", flush=True)
        app = GatewayApplication(
            gateway_id=args.gateway_id,
            node_id=args.node_id,
            topology=args.topology,
            reading_source=args.reading_source,
            mqtt_host=args.mqtt_host,
            mqtt_port=args.mqtt_port,
            queue_path=ROOT / args.queue_path,
        )

        def gateway_loop() -> None:
            try:
                app.run(_serial_lines_from_ports([device.slave_path], args.baudrate))
            except BaseException as exc:
                errors.append(exc)
                stop.set()

        thread = threading.Thread(target=gateway_loop, daemon=True)
        thread.start()
        time.sleep(0.1)

        batch_id = 1
        samples = default_samples(light=args.light)
        while not stop.is_set():
            device.write_lines(build_batch_lines(args.gateway_id, batch_id, samples))
            print(f"[simulated-gateway] emitted batch={batch_id}", flush=True)
            if args.once:
                time.sleep(0.3)
                break
            batch_id += 1
            stop.wait(args.interval_seconds)

        # Snapshot errors observed while the device was still open. Closing
        # the virtual serial device below (this `with` block's __exit__)
        # makes the background gateway thread's blocking read fail too —
        # that's expected teardown noise, not a real gateway failure, and
        # must not flip a successful --once run's exit code.
        errors_before_teardown = list(errors)

    if errors_before_teardown:
        print(f"[simulated-gateway] gateway failed: {errors_before_teardown[0]}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
