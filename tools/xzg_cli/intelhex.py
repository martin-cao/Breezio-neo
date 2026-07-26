from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HexSection:
    address: int
    data: bytes


class IntelHexImage:
    def __init__(self, sections: list[HexSection]):
        self.sections = sections

    @classmethod
    def from_file(cls, path: Path) -> "IntelHexImage":
        return cls.from_text(path.read_text(encoding="utf-8"))

    @classmethod
    def from_text(cls, content: str) -> "IntelHexImage":
        sections: list[HexSection] = []
        current: HexSection | None = None
        address_prefix = 0
        saw_eof = False

        for line_number, raw_line in enumerate(content.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            if not line.startswith(":"):
                raise ValueError(f"line {line_number}: missing ':'")
            if len(line) % 2 != 1:
                raise ValueError(f"line {line_number}: odd record length")

            byte_count = int(line[1:3], 16)
            expected_len = 11 + byte_count * 2
            if len(line) != expected_len:
                raise ValueError(f"line {line_number}: expected length {expected_len}, got {len(line)}")

            address = int(line[3:7], 16)
            record_type = int(line[7:9], 16)
            data = bytes.fromhex(line[9 : 9 + byte_count * 2])
            checksum = int(line[9 + byte_count * 2 : 11 + byte_count * 2], 16)
            if ((byte_count + (address >> 8) + (address & 0xFF) + record_type + sum(data) + checksum) & 0xFF) != 0:
                raise ValueError(f"line {line_number}: checksum mismatch")

            if record_type == 0x00:
                full_address = address_prefix | address
                if current and current.address + len(current.data) == full_address:
                    current = HexSection(current.address, current.data + data)
                    sections[-1] = current
                else:
                    current = HexSection(full_address, data)
                    sections.append(current)
            elif record_type == 0x01:
                saw_eof = True
                break
            elif record_type == 0x02:
                if len(data) != 2:
                    raise ValueError(f"line {line_number}: invalid segment address")
                address_prefix = int.from_bytes(data, "big") << 4
            elif record_type == 0x04:
                if len(data) != 2:
                    raise ValueError(f"line {line_number}: invalid linear address")
                address_prefix = int.from_bytes(data, "big") << 16

        if not saw_eof:
            raise ValueError("missing EOF record")
        if not sections:
            raise ValueError("HEX file contains no data")
        return cls(sections)

    @property
    def lowest_address(self) -> int:
        return min(section.address for section in self.sections)

    @property
    def highest_address(self) -> int:
        return max(section.address + len(section.data) - 1 for section in self.sections)

    @property
    def size(self) -> int:
        return sum(len(section.data) for section in self.sections)

    def validate_range(self, max_address: int) -> None:
        if self.lowest_address < 0 or self.highest_address > max_address:
            raise ValueError(f"HEX address range 0x{self.lowest_address:05X}..0x{self.highest_address:05X} exceeds 0x{max_address:05X}")

