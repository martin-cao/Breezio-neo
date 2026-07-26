"""DNS-SD (Bonjour/mDNS) advertisement for the Wi-Fi/TCP gateway listener.

Publishes `_breezio-gateway._tcp.local` so ESP32 sensors on the LAN can find
the gateway's host/port without a compiled-in IP. Requires the optional
`zeroconf` package; if it is not installed the gateway logs a warning and
keeps running the plain TCP listener without advertising.
"""

from __future__ import annotations

import logging
import socket
from typing import Optional

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_breezio-gateway._tcp.local."


class GatewayServicePublisher:
    """Publishes the gateway's DNS-SD service for the lifetime of `start()`/`stop()`.

    Safe to call `start()` even when `zeroconf` is missing or registration fails:
    it logs a warning and leaves the publisher inert, so the caller doesn't need
    to special-case the "no mDNS available" path.
    """

    def __init__(self, *, gateway_id: str, port: int, protocol: str = "brz2", host: Optional[str] = None):
        self.gateway_id = gateway_id
        self.port = port
        self.protocol = protocol
        self.host = host
        self._zeroconf = None
        self._info = None

    def start(self) -> None:
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ModuleNotFoundError:
            logger.warning(
                "zeroconf not installed; skipping %s advertisement (sensors must use "
                "BREEZIO_GATEWAY_HOST/BREEZIO_GATEWAY_PORT fallback)",
                SERVICE_TYPE,
            )
            return

        try:
            address = self.host or _local_ipv4()
            info = ServiceInfo(
                SERVICE_TYPE,
                f"{self.gateway_id}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(address)],
                port=self.port,
                properties={"protocol": self.protocol, "gateway_id": self.gateway_id},
            )
            zc = Zeroconf()
            zc.register_service(info)
        except Exception:
            logger.warning("failed to publish %s service; continuing without mDNS", SERVICE_TYPE, exc_info=True)
            return

        self._zeroconf = zc
        self._info = info
        logger.info("published %s as %s at %s:%s", SERVICE_TYPE, self.gateway_id, address, self.port)

    def stop(self) -> None:
        if self._zeroconf is None:
            return
        try:
            if self._info is not None:
                self._zeroconf.unregister_service(self._info)
            self._zeroconf.close()
        except Exception:
            logger.warning("failed to cleanly unregister %s service", SERVICE_TYPE, exc_info=True)
        finally:
            self._zeroconf = None
            self._info = None


def _local_ipv4() -> str:
    """Best-effort LAN IPv4 address (no packets sent; UDP socket is never connected)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
