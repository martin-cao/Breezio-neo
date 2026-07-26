import json
import tempfile
from pathlib import Path
import unittest

from xzg_cli.flasher import CcToolFlasher
from xzg_cli.intelhex import IntelHexImage
from xzg_cli.ti_cc_debugger import image_from_hex


SAMPLE_HEX = """:100000000102030405060708090A0B0C0D0E0F1068
:00000001FF
"""


class XzgCliTest(unittest.TestCase):
    def test_intel_hex_parser(self):
        image = IntelHexImage.from_text(SAMPLE_HEX)
        self.assertEqual(image.size, 16)
        self.assertEqual(image.lowest_address, 0)
        self.assertEqual(image.highest_address, 15)

    def test_flash_plan_is_dry_run_safe_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            firmware = Path(tmp) / "fw.hex"
            firmware.write_text(SAMPLE_HEX, encoding="utf-8")
            plan = CcToolFlasher(cc_tool="cc-tool", target_name="CC2530").flash_plan(firmware)
            self.assertEqual(plan.command[:3], ["cc-tool", "--name", "CC2530"])
            self.assertIn("--erase", plan.command)
            self.assertIn("--write", plan.command)
            self.assertIn("--verify", plan.command)

    def test_ti_image_from_hex_pads_to_flash_block(self):
        image = image_from_hex(IntelHexImage.from_text(SAMPLE_HEX), block_size=1024)
        self.assertEqual(len(image), 1024)
        self.assertEqual(image[:16], bytes(range(1, 17)))
        self.assertEqual(image[16:], b"\xff" * (1024 - 16))


if __name__ == "__main__":
    unittest.main()
