from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Protocol

from breezio_backend.postgres_store import PostgresBackendStore


class OutboxStore(Protocol):
    def list_pending_outbox_messages(self, limit: int = 50) -> list[dict]:
        ...

    def mark_outbox_message_sent(self, message_id: int) -> dict | None:
        ...

    def mark_outbox_message_failed(self, message_id: int, retry_delay_seconds: int = 5) -> dict | None:
        ...


class MqttPublisher:
    def __init__(self, host: str, port: int) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ModuleNotFoundError as exc:
            raise SystemExit("paho-mqtt is required for control dispatching") from exc

        self._mqtt = mqtt
        self._client = mqtt.Client()
        self._client.connect(host, port, keepalive=30)
        self._client.loop_start()

    def publish(self, topic: str, payload: str) -> None:
        result = self._client.publish(topic, payload, qos=1)
        result.wait_for_publish()
        if result.rc != self._mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed for {topic}: rc={result.rc}")

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


def dispatch_once(
    store: OutboxStore,
    publisher: Any,
    *,
    limit: int = 50,
    retry_delay_seconds: int = 5,
) -> dict[str, int]:
    counts = {"sent": 0, "failed": 0}
    for message in store.list_pending_outbox_messages(limit=limit):
        payload = json.dumps(message["payload_json"], separators=(",", ":"), ensure_ascii=False)
        try:
            publisher.publish(message["topic"], payload)
        except Exception:
            store.mark_outbox_message_failed(message["id"], retry_delay_seconds=retry_delay_seconds)
            counts["failed"] += 1
            continue
        store.mark_outbox_message_sent(message["id"])
        counts["sent"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Breezio control outbox MQTT dispatcher")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--host", default=os.getenv("MQTT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--retry-delay-seconds", type=int, default=5)
    parser.add_argument("--once", action="store_true", help="dispatch one batch and exit")
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required for control dispatching")

    store = PostgresBackendStore(args.database_url)
    publisher = MqttPublisher(args.host, args.port)
    try:
        while True:
            dispatch_once(store, publisher, limit=args.batch_size, retry_delay_seconds=args.retry_delay_seconds)
            if args.once:
                return 0
            time.sleep(args.poll_interval)
    finally:
        publisher.close()


if __name__ == "__main__":
    raise SystemExit(main())
