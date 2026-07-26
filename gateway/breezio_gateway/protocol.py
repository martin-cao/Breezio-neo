from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import re
from typing import Iterable, Iterator, Optional, Union
from uuid import uuid4


logger = logging.getLogger(__name__)

LEGACY_RE = re.compile(r"^\{\s*A0\s*=\s*([-+]?\d+(?:\.\d+)?)\s*,\s*A1\s*=\s*([-+]?\d+(?:\.\d+)?)\s*\}$")

# ~BZ1, hex-framed protocol: "~BZ1,<len_hex>,<payload_hex>,<crc16_hex>\r\n".
# Designed to survive a noisy/contended UART line (see docs/sensor-network-protocol.md):
# the parser scans for the magic anywhere in a line and validates length/hex/CRC before
# trusting anything, so garbage before/inside/after a frame never raises and never
# silently fabricates a reading.
HEX_MAGIC = "~BZ1,"
HEX_MAX_PAYLOAD_LEN = 128  # bytes; guards against a corrupted/oversized length field

HEX_VERSION = 1
HEX_MSG_TELEMETRY = 0x01
HEX_MSG_COMMAND_ACK = 0x11

HEX_MISSING_I16 = 0x7FFF
HEX_MISSING_U16 = 0xFFFF
HEX_MISSING_I8 = -128

CC2530_COMMAND_ID_MAX_LEN = 36
CC2530_TARGET_NODE_ID_MAX_LEN = 14
CC2530_UART_COMMAND_MAX_LEN = 70
CC2530_COMMAND_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
CC2530_TARGET_NODE_ID_RE = re.compile(r"^(?:\*|[A-Za-z0-9._:-]+)$")
CC2530_DEVICES = frozenset({"LAMP", "LED", "WINDOW"})
CC2530_ACTIONS = frozenset({"ON", "OFF", "TOGGLE", "OPEN", "CLOSE"})


@dataclass
class HexFrameStats:
    """Counters for the hex-framed protocol, kept per SerialProtocolParser instance."""

    accepted: int = 0
    dropped_malformed: int = 0
    dropped_crc: int = 0

    def as_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "dropped_malformed": self.dropped_malformed,
            "dropped_crc": self.dropped_crc,
        }


def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no xorout."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _is_hex_digits(text: str) -> bool:
    if not text:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in text)


def _drop_malformed(stats: Optional[HexFrameStats], line: str, reason: str) -> None:
    if stats is not None:
        stats.dropped_malformed += 1
    logger.warning("dropping malformed hex frame (%s): %r", reason, line)


def _drop_crc(stats: Optional[HexFrameStats], line: str) -> None:
    if stats is not None:
        stats.dropped_crc += 1
    logger.warning("dropping hex frame with CRC mismatch: %r", line)


def extract_hex_frame_payload(line: str, stats: Optional[HexFrameStats] = None) -> Optional[bytes]:
    """Scan `line` for a ~BZ1, hex frame and return its validated payload bytes.

    Never raises. Garbage before or after the magic is ignored; any structural
    problem (bad hex, length mismatch, oversized length, CRC mismatch) drops the
    frame and updates `stats` instead of propagating an exception, so a noisy
    line can never crash the caller.
    """
    idx = line.find(HEX_MAGIC)
    if idx < 0:
        return None
    rest = line[idx + len(HEX_MAGIC):]

    if len(rest) < 5 or rest[4] != ",":
        _drop_malformed(stats, line, "missing length field")
        return None
    len_hex = rest[:4]
    if not _is_hex_digits(len_hex):
        _drop_malformed(stats, line, "invalid length hex")
        return None
    length = int(len_hex, 16)
    if length > HEX_MAX_PAYLOAD_LEN:
        _drop_malformed(stats, line, "oversized length")
        return None

    payload_hex_len = length * 2
    payload_hex = rest[5:5 + payload_hex_len]
    payload_ok = (len(payload_hex) == payload_hex_len) and (payload_hex_len == 0 or _is_hex_digits(payload_hex))
    if not payload_ok:
        _drop_malformed(stats, line, "payload length mismatch or invalid hex")
        return None

    after_payload = rest[5 + payload_hex_len:]
    if len(after_payload) < 5 or after_payload[0] != ",":
        _drop_malformed(stats, line, "missing crc field")
        return None
    crc_hex = after_payload[1:5]
    if not _is_hex_digits(crc_hex):
        _drop_malformed(stats, line, "invalid crc hex")
        return None

    payload = bytes.fromhex(payload_hex)
    expected_crc = int(crc_hex, 16)
    actual_crc = crc16_ccitt_false(payload)
    if actual_crc != expected_crc:
        _drop_crc(stats, line)
        return None
    return payload


def encode_hex_frame(payload: bytes) -> str:
    """Encode `payload` as a ~BZ1, hex frame line (mirrors the sensor-side encoder)."""
    if len(payload) > HEX_MAX_PAYLOAD_LEN:
        raise ValueError(f"payload of {len(payload)} bytes exceeds HEX_MAX_PAYLOAD_LEN")
    crc = crc16_ccitt_false(payload)
    return f"{HEX_MAGIC}{len(payload):04X},{payload.hex().upper()},{crc:04X}"


def _decode_hex_telemetry_records(payload: bytes) -> tuple[int, list[dict]]:
    if len(payload) < 5:
        raise ValueError("hex telemetry payload too short")
    msg_type = payload[1]
    if msg_type != HEX_MSG_TELEMETRY:
        raise ValueError(f"not a telemetry payload: msg_type=0x{msg_type:02X}")
    batch_id = int.from_bytes(payload[2:4], "little")
    record_count = payload[4]
    offset = 5
    records: list[dict] = []
    for _ in range(record_count):
        if offset >= len(payload):
            raise ValueError("truncated hex telemetry record header")
        node_id_len = payload[offset]
        offset += 1
        node_id_bytes = payload[offset:offset + node_id_len]
        if len(node_id_bytes) != node_id_len:
            raise ValueError("truncated node_id in hex telemetry record")
        offset += node_id_len
        if offset + 10 > len(payload):
            raise ValueError("truncated hex telemetry record fields")
        seq = int.from_bytes(payload[offset:offset + 2], "little")
        offset += 2
        temperature_raw = int.from_bytes(payload[offset:offset + 2], "little", signed=True)
        offset += 2
        humidity_raw = int.from_bytes(payload[offset:offset + 2], "little", signed=True)
        offset += 2
        light_raw = int.from_bytes(payload[offset:offset + 2], "little")
        offset += 2
        battery_raw = int.from_bytes(payload[offset:offset + 2], "little")
        offset += 2
        rssi_raw = int.from_bytes(payload[offset:offset + 1], "little", signed=True)
        offset += 1
        flags = payload[offset]
        offset += 1
        records.append(
            {
                "node_id": node_id_bytes.decode("ascii", errors="replace"),
                "seq": seq,
                "temperature": None if temperature_raw == HEX_MISSING_I16 else temperature_raw / 10.0,
                "humidity": None if humidity_raw == HEX_MISSING_I16 else humidity_raw / 10.0,
                "light": None if light_raw == HEX_MISSING_U16 else light_raw,
                "battery_mv": None if battery_raw == HEX_MISSING_U16 else battery_raw,
                "rssi": None if rssi_raw == HEX_MISSING_I8 else rssi_raw,
                "flags": flags,
            }
        )
    return batch_id, records


def parse_hex_telemetry_frame(
    payload: bytes,
    *,
    gateway_id: str,
    captured_at: Optional[str] = None,
    source: str = "serial_hex",
    topology: str = "batch",
    router_node_id: Optional[str] = None,
) -> BatchFrame:
    batch_id, raw_records = _decode_hex_telemetry_records(payload)
    timestamp = captured_at or utc_now_iso()
    records = []
    for raw in raw_records:
        role = "sensor"
        parent_node_id = None
        if topology == "router" and router_node_id:
            if raw["node_id"] == router_node_id:
                role = "router"
            else:
                parent_node_id = router_node_id
        records.append(
            NodeReading(
                gateway_id=gateway_id,
                batch_id=str(batch_id),
                node_id=raw["node_id"],
                seq=raw["seq"],
                captured_at=timestamp,
                temperature=raw["temperature"],
                humidity=raw["humidity"],
                light=raw["light"],
                battery_mv=raw["battery_mv"],
                rssi=raw["rssi"],
                flags=raw["flags"],
                source=source,
                role=role,
                parent_node_id=parent_node_id,
            )
        )
    return BatchFrame(gateway_id=gateway_id, batch_id=str(batch_id), captured_at=timestamp, records=records)


def _decode_hex_command_ack(payload: bytes) -> dict:
    if len(payload) < 4:
        raise ValueError("hex command-ack payload too short")
    msg_type = payload[1]
    if msg_type != HEX_MSG_COMMAND_ACK:
        raise ValueError(f"not a command-ack payload: msg_type=0x{msg_type:02X}")
    command_short_id = int.from_bytes(payload[2:4], "little")
    offset = 4
    if offset >= len(payload):
        raise ValueError("truncated hex command-ack node_id header")
    node_id_len = payload[offset]
    offset += 1
    node_id_bytes = payload[offset:offset + node_id_len]
    if len(node_id_bytes) != node_id_len:
        raise ValueError("truncated node_id in hex command-ack")
    offset += node_id_len
    if offset >= len(payload):
        raise ValueError("truncated hex command-ack result")
    result_code = payload[offset]
    offset += 1
    if offset >= len(payload):
        raise ValueError("truncated hex command-ack state header")
    state_len = payload[offset]
    offset += 1
    state_bytes = payload[offset:offset + state_len]
    if len(state_bytes) != state_len:
        raise ValueError("truncated state in hex command-ack")
    return {
        "command_id": str(command_short_id),
        "node_id": node_id_bytes.decode("ascii", errors="replace"),
        "result": "OK" if result_code == 0 else "ERROR",
        "state": state_bytes.decode("ascii", errors="replace") or None,
    }


def parse_hex_command_ack_frame(payload: bytes) -> CommandAck:
    decoded = _decode_hex_command_ack(payload)
    return CommandAck(
        command_id=decoded["command_id"],
        node_id=decoded["node_id"],
        result=decoded["result"],
        state=decoded["state"],
        acked_at=utc_now_iso(),
        raw_frame=f"hex:{decoded['command_id']}",
    )


def parse_hex_frame(
    line: str,
    *,
    gateway_id: str = "gw-01",
    topology: str = "batch",
    router_node_id: Optional[str] = None,
    source: str = "serial_hex",
    stats: Optional[HexFrameStats] = None,
) -> Optional[Union[BatchFrame, CommandAck]]:
    """Parse a single line for a ~BZ1, hex frame. Returns None (and updates
    `stats`) for anything malformed instead of raising, so noisy serial input
    can never crash the gateway."""
    payload = extract_hex_frame_payload(line, stats=stats)
    if payload is None:
        return None
    if len(payload) < 2:
        _drop_malformed(stats, line, "payload too short for a header")
        return None

    msg_type = payload[1]
    try:
        if msg_type == HEX_MSG_TELEMETRY:
            frame: Union[BatchFrame, CommandAck] = parse_hex_telemetry_frame(
                payload,
                gateway_id=gateway_id,
                source=source,
                topology=topology,
                router_node_id=router_node_id,
            )
        elif msg_type == HEX_MSG_COMMAND_ACK:
            frame = parse_hex_command_ack_frame(payload)
        else:
            _drop_malformed(stats, line, f"unknown msg_type 0x{msg_type:02X}")
            return None
    except (ValueError, IndexError) as exc:
        _drop_malformed(stats, line, str(exc))
        return None

    if stats is not None:
        stats.accepted += 1
    logger.debug("accepted hex frame: msg_type=0x%02X, stats=%s", msg_type, stats.as_dict() if stats else None)
    return frame


@dataclass(frozen=True)
class NodeReading:
    gateway_id: str
    node_id: str
    seq: Optional[int]
    captured_at: str
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    light: Optional[int] = None
    air_quality: Optional[int] = None
    battery_mv: Optional[int] = None
    rssi: Optional[int] = None
    flags: int = 0
    batch_id: Optional[str] = None
    source: str = "serial"
    role: str = "sensor"
    parent_node_id: Optional[str] = None


@dataclass(frozen=True)
class BatchFrame:
    gateway_id: str
    batch_id: str
    captured_at: str
    records: list[NodeReading]


@dataclass(frozen=True)
class CommandAck:
    command_id: str
    node_id: str
    result: str
    state: Optional[str]
    acked_at: str
    raw_frame: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_legacy_frame(
    line: str,
    gateway_id: str = "gw-01",
    node_id: str = "n1",
    seq: Optional[int] = None,
    *,
    source: str = "serial_legacy",
    role: str = "sensor",
    parent_node_id: Optional[str] = None,
) -> Optional[NodeReading]:
    """Parse the original Breezio sensor frame: {A0=<humidity>,A1=<temperature>}."""
    match = LEGACY_RE.match(line.strip())
    if not match:
        return None
    humidity = float(match.group(1))
    temperature = float(match.group(2))
    return NodeReading(
        gateway_id=gateway_id,
        node_id=node_id,
        seq=seq,
        captured_at=utc_now_iso(),
        temperature=temperature,
        humidity=humidity,
        source=source,
        role=role,
        parent_node_id=parent_node_id,
    )


def parse_ack_frame(line: str) -> Optional[CommandAck]:
    raw = line.strip()
    if not raw.startswith("@ACK,"):
        return None
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) < 5:
        raise ValueError(f"invalid ACK frame: {line!r}")
    return CommandAck(
        command_id=parts[1],
        node_id=parts[2],
        result=parts[3].upper(),
        state=parts[4] or None,
        acked_at=utc_now_iso(),
        raw_frame=raw,
    )


def parse_batch_lines(
    lines: Iterable[str],
    *,
    captured_at: Optional[str] = None,
    verify_crc: bool = False,
    source: str = "serial_batch",
    topology: str = "batch",
    router_node_id: Optional[str] = None,
) -> BatchFrame:
    clean = [line.strip() for line in lines if line.strip()]
    if len(clean) < 2:
        raise ValueError("batch frame needs @BATCH and @END lines")
    header = clean[0].split(",")
    if len(header) != 4 or header[0] != "@BATCH":
        raise ValueError(f"invalid batch header: {clean[0]!r}")
    if not clean[-1].startswith("@END,"):
        raise ValueError("batch frame missing @END")

    gateway_id = header[1].strip()
    batch_id = header[2].strip()
    expected_count = int(header[3])
    end_crc = clean[-1].split(",", 1)[1].strip().upper()
    body_lines = clean[:-1]

    if verify_crc:
        crc_input = "\n".join(body_lines).encode("utf-8")
        actual_crc = f"{crc8(crc_input):02X}"
        if actual_crc != end_crc:
            raise ValueError(f"batch CRC mismatch: expected {end_crc}, got {actual_crc}")

    timestamp = captured_at or utc_now_iso()
    records = [
        _parse_node_line(
            line,
            gateway_id,
            batch_id,
            timestamp,
            source=source,
            topology=topology,
            router_node_id=router_node_id,
        )
        for line in clean[1:-1]
    ]
    if len(records) != expected_count:
        raise ValueError(f"batch declared {expected_count} records but carried {len(records)}")
    return BatchFrame(gateway_id=gateway_id, batch_id=batch_id, captured_at=timestamp, records=records)


def _parse_node_line(
    line: str,
    gateway_id: str,
    batch_id: str,
    captured_at: str,
    *,
    source: str,
    topology: str,
    router_node_id: Optional[str],
) -> NodeReading:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) != 9 or parts[0] != "@NODE":
        raise ValueError(f"invalid node frame: {line!r}")
    node_id = parts[1]
    role = "sensor"
    parent_node_id = None
    if topology == "router" and router_node_id:
        if node_id == router_node_id:
            role = "router"
        else:
            parent_node_id = router_node_id
    return NodeReading(
        gateway_id=gateway_id,
        batch_id=batch_id,
        node_id=node_id,
        seq=_to_int(parts[2]),
        captured_at=captured_at,
        temperature=_x10(parts[3]),
        humidity=_x10(parts[4]),
        light=_to_int(parts[5]),
        battery_mv=_to_int(parts[6]),
        rssi=_to_int(parts[7]),
        flags=_to_int(parts[8]) or 0,
        source=source,
        role=role,
        parent_node_id=parent_node_id,
    )


def parse_node_line(
    line: str,
    *,
    gateway_id: str = "gw-01",
    batch_id: Optional[str] = None,
    captured_at: Optional[str] = None,
    source: str = "serial_direct",
) -> Optional[NodeReading]:
    raw = line.strip()
    if not raw.startswith("@NODE,"):
        return None
    return _parse_node_line(
        raw,
        gateway_id,
        batch_id or "direct",
        captured_at or utc_now_iso(),
        source=source,
        topology="direct",
        router_node_id=None,
    )


def frames_from_stream(lines: Iterable[str], gateway_id: str = "gw-01", node_id: str = "n1") -> Iterator[Union[NodeReading, BatchFrame, CommandAck]]:
    batch_buffer: list[str] = []
    legacy_seq = 0
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith("@BATCH,"):
            batch_buffer = [raw]
            continue
        if batch_buffer:
            batch_buffer.append(raw)
            if raw.startswith("@END,"):
                yield parse_batch_lines(batch_buffer)
                batch_buffer = []
            continue
        ack = parse_ack_frame(raw)
        if ack:
            yield ack
            continue
        legacy_seq += 1
        legacy = parse_legacy_frame(raw, gateway_id=gateway_id, node_id=node_id, seq=legacy_seq)
        if legacy:
            yield legacy


def telemetry_batch_payload(frame: Union[BatchFrame, NodeReading]) -> dict:
    if isinstance(frame, NodeReading):
        frame = BatchFrame(
            gateway_id=frame.gateway_id,
            batch_id=frame.batch_id or f"legacy-{frame.seq or uuid4().hex[:8]}",
            captured_at=frame.captured_at,
            records=[frame],
        )
    return {
        "message_id": str(uuid4()),
        "gateway_id": frame.gateway_id,
        "batch_id": frame.batch_id,
        "captured_at": frame.captured_at,
        "records": [asdict(record) for record in frame.records],
    }


def mqtt_messages(frame: Union[BatchFrame, NodeReading, CommandAck]) -> list[tuple[str, str]]:
    if isinstance(frame, CommandAck):
        payload = json.dumps(asdict(frame), separators=(",", ":"), ensure_ascii=False)
        return [(f"breezio/nodes/{frame.node_id}/command_ack", payload)]

    batch_payload = telemetry_batch_payload(frame)
    encoded_batch = json.dumps(batch_payload, separators=(",", ":"), ensure_ascii=False)
    messages = [(f"breezio/gateways/{batch_payload['gateway_id']}/batch", encoded_batch)]
    for record in batch_payload["records"]:
        encoded_record = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
        messages.append((f"breezio/nodes/{record['node_id']}/telemetry", encoded_record))
    return messages


# --- $BRZ2 dialect (sensor-new smart-window firmware, Wi-Fi/TCP or USB-serial) ---
#
# "$BRZ2,<kind>,...*CS\r\n" with an NMEA-style XOR checksum. The same lines the
# firmware writes to UART0 are served over its TCP link, so the Wi-Fi transport
# feeds them through here to land in the exact MQTT/backend shapes as serial.

BRZ2_MAGIC = "$BRZ2,"

# Flag bits carried into NodeReading.flags for window/alarm state.
BRZ2_FLAG_WINDOW_OPEN = 0x01
BRZ2_FLAG_ALARM = 0x02


def brz2_checksum(body: str) -> int:
    value = 0
    for byte in body.encode("ascii", errors="replace"):
        value ^= byte
    return value


def brz2_frame(body: str) -> str:
    return f"${body}*{brz2_checksum(body):02X}\r\n"


def brz2_command_short_id(command_id: str) -> int:
    """Map an arbitrary backend command_id (usually a UUID) onto the u16 id the
    $BRZ2 CMD frame carries. Callers keep a short-id -> command_id map so the
    matching ACK can be translated back."""
    return crc16_ccitt_false(str(command_id).encode("utf-8")) & 0x7FFF


def command_to_brz2_frame(command: dict) -> str:
    device = str(command["target_device"]).upper()
    action = str(command["action"]).upper()
    short_id = brz2_command_short_id(str(command["command_id"]))
    return brz2_frame(f"BRZ2,CMD,{short_id},{device},{action}")


def _brz2_opt_x1(field: str) -> Optional[float]:
    return None if field == "NA" else float(field)


def _brz2_opt_int(field: str) -> Optional[int]:
    return None if field == "NA" else int(field)


def parse_brz2_line(
    line: str,
    *,
    gateway_id: str = "gw-01",
    node_id: str = "n1",
    source: str = "wifi_tcp",
) -> Optional[Union[NodeReading, CommandAck]]:
    """Parse one $BRZ2 line into the gateway's internal frame shapes.

    Telemetry maps brightness -> light and packs window/alarm state into
    NodeReading.flags. HELLO frames and anything malformed return None; a noisy
    link can never raise out of here."""
    dollar = line.rfind("$")
    if dollar < 0:
        return None
    after = line[dollar + 1:]
    star = after.find("*")
    if star < 0 or len(after) < star + 3:
        return None
    body = after[:star]
    try:
        expected = int(after[star + 1:star + 3], 16)
    except ValueError:
        return None
    if brz2_checksum(body) != expected:
        logger.warning("dropping $BRZ2 frame with checksum mismatch: %r", line)
        return None

    fields = body.split(",")
    if len(fields) < 2 or fields[0] != "BRZ2":
        return None
    kind = fields[1]
    try:
        if kind == "T" and len(fields) == 9:
            flags = 0
            if fields[7] == "OPEN":
                flags |= BRZ2_FLAG_WINDOW_OPEN
            if fields[8] == "1":
                flags |= BRZ2_FLAG_ALARM
            return NodeReading(
                gateway_id=gateway_id,
                node_id=node_id,
                seq=int(fields[2]),
                captured_at=utc_now_iso(),
                temperature=_brz2_opt_x1(fields[3]),
                humidity=_brz2_opt_x1(fields[4]),
                light=_brz2_opt_int(fields[5]),
                air_quality=_brz2_opt_int(fields[6]),
                flags=flags,
                source=source,
            )
        if kind == "ACK" and len(fields) == 6:
            return CommandAck(
                command_id=fields[2],
                node_id=node_id,
                result="OK" if fields[4] == "OK" else "ERROR",
                state=fields[5] or None,
                acked_at=utc_now_iso(),
                raw_frame=line.strip(),
            )
    except ValueError:
        logger.warning("dropping malformed $BRZ2 frame: %r", line)
        return None
    return None  # HELLO and unknown kinds are informational only


def _cc2530_csv_field(
    command: dict,
    key: str,
    *,
    max_length: int,
    pattern: re.Pattern[str],
    uppercase: bool = False,
) -> str:
    value = str(command[key])
    if not 1 <= len(value) <= max_length:
        raise ValueError(f"{key} must contain 1-{max_length} characters for the CC2530 transport")
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{key} contains characters that are unsafe in a CC2530 CSV frame")
    return value.upper() if uppercase else value


def command_to_serial_frame(command: dict) -> str:
    """Encode a command for the bounded CC2530 ``@CMD`` UART/RF path.

    This validation belongs only to the legacy text transport. The ESP32-S3
    ``$BRZ2`` path hashes the backend command ID into its own short-ID field and
    is intentionally encoded by ``command_to_brz2_frame`` instead.
    """

    command_id = _cc2530_csv_field(
        command,
        "command_id",
        max_length=CC2530_COMMAND_ID_MAX_LEN,
        pattern=CC2530_COMMAND_ID_RE,
    )
    target = _cc2530_csv_field(
        command,
        "target_node_id",
        max_length=CC2530_TARGET_NODE_ID_MAX_LEN,
        pattern=CC2530_TARGET_NODE_ID_RE,
    )
    device = _cc2530_csv_field(
        command,
        "target_device",
        max_length=6,
        pattern=CC2530_COMMAND_ID_RE,
        uppercase=True,
    )
    action = _cc2530_csv_field(
        command,
        "action",
        max_length=6,
        pattern=CC2530_COMMAND_ID_RE,
        uppercase=True,
    )
    if device not in CC2530_DEVICES:
        raise ValueError(f"target_device must be one of {sorted(CC2530_DEVICES)} for the CC2530 transport")
    if action not in CC2530_ACTIONS:
        raise ValueError(f"action must be one of {sorted(CC2530_ACTIONS)} for the CC2530 transport")
    frame = f"@CMD,{command_id},{target},{device},{action}"
    if len(frame) > CC2530_UART_COMMAND_MAX_LEN:
        raise ValueError(
            f"CC2530 command frame exceeds the {CC2530_UART_COMMAND_MAX_LEN}-character UART limit"
        )
    return f"{frame}\n"


def crc8(data: bytes, poly: int = 0x07, init: int = 0x00) -> int:
    value = init
    for byte in data:
        value ^= byte
        for _ in range(8):
            if value & 0x80:
                value = ((value << 1) ^ poly) & 0xFF
            else:
                value = (value << 1) & 0xFF
    return value


def _to_int(value: str) -> Optional[int]:
    if value == "":
        return None
    return int(value)


def _x10(value: str) -> Optional[float]:
    parsed = _to_int(value)
    return None if parsed is None else parsed / 10.0


class SerialProtocolParser:
    def __init__(
        self,
        gateway_id: str = "gw-01",
        node_id: str = "n1",
        topology: str = "router",
        router_node_id: Optional[str] = None,
        reading_source: Optional[str] = None,
    ):
        if topology not in {"direct", "router"}:
            raise ValueError(f"unsupported gateway topology: {topology}")
        self.gateway_id = gateway_id
        self.node_id = node_id
        self.topology = topology
        self.router_node_id = router_node_id or node_id
        self.reading_source = reading_source
        self._batch_buffer: list[str] = []
        self._legacy_seq = 0
        self.hex_stats = HexFrameStats()

    def feed_line(self, line: str) -> Optional[Union[NodeReading, BatchFrame, CommandAck]]:
        raw = line.strip()
        if not raw:
            return None
        if HEX_MAGIC in raw:
            # Checked ahead of the text @BATCH/@NODE/@ACK/legacy paths, and
            # never falls through to them: a noisy line that happens to
            # contain the hex magic is vanishingly unlikely to also be a
            # valid text frame, and if a stray hex-looking line arrived while
            # a text @BATCH was mid-buffer (its own @END lost to corruption),
            # falling through would just feed it into that stale buffer,
            # which never got the robustness work this protocol is for.
            return parse_hex_frame(
                raw,
                gateway_id=self.gateway_id,
                topology=self.topology,
                router_node_id=self.router_node_id,
                source=self._reading_source(),
                stats=self.hex_stats,
            )
        if BRZ2_MAGIC in raw:
            # sensor-new smart-window dialect; single-node lines attributed to
            # this parser's node_id (Wi-Fi mode runs one parser per sensor).
            return parse_brz2_line(
                raw,
                gateway_id=self.gateway_id,
                node_id=self.node_id,
                source=self._reading_source(),
            )
        if raw.startswith("@BATCH,"):
            self._batch_buffer = [raw]
            return None
        if self._batch_buffer:
            self._batch_buffer.append(raw)
            if raw.startswith("@END,"):
                buffered, self._batch_buffer = self._batch_buffer, []
                try:
                    return parse_batch_lines(
                        buffered,
                        source=self._reading_source(),
                        topology=self.topology,
                        router_node_id=self.router_node_id,
                    )
                except ValueError as exc:
                    logger.warning("dropping malformed legacy batch frame (%s): %r", exc, buffered)
                    return None
            return None
        try:
            node = parse_node_line(raw, gateway_id=self.gateway_id, batch_id=f"direct-{self._legacy_seq + 1}", source=self._reading_source())
        except ValueError as exc:
            logger.warning("dropping malformed legacy @NODE line (%s): %r", exc, raw)
            node = None
        if node:
            self._legacy_seq += 1
            return node
        ack = parse_ack_frame(raw)
        if ack:
            return ack
        self._legacy_seq += 1
        return parse_legacy_frame(
            raw,
            gateway_id=self.gateway_id,
            node_id=self.node_id,
            seq=self._legacy_seq,
            source="serial_direct" if self.topology == "direct" else "serial_legacy",
        )

    def parse_stream(self, lines: Iterable[str]) -> Iterator[Union[NodeReading, BatchFrame, CommandAck]]:
        for line in lines:
            frame = self.feed_line(line)
            if frame is not None:
                yield frame

    def _reading_source(self) -> str:
        if self.reading_source:
            return self.reading_source
        return "serial_direct" if self.topology == "direct" else "serial_router"


class MqttMessageFactory:
    def messages_for(self, frame: Union[BatchFrame, NodeReading, CommandAck]) -> list[tuple[str, str]]:
        return mqtt_messages(frame)
