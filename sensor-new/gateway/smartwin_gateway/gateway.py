"""Runtime glue: turn a stream of `$BRZ2` lines into normalized JSON records, and
open serial ports for reading/sending. Serial support uses pyserial when present
and falls back to a plain file read so `--stdin` works with no dependencies.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable, Iterable, Iterator, Optional

from .protocol import Ack, Hello, Message, Telemetry, parse_line


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def message_to_dict(msg: Message, node_id: Optional[str]) -> dict:
    """Normalize a parsed message into a JSON-friendly dict with a timestamp."""
    record: dict = {"captured_at": _utc_now_iso()}
    if isinstance(msg, Telemetry):
        record.update(
            type="telemetry",
            node_id=node_id,
            **asdict(msg),
            air_quality_band=msg.air_quality_band,
        )
    elif isinstance(msg, Hello):
        record.update(type="hello", **asdict(msg))
    elif isinstance(msg, Ack):
        record.update(type="ack", node_id=node_id, **asdict(msg))
    return record


def parse_stream(lines: Iterable[str]) -> Iterator[tuple[Message, Optional[str]]]:
    """Yield (message, node_id) for every valid frame, tracking the node_id learned
    from the most recent HELLO banner."""
    node_id: Optional[str] = None
    for line in lines:
        msg = parse_line(line)
        if msg is None:
            continue
        if isinstance(msg, Hello):
            node_id = msg.node_id
        yield msg, node_id


def run_monitor(lines: Iterable[str], emit: Callable[[str], None] = print) -> int:
    """Read frames and emit one JSON object per message."""
    for msg, node_id in parse_stream(lines):
        emit(json.dumps(message_to_dict(msg, node_id), ensure_ascii=False))
    return 0


def serial_lines(port: str, baudrate: int = 115200) -> Iterator[str]:
    """Yield decoded lines from a serial port (requires pyserial)."""
    try:
        import serial  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "pyserial is required to read a serial port; "
            "install it (`pip install pyserial`) or use --stdin"
        ) from exc

    with serial.Serial(port, baudrate, timeout=1) as handle:
        while True:
            raw = handle.readline()
            if raw:
                yield raw.decode("utf-8", errors="replace")


def send_command(port: str, command_line: str, baudrate: int = 115200) -> None:
    """Write one command line to the serial port (requires pyserial)."""
    try:
        import serial  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "pyserial is required to send a command; install it or use --stdout"
        ) from exc

    with serial.Serial(port, baudrate, timeout=1) as handle:
        handle.write(command_line.encode("ascii"))
        handle.flush()
