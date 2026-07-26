from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


VISION_TASKS = {"scene-classification", "person-detection", "face-identification", "anomaly-detection", "scene-caption"}


class VisionEventSinkUnavailable(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_vision_event(payload: dict[str, Any]) -> dict[str, Any]:
    task = payload["task"]
    if task not in VISION_TASKS:
        raise ValueError(f"unsupported vision task: {task}")

    outputs = payload.get("outputs") or {}
    if not isinstance(outputs, dict):
        raise ValueError("outputs must be an object")
    input_payload = payload.get("input") or {}
    if not isinstance(input_payload, dict):
        raise ValueError("input must be an object")

    confidence = outputs.get("scene_confidence")
    if confidence is None:
        confidence = outputs.get("confidence")

    return {
        "event_id": str(payload.get("event_id") or f"vision-{uuid4().hex[:12]}"),
        "trace_id": payload.get("trace_id"),
        "camera_id": payload.get("camera_id") or input_payload.get("camera_id"),
        "frame_id": payload.get("frame_id") or input_payload.get("frame_id"),
        "task": task,
        "model_id": payload.get("model_id"),
        "model_version": payload.get("model_version"),
        "runtime": payload.get("runtime"),
        "latency_ms": payload.get("latency_ms"),
        "people_count": outputs.get("people_count"),
        "scene_label": outputs.get("scene_label"),
        "identified": outputs.get("identified"),
        "person_id": outputs.get("person_id"),
        "display_name": outputs.get("display_name"),
        "confidence": confidence,
        "outputs": outputs,
        "source": payload.get("source", "inference-service"),
        "captured_at": payload.get("captured_at") or now_iso(),
    }


class InMemoryVisionEventSink:
    """Non-persistent test double; production storage is PostgreSQL."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._next_id = 1

    def ingest_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        item = {"id": self._next_id, **normalize_vision_event(payload)}
        self._next_id += 1
        self._events.insert(0, item)
        return dict(item)

    def list_events(
        self,
        camera_id: Optional[str] = None,
        task: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        items = self._events
        if camera_id:
            items = [item for item in items if item.get("camera_id") == camera_id]
        if task:
            items = [item for item in items if item.get("task") == task]
        return [dict(item) for item in items[:limit]]


class PostgresVisionEventSink:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def ingest_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        item = normalize_vision_event(payload)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vision_events(
                      event_id, trace_id, camera_id, frame_id, task, model_id, model_version, runtime,
                      latency_ms, people_count, scene_label, identified, person_id, display_name,
                      confidence, outputs_json, source, captured_at
                    ) VALUES (
                      %(event_id)s, %(trace_id)s, %(camera_id)s, %(frame_id)s, %(task)s,
                      %(model_id)s, %(model_version)s, %(runtime)s, %(latency_ms)s,
                      %(people_count)s, %(scene_label)s, %(identified)s, %(person_id)s,
                      %(display_name)s, %(confidence)s, %(outputs_json)s::jsonb,
                      %(source)s, %(captured_at)s
                    )
                    RETURNING *
                    """,
                    {**item, "outputs_json": json.dumps(item["outputs"], separators=(",", ":"))},
                )
                row = cur.fetchone()
            conn.commit()
        return self._row_to_event(row)

    def list_events(
        self,
        camera_id: Optional[str] = None,
        task: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: dict[str, Any] = {"limit": limit}
        if camera_id:
            clauses.append("camera_id = %(camera_id)s")
            params["camera_id"] = camera_id
        if task:
            clauses.append("task = %(task)s")
            params["task"] = task
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM vision_events {where} ORDER BY captured_at DESC, id DESC LIMIT %(limit)s",
                    params,
                )
                rows = cur.fetchall()
        return [self._row_to_event(row) for row in rows]

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise VisionEventSinkUnavailable("psycopg is required for PostgreSQL vision event storage") from exc
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _row_to_event(self, row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["outputs"] = item.pop("outputs_json") or {}
        return item
