from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .bridge import SerialControl, XzgBridgeClient
from .flasher import CcToolFlasher
from .intelhex import IntelHexImage
from .ti_cc_debugger import flash_ti_debugger, probe_ti_debugger


class XzgCli:
    def run(self, argv: list[str] | None = None) -> int:
        args = self._parser().parse_args(argv)
        return args.handler(args)

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Breezio/XZG command-line flashing helper")
        sub = parser.add_subparsers(dest="command", required=True)

        check = sub.add_parser("check-hex", help="validate an Intel HEX firmware image")
        check.add_argument("firmware", type=Path)
        check.set_defaults(handler=self.check_hex)

        probe = sub.add_parser("probe-cc", help="run cc-tool --test")
        probe.add_argument("--cc-tool", default="cc-tool")
        probe.set_defaults(handler=self.probe_cc)

        flash = sub.add_parser("flash-cc2530", help="prepare or execute a CC2530 cc-tool flash command")
        flash.add_argument("firmware", type=Path)
        flash.add_argument("--cc-tool", default="cc-tool")
        flash.add_argument("--target-name", default="CC2530")
        flash.add_argument("--no-erase", action="store_true")
        flash.add_argument("--no-write", action="store_true")
        flash.add_argument("--no-verify", action="store_true")
        flash.add_argument("--execute", action="store_true", help="actually run cc-tool; default is dry-run")
        flash.set_defaults(handler=self.flash_cc2530)

        probe_ti = sub.add_parser("probe-ti", help="probe a TI CC Debugger or SmartRF04EB via libusb")
        probe_ti.add_argument("--no-ieee", action="store_true", help="skip target IEEE read")
        probe_ti.set_defaults(handler=self.probe_ti)

        flash_ti = sub.add_parser("flash-ti", help="flash CC2530 through TI CC Debugger/SmartRF04EB via libusb")
        flash_ti.add_argument("firmware", type=Path)
        flash_ti.add_argument("--no-erase", action="store_true")
        flash_ti.add_argument("--no-write", action="store_true")
        flash_ti.add_argument("--no-verify", action="store_true")
        flash_ti.set_defaults(handler=self.flash_ti)

        mdns = sub.add_parser("bridge-discover", help="query an XZG-MT bridge /mdns endpoint")
        mdns.add_argument("--bridge", default="http://127.0.0.1:8765")
        mdns.add_argument("--types", default="_xzg-mt._tcp.local")
        mdns.add_argument("--timeout-ms", type=int, default=2000)
        mdns.set_defaults(handler=self.bridge_discover)

        serial = sub.add_parser("bridge-serial", help="call an XZG-MT bridge /sc serial-control endpoint")
        serial.add_argument("path")
        serial.add_argument("--bridge", default="http://127.0.0.1:8765")
        serial.add_argument("--baud", type=int)
        serial.add_argument("--dtr", choices=["0", "1"])
        serial.add_argument("--rts", choices=["0", "1"])
        serial.set_defaults(handler=self.bridge_serial)
        return parser

    def check_hex(self, args: argparse.Namespace) -> int:
        image = IntelHexImage.from_file(args.firmware)
        image.validate_range(0xFFFF)
        print(json.dumps(_image_summary(image), indent=2))
        return 0

    def probe_cc(self, args: argparse.Namespace) -> int:
        flasher = CcToolFlasher(args.cc_tool)
        result = flasher.run(flasher.probe_command())
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode

    def flash_cc2530(self, args: argparse.Namespace) -> int:
        flasher = CcToolFlasher(args.cc_tool, args.target_name)
        plan = flasher.flash_plan(
            args.firmware,
            erase=not args.no_erase,
            write=not args.no_write,
            verify=not args.no_verify,
        )
        print(
            json.dumps(
                {
                    "dry_run": not args.execute,
                    "command": plan.command,
                    "image_size": plan.image_size,
                    "address_range": f"0x{plan.lowest_address:05X}..0x{plan.highest_address:05X}",
                },
                indent=2,
            )
        )
        if not args.execute:
            return 0
        result = flasher.run(plan.command)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode

    def probe_ti(self, args: argparse.Namespace) -> int:
        info = probe_ti_debugger(read_ieee=not args.no_ieee)
        print(
            json.dumps(
                {
                    "programmer": info.programmer,
                    "chip_id": f"0x{info.chip_id:04X}",
                    "fw_version": f"0x{info.fw_version:04X}",
                    "fw_revision": f"0x{info.fw_revision:04X}",
                    "ieee_address": info.ieee_address,
                },
                indent=2,
            )
        )
        return 0

    def flash_ti(self, args: argparse.Namespace) -> int:
        flash_ti_debugger(
            args.firmware,
            erase=not args.no_erase,
            write=not args.no_write,
            verify=not args.no_verify,
        )
        return 0

    def bridge_discover(self, args: argparse.Namespace) -> int:
        result = XzgBridgeClient(args.bridge).discover(args.types, args.timeout_ms)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    def bridge_serial(self, args: argparse.Namespace) -> int:
        result = XzgBridgeClient(args.bridge).serial_control(
            SerialControl(
                path=args.path,
                baud=args.baud,
                dtr=None if args.dtr is None else args.dtr == "1",
                rts=None if args.rts is None else args.rts == "1",
            )
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0


def _image_summary(image: IntelHexImage) -> dict:
    return {
        "sections": len(image.sections),
        "image_size": image.size,
        "lowest_address": f"0x{image.lowest_address:05X}",
        "highest_address": f"0x{image.highest_address:05X}",
    }


def main() -> int:
    return XzgCli().run()


if __name__ == "__main__":
    raise SystemExit(main())
