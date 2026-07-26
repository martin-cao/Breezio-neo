#!/usr/bin/env python3
"""Offline acceptance checks for the CC2530 SDCC port."""

from __future__ import annotations

import argparse
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    ROOT / "Makefile",
    ROOT / "source" / "platform.h",
    ROOT / "source" / "platform.c",
    ROOT / "tools" / "serial_monitor.py",
)
ACTIVE_SOURCES = (
    ROOT / "source" / "main.c",
    ROOT / "source" / "dht11.c",
    ROOT / "source" / "dht11.h",
    ROOT / "source" / "light_sensor.c",
    ROOT / "source" / "light_sensor.h",
    ROOT / "source" / "info.c",
    ROOT / "source" / "sys_init.c",
    ROOT / "source" / "sys_init.h",
    ROOT / "source" / "uart.c",
    ROOT / "source" / "uart.h",
    ROOT / "source" / "basic_rf.c",
    ROOT / "source" / "hal_rf.c",
    ROOT / "source" / "clock.c",
    ROOT / "source" / "hal_mcu.c",
    ROOT / "source" / "hal_int.c",
    ROOT / "source" / "hal_assert.c",
    ROOT / "source" / "mesh_net.c",
    ROOT / "source" / "mesh_net.h",
    ROOT / "source" / "hex_protocol.c",
    ROOT / "source" / "hex_protocol.h",
)
# Note: hal_types.h/hal_int.h/hal_mcu.h/hal_rf.h/clock.h/hal_assert.h/basic_rf.h
# are intentionally excluded, like platform.h: they retain legitimate
# IAR-specific branches (#elif defined __ICC8051__ etc.) for cross-compiler
# portability, which would otherwise trip the forbidden-pattern scan below.
FORBIDDEN_PATTERNS = (
    (re.compile(r"#\s*include\s*[<\"]ioCC2530\.h[>\"]", re.IGNORECASE), "direct IAR device include"),
    (re.compile(r"\b__near_func\b"), "IAR __near_func qualifier"),
    (re.compile(r"^\s*#\s*pragma\s+optimize\b", re.MULTILINE), "IAR optimize pragma"),
)
DHT11_UNBOUNDED_WAIT = re.compile(r"while\s*\(\s*!?\s*COM_R\b[^)]*\)\s*;")
MESH_DEFINE = re.compile(r"^\s*#\s*define\s+(MESH_CMDID_LEN|MESH_STR_LEN|MESH_RECV_BUF)\s+(\d+)", re.MULTILINE)
FLASH_SIZE = 256 * 1024
BASIC_RF_MAX_PAYLOAD_SIZE = 103


def check_static() -> list[str]:
    errors: list[str] = []
    for path in REQUIRED_FILES:
        if not path.is_file():
            errors.append(f"missing required port artifact: {path.relative_to(ROOT)}")

    makefile = ROOT / "Makefile"
    if makefile.is_file():
        makefile_text = makefile.read_text(encoding="utf-8", errors="replace")
        if "-DSPI_LCD" not in makefile_text:
            errors.append("Makefile does not preserve the IAR SPI_LCD build definition")

    platform_source = ROOT / "source" / "platform.c"
    if platform_source.is_file():
        platform_text = platform_source.read_text(encoding="utf-8", errors="replace")
        if "const PLATFORM_XDATA unsigned char *source" not in platform_text:
            errors.append("CC2530 information-page reader must use the XDATA address space")

    dht11_source = ROOT / "source" / "dht11.c"
    if dht11_source.is_file():
        dht11_text = dht11_source.read_text(encoding="utf-8", errors="replace")
        if DHT11_UNBOUNDED_WAIT.search(dht11_text):
            errors.append("source/dht11.c contains an unbounded COM_R wait that can hang telemetry")

    mesh_source = ROOT / "source" / "mesh_net.c"
    if mesh_source.is_file():
        mesh_text = mesh_source.read_text(encoding="utf-8", errors="replace")
        mesh_sizes = {name: int(value) for name, value in MESH_DEFINE.findall(mesh_text)}
        command_id_size = mesh_sizes.get("MESH_CMDID_LEN", 0)
        string_size = mesh_sizes.get("MESH_STR_LEN", 0)
        receive_size = mesh_sizes.get("MESH_RECV_BUF", 0)
        if command_id_size < 37:
            errors.append("RF command ID field cannot preserve a 36-character UUID")
        command_ack_size = 3 + command_id_size + 2 * string_size
        if receive_size < command_ack_size:
            errors.append("RF receive buffer is smaller than a complete command ACK")
        if command_ack_size > BASIC_RF_MAX_PAYLOAD_SIZE:
            errors.append("RF command ACK exceeds the Basic RF payload limit")

    main_source = ROOT / "source" / "main.c"
    if main_source.is_file():
        main_text = main_source.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bsprintf\s*\(\s*ack\b", main_text):
            errors.append("UART command ACK uses an unbounded fixed-buffer sprintf")

    for path in ACTIVE_SOURCES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, description in FORBIDDEN_PATTERNS:
            if pattern.search(text):
                errors.append(f"{path.relative_to(ROOT)} contains {description}")
    return errors


def parse_hex(path: pathlib.Path) -> tuple[list[str], int]:
    errors: list[str] = []
    upper_address = 0
    highest_address = -1
    saw_eof = False

    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        return [f"cannot read Intel HEX file {path}: {exc}"], highest_address

    for line_number, line in enumerate(lines, 1):
        if not line.startswith(":"):
            errors.append(f"{path}:{line_number}: record does not start with ':'")
            continue
        try:
            record = bytes.fromhex(line[1:])
        except ValueError:
            errors.append(f"{path}:{line_number}: invalid hexadecimal record")
            continue
        if len(record) < 5:
            errors.append(f"{path}:{line_number}: record is too short")
            continue
        length = record[0]
        if len(record) != length + 5:
            errors.append(f"{path}:{line_number}: byte count does not match record length")
            continue
        if sum(record) & 0xFF:
            errors.append(f"{path}:{line_number}: checksum mismatch")
            continue

        address = (record[1] << 8) | record[2]
        record_type = record[3]
        data = record[4:-1]
        if record_type == 0x00:
            absolute_end = upper_address + address + length
            if length:
                highest_address = max(highest_address, absolute_end - 1)
            if absolute_end > FLASH_SIZE:
                errors.append(
                    f"{path}:{line_number}: data exceeds CC2530F256 flash "
                    f"(end 0x{absolute_end:05X})"
                )
        elif record_type == 0x01:
            saw_eof = True
        elif record_type == 0x02 and len(data) == 2:
            upper_address = int.from_bytes(data, "big") << 4
        elif record_type == 0x04 and len(data) == 2:
            upper_address = int.from_bytes(data, "big") << 16

    if not saw_eof:
        errors.append(f"{path}: missing Intel HEX EOF record")
    if highest_address < 0:
        errors.append(f"{path}: contains no data records")
    return errors, highest_address


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static", action="store_true", help="run static portability checks")
    parser.add_argument("--hex", type=pathlib.Path, help="validate an Intel HEX image")
    args = parser.parse_args()

    if not args.static and args.hex is None:
        parser.error("select --static and/or --hex")

    errors: list[str] = []
    if args.static:
        errors.extend(check_static())
    highest_address = -1
    if args.hex is not None:
        hex_errors, highest_address = parse_hex(args.hex)
        errors.extend(hex_errors)

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    if args.static:
        print("PASS: static SDCC portability checks")
    if args.hex is not None:
        print(f"PASS: Intel HEX valid, highest flash address 0x{highest_address:05X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
