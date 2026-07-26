"""CLI for the Breezio window-node gateway.

Examples
--------
Read live telemetry as JSON from the node's USB serial port:

    python -m smartwin_gateway monitor --serial-port /dev/cu.usbserial-XXXX

Dry-run the parser against a captured log (no hardware, no pyserial):

    python -m smartwin_gateway monitor --stdin < capture.txt

Open the window (sends `$BRZ2,CMD,<id>,WINDOW,OPEN*CS`):

    python -m smartwin_gateway send --serial-port /dev/cu.usbserial-XXXX \\
        --device WINDOW --action OPEN
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from .gateway import run_monitor, send_command, serial_lines
from .protocol import encode_command


def _next_command_id() -> int:
    # A small, monotonic-ish id derived from the clock; good enough to correlate
    # a command with its ACK in a single-node setup.
    return int(time.time()) % 100000


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="smartwin_gateway", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    mon = sub.add_parser("monitor", help="read telemetry and print JSON lines")
    src = mon.add_mutually_exclusive_group(required=True)
    src.add_argument("--stdin", action="store_true", help="read frames from stdin")
    src.add_argument("--serial-port", help="serial device to read from")
    mon.add_argument("--baudrate", type=int, default=115200)

    snd = sub.add_parser("send", help="send one command to the node")
    snd.add_argument("--device", required=True, choices=["WINDOW", "BUZZER"])
    snd.add_argument("--action", required=True, help="WINDOW: OPEN/CLOSE/TOGGLE; BUZZER: MUTE/TEST")
    snd.add_argument("--id", type=int, default=None, help="command id (default: derived from clock)")
    out = snd.add_mutually_exclusive_group(required=True)
    out.add_argument("--serial-port", help="serial device to write to")
    out.add_argument("--stdout", action="store_true", help="print the command line instead of sending")
    snd.add_argument("--baudrate", type=int, default=115200)

    args = parser.parse_args(argv)

    if args.cmd == "monitor":
        lines = sys.stdin if args.stdin else serial_lines(args.serial_port, args.baudrate)
        return run_monitor(lines)

    # send
    command_id = args.id if args.id is not None else _next_command_id()
    try:
        line = encode_command(command_id, args.device, args.action)
    except ValueError as exc:
        parser.error(str(exc))
    if args.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    else:
        send_command(args.serial_port, line, args.baudrate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
