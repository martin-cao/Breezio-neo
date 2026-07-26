from __future__ import annotations

import sys
import unittest
import unittest.mock
from unittest.mock import MagicMock

from breezio_gateway.discovery import SERVICE_TYPE, GatewayServicePublisher


class GatewayServicePublisherTests(unittest.TestCase):
    def test_start_registers_service_with_expected_metadata(self):
        fake_zeroconf_module = MagicMock()
        fake_zc_instance = MagicMock()
        fake_zeroconf_module.Zeroconf.return_value = fake_zc_instance
        fake_zeroconf_module.ServiceInfo.side_effect = lambda *a, **kw: MagicMock(args=a, kwargs=kw)

        publisher = GatewayServicePublisher(gateway_id="gw-smartwin-01", port=5568, protocol="brz2", host="192.0.2.40")
        with unittest.mock.patch.dict(sys.modules, {"zeroconf": fake_zeroconf_module}):
            publisher.start()

        self.assertTrue(fake_zc_instance.register_service.called)
        info_call = fake_zeroconf_module.ServiceInfo.call_args
        self.assertEqual(info_call.args[0], SERVICE_TYPE)
        self.assertEqual(info_call.args[1], f"gw-smartwin-01.{SERVICE_TYPE}")
        self.assertEqual(info_call.kwargs["port"], 5568)
        self.assertEqual(info_call.kwargs["properties"], {"protocol": "brz2", "gateway_id": "gw-smartwin-01"})

        publisher.stop()
        self.assertTrue(fake_zc_instance.unregister_service.called)
        self.assertTrue(fake_zc_instance.close.called)

    def test_start_without_zeroconf_module_does_not_raise(self):
        publisher = GatewayServicePublisher(gateway_id="gw-01", port=5568)
        with unittest.mock.patch.dict(sys.modules, {"zeroconf": None}):
            publisher.start()  # should log a warning and return quietly
        publisher.stop()  # no-op, must not raise

    def test_start_survives_registration_failure(self):
        fake_zeroconf_module = MagicMock()
        fake_zeroconf_module.Zeroconf.side_effect = OSError("no network interfaces")

        publisher = GatewayServicePublisher(gateway_id="gw-01", port=5568, host="127.0.0.1")
        with unittest.mock.patch.dict(sys.modules, {"zeroconf": fake_zeroconf_module}):
            publisher.start()  # must not raise
        publisher.stop()  # must not raise


if __name__ == "__main__":
    unittest.main()
