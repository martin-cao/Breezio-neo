#!/usr/bin/env python3
"""Create a self-contained Windows flashing handoff package."""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import zipfile


INSTRUCTIONS = """Breezio Sensor SDCC Flash Package
==================================

Target: Texas Instruments CC2530F256
Image: breezio-sensor.hex

Required Windows tool:
TI SmartRF Flash Programmer (the legacy 8051 version, not Flash Programmer 2)

Procedure:
1. Connect the SmartRF04EB and the CC2530 target.
2. Start SmartRF Flash Programmer with administrator privileges.
3. Confirm that the target is detected as CC2530.
4. Select breezio-sensor.hex.
5. Choose erase, program, and verify.
6. Do not enable debug lock.
7. Reset the target.
8. Monitor UART0 at 38400 baud, 8N1.

Expected default telemetry frame:
~BZ1,<length_hex>,<payload_hex>,<crc16_hex>

After flashing, verify sensor readings and @CMD/@ACK lamp control:
@CMD,cmd-1,n1,LAMP,ON
@ACK,cmd-1,n1,OK,ON
@CMD,cmd-2,n1,LAMP,OFF
@ACK,cmd-2,n1,OK,OFF
"""


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hex", type=pathlib.Path, required=True)
    parser.add_argument("--memory", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()

    for path in (args.hex, args.memory):
        if not path.is_file():
            parser.error(f"missing build artifact: {path}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    checksum_line = f"{sha256(args.hex)}  breezio-sensor.hex\n"
    with zipfile.ZipFile(
        args.output, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.write(args.hex, "breezio-sensor.hex")
        archive.write(args.memory, "breezio-sensor.mem")
        archive.writestr("SHA256SUMS.txt", checksum_line)
        archive.writestr("FLASHING.txt", INSTRUCTIONS)

    print(f"created {args.output}")
    print(checksum_line, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
