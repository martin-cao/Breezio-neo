#!/usr/bin/env python3
"""Minimal dependency-free UART monitor for the Breezio CC2530 sensor."""

from __future__ import annotations

import argparse
import os
import select
import sys
import termios
import time


BAUD_RATES = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


def configure_serial(fd: int, baud: int) -> list:
    attributes = termios.tcgetattr(fd)
    configured = list(attributes)
    configured[0] = 0
    configured[1] = 0
    configured[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    configured[3] = 0
    configured[4] = BAUD_RATES[baud]
    configured[5] = BAUD_RATES[baud]
    configured[6] = list(configured[6])
    configured[6][termios.VMIN] = 0
    configured[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, configured)
    termios.tcflush(fd, termios.TCIFLUSH)
    return attributes


def monitor(device: str, baud: int, timeout: float | None) -> int:
    fd = os.open(device, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    original = None
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        original = configure_serial(fd, baud)
        while deadline is None or time.monotonic() < deadline:
            wait = 0.5
            if deadline is not None:
                wait = max(0.0, min(wait, deadline - time.monotonic()))
            readable, _, _ = select.select([fd], [], [], wait)
            if not readable:
                continue
            try:
                data = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if data:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
    finally:
        if original is not None:
            termios.tcsetattr(fd, termios.TCSANOW, original)
        os.close(fd)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Monitor Breezio sensor UART output (default: 38400 8N1)."
    )
    parser.add_argument("device", help="serial device, for example /dev/cu.PL2303G-USBtoUART1410")
    parser.add_argument("--baud", type=int, choices=sorted(BAUD_RATES), default=38400)
    parser.add_argument(
        "--timeout",
        type=float,
        help="stop after this many seconds; omit to monitor until interrupted",
    )
    args = parser.parse_args()

    if args.timeout is not None and args.timeout < 0:
        parser.error("--timeout must be non-negative")

    try:
        return monitor(args.device, args.baud, args.timeout)
    except OSError as exc:
        print(f"serial monitor: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
