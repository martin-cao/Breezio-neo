-- Alert rules: user-managed threshold rules evaluated on telemetry ingestion.
-- A rule fires an alert (existing alerts table) when a matching reading
-- crosses the threshold; cooldown_seconds suppresses re-firing storms.
CREATE TABLE IF NOT EXISTS alert_rules (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  metric TEXT NOT NULL CHECK (metric IN ('temperature', 'humidity', 'light', 'air_quality', 'battery_mv', 'rssi')),
  node_id TEXT,               -- NULL = applies to all nodes
  condition TEXT NOT NULL CHECK (condition IN ('gt', 'gte', 'lt', 'lte')),
  threshold DOUBLE PRECISION NOT NULL,
  severity TEXT NOT NULL DEFAULT 'medium' CHECK (severity IN ('low', 'medium', 'high', 'critical')),
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  cooldown_seconds INTEGER NOT NULL DEFAULT 300 CHECK (cooldown_seconds >= 0),
  last_fired_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled_metric ON alert_rules(enabled, metric);
