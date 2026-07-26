from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from .intelhex import IntelHexImage


@dataclass(frozen=True)
class FlashPlan:
    command: list[str]
    image_size: int
    lowest_address: int
    highest_address: int


class CcToolFlasher:
    def __init__(self, cc_tool: str = "cc-tool", target_name: str = "CC2530"):
        self.cc_tool = cc_tool
        self.target_name = target_name

    def probe_command(self) -> list[str]:
        return [self.cc_tool, "--test"]

    def flash_plan(self, firmware: Path, *, erase: bool = True, write: bool = True, verify: bool = True) -> FlashPlan:
        image = IntelHexImage.from_file(firmware)
        image.validate_range(0xFFFF)
        command = [self.cc_tool, "--name", self.target_name]
        if erase:
            command.append("--erase")
        if write:
            command.extend(["--write", str(firmware)])
        if verify:
            command.extend(["--verify", "read"])
        return FlashPlan(command, image.size, image.lowest_address, image.highest_address)

    def ensure_available(self) -> None:
        if shutil.which(self.cc_tool) is None:
            raise FileNotFoundError(f"{self.cc_tool!r} not found on PATH")

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.ensure_available()
        return subprocess.run(command, check=False, text=True, capture_output=True)

