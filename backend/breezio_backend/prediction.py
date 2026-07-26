"""Temperature/humidity forecasting: feature engineering, training and evaluation.

Pure functions only — no sqlite access here, so this module is testable without
a database and reusable if prediction later moves to its own worker process
(see docs/backend-architecture.md `prediction-worker`).

Time-series note: readings are not resampled to a fixed grid. Instead we infer
the dominant sampling interval from the median gap between consecutive
readings and convert `horizon_minutes` into a step count. This is a documented
simplification (docs/prediction-model-design.md recommends real resampling)
that is accurate enough for roughly-regular sensor/simulator cadences and
avoids building a gap-filling resampler under the project's time budget.
"""

from __future__ import annotations

import pickle
from datetime import datetime, timedelta
from statistics import mean, pstdev
from time import perf_counter
from typing import Any, Optional

MODEL_NAMES = ("persistence", "linear_regression", "random_forest", "hist_gradient_boosting", "mlp_regressor")
FEATURE_COLUMNS = (
    "lag_1",
    "lag_3",
    "lag_5",
    "rolling_mean_5",
    "rolling_mean_15",
    "rolling_std_15",
    "delta_1",
    "delta_5",
    "value",
    "hour",
    "minute_of_day",
    "day_of_week",
    "is_opening_hours",
)
MIN_WINDOW = 15
MIN_ROWS_FOR_TRAINING = 30
# Readings are resampled onto a per-horizon grid of this many steps before
# training and forecasting, so a 6h horizon at the wifi-gateway's 1s cadence
# doesn't need tens of thousands of rows per query.
FORECAST_GRID_STEPS = 72


def forecast_bucket_seconds(horizon_minutes: int) -> int:
    return max(1, (horizon_minutes * 60) // FORECAST_GRID_STEPS)


def prediction_feature_description() -> dict[str, Any]:
    return {
        "feature_columns": list(FEATURE_COLUMNS),
        "min_window": MIN_WINDOW,
        "min_training_rows": MIN_ROWS_FOR_TRAINING,
        "split": {"train": 0.70, "validation": 0.15, "test": 0.15},
        "horizon_minutes_supported": [10, 15],
        "models": list(MODEL_NAMES),
        "notes": (
            "Horizon is converted from minutes to steps using the median gap "
            "between consecutive readings for the node/metric, not a fixed-grid "
            "resample. See docs/prediction-model-design.md."
        ),
    }


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def infer_horizon_steps(readings_asc: list[dict[str, Any]], horizon_minutes: int) -> int:
    if len(readings_asc) < 2:
        return 1
    gaps = [
        (_parse_time(b["captured_at"]) - _parse_time(a["captured_at"])).total_seconds()
        for a, b in zip(readings_asc, readings_asc[1:])
    ]
    gaps = [gap for gap in gaps if gap > 0]
    if not gaps:
        return 1
    gaps.sort()
    median_gap = gaps[len(gaps) // 2]
    return max(1, round((horizon_minutes * 60) / median_gap))


def _time_features(dt: datetime) -> dict[str, float]:
    return {
        "hour": float(dt.hour),
        "minute_of_day": float(dt.hour * 60 + dt.minute),
        "day_of_week": float(dt.weekday()),
        "is_opening_hours": 1.0 if 8 <= dt.hour < 22 else 0.0,
    }


def build_feature_rows(
    readings_asc: list[dict[str, Any]], horizon_steps: int
) -> tuple[list[dict[str, float]], list[float], list[str]]:
    """Build (feature_rows, targets, target_captured_at) from chronological readings.

    readings_asc items need `captured_at` (ISO string) and `value` (float).
    """
    values = [float(r["value"]) for r in readings_asc]
    rows: list[dict[str, float]] = []
    targets: list[float] = []
    target_times: list[str] = []
    for i in range(MIN_WINDOW - 1, len(values) - horizon_steps):
        window15 = values[max(0, i - 14) : i + 1]
        dt = _parse_time(readings_asc[i]["captured_at"])
        row = {
            "lag_1": values[i - 1],
            "lag_3": values[i - 3],
            "lag_5": values[i - 5],
            "rolling_mean_5": mean(values[i - 4 : i + 1]),
            "rolling_mean_15": mean(window15),
            "rolling_std_15": pstdev(window15) if len(window15) > 1 else 0.0,
            "delta_1": values[i] - values[i - 1],
            "delta_5": values[i] - values[i - 5],
            "value": values[i],
            **_time_features(dt),
        }
        rows.append(row)
        targets.append(values[i + horizon_steps])
        target_times.append(readings_asc[i + horizon_steps]["captured_at"])
    return rows, targets, target_times


def _rows_to_matrix(rows: list[dict[str, float]]):
    import numpy as np

    return np.array([[row[col] for col in FEATURE_COLUMNS] for row in rows], dtype=float)


def _build_estimator(model_name: str):
    if model_name == "linear_regression":
        from sklearn.linear_model import LinearRegression

        return LinearRegression()
    if model_name == "random_forest":
        from sklearn.ensemble import RandomForestRegressor

        return RandomForestRegressor(n_estimators=200, max_depth=8, random_state=42)
    if model_name == "hist_gradient_boosting":
        from sklearn.ensemble import HistGradientBoostingRegressor

        return HistGradientBoostingRegressor(random_state=42)
    if model_name == "mlp_regressor":
        from sklearn.neural_network import MLPRegressor
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        return make_pipeline(
            StandardScaler(),
            MLPRegressor(
                hidden_layer_sizes=(32, 16),
                alpha=1e-2,
                max_iter=500,
                early_stopping=True,
                random_state=42,
            ),
        )
    raise ValueError(f"unsupported model_name: {model_name}")


def split_ranges(n: int) -> tuple[int, int]:
    """Return (train_end, val_end) indices for a 70/15/15 chronological split."""
    train_end = max(1, round(n * 0.7))
    val_end = max(train_end + 1, round(n * 0.85))
    return min(train_end, n - 1), min(val_end, n)


def train_and_evaluate(
    readings_asc: list[dict[str, Any]],
    model_name: str,
    horizon_minutes: int,
) -> dict[str, Any]:
    """Train `model_name` on readings_asc and evaluate on a chronological test split.

    Returns a dict with metrics, sample predictions, ranges and (for non-persistence
    models) a pickled model blob, or an `error` key if there isn't enough data.
    """
    if model_name not in MODEL_NAMES:
        raise ValueError(f"unsupported model_name: {model_name}")

    horizon_steps = infer_horizon_steps(readings_asc, horizon_minutes)
    rows, targets, target_times = build_feature_rows(readings_asc, horizon_steps)
    if len(rows) < MIN_ROWS_FOR_TRAINING:
        return {
            "error": (
                f"not enough readings to train: have {len(rows)} feature rows, "
                f"need at least {MIN_ROWS_FOR_TRAINING}"
            ),
            "horizon_steps": horizon_steps,
        }

    train_end, val_end = split_ranges(len(rows))
    train_start_at = readings_asc[MIN_WINDOW - 1]["captured_at"]
    train_end_at = target_times[train_end - 1]
    test_start_at = target_times[val_end]
    test_end_at = target_times[-1]

    test_rows = rows[val_end:]
    test_targets = targets[val_end:]
    test_times = target_times[val_end:]

    model_blob: Optional[bytes] = None
    if model_name == "persistence":
        predictions = [row["value"] for row in test_rows]
        started = perf_counter()
        for row in test_rows:
            _ = row["value"]
        latency_ms = round(((perf_counter() - started) / max(1, len(test_rows))) * 1000, 4)
        model_size_bytes = 0
    else:
        train_rows = rows[:train_end]
        train_targets = targets[:train_end]
        x_train = _rows_to_matrix(train_rows)
        y_train = train_targets
        x_test = _rows_to_matrix(test_rows)

        estimator = _build_estimator(model_name)
        estimator.fit(x_train, y_train)

        started = perf_counter()
        predictions = list(estimator.predict(x_test))
        latency_ms = round(((perf_counter() - started) / max(1, len(x_test))) * 1000, 4)

        model_blob = pickle.dumps(estimator)
        model_size_bytes = len(model_blob)

    mae, rmse, r2 = _regression_metrics(test_targets, predictions)
    sample = [
        {"captured_at": t, "actual": round(a, 4), "predicted": round(p, 4)}
        for t, a, p in zip(test_times, test_targets, predictions)
    ][-50:]

    return {
        "horizon_steps": horizon_steps,
        "train_start_at": train_start_at,
        "train_end_at": train_end_at,
        "test_start_at": test_start_at,
        "test_end_at": test_end_at,
        "mae": mae,
        "rmse": rmse,
        "r2": r2,
        "latency_ms": latency_ms,
        "model_size_bytes": model_size_bytes,
        "sample_predictions": sample,
        "model_blob": model_blob,
    }


def _regression_metrics(actual: list[float], predicted: list[float]) -> tuple[float, float, float]:
    n = len(actual)
    errors = [p - a for a, p in zip(actual, predicted)]
    mae = round(sum(abs(e) for e in errors) / n, 4)
    rmse = round((sum(e * e for e in errors) / n) ** 0.5, 4)
    actual_mean = sum(actual) / n
    ss_tot = sum((a - actual_mean) ** 2 for a in actual)
    ss_res = sum(e * e for e in errors)
    r2 = round(1 - ss_res / ss_tot, 4) if ss_tot > 0 else 0.0
    return mae, rmse, r2


def _plausible_bounds(values: list[float]) -> tuple[float, float]:
    """A generous [lo, hi] band around recently observed values.

    Tree models (random_forest, hist_gradient_boosting) can only predict
    within their training range, but a small MLP can extrapolate to
    physically absurd values (e.g. 90 degC on a room-temperature sensor)
    when it overfits a short window. Metrics are computed unclamped so the
    comparison table still shows such a model as bad; only served forecasts
    are clamped, so a badly-fit model degrades to "looks like persistence"
    instead of rendering nonsense on the chart.
    """
    lo, hi = min(values), max(values)
    pad = max(4 * pstdev(values) if len(values) > 1 else 0.0, (hi - lo) * 0.5, 1.0)
    return lo - pad, hi + pad


def _clamp(yhat: float, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return min(max(yhat, lo), hi)


def _feature_row_at(readings_asc: list[dict[str, Any]], values: list[float], i: int) -> dict[str, float]:
    window15 = values[max(0, i - 14) : i + 1]
    dt = _parse_time(readings_asc[i]["captured_at"])
    return {
        "lag_1": values[i - 1],
        "lag_3": values[i - 3],
        "lag_5": values[i - 5],
        "rolling_mean_5": mean(values[i - 4 : i + 1]),
        "rolling_mean_15": mean(window15),
        "rolling_std_15": pstdev(window15) if len(window15) > 1 else 0.0,
        "delta_1": values[i] - values[i - 1],
        "delta_5": values[i] - values[i - 5],
        "value": values[i],
        **_time_features(dt),
    }


def predict_next(readings_asc: list[dict[str, Any]], model_name: str, horizon_minutes: int, model_blob: Optional[bytes]) -> dict[str, Any]:
    """Forecast `horizon_minutes` ahead of the most recent reading.

    For persistence this is just the latest observed value. For trained models
    this rebuilds the latest feature row and calls the unpickled estimator.
    """
    horizon_steps = infer_horizon_steps(readings_asc, horizon_minutes)
    if len(readings_asc) < MIN_WINDOW:
        return {"error": f"not enough readings to forecast: need at least {MIN_WINDOW}"}

    values = [float(r["value"]) for r in readings_asc]
    row = _feature_row_at(readings_asc, values, len(values) - 1)

    if model_name == "persistence" or model_blob is None:
        yhat = row["value"]
    else:
        estimator = pickle.loads(model_blob)
        x = _rows_to_matrix([row])
        yhat = _clamp(float(estimator.predict(x)[0]), _plausible_bounds(values))

    latest_time = _parse_time(readings_asc[-1]["captured_at"])
    target_time = latest_time + timedelta(seconds=horizon_minutes * 60)
    return {
        "yhat": round(yhat, 4),
        "target_time": target_time.isoformat(),
        "horizon_steps": horizon_steps,
    }


# How far back (in multiples of the horizon) the "historical prediction"
# curve reaches. Mirrors the frontend's `historyMultiplier` (main.tsx) so the
# backtest curve can span the same window as the "actual history" line the
# chart plots alongside it — keep the two in sync if either changes.
ROLLING_HISTORY_MULTIPLIER = 4


def _subsample(items: list[Any], max_points: int) -> list[Any]:
    if len(items) <= max_points:
        return items
    stride = len(items) / max_points
    picked = sorted({int(k * stride) for k in range(max_points)} | {len(items) - 1})
    return [items[i] for i in picked]


def predict_rolling_series(
    readings_asc: list[dict[str, Any]],
    model_name: str,
    horizon_minutes: int,
    model_blob: Optional[bytes],
    max_points_each: int = 96,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reapply the model at every bucketed point, oldest-first, split into
    (historical, future) by whether each point's target time has already
    happened.

    The metrics table's MAE/RMSE/R² come from `train_and_evaluate`'s
    statistically honest held-out test split, which is intentionally short
    (~15% of the training window). That split is fine for scoring a model but
    too short to plot alongside "actual history" without looking like a stub.
    This instead reruns the model over the whole bucketed window read by the
    caller, so the historical curve can span as much of the chart as there is
    data for — a rolling backtest for display, not a second evaluation.
    """
    if len(readings_asc) < MIN_WINDOW:
        return [], []

    values = [float(r["value"]) for r in readings_asc]
    latest_time = _parse_time(readings_asc[-1]["captured_at"])

    estimator = None
    if model_name != "persistence" and model_blob is not None:
        estimator = pickle.loads(model_blob)

    indices = list(range(MIN_WINDOW - 1, len(values)))
    rows = [_feature_row_at(readings_asc, values, i) for i in indices]
    if estimator is not None:
        bounds = _plausible_bounds(values)
        preds = [_clamp(float(p), bounds) for p in estimator.predict(_rows_to_matrix(rows))]
    else:
        preds = [row["value"] for row in rows]

    historical: list[dict[str, Any]] = []
    future: list[dict[str, Any]] = []
    for i, yhat in zip(indices, preds):
        target_time = _parse_time(readings_asc[i]["captured_at"]) + timedelta(seconds=horizon_minutes * 60)
        point = {"target_time": target_time.isoformat(), "yhat": round(yhat, 4)}
        (historical if target_time <= latest_time else future).append(point)

    return _subsample(historical, max_points_each), _subsample(future, max_points_each)
