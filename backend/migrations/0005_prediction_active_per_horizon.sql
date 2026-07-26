-- Each forecast horizon (30m/2h/6h/...) now keeps its own independently
-- active model per (node, metric), instead of one horizon winning "active"
-- for the whole metric and leaving the others with no live forecast.
DROP INDEX IF EXISTS idx_prediction_runs_one_active;

CREATE UNIQUE INDEX IF NOT EXISTS idx_prediction_runs_one_active_per_horizon
  ON prediction_runs(node_id, metric, horizon_minutes)
  WHERE is_active;
