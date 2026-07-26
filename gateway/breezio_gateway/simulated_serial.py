from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import pty
import sys
import time
from typing import Iterable, Optional

from .protocol import crc8


@dataclass(frozen=True)
class SensorSample:
    node_id: str
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    light: Optional[int] = None
    battery_mv: Optional[int] = None
    rssi: int = 0
    flags: int = 0


def default_samples(light: int = 734) -> list[SensorSample]:
    return [
        SensorSample(node_id="n1", temperature=28.0, humidity=12.0),
        SensorSample(node_id="n2", light=light),
    ]


def build_batch_lines(gateway_id: str, batch_id: int, samples: Iterable[SensorSample]) -> list[str]:
    records = list(samples)
    header = f"@BATCH,{gateway_id},{batch_id},{len(records)}"
    node_lines = [_sample_to_node_line(sample, batch_id) for sample in records]
    crc_input = "\n".join([header, *node_lines]).encode("utf-8")
    return [header, *node_lines, f"@END,{crc8(crc_input):02X}"]


class VirtualSerialDevice:
    def __init__(self) -> None:
        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self.slave_path: Optional[str] = None

    def __enter__(self) -> "VirtualSerialDevice":
        self.master_fd, self.slave_fd = pty.openpty()
        self.slave_path = os.ttyname(self.slave_fd)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.master_fd is not None:
            os.close(self.master_fd)
            self.master_fd = None
        if self.slave_fd is not None:
            os.close(self.slave_fd)
            self.slave_fd = None

    def write_lines(self, lines: Iterable[str]) -> None:
        if self.master_fd is None:
            raise RuntimeError("virtual serial device is not open")
        payload = "".join(f"{line}\r\n" for line in lines).encode("ascii")
        os.write(self.master_fd, payload)


def run_simulator(
    *,
    gateway_id: str,
    samples: list[SensorSample],
    interval_seconds: float,
    once: bool,
    stream,
) -> int:
    with VirtualSerialDevice() as device:
        print(device.slave_path, file=stream, flush=True)
        batch_id = 1
        while True:
            device.write_lines(build_batch_lines(gateway_id, batch_id, samples))
            if once:
                break
            batch_id += 1
            time.sleep(interval_seconds)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Breezio virtual serial sensor for gateway demos")
    parser.add_argument("--gateway-id", default="gw-01")
    parser.add_argument("--light", type=int, default=734)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true", help="emit one batch and exit; useful for smoke tests")
    args = parser.parse_args(argv)
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds must be positive")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    return run_simulator(
        gateway_id=args.gateway_id,
        samples=default_samples(light=args.light),
        interval_seconds=args.interval_seconds,
        once=args.once,
        stream=sys.stdout,
    )


def _sample_to_node_line(sample: SensorSample, seq: int) -> str:
    temperature = _x10(sample.temperature)
    humidity = _x10(sample.humidity)
    light = "" if sample.light is None else str(sample.light)
    battery_mv = "" if sample.battery_mv is None else str(sample.battery_mv)
    return (
        f"@NODE,{sample.node_id},{seq},{temperature},{humidity},{light},"
        f"{battery_mv},{sample.rssi},{sample.flags}"
    )


def _x10(value: Optional[float]) -> str:
    return "" if value is None else str(int(round(value * 10)))


if __name__ == "__main__":
    raise SystemExit(main())
