from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Optional
from uuid import uuid4

from .auth import hash_password, verify_password
from .camera_sources import normalize_camera_payload
from .node_status import apply_effective_node_status
from .prediction import (
    FEATURE_COLUMNS,
    FORECAST_GRID_STEPS,
    MIN_WINDOW,
    MODEL_NAMES,
    ROLLING_HISTORY_MULTIPLIER,
    forecast_bucket_seconds,
    predict_next,
    predict_rolling_series,
    prediction_feature_description,
    train_and_evaluate,
)


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


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return None if row is None else dict(row)


def _decode_settings(value: Any) -> dict:
    if not value:
        return dict(DEFAULT_SETTINGS)
    if isinstance(value, dict):
        return value
    return {**DEFAULT_SETTINGS, **json.loads(value)}


def _public_user(row: Optional[sqlite3.Row]) -> Optional[dict]:
    item = row_to_dict(row)
    if item is None:
        return None
    item.pop("password_hash", None)
    item.pop("settings", None)
    return item


def _current_user_row(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM users WHERE status = 'active' ORDER BY role = 'admin' DESC, id LIMIT 1"
    ).fetchone()


def list_users(conn: sqlite3.Connection, role: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    clauses = []
    args: list[Any] = []
    if role:
        clauses.append("role = ?")
        args.append(role)
    if status:
        clauses.append("status = ?")
        args.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM users {where} ORDER BY created_at DESC, id DESC", args).fetchall()
    return [item for row in rows if (item := _public_user(row)) is not None]


def create_user(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict:
    settings = json.dumps({**DEFAULT_SETTINGS, **payload.get("settings", {})}, separators=(",", ":"))
    password_hash = hash_password(payload["password"]) if payload.get("password") else payload.get("password_hash")
    cur = conn.execute(
        """
        INSERT INTO users(name, email, password_hash, role, status, settings, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["name"],
            payload["email"],
            password_hash,
            payload["role"],
            payload.get("status", "invited"),
            settings,
            now_iso(),
        ),
    )
    conn.commit()
    return get_user(conn, cur.lastrowid) or {}


def get_user_by_email(conn: sqlite3.Connection, email: str) -> Optional[dict]:
    return _public_user(conn.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (email,)).fetchone())


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> Optional[dict]:
    return get_user(conn, user_id)


def get_user(conn: sqlite3.Connection, user_id: int) -> Optional[dict]:
    return _public_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def authenticate_user(conn: sqlite3.Connection, email: str, password: str, bootstrap_password: Optional[str] = None) -> Optional[dict]:
    row = conn.execute("SELECT * FROM users WHERE lower(email) = lower(?) AND status = 'active'", (email,)).fetchone()
    if row is None:
        return None
    if not verify_password(row["password_hash"], password):
        if not (bootstrap_password and not row["password_hash"] and hmac_compare(password, bootstrap_password)):
            return None
        conn.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", (hash_password(password), now_iso(), row["id"]))
    conn.execute("UPDATE users SET last_login = ?, updated_at = ? WHERE id = ?", (now_iso(), now_iso(), row["id"]))
    conn.commit()
    return get_user(conn, row["id"])


def hmac_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def update_user(conn: sqlite3.Connection, user_id: int, payload: dict[str, Any]) -> Optional[dict]:
    existing = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if existing is None:
        return None
    conn.execute(
        """
        UPDATE users
        SET name = ?, email = ?, role = ?, status = ?, password_hash = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            payload.get("name", existing["name"]),
            payload.get("email", existing["email"]),
            payload.get("role", existing["role"]),
            payload.get("status", existing["status"]),
            hash_password(payload["password"]) if payload.get("password") else existing["password_hash"],
            now_iso(),
            user_id,
        ),
    )
    conn.commit()
    return get_user(conn, user_id)


def disable_user(conn: sqlite3.Connection, user_id: int) -> bool:
    cur = conn.execute("UPDATE users SET status = 'disabled', updated_at = ? WHERE id = ?", (now_iso(), user_id))
    conn.commit()
    return cur.rowcount > 0


def get_current_user(conn: sqlite3.Connection, user_id: Optional[int] = None) -> Optional[dict]:
    if user_id is not None:
        return get_user(conn, user_id)
    return _public_user(_current_user_row(conn))


def update_current_user(conn: sqlite3.Connection, payload: dict[str, Any], user_id: Optional[int] = None) -> Optional[dict]:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone() if user_id is not None else _current_user_row(conn)
    if row is None:
        return None
    return update_user(conn, row["id"], {key: payload[key] for key in ("name", "email", "password") if key in payload})


def get_current_user_settings(conn: sqlite3.Connection, user_id: Optional[int] = None) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone() if user_id is not None else _current_user_row(conn)
    return _decode_settings(row["settings"] if row else None)


def update_current_user_settings(conn: sqlite3.Connection, payload: dict[str, Any], user_id: Optional[int] = None) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone() if user_id is not None else _current_user_row(conn)
    if row is None:
        return dict(DEFAULT_SETTINGS)
    current = _decode_settings(row["settings"])
    merged = {
        **current,
        **{key: value for key, value in payload.items() if key not in {"notifications", "vision", "chatbot"}},
        "notifications": {**current.get("notifications", {}), **payload.get("notifications", {})},
        "vision": {**current.get("vision", {}), **payload.get("vision", {})},
        "chatbot": {**current.get("chatbot", {}), **payload.get("chatbot", {})},
    }
    conn.execute("UPDATE users SET settings = ?, updated_at = ? WHERE id = ?", (json.dumps(merged, separators=(",", ":")), now_iso(), row["id"]))
    conn.commit()
    return merged


def append_chat_message(
    conn: sqlite3.Connection,
    session_id: str,
    role: str,
    content: Optional[str],
    tool_calls: Optional[list] = None,
) -> dict:
    cur = conn.execute(
        "INSERT INTO chat_messages(session_id, role, content, tool_calls_json, created_at) VALUES(?, ?, ?, ?, ?)",
        (session_id, role, content, json.dumps(tool_calls) if tool_calls else None, now_iso()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM chat_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _chat_row_to_dict(row)


def list_chat_messages(conn: sqlite3.Connection, session_id: str, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id ASC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [_chat_row_to_dict(row) for row in rows]


def _chat_row_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    raw = item.pop("tool_calls_json", None)
    item["tool_calls"] = json.loads(raw) if raw else []
    return item


def list_devices(conn: sqlite3.Connection, status: Optional[str] = None) -> list[dict]:
    if status:
        rows = conn.execute("SELECT * FROM logical_devices WHERE status = ? ORDER BY created_at DESC, id DESC", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM logical_devices ORDER BY created_at DESC, id DESC").fetchall()
    return [dict(row) for row in rows]


def create_device(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict:
    payload = normalize_camera_payload(payload) if payload.get("device_type") == "camera" else payload
    cur = conn.execute(
        """
        INSERT INTO logical_devices(name, device_type, location, status, client_id, protocol, ip_address, port, source_uri, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            now_iso(),
        ),
    )
    conn.commit()
    return get_device(conn, cur.lastrowid) or {}


def get_device(conn: sqlite3.Connection, device_id: int) -> Optional[dict]:
    return row_to_dict(conn.execute("SELECT * FROM logical_devices WHERE id = ?", (device_id,)).fetchone())


def update_device(conn: sqlite3.Connection, device_id: int, payload: dict[str, Any]) -> Optional[dict]:
    existing = conn.execute("SELECT * FROM logical_devices WHERE id = ?", (device_id,)).fetchone()
    if existing is None:
        return None
    merged_type = payload.get("device_type", existing["device_type"])
    if merged_type == "camera":
        payload = normalize_camera_payload(payload, existing=dict(existing))
    conn.execute(
        """
        UPDATE logical_devices
        SET name = ?, device_type = ?, location = ?, status = ?, client_id = ?,
            protocol = ?, ip_address = ?, port = ?, source_uri = ?, updated_at = ?
        WHERE id = ?
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
            now_iso(),
            device_id,
        ),
    )
    conn.commit()
    return get_device(conn, device_id)


def disable_device(conn: sqlite3.Connection, device_id: int) -> bool:
    cur = conn.execute("UPDATE logical_devices SET status = 'offline', updated_at = ? WHERE id = ?", (now_iso(), device_id))
    conn.commit()
    return cur.rowcount > 0


def delete_device(conn: sqlite3.Connection, device_id: int) -> bool:
    cur = conn.execute("DELETE FROM logical_devices WHERE id = ?", (device_id,))
    conn.commit()
    return cur.rowcount > 0


def register_gateway(conn: sqlite3.Connection, gateway_id: str, payload: dict[str, Any]) -> dict:
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO gateways(gateway_id, name, location, status, ip_address, version, last_seen_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(gateway_id) DO UPDATE SET
          name=COALESCE(excluded.name, gateways.name),
          location=COALESCE(excluded.location, gateways.location),
          status=excluded.status,
          ip_address=COALESCE(excluded.ip_address, gateways.ip_address),
          version=COALESCE(excluded.version, gateways.version),
          last_seen_at=excluded.last_seen_at,
          updated_at=excluded.updated_at
        """,
        (
            gateway_id,
            payload.get("name"),
            payload.get("location"),
            payload.get("status", "online"),
            payload.get("ip_address"),
            payload.get("version"),
            payload.get("last_seen_at", timestamp),
            timestamp,
        ),
    )
    conn.commit()
    return get_gateway(conn, gateway_id) or {}


def get_gateway(conn: sqlite3.Connection, gateway_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM gateways WHERE gateway_id = ?", (gateway_id,)).fetchone()
    return row_to_dict(row)


def list_gateways(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM gateways ORDER BY last_seen_at DESC NULLS LAST, gateway_id")]


def list_nodes(conn: sqlite3.Connection, gateway_id: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    clauses = []
    args: list[Any] = []
    if gateway_id:
        clauses.append("gateway_id = ?")
        args.append(gateway_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = [
        apply_effective_node_status(dict(row))
        for row in conn.execute(f"SELECT * FROM zigbee_nodes {where} ORDER BY node_id", args)
    ]
    if status:
        return [row for row in rows if row["status"] == status]
    return rows


def register_node(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict:
    timestamp = now_iso()
    register_gateway(conn, payload["gateway_id"], {"status": "online", "last_seen_at": timestamp})
    conn.execute(
        """
        INSERT INTO zigbee_nodes(node_id, gateway_id, parent_node_id, role, area, ip_address, status, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(node_id) DO UPDATE SET
          gateway_id=excluded.gateway_id,
          parent_node_id=excluded.parent_node_id,
          role=excluded.role,
          area=excluded.area,
          ip_address=COALESCE(excluded.ip_address, zigbee_nodes.ip_address),
          status=excluded.status,
          updated_at=excluded.updated_at
        """,
        (
            payload["node_id"],
            payload["gateway_id"],
            payload.get("parent_node_id"),
            payload.get("role", "sensor"),
            payload.get("area"),
            payload.get("ip_address"),
            payload.get("status", "offline"),
            timestamp,
        ),
    )
    conn.commit()
    return get_node(conn, payload["node_id"]) or {}


def get_node(conn: sqlite3.Connection, node_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM zigbee_nodes WHERE node_id = ?", (node_id,)).fetchone()
    result = row_to_dict(row)
    return apply_effective_node_status(result) if result else None


def ingest_telemetry_batch(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict:
    gateway_id = payload["gateway_id"]
    batch_id = str(payload.get("batch_id", "manual"))
    captured_at = payload.get("captured_at") or now_iso()
    records = payload.get("records") or []
    register_gateway(conn, gateway_id, {"status": "online", "last_seen_at": captured_at})

    inserted = 0
    for record in records:
        node_id = record["node_id"]
        _upsert_node(conn, gateway_id, record, captured_at)
        for metric in ("temperature", "humidity", "light", "air_quality"):
            value = record.get(metric)
            if value is None:
                continue
            message_id = f"{payload.get('message_id', uuid4())}:{node_id}:{record.get('seq')}:{metric}"
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO sensor_readings(
                  message_id, gateway_id, batch_id, node_id, seq, metric, value, unit,
                  rssi, battery_mv, flags, source, captured_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            inserted += cursor.rowcount
    conn.commit()
    alerts_fired = 0
    for record in records:
        alerts_fired += len(evaluate_alert_rules(conn, record["node_id"], record, captured_at))
    return {"gateway_id": gateway_id, "batch_id": batch_id, "records": len(records), "inserted_metrics": inserted, "alerts_fired": alerts_fired}


def _upsert_node(conn: sqlite3.Connection, gateway_id: str, record: dict[str, Any], captured_at: str) -> None:
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO zigbee_nodes(node_id, gateway_id, parent_node_id, role, status, battery_mv, rssi, last_seq, last_seen_at, updated_at)
        VALUES(?, ?, ?, ?, 'online', ?, ?, ?, ?, ?)
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
            record["node_id"],
            gateway_id,
            record.get("parent_node_id"),
            record.get("role", "sensor"),
            record.get("battery_mv"),
            record.get("rssi"),
            record.get("seq"),
            captured_at,
            timestamp,
        ),
    )


def query_readings(
    conn: sqlite3.Connection,
    node_id: Optional[str] = None,
    metric: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    clauses = []
    args: list[Any] = []
    if node_id:
        clauses.append("node_id = ?")
        args.append(node_id)
    if metric:
        clauses.append("metric = ?")
        args.append(metric)
    if start:
        clauses.append("captured_at >= ?")
        args.append(start)
    if end:
        clauses.append("captured_at <= ?")
        args.append(end)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    bucket_seconds = reading_bucket_seconds(start, end, limit)
    if bucket_seconds is None:
        sql = f"SELECT * FROM sensor_readings {where} ORDER BY captured_at DESC, id DESC LIMIT ?"
        return [dict(row) for row in conn.execute(sql, [*args, limit])]
    # Downsample to one (latest-in-bucket) row per fixed-width time bucket so
    # every range button spans its own full window instead of the same tail
    # of raw rows (see reading_bucket_seconds).
    sql = f"SELECT * FROM sensor_readings {where} ORDER BY captured_at DESC, id DESC"
    bucketed: dict[tuple[Any, Any, int], dict] = {}
    for row in conn.execute(sql, args):
        record = dict(row)
        try:
            epoch = datetime.fromisoformat(str(record.get("captured_at") or "").replace("Z", "+00:00")).timestamp()
        except ValueError:
            epoch = 0.0
        key = (record.get("node_id"), record.get("metric"), int(epoch // bucket_seconds))
        bucketed.setdefault(key, record)
    return list(bucketed.values())[:limit]


def latest_reading_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM sensor_readings").fetchone()
    return int(row[0])


def readings_after(conn: sqlite3.Connection, cursor_id: int, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM sensor_readings WHERE id > ? ORDER BY id ASC LIMIT ?",
        (cursor_id, limit),
    )
    return [dict(row) for row in rows]


def latest_readings(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
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
    ).fetchall()
    return [dict(row) for row in rows]


def create_control_command(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict:
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
    conn.execute(
        """
        INSERT INTO control_commands(command_id, source, target_node_id, target_device, action, desired_state, reason, status, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, 'pending', ?)
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
    conn.execute(
        """
        INSERT INTO outbox_messages(message_id, topic, payload_json)
        VALUES(?, ?, ?)
        """,
        (str(uuid4()), f"breezio/nodes/{target_node_id}/command", json.dumps(command, separators=(",", ":"))),
    )
    conn.commit()
    return get_control_command(conn, command_id) or {}


def get_control_command(conn: sqlite3.Connection, command_id: str) -> Optional[dict]:
    command = row_to_dict(conn.execute("SELECT * FROM control_commands WHERE command_id = ?", (command_id,)).fetchone())
    if command:
        if command.get("desired_state") is not None:
            command["desired_state"] = json.loads(command["desired_state"])
        command["acks"] = [
            dict(row)
            for row in conn.execute("SELECT * FROM command_acks WHERE command_id = ? ORDER BY created_at DESC", (command_id,))
        ]
    return command


def list_control_commands(conn: sqlite3.Connection, status: Optional[str] = None) -> list[dict]:
    if status:
        rows = conn.execute("SELECT * FROM control_commands WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM control_commands ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def ingest_command_ack(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict:
    command_id = payload["command_id"]
    result = payload["result"].upper()
    timestamp = payload.get("acked_at") or now_iso()
    status = "acked" if result == "OK" else "failed"
    conn.execute(
        "INSERT INTO command_acks(command_id, node_id, result, message, raw_frame, created_at) VALUES(?, ?, ?, ?, ?, ?)",
        (command_id, payload["node_id"], result, payload.get("message"), payload.get("raw_frame"), timestamp),
    )
    conn.execute("UPDATE control_commands SET status = ?, acked_at = ? WHERE command_id = ?", (status, timestamp, command_id))
    conn.commit()
    return get_control_command(conn, command_id) or {"command_id": command_id, "status": status}


def list_alerts(conn: sqlite3.Connection, status: Optional[str] = None) -> list[dict]:
    if status:
        rows = conn.execute("SELECT * FROM alerts WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM alerts ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def create_alert(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict:
    conn.execute(
        """
        INSERT INTO alerts(alert_type, severity, status, node_id, camera_id, message, trigger_payload)
        VALUES(?, ?, ?, ?, ?, ?, ?)
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
    alert_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return dict(conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone())


def update_alert_status(conn: sqlite3.Connection, alert_id: int, status: str) -> Optional[dict]:
    timestamp_field = "acked_at" if status == "acked" else "resolved_at"
    conn.execute(f"UPDATE alerts SET status = ?, {timestamp_field} = ? WHERE id = ?", (status, now_iso(), alert_id))
    conn.commit()
    return row_to_dict(conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone())


ALERT_RULE_METRICS = ("temperature", "humidity", "light", "air_quality", "battery_mv", "rssi")
ALERT_RULE_CONDITIONS = {"gt": lambda v, t: v > t, "gte": lambda v, t: v >= t, "lt": lambda v, t: v < t, "lte": lambda v, t: v <= t}
ALERT_RULE_SEVERITIES = ("low", "medium", "high", "critical")


def _validate_alert_rule(payload: dict[str, Any], partial: bool = False) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in ("name", "metric", "node_id", "condition", "threshold", "severity", "enabled", "cooldown_seconds"):
        if key in payload:
            fields[key] = payload[key]
    if not partial:
        for required in ("name", "metric", "condition", "threshold"):
            if required not in fields:
                raise ValueError(f"{required} is required")
    if "metric" in fields and fields["metric"] not in ALERT_RULE_METRICS:
        raise ValueError(f"metric must be one of {ALERT_RULE_METRICS}")
    if "condition" in fields and fields["condition"] not in ALERT_RULE_CONDITIONS:
        raise ValueError("condition must be one of gt, gte, lt, lte")
    if "severity" in fields and fields["severity"] not in ALERT_RULE_SEVERITIES:
        raise ValueError(f"severity must be one of {ALERT_RULE_SEVERITIES}")
    if "threshold" in fields:
        fields["threshold"] = float(fields["threshold"])
    if "cooldown_seconds" in fields:
        fields["cooldown_seconds"] = max(0, int(fields["cooldown_seconds"]))
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    return fields


def list_alert_rules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM alert_rules ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def create_alert_rule(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict:
    fields = _validate_alert_rule(payload)
    conn.execute(
        """
        INSERT INTO alert_rules(name, metric, node_id, condition, threshold, severity, enabled, cooldown_seconds)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fields["name"],
            fields["metric"],
            fields.get("node_id"),
            fields["condition"],
            fields["threshold"],
            fields.get("severity", "medium"),
            fields.get("enabled", 1),
            fields.get("cooldown_seconds", 300),
        ),
    )
    rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return dict(conn.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,)).fetchone())


def update_alert_rule(conn: sqlite3.Connection, rule_id: int, payload: dict[str, Any]) -> Optional[dict]:
    fields = _validate_alert_rule(payload, partial=True)
    if fields:
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE alert_rules SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), now_iso(), rule_id),
        )
        conn.commit()
    return row_to_dict(conn.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,)).fetchone())


def delete_alert_rule(conn: sqlite3.Connection, rule_id: int) -> bool:
    cur = conn.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
    conn.commit()
    return cur.rowcount > 0


def evaluate_alert_rules(conn: sqlite3.Connection, node_id: str, metrics: dict[str, Any], captured_at: Optional[str] = None) -> list[dict]:
    """Fire alerts for enabled rules matched by this node's new readings.

    Called from telemetry ingestion. Cooldown is enforced per rule via
    last_fired_at so a flapping metric doesn't create an alert storm.
    """
    fired = []
    now = datetime.now(timezone.utc)
    rules = conn.execute("SELECT * FROM alert_rules WHERE enabled = 1").fetchall()
    for row in rules:
        rule = dict(row)
        if rule["node_id"] and rule["node_id"] != node_id:
            continue
        value = metrics.get(rule["metric"])
        if value is None:
            continue
        if not ALERT_RULE_CONDITIONS[rule["condition"]](float(value), rule["threshold"]):
            continue
        if rule["last_fired_at"]:
            try:
                last = datetime.fromisoformat(str(rule["last_fired_at"]).replace("Z", "+00:00"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() < rule["cooldown_seconds"]:
                    continue
            except ValueError:
                pass
        alert = create_alert(
            conn,
            {
                "alert_type": f"rule:{rule['metric']}",
                "severity": rule["severity"],
                "node_id": node_id,
                "message": f"{rule['name']}: {rule['metric']}={value} {rule['condition']} {rule['threshold']} on {node_id}",
                "trigger_payload": {"rule_id": rule["id"], "metric": rule["metric"], "value": value, "threshold": rule["threshold"], "condition": rule["condition"], "captured_at": captured_at},
            },
        )
        conn.execute("UPDATE alert_rules SET last_fired_at = ? WHERE id = ?", (now_iso(), rule["id"]))
        conn.commit()
        fired.append(alert)
    return fired


def dashboard_summary(conn: sqlite3.Connection) -> dict:
    counts = {}
    for name, sql in {
        "gateways": "SELECT COUNT(*) FROM gateways",
        "nodes": "SELECT COUNT(*) FROM zigbee_nodes",
        "readings": "SELECT COUNT(*) FROM sensor_readings",
        "active_alerts": "SELECT COUNT(*) FROM alerts WHERE status = 'active'",
        "pending_commands": "SELECT COUNT(*) FROM control_commands WHERE status = 'pending'",
    }.items():
        counts[name] = conn.execute(sql).fetchone()[0]
    return {"counts": counts, "latest": latest_readings(conn)}


def _recent_readings_asc(conn: sqlite3.Connection, node_id: str, metric: str, limit: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT captured_at, value FROM (
          SELECT captured_at, value FROM sensor_readings
          WHERE node_id = ? AND metric = ?
          ORDER BY captured_at DESC LIMIT ?
        ) recent
        ORDER BY captured_at ASC
        """,
        (node_id, metric, limit),
    ).fetchall()
    return [{"captured_at": row["captured_at"], "value": row["value"]} for row in rows]


def _recent_readings_bucketed_asc(
    conn: sqlite3.Connection, node_id: str, metric: str, bucket_seconds: int, max_buckets: int
) -> list[dict]:
    """Bucket-averaged recent readings on a fixed grid, oldest first."""
    rows = conn.execute(
        """
        SELECT (CAST(strftime('%s', captured_at) AS INTEGER) / ?) * ? AS bucket_epoch,
               AVG(value) AS value
        FROM sensor_readings
        WHERE node_id = ? AND metric = ?
          -- Window is anchored to the newest reading, not 'now', so a node
          -- that stopped reporting still trains/forecasts.
          AND julianday(captured_at) >= (
            SELECT julianday(MAX(captured_at)) FROM sensor_readings WHERE node_id = ? AND metric = ?
          ) - (? / 86400.0)
        GROUP BY bucket_epoch
        ORDER BY bucket_epoch ASC
        """,
        (bucket_seconds, bucket_seconds, node_id, metric, node_id, metric, bucket_seconds * max_buckets),
    ).fetchall()
    return [
        {
            "captured_at": datetime.fromtimestamp(row["bucket_epoch"], tz=timezone.utc).isoformat(),
            "value": row["value"],
        }
        for row in rows
    ]


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


def _training_window_data_source(conn: sqlite3.Connection, node_id: str, metric: str, limit: int) -> str:
    rows = conn.execute(
        """
        SELECT DISTINCT source FROM (
          SELECT source FROM sensor_readings
          WHERE node_id = ? AND metric = ?
          ORDER BY captured_at DESC LIMIT ?
        ) recent
        """,
        (node_id, metric, limit),
    ).fetchall()
    sources = sorted(row["source"] for row in rows)
    if not sources:
        return "unknown"
    if all(source in REAL_READING_SOURCES for source in sources):
        return "real"
    return "simulated(" + ",".join(sources) + ")"


def _prediction_run_row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data.pop("model_blob", None)
    data["feature_columns"] = json.loads(data["feature_columns"]) if data.get("feature_columns") else []
    data["sample_predictions"] = json.loads(data["sample_predictions"]) if data.get("sample_predictions") else []
    data["is_active"] = bool(data.get("is_active"))
    return data


def create_prediction_run(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict:
    node_id = payload["target_node_id"]
    metric = payload["metric"]
    model_name = payload["model_name"]
    if model_name not in MODEL_NAMES:
        raise ValueError(f"unsupported model_name: {model_name}")
    horizon_minutes = int(payload.get("horizon_minutes", 15))
    training_limit = int(payload.get("training_limit", 2000))
    # data_source is derived from the training rows' own source tags so
    # simulated data can never be recorded as real, regardless of caller input.
    data_source = _training_window_data_source(conn, node_id, metric, training_limit)

    bucket_seconds = forecast_bucket_seconds(horizon_minutes)
    readings_asc = _recent_readings_bucketed_asc(
        conn, node_id, metric, bucket_seconds, FORECAST_GRID_STEPS + MIN_WINDOW + 800
    )
    result = train_and_evaluate(readings_asc, model_name, horizon_minutes)

    run_id = f"run-{uuid4().hex[:12]}"
    status = "failed" if "error" in result else "completed"
    conn.execute(
        """
        INSERT INTO prediction_runs(
          run_id, node_id, metric, model_name, status, data_source, horizon_minutes, horizon_steps,
          feature_columns, train_start_at, train_end_at, test_start_at, test_end_at,
          mae, rmse, r2, latency_ms, model_size_bytes, sample_predictions, model_blob,
          error_message, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        current = conn.execute(
            "SELECT run_id, mae FROM prediction_runs WHERE node_id = ? AND metric = ? AND horizon_minutes = ? AND is_active = 1",
            (node_id, metric, horizon_minutes),
        ).fetchone()
        if current is None or (current["mae"] is not None and result["mae"] < current["mae"]):
            activate_prediction_run(conn, run_id)

    return get_prediction_run(conn, run_id) or {}


def get_prediction_run(conn: sqlite3.Connection, run_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM prediction_runs WHERE run_id = ?", (run_id,)).fetchone()
    return _prediction_run_row_to_dict(row) if row else None


def list_prediction_runs(
    conn: sqlite3.Connection, node_id: Optional[str] = None, metric: Optional[str] = None, limit: int = 50
) -> list[dict]:
    clauses = []
    args: list[Any] = []
    if node_id:
        clauses.append("node_id = ?")
        args.append(node_id)
    if metric:
        clauses.append("metric = ?")
        args.append(metric)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    args.append(limit)
    rows = conn.execute(f"SELECT * FROM prediction_runs {where} ORDER BY created_at DESC LIMIT ?", args).fetchall()
    return [_prediction_run_row_to_dict(row) for row in rows]


def activate_prediction_run(conn: sqlite3.Connection, run_id: str) -> dict:
    row = conn.execute("SELECT node_id, metric, horizon_minutes FROM prediction_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"prediction run not found: {run_id}")
    # Deactivate only the same horizon's runs — each horizon (30m/2h/6h/...)
    # keeps its own independently-active model so every forecast tab has a
    # live prediction, instead of one horizon winning "active" for the whole
    # metric and starving the others.
    conn.execute(
        "UPDATE prediction_runs SET is_active = 0 WHERE node_id = ? AND metric = ? AND horizon_minutes = ?",
        (row["node_id"], row["metric"], row["horizon_minutes"]),
    )
    conn.execute("UPDATE prediction_runs SET is_active = 1 WHERE run_id = ?", (run_id,))
    conn.commit()
    return get_prediction_run(conn, run_id) or {}


def get_prediction_metrics(
    conn: sqlite3.Connection, node_id: Optional[str] = None, metric: Optional[str] = None, horizon_minutes: Optional[int] = None
) -> list[dict]:
    clauses = ["status = 'completed'"]
    args: list[Any] = []
    if node_id:
        clauses.append("node_id = ?")
        args.append(node_id)
    if metric:
        clauses.append("metric = ?")
        args.append(metric)
    if horizon_minutes is not None:
        clauses.append("horizon_minutes = ?")
        args.append(horizon_minutes)
    where = f"WHERE {' AND '.join(clauses)}"
    # Prefer the run actually flagged active over merely the newest one:
    # repeated retrains can leave a model with several runs where the active
    # flag sits on an older row, and callers use is_active here to pick a
    # default selection in the UI.
    rows = conn.execute(
        f"""
        SELECT * FROM (
          SELECT r.*, ROW_NUMBER() OVER (
            PARTITION BY r.node_id, r.metric, r.model_name
            ORDER BY r.is_active DESC, r.created_at DESC
          ) AS rn
          FROM prediction_runs r
          {where}
        ) ranked
        WHERE rn = 1
        ORDER BY node_id, metric, model_name
        """,
        args,
    ).fetchall()
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


def get_latest_prediction(conn: sqlite3.Connection, node_id: str, metric: str, horizon_minutes: Optional[int] = None) -> Optional[dict]:
    if horizon_minutes is not None:
        active = conn.execute(
            "SELECT * FROM prediction_runs WHERE node_id = ? AND metric = ? AND horizon_minutes = ? AND is_active = 1",
            (node_id, metric, horizon_minutes),
        ).fetchone()
    else:
        # No horizon requested (legacy callers): several horizons may each be
        # active now, so just take the most recently activated one.
        active = conn.execute(
            "SELECT * FROM prediction_runs WHERE node_id = ? AND metric = ? AND is_active = 1 ORDER BY created_at DESC LIMIT 1",
            (node_id, metric),
        ).fetchone()
    if active is None:
        return None

    readings_asc = _recent_readings_bucketed_asc(
        conn,
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


def prediction_features() -> dict:
    return prediction_feature_description()


class BackendStore:
    """Application-facing persistence service.

    The HTTP API and workers depend on this object instead of calling sqlite3
    directly. The module-level functions remain small pure operations for tests
    and for a later PostgreSQL repository split.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def register_gateway(self, gateway_id: str, payload: dict[str, Any]) -> dict:
        return register_gateway(self.conn, gateway_id, payload)

    def list_users(self, role: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
        return list_users(self.conn, role, status)

    def create_user(self, payload: dict[str, Any]) -> dict:
        return create_user(self.conn, payload)

    def get_user_by_email(self, email: str) -> Optional[dict]:
        return get_user_by_email(self.conn, email)

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        return get_user_by_id(self.conn, user_id)

    def authenticate_user(self, email: str, password: str, bootstrap_password: Optional[str] = None) -> Optional[dict]:
        return authenticate_user(self.conn, email, password, bootstrap_password)

    def get_user(self, user_id: int) -> Optional[dict]:
        return get_user(self.conn, user_id)

    def update_user(self, user_id: int, payload: dict[str, Any]) -> Optional[dict]:
        return update_user(self.conn, user_id, payload)

    def disable_user(self, user_id: int) -> bool:
        return disable_user(self.conn, user_id)

    def get_current_user(self, user_id: Optional[int] = None) -> Optional[dict]:
        return get_current_user(self.conn, user_id)

    def update_current_user(self, payload: dict[str, Any], user_id: Optional[int] = None) -> Optional[dict]:
        return update_current_user(self.conn, payload, user_id)

    def get_current_user_settings(self, user_id: Optional[int] = None) -> dict:
        return get_current_user_settings(self.conn, user_id)

    def update_current_user_settings(self, payload: dict[str, Any], user_id: Optional[int] = None) -> dict:
        return update_current_user_settings(self.conn, payload, user_id)

    def append_chat_message(self, session_id: str, role: str, content: Optional[str], tool_calls: Optional[list] = None) -> dict:
        return append_chat_message(self.conn, session_id, role, content, tool_calls)

    def list_chat_messages(self, session_id: str, limit: int = 200) -> list[dict]:
        return list_chat_messages(self.conn, session_id, limit)

    def list_devices(self, status: Optional[str] = None) -> list[dict]:
        return list_devices(self.conn, status)

    def create_device(self, payload: dict[str, Any]) -> dict:
        return create_device(self.conn, payload)

    def get_device(self, device_id: int) -> Optional[dict]:
        return get_device(self.conn, device_id)

    def update_device(self, device_id: int, payload: dict[str, Any]) -> Optional[dict]:
        return update_device(self.conn, device_id, payload)

    def disable_device(self, device_id: int) -> bool:
        return disable_device(self.conn, device_id)

    def delete_device(self, device_id: int) -> bool:
        return delete_device(self.conn, device_id)

    def register_gateway(self, gateway_id: str, payload: dict[str, Any]) -> dict:
        return register_gateway(self.conn, gateway_id, payload)

    def list_gateways(self) -> list[dict]:
        return list_gateways(self.conn)

    def list_nodes(self, gateway_id: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
        return list_nodes(self.conn, gateway_id, status)

    def register_node(self, payload: dict[str, Any]) -> dict:
        return register_node(self.conn, payload)

    def get_node(self, node_id: str) -> Optional[dict]:
        return get_node(self.conn, node_id)

    def ingest_telemetry_batch(self, payload: dict[str, Any]) -> dict:
        return ingest_telemetry_batch(self.conn, payload)

    def query_readings(
        self,
        node_id: Optional[str] = None,
        metric: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        return query_readings(self.conn, node_id, metric, start, end, limit)

    def latest_readings(self) -> list[dict]:
        return latest_readings(self.conn)

    def latest_reading_id(self) -> int:
        return latest_reading_id(self.conn)

    def readings_after(self, cursor_id: int, limit: int = 500) -> list[dict]:
        return readings_after(self.conn, cursor_id, limit)

    def create_control_command(self, payload: dict[str, Any]) -> dict:
        return create_control_command(self.conn, payload)

    def get_control_command(self, command_id: str) -> Optional[dict]:
        return get_control_command(self.conn, command_id)

    def list_control_commands(self, status: Optional[str] = None) -> list[dict]:
        return list_control_commands(self.conn, status)

    def ingest_command_ack(self, payload: dict[str, Any]) -> dict:
        return ingest_command_ack(self.conn, payload)

    def create_alert(self, payload: dict[str, Any]) -> dict:
        return create_alert(self.conn, payload)

    def list_alerts(self, status: Optional[str] = None) -> list[dict]:
        return list_alerts(self.conn, status)

    def update_alert_status(self, alert_id: int, status: str) -> Optional[dict]:
        return update_alert_status(self.conn, alert_id, status)

    def list_alert_rules(self) -> list[dict]:
        return list_alert_rules(self.conn)

    def create_alert_rule(self, payload: dict[str, Any]) -> dict:
        return create_alert_rule(self.conn, payload)

    def update_alert_rule(self, rule_id: int, payload: dict[str, Any]) -> Optional[dict]:
        return update_alert_rule(self.conn, rule_id, payload)

    def delete_alert_rule(self, rule_id: int) -> bool:
        return delete_alert_rule(self.conn, rule_id)

    def dashboard_summary(self) -> dict:
        return dashboard_summary(self.conn)

    def create_prediction_run(self, payload: dict[str, Any]) -> dict:
        return create_prediction_run(self.conn, payload)

    def get_prediction_run(self, run_id: str) -> Optional[dict]:
        return get_prediction_run(self.conn, run_id)

    def list_prediction_runs(self, node_id: Optional[str] = None, metric: Optional[str] = None, limit: int = 50) -> list[dict]:
        return list_prediction_runs(self.conn, node_id, metric, limit)

    def activate_prediction_run(self, run_id: str) -> dict:
        return activate_prediction_run(self.conn, run_id)

    def get_prediction_metrics(self, node_id: Optional[str] = None, metric: Optional[str] = None, horizon_minutes: Optional[int] = None) -> list[dict]:
        return get_prediction_metrics(self.conn, node_id, metric, horizon_minutes)

    def get_latest_prediction(self, node_id: str, metric: str, horizon_minutes: Optional[int] = None) -> Optional[dict]:
        return get_latest_prediction(self.conn, node_id, metric, horizon_minutes)

    def prediction_features(self) -> dict:
        return prediction_features()
