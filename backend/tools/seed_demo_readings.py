"""Seed simulated multi-day sensor readings so the prediction pipeline has
something to train on without physical hardware.

All rows are tagged source="simulated" per docs/prediction-model-design.md
("模拟数据可以用于开发和演示，但报告中必须标明来源") — do not present this as
real lab data in the demo/report.

Usage:
    PYTHONPATH=backend uv run python backend/tools/seed_demo_readings.py \
        --node-ids n1 n2 --days 2
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from breezio_backend.postgres_store import PostgresBackendStore  # noqa: E402


def synthetic_record(node_id: str, seq: int, t: datetime, rng: random.Random) -> dict:
    minute_of_day = t.hour * 60 + t.minute
    temperature = 23.0 + 3.5 * math.sin(2 * math.pi * minute_of_day / 1440) + rng.gauss(0, 0.15)
    humidity = 55.0 - 8.0 * math.sin(2 * math.pi * minute_of_day / 1440) + rng.gauss(0, 0.6)
    if 7 <= t.hour <= 21:
        light = max(0.0, 400.0 * math.sin(math.pi * (t.hour - 7) / 14) + rng.gauss(0, 8))
    else:
        light = max(0.0, rng.gauss(2, 1))
    return {
        "node_id": node_id,
        "seq": seq,
        "temperature": round(temperature, 2),
        "humidity": round(max(20.0, min(95.0, humidity)), 2),
        "light": round(light, 1),
        "battery_mv": 3000 + rng.randint(-100, 100),
        "rssi": -55 + rng.randint(-15, 10),
        "source": "simulated",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--gateway-id", default="gw-demo")
    parser.add_argument("--node-ids", nargs="+", default=["n1"])
    parser.add_argument("--days", type=float, default=2.0)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required for seeding demo readings")

    store = PostgresBackendStore(args.database_url)
    rng = random.Random(args.seed)

    total_steps = int(args.days * 24 * 3600 / args.interval_seconds)
    start = datetime.now(timezone.utc) - timedelta(seconds=total_steps * args.interval_seconds)

    inserted = 0
    for step in range(total_steps):
        t = start + timedelta(seconds=step * args.interval_seconds)
        records = [synthetic_record(node_id, step, t, rng) for node_id in args.node_ids]
        result = store.ingest_telemetry_batch(
            {
                "message_id": f"seed-{step}",
                "gateway_id": args.gateway_id,
                "batch_id": f"seed-batch-{step}",
                "captured_at": t.isoformat(),
                "source": "simulated",
                "records": records,
            }
        )
        inserted += result["inserted_metrics"]

    destination = urlparse(args.database_url)
    print(f"Seeded {inserted} readings for nodes {args.node_ids} over {args.days} days into {destination.hostname}/{destination.path.lstrip('/')}")
    print("Rows are tagged source='simulated' — label accordingly in any report.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
