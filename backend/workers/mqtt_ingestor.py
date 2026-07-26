from __future__ import annotations

import argparse
import json
import os

from breezio_backend.postgres_store import PostgresBackendStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Breezio MQTT-to-database ingestor")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--host", default=os.getenv("MQTT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required for MQTT ingestion")

    try:
        import paho.mqtt.client as mqtt
    except ModuleNotFoundError as exc:
        raise SystemExit("paho-mqtt is required for MQTT ingestion") from exc

    store = PostgresBackendStore(args.database_url)

    def on_message(client, userdata, message):
        payload = json.loads(message.payload.decode("utf-8"))
        if message.topic.endswith("/batch"):
            store.ingest_telemetry_batch(payload)
        elif message.topic.endswith("/command_ack"):
            store.ingest_command_ack(payload)

    client = mqtt.Client()
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.subscribe("breezio/gateways/+/batch", qos=1)
    client.subscribe("breezio/nodes/+/command_ack", qos=1)
    client.loop_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
