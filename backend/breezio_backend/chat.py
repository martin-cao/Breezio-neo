"""Tool-calling lab-assistant orchestration.

Stateless per turn: the full transcript is replayed to the LLM on every
message. Each tool is a thin wrapper over an existing store/vision/inference
method — no new business logic lives here. Tool failures are fed back to the
LLM as JSON error strings so it can verbalize them instead of the request
500ing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

from .camera_sources import probe_camera_source
from .llm_client import LLMClient, LLMClientError

MAX_TOOL_ROUNDS = 8

SYSTEM_PROMPT = """You are the Smart Lab assistant for a laboratory IoT monitoring system.
You can call tools to read live sensor data, alerts, camera vision events, forecasts,
node status, and to ask the vision-language model to describe what a camera currently sees.
Ground every factual answer in tool results — if a tool fails or returns nothing, say so
plainly instead of guessing. Keep answers short and concrete; include units (temperature C,
humidity %, air quality mV; light is a unitless photoresistor index where lower = brighter).
Data caveat: sensor win-01 was relocated to the attic at 2026-07-07T11:14:00Z; treat earlier
readings as not representative of the current environment."""

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_latest_readings",
            "description": "Latest sensor reading per node and metric (temperature, humidity, light, air_quality).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_alerts",
            "description": "Currently active (unresolved) alerts.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_vision_events",
            "description": "Recent camera vision events (person detections, face identifications, anomaly checks, scene captions).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Optional filter: person-detection, face-identification, anomaly-detection, scene-caption or scene-classification.",
                    },
                    "limit": {"type": "integer", "description": "Max events to return (default 10)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_prediction_forecast",
            "description": "Forecast for a sensor metric from the active prediction model.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "Sensor node id, e.g. win-01."},
                    "metric": {"type": "string", "description": "temperature or humidity."},
                    "horizon_minutes": {"type": "integer", "description": "30, 120 or 360."},
                },
                "required": ["node_id", "metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_status",
            "description": "Registered sensor nodes with online/offline status and last-seen times.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_camera_scene",
            "description": "Capture a fresh frame from a camera and describe it with the vision-language model. Slow (can take up to a minute).",
            "parameters": {
                "type": "object",
                "properties": {
                    "camera_id": {"type": "string", "description": "Camera client id, e.g. cam-rtsp-01. Omit to use the first registered camera."},
                },
                "required": [],
            },
        },
    },
]


class ChatToolbox:
    """Executes tool calls against the backend's existing collaborators."""

    def __init__(self, store: Any, vision_sink: Any, inference: Any) -> None:
        self.store = store
        self.vision_sink = vision_sink
        self.inference = inference
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "get_latest_readings": self._latest_readings,
            "get_active_alerts": self._active_alerts,
            "get_recent_vision_events": self._vision_events,
            "get_prediction_forecast": self._prediction_forecast,
            "get_node_status": self._node_status,
            "describe_camera_scene": self._describe_camera_scene,
        }

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        handler = self._handlers.get(name)
        if handler is None:
            return json.dumps({"error": "unknown_tool", "tool": name})
        try:
            return json.dumps(handler(arguments), default=str)
        except Exception as exc:  # noqa: BLE001 — errors become tool results the LLM can verbalize
            return json.dumps({"error": "tool_failed", "tool": name, "detail": str(exc)})

    def _latest_readings(self, _args: dict[str, Any]) -> Any:
        return self.store.latest_readings()

    def _active_alerts(self, _args: dict[str, Any]) -> Any:
        return self.store.list_alerts(status="active")

    def _vision_events(self, args: dict[str, Any]) -> Any:
        if self.vision_sink is None:
            return {"error": "vision_events_unavailable"}
        limit = int(args.get("limit") or 10)
        events = self.vision_sink.list_events(task=args.get("task"), limit=max(1, min(50, limit)))
        # Trim the raw outputs blob down to the fields the LLM can actually use.
        trimmed = []
        for event in events:
            outputs = event.get("outputs") or {}
            trimmed.append(
                {
                    "task": event.get("task"),
                    "camera_id": event.get("camera_id"),
                    "captured_at": event.get("captured_at"),
                    "people_count": event.get("people_count"),
                    "scene_label": event.get("scene_label"),
                    "display_name": event.get("display_name"),
                    "scene_summary": outputs.get("scene_summary"),
                    "anomaly_label": outputs.get("anomaly_label"),
                }
            )
        return trimmed

    def _prediction_forecast(self, args: dict[str, Any]) -> Any:
        horizon = args.get("horizon_minutes")
        result = self.store.get_latest_prediction(
            str(args["node_id"]),
            str(args["metric"]),
            int(horizon) if horizon is not None else None,
        )
        return result if result is not None else {"error": "no_active_prediction_model"}

    def _node_status(self, _args: dict[str, Any]) -> Any:
        return self.store.list_nodes()

    def _describe_camera_scene(self, args: dict[str, Any]) -> Any:
        cameras = [d for d in self.store.list_devices() if d.get("device_type") == "camera"]
        camera_id = args.get("camera_id")
        device = next((d for d in cameras if d.get("client_id") == camera_id), None) if camera_id else (cameras[0] if cameras else None)
        if device is None:
            return {"error": "camera_not_found", "known_cameras": [d.get("client_id") for d in cameras]}
        capture = probe_camera_source(device)
        if not capture.get("ok") or not capture.get("snapshot_path"):
            return {"error": "capture_failed", "detail": capture.get("error")}
        image_uri = Path(capture["snapshot_path"]).resolve().as_uri()
        result = self.inference.predict(
            {
                "task": "scene-caption",
                "input": {"camera_id": device.get("client_id"), "image_uri": image_uri},
            }
        )
        outputs = result.get("outputs") or {}
        return {
            "camera_id": device.get("client_id"),
            "scene_summary": outputs.get("scene_summary"),
            "reason": outputs.get("reason"),
            "detail": outputs.get("detail"),
        }


def run_agent_turn(
    llm: LLMClient,
    toolbox: ChatToolbox,
    history: list[dict[str, Any]],
    user_message: str,
) -> dict[str, Any]:
    """One user turn: agent loop until the LLM answers in plain text.

    Returns {"content": str, "tool_calls_made": [names], "error": optional}.
    History is the prior transcript as chat-completions messages (no system
    prompt — it's prepended here).
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": user_message}]
    tools_used: list[str] = []
    try:
        for _round in range(MAX_TOOL_ROUNDS):
            message = llm.chat(messages, tools=TOOL_DEFINITIONS)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return {"content": message.get("content") or "", "tool_calls_made": tools_used}
            # content is frequently null alongside tool_calls; keep the shape valid.
            messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": tool_calls})
            for call in tool_calls:
                call_id = call.get("id") or ""
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError("arguments must be a JSON object")
                except (json.JSONDecodeError, ValueError) as exc:
                    result = json.dumps({"error": "invalid_arguments", "detail": str(exc)})
                else:
                    result = toolbox.run(name, arguments)
                    tools_used.append(name)
                messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
        return {
            "content": "I could not reach a final answer within the tool-call limit; please try a more specific question.",
            "tool_calls_made": tools_used,
            "error": "tool_round_limit",
        }
    except LLMClientError as exc:
        return {
            "content": f"The language-model backend is not responding ({exc}). Check the chatbot settings (base URL and model).",
            "tool_calls_made": tools_used,
            "error": "llm_unreachable",
        }
