from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT,
  role TEXT NOT NULL DEFAULT 'operator',
  status TEXT NOT NULL DEFAULT 'invited',
  settings TEXT NOT NULL DEFAULT '{}',
  last_login TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

INSERT OR IGNORE INTO users(name, email, role, status, settings)
VALUES (
  'Lab Admin',
  'operator@lab-317',
  'admin',
  'active',
  '{"theme_mode":"system","notifications":{"critical_alerts":true,"warning_alerts":true,"vacant_energy_waste":true},"default_view":"dashboard"}'
);

CREATE TABLE IF NOT EXISTS logical_devices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  device_type TEXT NOT NULL,
  location TEXT,
  status TEXT NOT NULL DEFAULT 'offline',
  client_id TEXT,
  protocol TEXT,
  ip_address TEXT,
  port INTEGER,
  source_uri TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_logical_devices_status ON logical_devices(status);
CREATE INDEX IF NOT EXISTS idx_logical_devices_type ON logical_devices(device_type);

CREATE TABLE IF NOT EXISTS gateways (
  gateway_id TEXT PRIMARY KEY,
  name TEXT,
  location TEXT,
  status TEXT NOT NULL DEFAULT 'offline',
  ip_address TEXT,
  version TEXT,
  last_seen_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zigbee_nodes (
  node_id TEXT PRIMARY KEY,
  gateway_id TEXT,
  parent_node_id TEXT,
  role TEXT NOT NULL DEFAULT 'sensor',
  area TEXT,
  ip_address TEXT,
  status TEXT NOT NULL DEFAULT 'offline',
  battery_mv INTEGER,
  rssi INTEGER,
  packet_loss REAL,
  last_seq INTEGER,
  last_seen_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sensor_readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id TEXT NOT NULL UNIQUE,
  gateway_id TEXT NOT NULL,
  batch_id TEXT,
  node_id TEXT NOT NULL,
  seq INTEGER,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT,
  rssi INTEGER,
  battery_mv INTEGER,
  flags INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'serial',
  captured_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sensor_node_metric_time ON sensor_readings(node_id, metric, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_sensor_batch ON sensor_readings(gateway_id, batch_id);

CREATE TABLE IF NOT EXISTS control_commands (
  command_id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  target_node_id TEXT NOT NULL,
  target_device TEXT NOT NULL,
  action TEXT NOT NULL,
  desired_state TEXT,
  reason TEXT,
  status TEXT NOT NULL,
  sent_at TEXT,
  acked_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_control_status ON control_commands(status, created_at DESC);

CREATE TABLE IF NOT EXISTS command_acks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  command_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  result TEXT NOT NULL,
  message TEXT,
  raw_frame TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ack_command ON command_acks(command_id);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_type TEXT NOT NULL,
  severity TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  node_id TEXT,
  camera_id TEXT,
  message TEXT NOT NULL,
  trigger_payload TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  acked_at TEXT,
  resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_status ON alerts(status, created_at DESC);

CREATE TABLE IF NOT EXISTS alert_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  metric TEXT NOT NULL CHECK (metric IN ('temperature', 'humidity', 'light', 'air_quality', 'battery_mv', 'rssi')),
  node_id TEXT,
  condition TEXT NOT NULL CHECK (condition IN ('gt', 'gte', 'lt', 'lte')),
  threshold REAL NOT NULL,
  severity TEXT NOT NULL DEFAULT 'medium' CHECK (severity IN ('low', 'medium', 'high', 'critical')),
  enabled INTEGER NOT NULL DEFAULT 1,
  cooldown_seconds INTEGER NOT NULL DEFAULT 300 CHECK (cooldown_seconds >= 0),
  last_fired_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled_metric ON alert_rules(enabled, metric);

CREATE TABLE IF NOT EXISTS prediction_runs (
  run_id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  model_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'completed',
  data_source TEXT NOT NULL DEFAULT 'real',
  horizon_minutes INTEGER NOT NULL,
  horizon_steps INTEGER,
  feature_columns TEXT NOT NULL,
  train_start_at TEXT,
  train_end_at TEXT,
  test_start_at TEXT,
  test_end_at TEXT,
  mae REAL,
  rmse REAL,
  r2 REAL,
  latency_ms REAL,
  model_size_bytes INTEGER,
  sample_predictions TEXT,
  model_blob BLOB,
  is_active INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prediction_runs_node_metric ON prediction_runs(node_id, metric, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_prediction_runs_active ON prediction_runs(node_id, metric, is_active);

CREATE TABLE IF NOT EXISTS outbox_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id TEXT NOT NULL UNIQUE,
  topic TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  retry_count INTEGER NOT NULL DEFAULT 0,
  next_retry_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  sent_at TEXT
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content TEXT,
  tool_calls_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
"""


def connect(path: Union[str, Path]) -> sqlite3.Connection:
    db_path = Path(path)
    if db_path != Path(":memory:"):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
