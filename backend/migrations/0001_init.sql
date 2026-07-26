CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  email VARCHAR(160) NOT NULL UNIQUE,
  password_hash VARCHAR(255),
  role VARCHAR(20) NOT NULL DEFAULT 'operator',
  status VARCHAR(20) NOT NULL DEFAULT 'invited',
  settings JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_login TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT users_role_check CHECK (role IN ('admin', 'operator', 'viewer')),
  CONSTRAINT users_status_check CHECK (status IN ('active', 'invited', 'disabled'))
);

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

INSERT INTO users(name, email, role, status, settings)
VALUES (
  'Lab Admin',
  'operator@lab-317',
  'admin',
  'active',
  '{"theme_mode":"system","notifications":{"critical_alerts":true,"warning_alerts":true,"vacant_energy_waste":true},"default_view":"dashboard"}'::jsonb
)
ON CONFLICT(email) DO NOTHING;

CREATE TABLE IF NOT EXISTS logical_devices (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  device_type VARCHAR(50) NOT NULL,
  location VARCHAR(255),
  status VARCHAR(20) NOT NULL DEFAULT 'offline',
  client_id VARCHAR(128),
  protocol VARCHAR(20),
  ip_address VARCHAR(255),
  port INTEGER,
  source_uri VARCHAR(512),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT logical_devices_status_check CHECK (status IN ('online', 'offline'))
);

CREATE INDEX IF NOT EXISTS idx_logical_devices_status
  ON logical_devices(status);

CREATE INDEX IF NOT EXISTS idx_logical_devices_type
  ON logical_devices(device_type);

CREATE TABLE IF NOT EXISTS gateways (
  id BIGSERIAL PRIMARY KEY,
  gateway_id VARCHAR(64) NOT NULL UNIQUE,
  name VARCHAR(128),
  location VARCHAR(255),
  status VARCHAR(20) NOT NULL DEFAULT 'offline',
  ip_address INET,
  version VARCHAR(64),
  last_seen_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT gateways_status_check CHECK (status IN ('online', 'offline', 'degraded'))
);

CREATE TABLE IF NOT EXISTS zigbee_nodes (
  id BIGSERIAL PRIMARY KEY,
  node_id VARCHAR(64) NOT NULL UNIQUE,
  gateway_id VARCHAR(64) REFERENCES gateways(gateway_id),
  parent_node_id VARCHAR(64),
  role VARCHAR(20) NOT NULL DEFAULT 'sensor',
  area VARCHAR(128),
  status VARCHAR(20) NOT NULL DEFAULT 'offline',
  battery_mv INT,
  rssi INT,
  packet_loss NUMERIC(6,4),
  last_seq INT,
  last_seen_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT zigbee_nodes_role_check CHECK (role IN ('sensor', 'router', 'sink', 'actuator')),
  CONSTRAINT zigbee_nodes_status_check CHECK (status IN ('online', 'offline', 'fault'))
);

CREATE TABLE IF NOT EXISTS sensor_readings (
  id BIGSERIAL PRIMARY KEY,
  message_id VARCHAR(128) NOT NULL UNIQUE,
  gateway_id VARCHAR(64) NOT NULL,
  batch_id VARCHAR(64),
  node_id VARCHAR(64) NOT NULL,
  seq INT,
  metric VARCHAR(50) NOT NULL,
  value DOUBLE PRECISION NOT NULL,
  unit VARCHAR(20),
  rssi INT,
  battery_mv INT,
  flags INT NOT NULL DEFAULT 0,
  source VARCHAR(20) NOT NULL DEFAULT 'serial',
  captured_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sensor_node_metric_time
  ON sensor_readings(node_id, metric, captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_sensor_batch
  ON sensor_readings(gateway_id, batch_id);

CREATE TABLE IF NOT EXISTS control_commands (
  id BIGSERIAL PRIMARY KEY,
  command_id VARCHAR(64) NOT NULL UNIQUE,
  source VARCHAR(20) NOT NULL,
  target_node_id VARCHAR(64) NOT NULL,
  target_device VARCHAR(32) NOT NULL,
  action VARCHAR(32) NOT NULL,
  desired_state JSONB,
  reason VARCHAR(255),
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  created_by BIGINT,
  sent_at TIMESTAMPTZ,
  acked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT control_status_check CHECK (status IN ('pending', 'sent', 'acked', 'timeout', 'failed'))
);

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'control_commands'
      AND column_name = 'desired_state'
      AND data_type <> 'jsonb'
  ) THEN
    ALTER TABLE control_commands
      ALTER COLUMN desired_state TYPE JSONB
      USING to_jsonb(desired_state);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_control_status
  ON control_commands(status, created_at DESC);

CREATE TABLE IF NOT EXISTS command_acks (
  id BIGSERIAL PRIMARY KEY,
  command_id VARCHAR(64) NOT NULL,
  node_id VARCHAR(64) NOT NULL,
  result VARCHAR(20) NOT NULL,
  message VARCHAR(255),
  raw_frame VARCHAR(255),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ack_command ON command_acks(command_id);

CREATE TABLE IF NOT EXISTS alerts (
  id BIGSERIAL PRIMARY KEY,
  alert_type VARCHAR(50) NOT NULL,
  severity VARCHAR(20) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  node_id VARCHAR(64),
  camera_id VARCHAR(64),
  message TEXT NOT NULL,
  snapshot_artifact_id BIGINT,
  trigger_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  acked_at TIMESTAMPTZ,
  resolved_at TIMESTAMPTZ,
  CONSTRAINT alerts_status_check CHECK (status IN ('active', 'acked', 'resolved'))
);

CREATE INDEX IF NOT EXISTS idx_alert_status
  ON alerts(status, created_at DESC);

CREATE TABLE IF NOT EXISTS prediction_runs (
  id BIGSERIAL PRIMARY KEY,
  run_id VARCHAR(64) NOT NULL UNIQUE,
  node_id VARCHAR(64) NOT NULL,
  metric VARCHAR(50) NOT NULL,
  model_name VARCHAR(64) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'completed',
  data_source VARCHAR(20) NOT NULL DEFAULT 'real',
  horizon_minutes INT NOT NULL,
  horizon_steps INT,
  feature_columns JSONB NOT NULL DEFAULT '[]'::jsonb,
  train_start_at TIMESTAMPTZ,
  train_end_at TIMESTAMPTZ,
  test_start_at TIMESTAMPTZ,
  test_end_at TIMESTAMPTZ,
  mae DOUBLE PRECISION,
  rmse DOUBLE PRECISION,
  r2 DOUBLE PRECISION,
  latency_ms DOUBLE PRECISION,
  model_size_bytes BIGINT,
  sample_predictions JSONB NOT NULL DEFAULT '[]'::jsonb,
  model_blob BYTEA,
  is_active BOOLEAN NOT NULL DEFAULT FALSE,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT prediction_status_check CHECK (status IN ('queued', 'running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_prediction_runs_node_metric
  ON prediction_runs(node_id, metric, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_prediction_runs_one_active
  ON prediction_runs(node_id, metric)
  WHERE is_active;

CREATE TABLE IF NOT EXISTS vision_events (
  id BIGSERIAL PRIMARY KEY,
  event_id VARCHAR(64) NOT NULL UNIQUE,
  trace_id VARCHAR(64),
  camera_id VARCHAR(64),
  frame_id VARCHAR(128),
  task VARCHAR(64) NOT NULL,
  model_id VARCHAR(128),
  model_version VARCHAR(64),
  runtime VARCHAR(64),
  latency_ms DOUBLE PRECISION,
  people_count INT,
  scene_label VARCHAR(64),
  identified BOOLEAN,
  person_id VARCHAR(128),
  display_name VARCHAR(160),
  confidence DOUBLE PRECISION,
  outputs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  source VARCHAR(64) NOT NULL DEFAULT 'inference-service',
  captured_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT vision_task_check CHECK (task IN ('scene-classification', 'person-detection', 'face-identification'))
);

CREATE INDEX IF NOT EXISTS idx_vision_events_camera_time
  ON vision_events(camera_id, captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_vision_events_task_time
  ON vision_events(task, captured_at DESC);

CREATE TABLE IF NOT EXISTS object_artifacts (
  id BIGSERIAL PRIMARY KEY,
  bucket VARCHAR(128) NOT NULL,
  object_key VARCHAR(512) NOT NULL,
  artifact_type VARCHAR(32) NOT NULL,
  content_type VARCHAR(128),
  size_bytes BIGINT,
  sha256 VARCHAR(128),
  etag VARCHAR(128),
  meta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT object_artifact_type_check CHECK (artifact_type IN ('snapshot', 'dataset', 'model', 'onnx', 'benchmark', 'report')),
  CONSTRAINT object_artifacts_bucket_key_unique UNIQUE (bucket, object_key)
);

CREATE INDEX IF NOT EXISTS idx_object_artifacts_type_time
  ON object_artifacts(artifact_type, created_at DESC);

CREATE TABLE IF NOT EXISTS outbox_messages (
  id BIGSERIAL PRIMARY KEY,
  message_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
  topic TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  retry_count INT NOT NULL DEFAULT 0,
  next_retry_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  sent_at TIMESTAMPTZ
);
