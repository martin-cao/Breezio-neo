"""Wi-Fi/TCP sensor transport.

The preferred topology is sensor-initiated: sensors join the LAN themselves
(STA mode, DHCP), connect to the gateway's TCP listener, and stream their
existing line protocol ($BRZ2 or the legacy @NODE/~BZ1 text frames). The gateway
also keeps the older dial-out transport for compatibility with previous demos.

Every received line feeds through the same SerialProtocolParser /
MqttMessageFactory pipeline as the serial paths, so downstream MQTT topics,
backend ingestion, and ACK contracts are unchanged.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional, Union

from .protocol import (
    BatchFrame,
    CommandAck,
    NodeReading,
    SerialProtocolParser,
    brz2_checksum,
    brz2_command_short_id,
    command_to_brz2_frame,
    command_to_serial_frame,
)

logger = logging.getLogger(__name__)

DEFAULT_SENSOR_PORT = 5568
RECONNECT_DELAY_S = 3.0


@dataclass(frozen=True)
class SensorEndpoint:
    node_id: str
    host: str
    port: int = DEFAULT_SENSOR_PORT


def parse_endpoint_spec(spec: str, default_port: int = DEFAULT_SENSOR_PORT) -> SensorEndpoint:
    """Parse "node_id@ip[:port]" or "ip[:port]" (node_id defaults to the host)."""
    node_id, _, addr = spec.rpartition("@")
    host, _, port_text = addr.partition(":")
    if not host:
        raise ValueError(f"invalid sensor endpoint spec: {spec!r}")
    port = int(port_text) if port_text else default_port
    return SensorEndpoint(node_id=node_id or host, host=host, port=port)


def parse_listen_spec(spec: str, default_port: int = DEFAULT_SENSOR_PORT) -> tuple[str, int]:
    """Parse "host:port", ":port", or a bare port for gateway listen mode."""
    text = spec.strip()
    if not text:
        raise ValueError("empty Wi-Fi listen spec")
    if text.isdigit():
        return "0.0.0.0", int(text)
    host, sep, port_text = text.rpartition(":")
    if not sep:
        return text, default_port
    return host or "0.0.0.0", int(port_text)


def fetch_node_endpoints(
    backend_url: str,
    *,
    gateway_id: Optional[str] = None,
    token: Optional[str] = None,
    default_port: int = DEFAULT_SENSOR_PORT,
    timeout: float = 5.0,
) -> list[SensorEndpoint]:
    """Read registered nodes from the backend and keep the ones with an IP."""
    url = backend_url.rstrip("/") + "/api/nodes"
    if gateway_id:
        url += f"?gateway_id={gateway_id}"
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    endpoints = []
    for node in payload.get("data") or []:
        ip_address = node.get("ip_address")
        if not ip_address:
            continue
        endpoint = parse_endpoint_spec(str(ip_address), default_port)
        endpoints.append(SensorEndpoint(node_id=node["node_id"], host=endpoint.host, port=endpoint.port))
    return endpoints


class SensorSession:
    """One sensor's TCP link: line reader with reconnect, plus command writes."""

    def __init__(self, endpoint: SensorEndpoint, *, reconnect_delay: float = RECONNECT_DELAY_S, connect_timeout: float = 5.0):
        self.endpoint = endpoint
        self.reconnect_delay = reconnect_delay
        self.connect_timeout = connect_timeout
        self._sock: Optional[socket.socket] = None
        self._sock_lock = threading.Lock()
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()
        with self._sock_lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def send_line(self, line: str) -> None:
        if not line.endswith("\n"):
            line += "\n"
        with self._sock_lock:
            if self._sock is None:
                raise ConnectionError(f"sensor {self.endpoint.node_id} ({self.endpoint.host}:{self.endpoint.port}) is not connected")
            self._sock.sendall(line.encode("utf-8"))

    def lines(self):
        """Yield decoded lines forever, reconnecting on failure, until stop()."""
        buffer = b""
        while not self._stop.is_set():
            sock = self._connect()
            if sock is None:
                continue
            try:
                while not self._stop.is_set():
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("sensor closed the connection")
                    buffer += chunk
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        decoded = raw.decode("utf-8", errors="replace").rstrip("\r")
                        if decoded:
                            yield decoded
            except (OSError, ConnectionError) as exc:
                if not self._stop.is_set():
                    logger.warning("sensor %s link dropped (%s); reconnecting in %.1fs", self.endpoint.node_id, exc, self.reconnect_delay)
                with self._sock_lock:
                    self._sock = None
                try:
                    sock.close()
                except OSError:
                    pass
                buffer = b""
                self._stop.wait(self.reconnect_delay)

    def _connect(self) -> Optional[socket.socket]:
        try:
            sock = socket.create_connection((self.endpoint.host, self.endpoint.port), timeout=self.connect_timeout)
            sock.settimeout(None)
        except OSError as exc:
            logger.warning("cannot reach sensor %s at %s:%s (%s); retrying in %.1fs", self.endpoint.node_id, self.endpoint.host, self.endpoint.port, exc, self.reconnect_delay)
            self._stop.wait(self.reconnect_delay)
            return None
        with self._sock_lock:
            self._sock = sock
        logger.info("connected to sensor %s at %s:%s", self.endpoint.node_id, self.endpoint.host, self.endpoint.port)
        return sock


class AcceptedSensorSession:
    """One sensor-initiated TCP connection registered after HELLO identifies it."""

    def __init__(self, node_id: str, sock: socket.socket):
        self.node_id = node_id
        self._sock = sock
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            try:
                self._sock.close()
            except OSError:
                pass

    def send_line(self, line: str) -> None:
        if not line.endswith("\n"):
            line += "\n"
        with self._lock:
            self._sock.sendall(line.encode("utf-8"))


Frame = Union[NodeReading, BatchFrame, CommandAck]


class WifiTransport:
    """Owns one SensorSession + SerialProtocolParser per registered sensor.

    Parsed frames go to `sink` (already the internal shapes the MQTT factory
    understands). Control commands are routed to the target sensor's socket;
    $BRZ2 short ACK ids are translated back to the original backend command_id
    before the ACK reaches the sink, so the existing ACK path is untouched.
    """

    def __init__(
        self,
        endpoints: list[SensorEndpoint],
        sink: Callable[[Frame], None],
        *,
        gateway_id: str = "gw-01",
        dialect: str = "brz2",
        reading_source: str = "wifi_tcp",
        reconnect_delay: float = RECONNECT_DELAY_S,
    ):
        if dialect not in {"brz2", "breezio"}:
            raise ValueError(f"unsupported sensor dialect: {dialect}")
        self.sink = sink
        self.dialect = dialect
        self.gateway_id = gateway_id
        self.reading_source = reading_source
        self.sessions: dict[str, SensorSession] = {
            ep.node_id: SensorSession(ep, reconnect_delay=reconnect_delay) for ep in endpoints
        }
        self._short_ids: dict[str, str] = {}
        self._short_ids_lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for node_id, session in self.sessions.items():
            thread = threading.Thread(target=self._pump, args=(node_id, session), daemon=True, name=f"wifi-sensor-{node_id}")
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        for session in self.sessions.values():
            session.stop()

    def join(self, timeout: Optional[float] = None) -> None:
        for thread in self._threads:
            thread.join(timeout)

    def send_command(self, command: dict) -> None:
        """Encode a backend control command and write it to the target sensor."""
        target = str(command.get("target_node_id", ""))
        session = self.sessions.get(target)
        if session is None:
            raise KeyError(f"no Wi-Fi sensor registered for node {target!r}")
        if self.dialect == "brz2":
            command_id = str(command["command_id"])
            with self._short_ids_lock:
                self._short_ids[str(brz2_command_short_id(command_id))] = command_id
            session.send_line(command_to_brz2_frame(command))
        else:
            session.send_line(command_to_serial_frame(command))

    def _pump(self, node_id: str, session: SensorSession) -> None:
        parser = SerialProtocolParser(
            gateway_id=self.gateway_id,
            node_id=node_id,
            topology="direct",
            reading_source=self.reading_source,
        )
        for line in session.lines():
            frame = parser.feed_line(line)
            if frame is None:
                continue
            if isinstance(frame, CommandAck):
                frame = self._translate_ack(frame)
            self.sink(frame)

    def _translate_ack(self, ack: CommandAck) -> CommandAck:
        with self._short_ids_lock:
            original = self._short_ids.pop(ack.command_id, None)
        if original is None:
            return ack
        return CommandAck(
            command_id=original,
            node_id=ack.node_id,
            result=ack.result,
            state=ack.state,
            acked_at=ack.acked_at,
            raw_frame=ack.raw_frame,
        )


class WifiListenerTransport:
    """Gateway-side TCP server for sensor-initiated Wi-Fi links."""

    def __init__(
        self,
        host: str,
        port: int,
        sink: Callable[[Frame], None],
        *,
        gateway_id: str = "gw-01",
        dialect: str = "brz2",
        reading_source: str = "wifi_tcp",
    ):
        if dialect not in {"brz2", "breezio"}:
            raise ValueError(f"unsupported sensor dialect: {dialect}")
        self.host = host
        self.port = port
        self.sink = sink
        self.gateway_id = gateway_id
        self.dialect = dialect
        self.reading_source = reading_source
        self.sessions: dict[str, AcceptedSensorSession] = {}
        self._sessions_lock = threading.Lock()
        self._short_ids: dict[str, str] = {}
        self._short_ids_lock = threading.Lock()
        self._stop = threading.Event()
        self._server: Optional[socket.socket] = None
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen()
        server.settimeout(0.2)
        self.host, self.port = server.getsockname()[:2]
        self._server = server
        thread = threading.Thread(target=self._accept_loop, daemon=True, name="wifi-listener")
        thread.start()
        self._threads.append(thread)
        logger.info("Wi-Fi sensor listener accepting connections on %s:%s", self.host, self.port)

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        with self._sessions_lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for session in sessions:
            session.close()

    def join(self, timeout: Optional[float] = None) -> None:
        for thread in self._threads:
            thread.join(timeout)

    def send_command(self, command: dict) -> None:
        target = str(command.get("target_node_id", ""))
        with self._sessions_lock:
            session = self.sessions.get(target)
        if session is None:
            raise KeyError(f"no connected Wi-Fi sensor for node {target!r}")
        if self.dialect == "brz2":
            command_id = str(command["command_id"])
            with self._short_ids_lock:
                self._short_ids[str(brz2_command_short_id(command_id))] = command_id
            session.send_line(command_to_brz2_frame(command))
        else:
            session.send_line(command_to_serial_frame(command))

    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                sock, peer = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    logger.exception("Wi-Fi listener socket failed")
                return
            sock.settimeout(None)
            thread = threading.Thread(target=self._handle_socket, args=(sock, peer), daemon=True, name=f"wifi-client-{peer[0]}:{peer[1]}")
            thread.start()
            self._threads.append(thread)

    def _handle_socket(self, sock: socket.socket, peer) -> None:
        logger.info("Wi-Fi sensor connected from %s:%s", peer[0], peer[1])
        node_id: Optional[str] = None
        session: Optional[AcceptedSensorSession] = None
        parser: Optional[SerialProtocolParser] = None
        buffer = b""
        try:
            while not self._stop.is_set():
                chunk = sock.recv(4096)
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.decode("utf-8", errors="replace").rstrip("\r")
                    if not line:
                        continue
                    if node_id is None:
                        candidate = self._node_id_from_first_line(line, peer)
                        if candidate is None:
                            logger.warning("dropping pre-handshake Wi-Fi line from %s:%s: %r", peer[0], peer[1], line)
                            continue
                        node_id = candidate
                        session = AcceptedSensorSession(node_id, sock)
                        parser = SerialProtocolParser(
                            gateway_id=self.gateway_id,
                            node_id=node_id,
                            topology="direct",
                            reading_source=self.reading_source,
                        )
                        self._register_session(node_id, session)
                        if self.dialect == "brz2" and _brz2_hello_node_id(line) == node_id:
                            continue
                    assert parser is not None
                    frame = parser.feed_line(line)
                    if frame is None:
                        continue
                    if isinstance(frame, CommandAck):
                        frame = self._translate_ack(frame)
                    self.sink(frame)
        except (OSError, ConnectionError) as exc:
            if not self._stop.is_set():
                logger.warning("Wi-Fi sensor %s disconnected (%s)", node_id or f"{peer[0]}:{peer[1]}", exc)
        finally:
            if node_id is not None and session is not None:
                with self._sessions_lock:
                    if self.sessions.get(node_id) is session:
                        self.sessions.pop(node_id, None)
            try:
                sock.close()
            except OSError:
                pass

    def _node_id_from_first_line(self, line: str, peer) -> Optional[str]:
        if self.dialect == "brz2":
            return _brz2_hello_node_id(line)
        if line.startswith("@NODE,"):
            parts = line.split(",")
            if len(parts) > 1 and parts[1]:
                return parts[1]
        return None

    def _register_session(self, node_id: str, session: AcceptedSensorSession) -> None:
        with self._sessions_lock:
            old = self.sessions.get(node_id)
            self.sessions[node_id] = session
        if old is not None:
            old.close()
        logger.info("registered Wi-Fi sensor %s", node_id)

    def _translate_ack(self, ack: CommandAck) -> CommandAck:
        with self._short_ids_lock:
            original = self._short_ids.pop(ack.command_id, None)
        if original is None:
            return ack
        return CommandAck(
            command_id=original,
            node_id=ack.node_id,
            result=ack.result,
            state=ack.state,
            acked_at=ack.acked_at,
            raw_frame=ack.raw_frame,
        )


def _brz2_hello_node_id(line: str) -> Optional[str]:
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
        return None
    fields = body.split(",")
    if len(fields) == 4 and fields[:2] == ["BRZ2", "HELLO"] and fields[3]:
        return fields[3]
    return None
