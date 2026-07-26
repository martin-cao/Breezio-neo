"""`$BRZ2` line-protocol codec (host side). Mirrors sensor-new/src/proto.rs.

See sensor-new/docs/PROTOCOL.md. Everything here is pure and dependency-free so it
is trivially unit-testable without any serial hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

# Air-quality thresholds, kept in sync with sensor-new/src/sensors.rs.
AQ_ALARM_MV = 2000
AQ_CLEAR_MV = 1600


def checksum(body: str) -> int:
    """NMEA-style XOR of every byte between `$` and `*`."""
    cs = 0
    for byte in body.encode("ascii", errors="replace"):
        cs ^= byte
    return cs


def frame(body: str) -> str:
    """Wrap a comma-joined body (no `$`/`*`) into a full `$<body>*CS\\r\\n` line."""
    return f"${body}*{checksum(body):02X}\r\n"


def air_quality_band(mv: int) -> str:
    """Coarse GOOD/FAIR/POOR band for an uncalibrated MQ-135 reading."""
    if mv >= AQ_ALARM_MV:
        return "POOR"
    if mv >= AQ_CLEAR_MV:
        return "FAIR"
    return "GOOD"


@dataclass(frozen=True)
class Telemetry:
    seq: int
    temperature_c: Optional[float]
    humidity_pct: Optional[float]
    brightness_pct: int
    air_quality_mv: int
    window_open: bool
    alarm: bool

    @property
    def air_quality_band(self) -> str:
        return air_quality_band(self.air_quality_mv)


@dataclass(frozen=True)
class Hello:
    fw_version: str
    node_id: str


@dataclass(frozen=True)
class Ack:
    id: int
    device: str
    result: str
    state: str


Message = Union[Telemetry, Hello, Ack]


def _opt_float(field: str) -> Optional[float]:
    return None if field == "NA" else float(field)


def parse_line(line: str) -> Optional[Message]:
    """Parse one line into a message, or return None if it isn't a valid frame.

    Tolerant: leading/trailing garbage is ignored (anchored on the last `$`), and
    any checksum/shape problem yields None instead of raising, so a noisy serial
    stream never crashes the caller.
    """
    dollar = line.rfind("$")
    if dollar < 0:
        return None
    after = line[dollar + 1 :]
    star = after.find("*")
    if star < 0 or len(after) < star + 3:
        return None
    body = after[:star]
    cs_hex = after[star + 1 : star + 3]
    try:
        expected = int(cs_hex, 16)
    except ValueError:
        return None
    if checksum(body) != expected:
        return None

    fields = body.split(",")
    if len(fields) < 2 or fields[0] != "BRZ2":
        return None

    try:
        kind = fields[1]
        if kind == "T" and len(fields) == 9:
            return Telemetry(
                seq=int(fields[2]),
                temperature_c=_opt_float(fields[3]),
                humidity_pct=_opt_float(fields[4]),
                brightness_pct=int(fields[5]),
                air_quality_mv=int(fields[6]),
                window_open=fields[7] == "OPEN",
                alarm=fields[8] == "1",
            )
        if kind == "HELLO" and len(fields) == 4:
            return Hello(fw_version=fields[2], node_id=fields[3])
        if kind == "ACK" and len(fields) == 6:
            return Ack(
                id=int(fields[2]),
                device=fields[3],
                result=fields[4],
                state=fields[5],
            )
    except ValueError:
        return None
    return None


VALID_ACTIONS = {
    "WINDOW": {"OPEN", "CLOSE", "TOGGLE"},
    "BUZZER": {"MUTE", "TEST"},
}


def encode_command(command_id: int, device: str, action: str) -> str:
    """Build a `$BRZ2,CMD,...` line for the node. Raises ValueError on bad args."""
    device = device.upper()
    action = action.upper()
    if device not in VALID_ACTIONS:
        raise ValueError(f"unknown device {device!r}")
    if action not in VALID_ACTIONS[device]:
        raise ValueError(f"invalid action {action!r} for {device}")
    return frame(f"BRZ2,CMD,{command_id},{device},{action}")
