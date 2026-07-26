import math
import unittest
from datetime import datetime, timedelta, timezone

from breezio_backend.prediction import (
    build_feature_rows,
    infer_horizon_steps,
    predict_next,
    train_and_evaluate,
)

try:
    import numpy  # noqa: F401
    import sklearn  # noqa: F401

    HAS_ML_DEPS = True
except ImportError:
    HAS_ML_DEPS = False

requires_ml_deps = unittest.skipUnless(HAS_ML_DEPS, "numpy/scikit-learn not installed")


def synthetic_readings(n=400, interval_seconds=60, start=None, noise=0.05):
    start = start or datetime(2026, 6, 1, tzinfo=timezone.utc)
    readings = []
    for i in range(n):
        t = start + timedelta(seconds=i * interval_seconds)
        # diurnal cycle plus a slow drift, deterministic "noise" so tests are stable
        value = 24.0 + 3.0 * math.sin(2 * math.pi * (t.hour * 60 + t.minute) / 1440) + noise * math.sin(i)
        readings.append({"captured_at": t.isoformat(), "value": value})
    return readings


class InferHorizonStepsTest(unittest.TestCase):
    def test_infers_steps_from_median_gap(self):
        readings = synthetic_readings(n=10, interval_seconds=60)
        self.assertEqual(infer_horizon_steps(readings, horizon_minutes=15), 15)

    def test_returns_at_least_one_step(self):
        readings = synthetic_readings(n=10, interval_seconds=600)
        self.assertEqual(infer_horizon_steps(readings, horizon_minutes=1), 1)


class BuildFeatureRowsTest(unittest.TestCase):
    def test_row_count_matches_expected_window_and_horizon(self):
        readings = synthetic_readings(n=100, interval_seconds=60)
        rows, targets, target_times = build_feature_rows(readings, horizon_steps=15)
        self.assertEqual(len(rows), 100 - 14 - 15)
        self.assertEqual(len(rows), len(targets))
        self.assertEqual(len(rows), len(target_times))
        self.assertIn("rolling_mean_15", rows[0])
        self.assertIn("is_opening_hours", rows[0])


class TrainAndEvaluateTest(unittest.TestCase):
    def test_persistence_model_has_no_blob_and_reports_metrics(self):
        readings = synthetic_readings(n=400, interval_seconds=60)
        result = train_and_evaluate(readings, "persistence", horizon_minutes=15)
        self.assertNotIn("error", result)
        self.assertIsNone(result["model_blob"])
        self.assertEqual(result["model_size_bytes"], 0)
        self.assertGreaterEqual(result["mae"], 0)
        self.assertGreaterEqual(result["rmse"], 0)
        self.assertTrue(len(result["sample_predictions"]) > 0)

    @requires_ml_deps
    def test_linear_regression_beats_persistence_on_smooth_synthetic_signal(self):
        readings = synthetic_readings(n=400, interval_seconds=60)
        persistence = train_and_evaluate(readings, "persistence", horizon_minutes=15)
        linear = train_and_evaluate(readings, "linear_regression", horizon_minutes=15)
        self.assertIsInstance(linear["model_blob"], bytes)
        self.assertGreater(linear["model_size_bytes"], 0)
        self.assertLessEqual(linear["mae"], persistence["mae"])

    @requires_ml_deps
    def test_random_forest_and_hist_gradient_boosting_train_without_error(self):
        readings = synthetic_readings(n=400, interval_seconds=60)
        for model_name in ("random_forest", "hist_gradient_boosting"):
            result = train_and_evaluate(readings, model_name, horizon_minutes=10)
            self.assertNotIn("error", result, msg=model_name)
            self.assertIsInstance(result["model_blob"], bytes)

    def test_reports_error_when_not_enough_data(self):
        readings = synthetic_readings(n=10, interval_seconds=60)
        result = train_and_evaluate(readings, "linear_regression", horizon_minutes=15)
        self.assertIn("error", result)

    def test_rejects_unsupported_model_name(self):
        readings = synthetic_readings(n=400, interval_seconds=60)
        with self.assertRaisesRegex(ValueError, "unsupported model_name"):
            train_and_evaluate(readings, "lstm", horizon_minutes=15)


class PredictNextTest(unittest.TestCase):
    def test_persistence_forecast_equals_latest_value(self):
        readings = synthetic_readings(n=50, interval_seconds=60)
        result = predict_next(readings, "persistence", horizon_minutes=15, model_blob=None)
        self.assertAlmostEqual(result["yhat"], readings[-1]["value"], places=4)

    @requires_ml_deps
    def test_trained_model_forecast_uses_unpickled_estimator(self):
        readings = synthetic_readings(n=400, interval_seconds=60)
        trained = train_and_evaluate(readings, "linear_regression", horizon_minutes=15)
        result = predict_next(readings, "linear_regression", horizon_minutes=15, model_blob=trained["model_blob"])
        self.assertIn("yhat", result)
        self.assertIn("target_time", result)

    def test_reports_error_when_history_too_short(self):
        readings = synthetic_readings(n=5, interval_seconds=60)
        result = predict_next(readings, "persistence", horizon_minutes=15, model_blob=None)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
