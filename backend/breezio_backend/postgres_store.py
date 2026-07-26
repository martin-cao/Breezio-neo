from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Optional
from urllib.parse import quote
from uuid import uuid4

from psycopg.rows import dict_row

from . import services
from .auth import hash_password, verify_password
from .camera_sources import normalize_camera_payload
from .node_status import apply_effective_node_status
from .prediction import (
    FEATURE_COLUMNS,
    MODEL_NAMES,
    FORECAST_GRID_STEPS,
    MIN_WINDOW,
    ROLLING_HISTORY_MULTIPLIER,
    forecast_bucket_seconds,
    predict_next,
    predict_rolling_series,
    prediction_feature_description,
    train_and_evaluate,
)


# Reading sources that come from actual hardware links. Everything else
# (serial_simulated, simulated, smoke, ...) marks a prediction run as simulated.
REAL_READING_SOURCES = {
    "mqtt",
    "serial",
    "serial_batch",
    "serial_direct",
    "serial_hardware",
    "serial_hex",
    "serial_legacy",
    "serial_router",
    "wifi_tcp",
}

METRIC_UNITS = {
    "temperature": "C",
    "humidity": "%",
    # Raw photoresistor index (0-100, lower = brighter) — not calibrated lux.
    "light": "",
    "air_quality": "mV",
}


def reading_bucket_seconds(start: Optional[str], end: Optional[str], limit: int) -> Optional[int]:
    """Bucket width so a ranged /api/readings query spans its whole [start, end]
    window in at most `limit` points, instead of just the newest `limit` raw rows
    (which for a ~1Hz sensor made every range button show the same recent tail)."""
    if not start or not end or limit <= 0:
        return None
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    span_seconds = (end_dt - start_dt).total_seconds()
    if span_seconds <= 0:
        return None
    return max(1, -(-int(span_seconds) // limit))

DEFAULT_SETTINGS = {
    "theme_mode": "system",
    "notifications": {
        "critical_alerts": True,
        "warning_alerts": True,
        "vacant_energy_waste": True,
    },
    "vision": {
        "preview_fps": 30,
        "preview_width": 960,
        "inference_hz": 5,
        "face_inference_hz": 1,
        "person_confidence": 0.25,
        "anomaly_inference_hz": 0.1,
        "caption_periodic_seconds": 300,
        "caption_on_anomaly": True,
    },
    # LLM chatbot backend: any OpenAI-compatible /chat/completions server
    # (Ollama, LM Studio, llama.cpp, hosted APIs). The API key deliberately
    # lives in the BREEZIO_LLM_API_KEY env var, never in this blob (which
    # round-trips to the browser).
    "chatbot": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "provider_label": "ollama-local",
    },
    "default_view": "dashboard",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PostgresBackendStore:
    """PostgreSQL-backed application store for the production data plane."""

    def __init__(self, database_url: str, rustfs_public_url: Optional[str] = None) -> None:
        self.database_url = database_url
        self.rustfs_public_url = (rustfs_public_url or os.getenv("RUSTFS_PUBLIC_URL") or "http://127.0.0.1:9000").rstrip("/")

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _public_user(self, row: Optional[dict]) -> Optional[dict]:
        if row is None:
            return None
        item = dict(row)
        item.pop("password_hash", None)
        item.pop("settings", None)
        return item

    def _current_user(self, cur) -> Optional[dict]:
        cur.execute("SELECT * FROM users WHERE status = 'active' ORDER BY (role = 'admin') DESC, id LIMIT 1")
        return cur.fetchone()

    def list_users(self, role: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
        clauses = []
        args: list[Any] = []
        if role:
            clauses.append("role = %s")
            args.append(role)
        if status:
            clauses.append("status = %s")
            args.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM users {where} ORDER BY created_at DESC, id DESC", args)
                return [item for row in cur.fetchall() if (item := self._public_user(row)) is not None]

    def create_user(self, payload: dict[str, Any]) -> dict:
        settings = {**DEFAULT_SETTINGS, **payload.get("settings", {})}
        password_hash = hash_password(payload["password"]) if payload.get("password") else payload.get("password_hash")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users(name, email, password_hash, role, status, settings, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, NOW())
                    RETURNING *
                    """,
                    (
                        payload["name"],
                        payload["email"],
                        password_hash,
                        payload["role"],
                        payload.get("status", "invited"),
                        json.dumps(settings, separators=(",", ":")),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return self._public_user(row) or {}

    def get_user_by_email(self, email: str) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE lower(email) = lower(%s)", (email,))
                return self._public_user(cur.fetchone())

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        return self.get_user(user_id)

    def authenticate_user(self, email: str, password: str, bootstrap_password: Optional[str] = None) -> Optional[dict]:
        import hmac

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE lower(email) = lower(%s) AND status = 'active'", (email,))
                row = cur.fetchone()
                if row is None:
                    return None
                if not verify_password(row.get("password_hash"), password):
                    if not (bootstrap_password and not row.get("password_hash") and hmac.compare_digest(password, bootstrap_password)):
                        return None
                    cur.execute("UPDATE users SET password_hash = %s, updated_at = NOW() WHERE id = %s", (hash_password(password), row["id"]))
                cur.execute("UPDATE users SET last_login = NOW(), updated_at = NOW() WHERE id = %s RETURNING *", (row["id"],))
                updated = cur.fetchone()
            conn.commit()
        return self._public_user(updated)

    def get_user(self, user_id: int) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                return self._public_user(cur.fetchone())

    def update_user(self, user_id: int, payload: dict[str, Any]) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                existing = cur.fetchone()
                if existing is None:
                    return None
                cur.execute(
                    """
                    UPDATE users
                    SET name = %s, email = %s, role = %s, status = %s, password_hash = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        payload.get("name", existing["name"]),
                        payload.get("email", existing["email"]),
                        payload.get("role", existing["role"]),
                        payload.get("status", existing["status"]),
                        hash_password(payload["password"]) if payload.get("password") else existing["password_hash"],
                        user_id,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return self._public_user(row)

    def disable_user(self, user_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET status = 'disabled', updated_at = NOW() WHERE id = %s", (user_id,))
                changed = cur.rowcount > 0
            conn.commit()
        return changed

    def get_current_user(self, user_id: Optional[int] = None) -> Optional[dict]:
        if user_id is not None:
            return self.get_user(user_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                return self._public_user(self._current_user(cur))

    def update_current_user(self, payload: dict[str, Any], user_id: Optional[int] = None) -> Optional[dict]:
        current = self.get_current_user(user_id)
        if current is None:
            return None
        return self.update_user(current["id"], {key: payload[key] for key in ("name", "email", "password") if key in payload})

    def get_current_user_settings(self, user_id: Optional[int] = None) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if user_id is not None:
                    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                    row = cur.fetchone()
                else:
                    row = self._current_user(cur)
                if row is None:
                    return dict(DEFAULT_SETTINGS)
                return {**DEFAULT_SETTINGS, **dict(row.get("settings") or {})}

    def update_current_user_settings(self, payload: dict[str, Any], user_id: Optional[int] = None) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if user_id is not None:
                    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                    row = cur.fetchone()
                else:
                    row = self._current_user(cur)
                if row is None:
                    return dict(DEFAULT_SETTINGS)
                current = {**DEFAULT_SETTINGS, **dict(row.get("settings") or {})}
                merged = {
                    **current,
                    **{key: value for key, value in payload.items() if key not in {"notifications", "vision", "chatbot"}},
                    "notifications": {**current.get("notifications", {}), **payload.get("notifications", {})},
                    "vision": {**current.get("vision", {}), **payload.get("vision", {})},
                    "chatbot": {**current.get("chatbot", {}), **payload.get("chatbot", {})},
                }
                cur.execute(
                    "UPDATE users SET settings = %s::jsonb, updated_at = NOW() WHERE id = %s",
                    (json.dumps(merged, separators=(",", ":")), row["id"]),
                )
            conn.commit()
        return merged

    def append_chat_message(self, session_id: str, role: str, content: Optional[str], tool_calls: Optional[list] = None) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_messages(session_id, role, content, tool_calls_json)
                    VALUES (%s, %s, %s, %s::jsonb)
                    RETURNING *
                    """,
                    (session_id, role, content, json.dumps(tool_calls) if tool_calls else None),
                )
                row = cur.fetchone()
            conn.commit()
        return self._chat_row_to_dict(row)

    def list_chat_messages(self, session_id: str, limit: int = 200) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM chat_messages WHERE session_id = %s ORDER BY id ASC LIMIT %s",
                    (session_id, limit),
                )
                return [self._chat_row_to_dict(row) for row in cur.fetchall()]

    def _chat_row_to_dict(self, row: dict[str, Any]) -> dict:
        item = dict(row)
        item["tool_calls"] = item.pop("tool_calls_json", None) or []
        return item

    def list_devices(self, status: Optional[str] = None) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute("SELECT * FROM logical_devices WHERE status = %s ORDER BY created_at DESC, id DESC", (status,))
                else:
                    cur.execute("SELECT * FROM logical_devices ORDER BY created_at DESC, id DESC")
                return [dict(row) for row in cur.fetchall()]

    def create_device(self, payload: dict[str, Any]) -> dict:
        payload = normalize_camera_payload(payload) if payload.get("device_type") == "camera" else payload
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO logical_devices(name, device_type, location, status, client_id, protocol, ip_address, port, source_uri, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    RETURNING *
                    """,
                    (
                        payload["name"],
                        payload["device_type"],
                        payload.get("location"),
                        payload.get("status", "offline"),
                        payload.get("client_id"),
                        payload.get("protocol"),
                        payload.get("ip_address"),
                        payload.get("port"),
                        payload.get("source_uri"),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return dict(row)

    def get_device(self, device_id: int) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM logical_devices WHERE id = %s", (device_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def update_device(self, device_id: int, payload: dict[str, Any]) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM logical_devices WHERE id = %s", (device_id,))
                existing = cur.fetchone()
                if existing is None:
                    return None
                merged_type = payload.get("device_type", existing["device_type"])
                if merged_type == "camera":
                    payload = normalize_camera_payload(payload, existing=dict(existing))
                cur.execute(
                    """
                    UPDATE logical_devices
                    SET name = %s, device_type = %s, location = %s, status = %s, client_id = %s,
                        protocol = %s, ip_address = %s, port = %s, source_uri = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        payload.get("name", existing["name"]),
                        merged_type,
                        payload.get("location", existing["location"]),
                        payload.get("status", existing["status"]),
                        payload.get("client_id", existing["client_id"]),
                        payload.get("protocol", existing["protocol"]),
                        payload.get("ip_address", existing["ip_address"]),
                        payload.get("port", existing["port"]),
                        payload.get("source_uri", existing["source_uri"]),
                        device_id,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return dict(row) if row else None

    def disable_device(self, device_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE logical_devices SET status = 'offline', updated_at = NOW() WHERE id = %s", (device_id,))
                changed = cur.rowcount > 0
            conn.commit()
        return changed

    def delete_device(self, device_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM logical_devices WHERE id = %s", (device_id,))
                changed = cur.rowcount > 0
            conn.commit()
        return changed

    def register_gateway(self, gateway_id: str, payload: dict[str, Any]) -> dict:
        timestamp = now_iso()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO gateways(gateway_id, name, location, status, ip_address, version, last_seen_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT(gateway_id) DO UPDATE SET
                      name=COALESCE(excluded.name, gateways.name),
                      location=COALESCE(excluded.location, gateways.location),
                      status=excluded.status,
                      ip_address=COALESCE(excluded.ip_address, gateways.ip_address),
                      version=COALESCE(excluded.version, gateways.version),
                      last_seen_at=excluded.last_seen_at,
                      updated_at=excluded.updated_at
                    RETURNING *
                    """,
                    (
                        gateway_id,
                        payload.get("name"),
                        payload.get("location"),
                        payload.get("status", "online"),
                        payload.get("ip_address"),
                        payload.get("version"),
                        payload.get("last_seen_at", timestamp),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return dict(row)

    def list_gateways(self) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM gateways ORDER BY last_seen_at DESC NULLS LAST, gateway_id")
                return [dict(row) for row in cur.fetchall()]

    def list_nodes(self, gateway_id: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
        clauses = []
        args: list[Any] = []
        if gateway_id:
            clauses.append("gateway_id = %s")
            args.append(gateway_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM zigbee_nodes {where} ORDER BY node_id", args)
                rows = [apply_effective_node_status(dict(row)) for row in cur.fetchall()]
        if status:
            return [row for row in rows if row["status"] == status]
        return rows

    def register_node(self, payload: dict[str, Any]) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO gateways(gateway_id, status, last_seen_at, updated_at)
                    VALUES (%s, 'online', NOW(), NOW())
                    ON CONFLICT(gateway_id) DO NOTHING
                    """,
                    (payload["gateway_id"],),
                )
                cur.execute(
                    """
                    INSERT INTO zigbee_nodes(node_id, gateway_id, parent_node_id, role, area, ip_address, status, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT(node_id) DO UPDATE SET
                      gateway_id=excluded.gateway_id,
                      parent_node_id=excluded.parent_node_id,
                      role=excluded.role,
                      area=excluded.area,
                      ip_address=COALESCE(excluded.ip_address, zigbee_nodes.ip_address),
                      status=excluded.status,
                      updated_at=excluded.updated_at
                    RETURNING *
                    """,
                    (
                        payload["node_id"],
                        payload["gateway_id"],
                        payload.get("parent_node_id"),
                        payload.get("role", "sensor"),
                        payload.get("area"),
                        payload.get("ip_address"),
                        payload.get("status", "offline"),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return dict(row)

    def get_node(self, node_id: str) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM zigbee_nodes WHERE node_id = %s", (node_id,))
                row = cur.fetchone()
                return apply_effective_node_status(dict(row)) if row else None

    def ingest_telemetry_batch(self, payload: dict[str, Any]) -> dict:
        gateway_id = payload["gateway_id"]
        batch_id = str(payload.get("batch_id", "manual"))
        captured_at = payload.get("captured_at") or now_iso()
        records = payload.get("records") or []
        inserted = 0

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO gateways(gateway_id, status, last_seen_at, updated_at)
                    VALUES (%s, 'online', %s, NOW())
                    ON CONFLICT(gateway_id) DO UPDATE SET
                      status='online',
                      last_seen_at=excluded.last_seen_at,
                      updated_at=excluded.updated_at
                    """,
                    (gateway_id, captured_at),
                )
                for record in records:
                    node_id = record["node_id"]
                    cur.execute(
                        """
                        INSERT INTO zigbee_nodes(node_id, gateway_id, parent_node_id, role, status, battery_mv, rssi, last_seq, last_seen_at, updated_at)
                        VALUES (%s, %s, %s, %s, 'online', %s, %s, %s, %s, NOW())
                        ON CONFLICT(node_id) DO UPDATE SET
                          gateway_id=excluded.gateway_id,
                          parent_node_id=excluded.parent_node_id,
                          role=excluded.role,
                          status='online',
                          battery_mv=COALESCE(excluded.battery_mv, zigbee_nodes.battery_mv),
                          rssi=COALESCE(excluded.rssi, zigbee_nodes.rssi),
                          last_seq=COALESCE(excluded.last_seq, zigbee_nodes.last_seq),
                          last_seen_at=excluded.last_seen_at,
                          updated_at=excluded.updated_at
                        """,
                        (
                            node_id,
                            gateway_id,
                            record.get("parent_node_id"),
                            record.get("role", "sensor"),
                            record.get("battery_mv"),
                            record.get("rssi"),
                            record.get("seq"),
                            record.get("captured_at", captured_at),
                        ),
                    )
                    for metric in ("temperature", "humidity", "light", "air_quality"):
                        value = record.get(metric)
                        if value is None:
                            continue
                        message_id = f"{payload.get('message_id', uuid4())}:{node_id}:{record.get('seq')}:{metric}"
                        cur.execute(
                            """
                            INSERT INTO sensor_readings(
                              message_id, gateway_id, batch_id, node_id, seq, metric, value, unit,
                              rssi, battery_mv, flags, source, captured_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT(message_id) DO NOTHING
                            """,
                            (
                                message_id,
                                gateway_id,
                                batch_id,
                                node_id,
                                record.get("seq"),
                                metric,
                                float(value),
                                METRIC_UNITS[metric],
                                record.get("rssi"),
                                record.get("battery_mv"),
                                record.get("flags", 0),
                                record.get("source", payload.get("source", "mqtt")),
                                record.get("captured_at", captured_at),
                            ),
                        )
                        inserted += cur.rowcount
            conn.commit()
        alerts_fired = self._evaluate_alert_rules(records, captured_at)
        return {"gateway_id": gateway_id, "batch_id": batch_id, "records": len(records), "inserted_metrics": inserted, "alerts_fired": alerts_fired}

    def query_readings(
        self,
        node_id: Optional[str] = None,
        metric: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses = []
        args: list[Any] = []
        if node_id:
            clauses.append("node_id = %s")
            args.append(node_id)
        if metric:
            clauses.append("metric = %s")
            args.append(metric)
        if start:
            clauses.append("captured_at >= %s")
            args.append(start)
        if end:
            clauses.append("captured_at <= %s")
            args.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            with conn.cursor() as cur:
                bucket_seconds = reading_bucket_seconds(start, end, limit)
                if bucket_seconds is not None:
                    # Downsample to one (latest-in-bucket) row per fixed-width time
                    # bucket so every range button (2h/12h/24h/...) actually spans
                    # its own full window instead of the same tail of raw rows.
                    cur.execute(
                        f"""
                        SELECT * FROM (
                          SELECT DISTINCT ON (node_id, metric, bucket) *
                          FROM (
                            SELECT *, to_timestamp(floor(extract(epoch FROM captured_at) / %s) * %s) AS bucket
                            FROM sensor_readings {where}
                          ) t
                          ORDER BY node_id, metric, bucket, captured_at DESC
                        ) buckets
                        ORDER BY captured_at DESC, id DESC
                        LIMIT %s
                        """,
                        [bucket_seconds, bucket_seconds, *args, limit],
                    )
                else:
                    cur.execute(f"SELECT * FROM sensor_readings {where} ORDER BY captured_at DESC, id DESC LIMIT %s", [*args, limit])
                return [{k: v for k, v in dict(row).items() if k != "bucket"} for row in cur.fetchall()]

    def latest_reading_id(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM sensor_readings")
                return int(cur.fetchone()["max_id"])

    def readings_after(self, cursor_id: int, limit: int = 500) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM sensor_readings WHERE id > %s ORDER BY id ASC LIMIT %s",
                    (cursor_id, limit),
                )
                return [dict(row) for row in cur.fetchall()]

    def latest_readings(self) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, message_id, gateway_id, batch_id, node_id, seq, metric,
                           value, unit, rssi, battery_mv, flags, source, captured_at, created_at
                    FROM (
                      SELECT sr.*,
                             ROW_NUMBER() OVER (
                               PARTITION BY node_id, metric
                               ORDER BY captured_at DESC, id DESC
                             ) AS rn
                      FROM sensor_readings sr
                    ) latest
                    WHERE rn = 1
                    ORDER BY captured_at DESC, id DESC
                    """
                )
                return [dict(row) for row in cur.fetchall()]

    def create_control_command(self, payload: dict[str, Any]) -> dict:
        command_id = payload.get("command_id") or f"cmd-{uuid4().hex[:12]}"
        created_at = now_iso()
        target_node_id = payload["target_node_id"]
        desired_state = payload.get("desired_state")
        command = {
            "command_id": command_id,
            "source": payload.get("source", "manual"),
            "target_node_id": target_node_id,
            "target_device": payload["target_device"],
            "action": payload["action"],
            "desired_state": desired_state,
            "ttl_seconds": payload.get("ttl_seconds", 10),
            "reason": payload.get("reason"),
            "created_at": created_at,
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO control_commands(command_id, source, target_node_id, target_device, action, desired_state, reason, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, 'pending', %s)
                    """,
                    (
                        command_id,
                        command["source"],
                        target_node_id,
                        command["target_device"],
                        command["action"],
                        json.dumps(desired_state, separators=(",", ":")) if desired_state is not None else None,
                        command["reason"],
                        created_at,
                    ),
                )
                cur.execute(
                    "INSERT INTO outbox_messages(topic, payload_json) VALUES (%s, %s::jsonb)",
                    (f"breezio/nodes/{target_node_id}/command", json.dumps(command, separators=(",", ":"))),
                )
            conn.commit()
        return self.get_control_command(command_id) or {}

    def list_pending_outbox_messages(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM outbox_messages
                    WHERE status = 'pending'
                      AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                    ORDER BY created_at, id
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [dict(row) for row in cur.fetchall()]

    def mark_outbox_message_sent(self, message_id: int) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE outbox_messages
                    SET status = 'sent', sent_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (message_id,),
                )
                row = cur.fetchone()
                if row and str(row["topic"]).endswith("/command"):
                    cur.execute(
                        """
                        UPDATE control_commands
                        SET status = 'sent'
                        WHERE command_id = %s AND status = 'pending'
                        """,
                        (row["payload_json"].get("command_id"),),
                    )
            conn.commit()
        return dict(row) if row else None

    def mark_outbox_message_failed(self, message_id: int, retry_delay_seconds: int = 5) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE outbox_messages
                    SET
                      status = 'pending',
                      retry_count = retry_count + 1,
                      next_retry_at = NOW() + (%s * INTERVAL '1 second')
                    WHERE id = %s
                    RETURNING *
                    """,
                    (retry_delay_seconds, message_id),
                )
                row = cur.fetchone()
            conn.commit()
        return dict(row) if row else None

    def get_control_command(self, command_id: str) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM control_commands WHERE command_id = %s", (command_id,))
                command = cur.fetchone()
                if command is None:
                    return None
                item = dict(command)
                cur.execute("SELECT * FROM command_acks WHERE command_id = %s ORDER BY created_at DESC", (command_id,))
                item["acks"] = [dict(row) for row in cur.fetchall()]
                return item

    def list_control_commands(self, status: Optional[str] = None) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute("SELECT * FROM control_commands WHERE status = %s ORDER BY created_at DESC", (status,))
                else:
                    cur.execute("SELECT * FROM control_commands ORDER BY created_at DESC")
                return [dict(row) for row in cur.fetchall()]

    def ingest_command_ack(self, payload: dict[str, Any]) -> dict:
        command_id = payload["command_id"]
        result = payload["result"].upper()
        timestamp = payload.get("acked_at") or now_iso()
        status = "acked" if result == "OK" else "failed"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO command_acks(command_id, node_id, result, message, raw_frame, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                    (command_id, payload["node_id"], result, payload.get("message"), payload.get("raw_frame"), timestamp),
                )
                cur.execute("UPDATE control_commands SET status = %s, acked_at = %s WHERE command_id = %s", (status, timestamp, command_id))
            conn.commit()
        return self.get_control_command(command_id) or {"command_id": command_id, "status": status}

    def create_alert(self, payload: dict[str, Any]) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alerts(alert_type, severity, status, node_id, camera_id, message, trigger_payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    RETURNING *
                    """,
                    (
                        payload["alert_type"],
                        payload.get("severity", "medium"),
                        payload.get("status", "active"),
                        payload.get("node_id"),
                        payload.get("camera_id"),
                        payload["message"],
                        json.dumps(payload.get("trigger_payload", {}), separators=(",", ":")),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return dict(row)

    def list_alerts(self, status: Optional[str] = None) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute("SELECT * FROM alerts WHERE status = %s ORDER BY created_at DESC", (status,))
                else:
                    cur.execute("SELECT * FROM alerts ORDER BY created_at DESC")
                return [dict(row) for row in cur.fetchall()]

    def update_alert_status(self, alert_id: int, status: str) -> Optional[dict]:
        timestamp_field = "acked_at" if status == "acked" else "resolved_at"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE alerts SET status = %s, {timestamp_field} = %s WHERE id = %s RETURNING *",
                    (status, now_iso(), alert_id),
                )
                row = cur.fetchone()
            conn.commit()
        return dict(row) if row else None

    def list_alert_rules(self) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM alert_rules ORDER BY id")
                return [dict(row) for row in cur.fetchall()]

    def create_alert_rule(self, payload: dict[str, Any]) -> dict:
        fields = services._validate_alert_rule(payload)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alert_rules(name, metric, node_id, condition, threshold, severity, enabled, cooldown_seconds)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        fields["name"],
                        fields["metric"],
                        fields.get("node_id"),
                        fields["condition"],
                        fields["threshold"],
                        fields.get("severity", "medium"),
                        bool(fields.get("enabled", 1)),
                        fields.get("cooldown_seconds", 300),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return dict(row)

    def update_alert_rule(self, rule_id: int, payload: dict[str, Any]) -> Optional[dict]:
        fields = services._validate_alert_rule(payload, partial=True)
        if "enabled" in fields:
            fields["enabled"] = bool(fields["enabled"])
        with self._connect() as conn:
            with conn.cursor() as cur:
                if fields:
                    assignments = ", ".join(f"{key} = %s" for key in fields)
                    cur.execute(
                        f"UPDATE alert_rules SET {assignments}, updated_at = NOW() WHERE id = %s RETURNING *",
                        (*fields.values(), rule_id),
                    )
                else:
                    cur.execute("SELECT * FROM alert_rules WHERE id = %s", (rule_id,))
                row = cur.fetchone()
            conn.commit()
        return dict(row) if row else None

    def delete_alert_rule(self, rule_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM alert_rules WHERE id = %s", (rule_id,))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def _evaluate_alert_rules(self, records: list[dict[str, Any]], captured_at: Optional[str]) -> int:
        """Postgres twin of services.evaluate_alert_rules, run after telemetry insert."""
        fired = 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM alert_rules WHERE enabled")
                rules = [dict(row) for row in cur.fetchall()]
        if not rules:
            return 0
        now = datetime.now(timezone.utc)
        for record in records:
            node_id = record.get("node_id")
            for rule in rules:
                if rule["node_id"] and rule["node_id"] != node_id:
                    continue
                value = record.get(rule["metric"])
                if value is None:
                    continue
                if not services.ALERT_RULE_CONDITIONS[rule["condition"]](float(value), rule["threshold"]):
                    continue
                last = rule.get("last_fired_at")
                if last is not None and (now - last).total_seconds() < rule["cooldown_seconds"]:
                    continue
                self.create_alert(
                    {
                        "alert_type": f"rule:{rule['metric']}",
                        "severity": rule["severity"],
                        "node_id": node_id,
                        "message": f"{rule['name']}: {rule['metric']}={value} {rule['condition']} {rule['threshold']} on {node_id}",
                        "trigger_payload": {"rule_id": rule["id"], "metric": rule["metric"], "value": value, "threshold": rule["threshold"], "condition": rule["condition"], "captured_at": captured_at},
                    }
                )
                with self._connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE alert_rules SET last_fired_at = NOW() WHERE id = %s", (rule["id"],))
                    conn.commit()
                rule["last_fired_at"] = now
                fired += 1
        return fired

    def dashboard_summary(self) -> dict:
        counts = {}
        with self._connect() as conn:
            with conn.cursor() as cur:
                for name, sql in {
                    "gateways": "SELECT COUNT(*) AS count FROM gateways",
                    "nodes": "SELECT COUNT(*) AS count FROM zigbee_nodes",
                    "readings": "SELECT COUNT(*) AS count FROM sensor_readings",
                    "active_alerts": "SELECT COUNT(*) AS count FROM alerts WHERE status = 'active'",
                    "pending_commands": "SELECT COUNT(*) AS count FROM control_commands WHERE status = 'pending'",
                }.items():
                    cur.execute(sql)
                    counts[name] = cur.fetchone()["count"]
        return {"counts": counts, "latest": self.latest_readings()}

    def create_prediction_run(self, payload: dict[str, Any]) -> dict:
        node_id = payload["target_node_id"]
        metric = payload["metric"]
        model_name = payload["model_name"]
        if model_name not in MODEL_NAMES:
            raise ValueError(f"unsupported model_name: {model_name}")
        horizon_minutes = int(payload.get("horizon_minutes", 15))
        training_limit = int(payload.get("training_limit", 2000))
        # data_source is derived from the training rows' own source tags so
        # simulated data can never be recorded as real, regardless of caller input.
        data_source = self._training_window_data_source(node_id, metric, training_limit)
        bucket_seconds = forecast_bucket_seconds(horizon_minutes)
        readings_asc = self._recent_readings_bucketed_asc(
            node_id, metric, bucket_seconds, FORECAST_GRID_STEPS + MIN_WINDOW + 800
        )
        result = train_and_evaluate(readings_asc, model_name, horizon_minutes)
        run_id = f"run-{uuid4().hex[:12]}"
        status = "failed" if "error" in result else "completed"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO prediction_runs(
                      run_id, node_id, metric, model_name, status, data_source, horizon_minutes, horizon_steps,
                      feature_columns, train_start_at, train_end_at, test_start_at, test_end_at,
                      mae, rmse, r2, latency_ms, model_size_bytes, sample_predictions, model_blob,
                      error_message, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                    """,
                    (
                        run_id,
                        node_id,
                        metric,
                        model_name,
                        status,
                        data_source,
                        horizon_minutes,
                        result.get("horizon_steps"),
                        json.dumps(list(FEATURE_COLUMNS)),
                        result.get("train_start_at"),
                        result.get("train_end_at"),
                        result.get("test_start_at"),
                        result.get("test_end_at"),
                        result.get("mae"),
                        result.get("rmse"),
                        result.get("r2"),
                        result.get("latency_ms"),
                        result.get("model_size_bytes"),
                        json.dumps(result.get("sample_predictions", [])),
                        result.get("model_blob"),
                        result.get("error"),
                        now_iso(),
                    ),
                )
            conn.commit()
        if status == "completed" and result.get("mae") is not None:
            # Scoped by horizon too: a 6h-ahead forecast is inherently less
            # accurate than a 30m one, so comparing MAE across horizons would
            # almost always keep the shortest horizon "active" and starve the
            # others of a usable model.
            current = self._active_prediction_run(node_id, metric, horizon_minutes)
            if current is None or (current["mae"] is not None and result["mae"] < current["mae"]):
                self.activate_prediction_run(run_id)
        return self.get_prediction_run(run_id) or {}

    def get_prediction_run(self, run_id: str) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM prediction_runs WHERE run_id = %s", (run_id,))
                row = cur.fetchone()
        return self._prediction_run_row_to_dict(row) if row else None

    def list_prediction_runs(self, node_id: Optional[str] = None, metric: Optional[str] = None, limit: int = 50) -> list[dict]:
        clauses = []
        args: list[Any] = []
        if node_id:
            clauses.append("node_id = %s")
            args.append(node_id)
        if metric:
            clauses.append("metric = %s")
            args.append(metric)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(limit)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM prediction_runs {where} ORDER BY created_at DESC LIMIT %s", args)
                return [self._prediction_run_row_to_dict(row) for row in cur.fetchall()]

    def activate_prediction_run(self, run_id: str) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT node_id, metric, horizon_minutes FROM prediction_runs WHERE run_id = %s", (run_id,))
                row = cur.fetchone()
                if row is None:
                    raise KeyError(f"prediction run not found: {run_id}")
                # Deactivate only the same horizon's runs — each horizon
                # (30m/2h/6h/...) keeps its own independently-active model so
                # every forecast tab has a live prediction, instead of one
                # horizon winning "active" for the whole metric.
                cur.execute(
                    "UPDATE prediction_runs SET is_active = FALSE WHERE node_id = %s AND metric = %s AND horizon_minutes = %s",
                    (row["node_id"], row["metric"], row["horizon_minutes"]),
                )
                cur.execute("UPDATE prediction_runs SET is_active = TRUE WHERE run_id = %s", (run_id,))
            conn.commit()
        return self.get_prediction_run(run_id) or {}

    def get_prediction_metrics(self, node_id: Optional[str] = None, metric: Optional[str] = None, horizon_minutes: Optional[int] = None) -> list[dict]:
        clauses = ["status = 'completed'"]
        args: list[Any] = []
        if node_id:
            clauses.append("node_id = %s")
            args.append(node_id)
        if metric:
            clauses.append("metric = %s")
            args.append(metric)
        if horizon_minutes is not None:
            clauses.append("horizon_minutes = %s")
            args.append(horizon_minutes)
        where = f"WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT DISTINCT ON (node_id, metric, model_name)
                      node_id, metric, model_name, mae, rmse, r2, latency_ms, model_size_bytes, is_active
                    FROM prediction_runs
                    {where}
                    -- Prefer the run actually flagged active over merely the
                    -- newest one: repeated retrains can leave a model with
                    -- several runs where the active flag sits on an older
                    -- row, and callers use is_active here to pick a default
                    -- selection in the UI.
                    ORDER BY node_id, metric, model_name, is_active DESC, created_at DESC
                    """,
                    args,
                )
                rows = cur.fetchall()
        return [
            {
                "node_id": row["node_id"],
                "metric": row["metric"],
                "model_name": row["model_name"],
                "mae": row["mae"],
                "rmse": row["rmse"],
                "r2": row["r2"],
                "inference_latency_ms": row["latency_ms"],
                "model_size_bytes": row["model_size_bytes"],
                "is_active": bool(row["is_active"]),
            }
            for row in rows
        ]

    def get_latest_prediction(self, node_id: str, metric: str, horizon_minutes: Optional[int] = None) -> Optional[dict]:
        active = self._active_prediction_run(node_id, metric, horizon_minutes)
        if active is None:
            return None
        readings_asc = self._recent_readings_bucketed_asc(
            node_id,
            metric,
            forecast_bucket_seconds(active["horizon_minutes"]),
            # Enough grid for ROLLING_HISTORY_MULTIPLIER lookback, the future
            # horizon itself, and feature-row warmup.
            (ROLLING_HISTORY_MULTIPLIER + 1) * FORECAST_GRID_STEPS + MIN_WINDOW + 30,
        )
        if not readings_asc:
            return {"node_id": node_id, "metric": metric, "model_name": active["model_name"], "error": "no readings available"}
        forecast = predict_next(readings_asc, active["model_name"], active["horizon_minutes"], active["model_blob"])
        if "error" in forecast:
            return {"node_id": node_id, "metric": metric, "model_name": active["model_name"], "error": forecast["error"]}
        historical_series, series = predict_rolling_series(readings_asc, active["model_name"], active["horizon_minutes"], active["model_blob"])
        band = round((active["rmse"] or 0) * 1.96, 4)
        return {
            "series": series,
            "historical_series": historical_series,
            "node_id": node_id,
            "metric": metric,
            "model_name": active["model_name"],
            "run_id": active["run_id"],
            "horizon_minutes": active["horizon_minutes"],
            "target_time": forecast["target_time"],
            "yhat": forecast["yhat"],
            "yhat_lower": round(forecast["yhat"] - band, 4) if band else None,
            "yhat_upper": round(forecast["yhat"] + band, 4) if band else None,
            "generated_at": now_iso(),
        }

    def prediction_features(self) -> dict:
        return prediction_feature_description()

    def list_artifacts(self, artifact_type: Optional[str] = None, limit: int = 100) -> list[dict]:
        clauses = []
        args: list[Any] = []
        if artifact_type:
            clauses.append("artifact_type = %s")
            args.append(artifact_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(limit)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM object_artifacts {where} ORDER BY created_at DESC, id DESC LIMIT %s", args)
                return [self._artifact_row_to_dict(row) for row in cur.fetchall()]

    def register_artifact(self, payload: dict[str, Any]) -> dict:
        meta = payload.get("meta") or payload.get("meta_json") or {}
        if not isinstance(meta, dict):
            raise ValueError("meta must be an object")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO object_artifacts(bucket, object_key, artifact_type, content_type, size_bytes, sha256, etag, meta_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT(bucket, object_key) DO UPDATE SET
                      artifact_type=excluded.artifact_type,
                      content_type=excluded.content_type,
                      size_bytes=excluded.size_bytes,
                      sha256=excluded.sha256,
                      etag=excluded.etag,
                      meta_json=excluded.meta_json
                    RETURNING *
                    """,
                    (
                        payload["bucket"],
                        payload["object_key"],
                        payload["artifact_type"],
                        payload.get("content_type"),
                        payload.get("size_bytes"),
                        payload.get("sha256"),
                        payload.get("etag"),
                        json.dumps(meta, separators=(",", ":")),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return self._artifact_row_to_dict(row)

    def presign_artifact_url(self, artifact_id: int) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM object_artifacts WHERE id = %s", (artifact_id,))
                row = cur.fetchone()
        if row is None:
            raise KeyError(f"artifact not found: {artifact_id}")
        item = self._artifact_row_to_dict(row)
        return {"artifact_id": artifact_id, "url": item["presigned_url"]}

    def _recent_readings_bucketed_asc(self, node_id: str, metric: str, bucket_seconds: int, max_buckets: int) -> list[dict]:
        """Bucket-averaged recent readings on a fixed grid, oldest first."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT to_timestamp(floor(extract(epoch FROM captured_at) / %s) * %s) AS bucket_at,
                           AVG(value) AS value
                    FROM sensor_readings
                    WHERE node_id = %s AND metric = %s
                      -- Window is anchored to the newest reading, not NOW(), so a
                      -- node that stopped reporting still trains/forecasts.
                      AND captured_at >= (
                        SELECT MAX(captured_at) FROM sensor_readings WHERE node_id = %s AND metric = %s
                      ) - make_interval(secs => %s)
                    GROUP BY bucket_at
                    ORDER BY bucket_at ASC
                    """,
                    (bucket_seconds, bucket_seconds, node_id, metric, node_id, metric, float(bucket_seconds * max_buckets)),
                )
                return [{"captured_at": row["bucket_at"].isoformat(), "value": float(row["value"])} for row in cur.fetchall()]

    def _recent_readings_asc(self, node_id: str, metric: str, limit: int) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT captured_at, value FROM (
                      SELECT captured_at, value FROM sensor_readings
                      WHERE node_id = %s AND metric = %s
                      ORDER BY captured_at DESC LIMIT %s
                    ) recent
                    ORDER BY captured_at ASC
                    """,
                    (node_id, metric, limit),
                )
                return [{"captured_at": row["captured_at"].isoformat(), "value": row["value"]} for row in cur.fetchall()]

    def _training_window_data_source(self, node_id: str, metric: str, limit: int) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT source FROM (
                      SELECT source FROM sensor_readings
                      WHERE node_id = %s AND metric = %s
                      ORDER BY captured_at DESC LIMIT %s
                    ) recent
                    """,
                    (node_id, metric, limit),
                )
                sources = sorted(row["source"] for row in cur.fetchall())
        if not sources:
            return "unknown"
        if all(source in REAL_READING_SOURCES for source in sources):
            return "real"
        return "simulated(" + ",".join(sources) + ")"

    def _active_prediction_run(self, node_id: str, metric: str, horizon_minutes: Optional[int] = None) -> Optional[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if horizon_minutes is not None:
                    cur.execute(
                        "SELECT * FROM prediction_runs WHERE node_id = %s AND metric = %s AND horizon_minutes = %s AND is_active = TRUE",
                        (node_id, metric, horizon_minutes),
                    )
                else:
                    # No horizon requested (legacy callers): several horizons
                    # may each be active now, so just take the most recently
                    # activated one.
                    cur.execute(
                        "SELECT * FROM prediction_runs WHERE node_id = %s AND metric = %s AND is_active = TRUE ORDER BY created_at DESC LIMIT 1",
                        (node_id, metric),
                    )
                row = cur.fetchone()
        return row

    def _prediction_run_row_to_dict(self, row: dict[str, Any]) -> dict:
        data = dict(row)
        data.pop("model_blob", None)
        data["feature_columns"] = data.get("feature_columns") or []
        data["sample_predictions"] = data.get("sample_predictions") or []
        data["is_active"] = bool(data.get("is_active"))
        return data

    def _artifact_row_to_dict(self, row: dict[str, Any]) -> dict:
        data = dict(row)
        data["meta"] = data.pop("meta_json") or {}
        data["presigned_url"] = self._object_url(data["bucket"], data["object_key"])
        return data

    def _object_url(self, bucket: str, object_key: str) -> str:
        return f"{self.rustfs_public_url}/{quote(bucket)}/{quote(object_key, safe='/')}"
