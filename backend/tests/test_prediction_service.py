import math
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from breezio_backend.db import init_db
from breezio_backend.services import BackendStore

try:
    import numpy  # noqa: F401
    import sklearn  # noqa: F401

    HAS_ML_DEPS = True
except ImportError:
    HAS_ML_DEPS = False


def seed_readings(conn, node_id="n1", metric="temperature", n=400, interval_seconds=60):
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        t = start + timedelta(seconds=i * interval_seconds)
        value = 24.0 + 3.0 * math.sin(2 * math.pi * (t.hour * 60 + t.minute) / 1440) + 0.05 * math.sin(i)
        rows.append((f"seed-{node_id}-{metric}-{i}", "gw-01", "seed", node_id, i, metric, value, "C", "simulated", t.isoformat()))
    conn.executemany(
        """
        INSERT INTO sensor_readings(message_id, gateway_id, batch_id, node_id, seq, metric, value, unit, source, captured_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


@unittest.skipUnless(HAS_ML_DEPS, "numpy/scikit-learn not installed")
class PredictionServiceTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        self.store = BackendStore(self.conn)
        seed_readings(self.conn)

    def test_create_run_trains_and_persists_metrics(self):
        run = self.store.create_prediction_run(
            {"target_node_id": "n1", "metric": "temperature", "model_name": "linear_regression", "horizon_minutes": 15}
        )
        self.assertEqual(run["status"], "completed")
        self.assertIsNotNone(run["mae"])
        self.assertTrue(run["is_active"])

    def test_second_better_run_takes_over_active_slot(self):
        self.store.create_prediction_run(
            {"target_node_id": "n1", "metric": "temperature", "model_name": "persistence", "horizon_minutes": 15}
        )
        second = self.store.create_prediction_run(
            {"target_node_id": "n1", "metric": "temperature", "model_name": "linear_regression", "horizon_minutes": 15}
        )
        runs = self.store.list_prediction_runs(node_id="n1", metric="temperature")
        active_runs = [r for r in runs if r["is_active"]]
        self.assertEqual(len(active_runs), 1)
        self.assertEqual(active_runs[0]["run_id"], second["run_id"])

    def test_prediction_metrics_returns_one_row_per_model(self):
        for model_name in ("persistence", "linear_regression", "random_forest", "hist_gradient_boosting"):
            self.store.create_prediction_run(
                {"target_node_id": "n1", "metric": "temperature", "model_name": model_name, "horizon_minutes": 10}
            )
        metrics = self.store.get_prediction_metrics(node_id="n1", metric="temperature")
        self.assertEqual(
            {m["model_name"] for m in metrics},
            {"persistence", "linear_regression", "random_forest", "hist_gradient_boosting"},
        )

    def test_latest_prediction_uses_active_model(self):
        self.store.create_prediction_run(
            {"target_node_id": "n1", "metric": "temperature", "model_name": "random_forest", "horizon_minutes": 15}
        )
        latest = self.store.get_latest_prediction("n1", "temperature")
        self.assertNotIn("error", latest)
        self.assertIn("yhat", latest)
        self.assertEqual(latest["model_name"], "random_forest")

    def test_manual_activate_overrides_auto_selection(self):
        first = self.store.create_prediction_run(
            {"target_node_id": "n1", "metric": "temperature", "model_name": "linear_regression", "horizon_minutes": 15}
        )
        self.store.create_prediction_run(
            {"target_node_id": "n1", "metric": "temperature", "model_name": "random_forest", "horizon_minutes": 15}
        )
        activated = self.store.activate_prediction_run(first["run_id"])
        self.assertTrue(activated["is_active"])
        latest = self.store.get_latest_prediction("n1", "temperature")
        self.assertEqual(latest["model_name"], "linear_regression")


class PredictionServiceWithoutMlDepsTest(unittest.TestCase):
    """Covers the parts of the prediction API that must work with zero ML deps."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        self.store = BackendStore(self.conn)
        seed_readings(self.conn)

    def test_latest_prediction_returns_none_without_any_run(self):
        self.assertIsNone(self.store.get_latest_prediction("n1", "temperature"))

    def test_features_endpoint_describes_pipeline(self):
        features = self.store.prediction_features()
        self.assertIn("feature_columns", features)
        self.assertIn("models", features)

    def test_persistence_run_trains_without_ml_deps(self):
        run = self.store.create_prediction_run(
            {"target_node_id": "n1", "metric": "temperature", "model_name": "persistence", "horizon_minutes": 15}
        )
        self.assertEqual(run["status"], "completed")
        self.assertIsNotNone(run["mae"])


if __name__ == "__main__":
    unittest.main()
