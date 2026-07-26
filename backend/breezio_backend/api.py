from __future__ import annotations

import os
import threading
from functools import wraps
from pathlib import Path
from typing import Any, Optional, Union

from flask import Flask, Response, g, jsonify, request, stream_with_context

from .auth import issue_token, new_auth_secret, verify_token
from .camera_sources import probe_camera_source, stream_camera_mjpeg
from .chat import ChatToolbox, run_agent_turn
from .db import connect, init_db
from .inference_client import InferenceClient, InferenceClientError
from .llm_client import LLMClient
from .postgres_store import PostgresBackendStore
from .services import BackendStore
from .vision_events import PostgresVisionEventSink, VISION_TASKS, VisionEventSinkUnavailable


def make_app(
    db_path: Union[str, Path, None] = None,
    token: Optional[str] = None,
    auth_secret: Optional[str] = None,
    auth_required: Optional[bool] = None,
    database_url: Optional[str] = None,
    store: Optional[Any] = None,
    inference_client: Optional[Any] = None,
    vision_event_sink: Optional[Any] = None,
) -> Flask:
    app = Flask(__name__)
    database_url = database_url or os.getenv("DATABASE_URL")
    token = token or os.getenv("API_TOKEN")
    auth_secret = auth_secret or os.getenv("BREEZIO_AUTH_SECRET") or new_auth_secret()
    if auth_required is None:
        auth_required = bool(token or os.getenv("BREEZIO_AUTH_SECRET") or (database_url and db_path is None and store is None))
    bootstrap_password = os.getenv("BREEZIO_BOOTSTRAP_PASSWORD")
    cors_origins = {
        origin.strip().rstrip("/")
        for origin in os.getenv("BREEZIO_CORS_ORIGINS", "").split(",")
        if origin.strip()
    }
    if store is None:
        if db_path is not None:
            conn = connect(db_path)
            init_db(conn)
            store = BackendStore(conn)
        elif database_url:
            store = PostgresBackendStore(database_url)
        else:
            raise RuntimeError("DATABASE_URL is required unless an explicit test store or legacy db_path is provided")
    # Generative tasks (scene-caption) can take tens of seconds on a cold
    # model load, far beyond the 3s default that suits YOLO/face lookups.
    inference = inference_client or InferenceClient(
        os.getenv("BREEZIO_INFERENCE_URL", "http://127.0.0.1:8100"),
        timeout_seconds=float(os.getenv("BREEZIO_INFERENCE_TIMEOUT_SECONDS", "60")),
    )
    vision_sink = vision_event_sink or (PostgresVisionEventSink(database_url) if database_url else None)
    db_lock = threading.RLock()

    def locked(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            with db_lock:
                return view(*args, **kwargs)

        return wrapper

    def body() -> dict:
        return request.get_json(silent=True) or {}

    def current_user_id() -> Optional[int]:
        user = getattr(g, "current_user", None)
        if not user:
            return None
        return int(user["id"])

    def auth_payload(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "token_type": "Bearer",
            "access_token": issue_token(user, auth_secret),
            "expires_in": 8 * 60 * 60,
            "user": user,
        }

    def require_admin(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if user is not None and user.get("role") != "admin":
                return jsonify(success=False, error="forbidden"), 403
            return view(*args, **kwargs)

        return wrapper

    @app.before_request
    def _authorize():
        g.current_user = None
        g.auth_kind = None
        if request.method == "OPTIONS" or request.path in {"/api/health", "/api/auth/login", "/api/auth/register"}:
            return None
        header = request.headers.get("Authorization", "")
        if not header:
            # EventSource cannot set request headers; SSE clients pass the
            # bearer token as ?access_token= instead.
            query_token = request.args.get("access_token", "")
            if query_token:
                header = f"Bearer {query_token}"
        if token and header == f"Bearer {token}":
            g.auth_kind = "service"
            return None
        if header.startswith("Bearer "):
            payload = verify_token(header.removeprefix("Bearer ").strip(), auth_secret)
            if payload and payload.get("sub"):
                user = store.get_user_by_id(int(payload["sub"]))
                if user and user.get("status") == "active":
                    g.current_user = user
                    g.auth_kind = "user"
                    return None
        if auth_required:
            return jsonify(success=False, error="unauthorized"), 401
        return None

    @app.after_request
    def _cors(response):
        origin = request.headers.get("Origin", "").rstrip("/")
        if origin and origin in cors_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            response.headers.add("Vary", "Origin")
        return response

    @app.errorhandler(KeyError)
    @app.errorhandler(ValueError)
    @app.errorhandler(TypeError)
    def _bad_request(exc: Exception):
        return jsonify(success=False, error="bad_request", message=str(exc)), 400

    @app.errorhandler(404)
    def _not_found(exc: Exception):
        return jsonify(success=False, error="not_found"), 404

    @app.errorhandler(InferenceClientError)
    def _inference_unavailable(exc: Exception):
        return jsonify(success=False, error="inference_unavailable", message=str(exc)), 502

    @app.errorhandler(VisionEventSinkUnavailable)
    def _vision_store_unavailable(exc: Exception):
        return jsonify(success=False, error="vision_store_unavailable", message=str(exc)), 503

    @app.get("/api/health")
    def health():
        return jsonify(success=True, service="breezio-backend", status="ok")

    @app.post("/api/auth/register")
    @locked
    def register_user():
        payload = body()
        email = str(payload.get("email", "")).strip().lower()
        password = str(payload.get("password", ""))
        if not email or not password or not payload.get("name"):
            raise ValueError("name, email and password are required")
        if store.get_user_by_email(email):
            return jsonify(success=False, error="email_exists", message="email already registered"), 409
        active_users = store.list_users(status="active")
        requested_role = payload.get("role", "operator")
        role = requested_role if requested_role in {"operator", "viewer"} else "operator"
        if not active_users and requested_role == "admin":
            role = "admin"
        user = store.create_user(
            {
                "name": payload["name"],
                "email": email,
                "password": password,
                "role": role,
                "status": "active",
            }
        )
        return jsonify(success=True, data=auth_payload(user)), 201

    @app.post("/api/auth/login")
    @locked
    def login_user():
        payload = body()
        user = store.authenticate_user(str(payload.get("email", "")).strip().lower(), str(payload.get("password", "")), bootstrap_password)
        if not user:
            return jsonify(success=False, error="invalid_credentials", message="invalid email or password"), 401
        return jsonify(success=True, data=auth_payload(user))

    @app.get("/api/me")
    @locked
    def get_me():
        item = store.get_current_user(current_user_id())
        return jsonify(success=item is not None, data=item), (200 if item else 404)

    @app.put("/api/me")
    @locked
    def update_me():
        item = store.update_current_user(body(), current_user_id())
        return jsonify(success=item is not None, data=item), (200 if item else 404)

    @app.get("/api/me/settings")
    @locked
    def get_my_settings():
        return jsonify(success=True, data=store.get_current_user_settings(current_user_id()))

    @app.put("/api/me/settings")
    @locked
    def update_my_settings():
        return jsonify(success=True, data=store.update_current_user_settings(body(), current_user_id()))

    @app.get("/api/users")
    @locked
    @require_admin
    def list_users():
        return jsonify(success=True, data=store.list_users(role=request.args.get("role"), status=request.args.get("status")))

    @app.post("/api/users")
    @locked
    @require_admin
    def create_user():
        return jsonify(success=True, data=store.create_user(body())), 201

    @app.get("/api/users/<int:user_id>")
    @locked
    @require_admin
    def get_user(user_id: int):
        item = store.get_user(user_id)
        return jsonify(success=item is not None, data=item), (200 if item else 404)

    @app.put("/api/users/<int:user_id>")
    @locked
    @require_admin
    def update_user(user_id: int):
        item = store.update_user(user_id, body())
        return jsonify(success=item is not None, data=item), (200 if item else 404)

    @app.delete("/api/users/<int:user_id>")
    @locked
    @require_admin
    def disable_user(user_id: int):
        return ("", 204) if store.disable_user(user_id) else (jsonify(success=False, error="not_found"), 404)

    @app.get("/api/devices")
    @locked
    def list_devices():
        return jsonify(success=True, data=store.list_devices(status=request.args.get("status")))

    @app.post("/api/devices")
    @locked
    def create_device():
        return jsonify(success=True, data=store.create_device(body())), 201

    @app.get("/api/devices/<int:device_id>")
    @locked
    def get_device(device_id: int):
        item = store.get_device(device_id)
        return jsonify(success=item is not None, data=item), (200 if item else 404)

    @app.put("/api/devices/<int:device_id>")
    @locked
    def update_device(device_id: int):
        item = store.update_device(device_id, body())
        return jsonify(success=item is not None, data=item), (200 if item else 404)

    @app.delete("/api/devices/<int:device_id>")
    @locked
    def disable_device(device_id: int):
        return ("", 204) if store.disable_device(device_id) else (jsonify(success=False, error="not_found"), 404)

    @app.delete("/api/devices/<int:device_id>/hard")
    @locked
    def delete_device(device_id: int):
        return ("", 204) if store.delete_device(device_id) else (jsonify(success=False, error="not_found"), 404)

    @app.post("/api/devices/<int:device_id>/probe")
    @locked
    def probe_device(device_id: int):
        item = store.get_device(device_id)
        if item is None:
            return jsonify(success=False, error="not_found"), 404
        if item.get("device_type") != "camera":
            return jsonify(success=False, error="unsupported_device_type", message="probe is only available for camera devices"), 400
        result = probe_camera_source(item)
        if result.get("ok"):
            store.update_device(device_id, {"status": "online"})
        status = 200 if result.get("ok") else 422
        return jsonify(success=result.get("ok", False), data=result), status

    @app.post("/api/devices/<int:device_id>/caption")
    @locked
    def caption_device(device_id: int):
        item = store.get_device(device_id)
        if item is None:
            return jsonify(success=False, error="not_found"), 404
        if item.get("device_type") != "camera":
            return jsonify(success=False, error="unsupported_device_type", message="caption is only available for camera devices"), 400
        capture = probe_camera_source(item)
        if not capture.get("ok") or not capture.get("snapshot_path"):
            return jsonify(success=False, error="capture_failed", data=capture), 422
        image_uri = Path(capture["snapshot_path"]).resolve().as_uri()
        predict_payload = {
            "task": "scene-caption",
            "input": {
                "camera_id": item.get("client_id") or f"device-{device_id}",
                "image_uri": image_uri,
            },
        }
        try:
            result = inference.predict(predict_payload)
        except InferenceClientError as exc:
            return jsonify(success=False, error="inference_unavailable", message=str(exc)), 502
        if result.get("task") in VISION_TASKS and vision_sink is not None:
            vision_sink.ingest_event({**predict_payload, **result})
        return jsonify(success=True, data=result)

    @app.get("/api/devices/<int:device_id>/preview.mjpg")
    def preview_device(device_id: int):
        with db_lock:
            item = store.get_device(device_id)
        if item is None:
            return jsonify(success=False, error="not_found"), 404
        if item.get("device_type") != "camera":
            return jsonify(success=False, error="unsupported_device_type", message="preview is only available for camera devices"), 400
        fps = max(1, min(30, int(request.args.get("fps", "30"))))
        width = max(320, min(1920, int(request.args.get("width", "960"))))
        with db_lock:
            store.update_device(device_id, {"status": "online"})
        return Response(
            stream_with_context(stream_camera_mjpeg(item, fps=fps, width=width)),
            mimetype="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/dashboard/summary")
    @locked
    def dashboard_summary():
        return jsonify(success=True, data=store.dashboard_summary())

    @app.get("/api/gateways")
    @locked
    def list_gateways():
        return jsonify(success=True, data=store.list_gateways())

    @app.post("/api/gateways")
    @locked
    def create_gateway():
        payload = body()
        return jsonify(success=True, data=store.register_gateway(payload["gateway_id"], payload)), 201

    @app.post("/api/gateways/<gateway_id>/heartbeat")
    @locked
    def gateway_heartbeat(gateway_id: str):
        return jsonify(success=True, data=store.register_gateway(gateway_id, body()))

    @app.get("/api/nodes")
    @locked
    def list_nodes():
        return jsonify(success=True, data=store.list_nodes(request.args.get("gateway_id"), request.args.get("status")))

    @app.post("/api/nodes")
    @locked
    def register_node():
        return jsonify(success=True, data=store.register_node(body())), 201

    @app.get("/api/nodes/<node_id>")
    @locked
    def get_node(node_id: str):
        item = store.get_node(node_id)
        return jsonify(success=item is not None, data=item), (200 if item else 404)

    @app.get("/api/readings")
    @locked
    def query_readings():
        return jsonify(
            success=True,
            data=store.query_readings(
                node_id=request.args.get("node_id"),
                metric=request.args.get("metric"),
                start=request.args.get("start"),
                end=request.args.get("end"),
                limit=int(request.args.get("limit", "200")),
            ),
        )

    @app.get("/api/readings/latest")
    @locked
    def latest_readings():
        return jsonify(success=True, data=store.latest_readings())

    @app.get("/api/stream/readings")
    def stream_readings():
        """SSE stream of new sensor readings (poll-based fan-out).

        Emits one `data:` event per batch of rows inserted after the client
        connected, plus keepalive comments so proxies do not drop the socket.
        The db_lock is only held during each poll, never while sleeping.
        """
        import json as json_module
        import time as time_module

        # Matches the sensor->gateway->backend report cadence (1s) so the
        # frontend sees new readings as soon as this poll can observe them.
        poll_seconds = float(os.getenv("BREEZIO_STREAM_POLL_SECONDS", "1"))

        def event_stream():
            with db_lock:
                cursor = store.latest_reading_id()
            yield "retry: 3000\n\n"
            while True:
                with db_lock:
                    rows = store.readings_after(cursor)
                if rows:
                    cursor = rows[-1]["id"]
                    payload = json_module.dumps({"readings": rows}, default=str)
                    yield f"data: {payload}\n\n"
                else:
                    yield ": keepalive\n\n"
                time_module.sleep(poll_seconds)

        response = app.response_class(event_stream(), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    @app.post("/api/readings")
    @locked
    def ingest_readings():
        return jsonify(success=True, data=store.ingest_telemetry_batch(body())), 201

    @app.get("/api/control/commands")
    @locked
    def list_control_commands():
        return jsonify(success=True, data=store.list_control_commands(request.args.get("status")))

    @app.post("/api/control/commands")
    @locked
    def create_control_command():
        return jsonify(success=True, data=store.create_control_command(body())), 201

    @app.get("/api/control/commands/<command_id>")
    @locked
    def get_control_command(command_id: str):
        item = store.get_control_command(command_id)
        return jsonify(success=item is not None, data=item), (200 if item else 404)

    @app.post("/api/control/acks")
    @locked
    def ingest_command_ack():
        return jsonify(success=True, data=store.ingest_command_ack(body()))

    @app.get("/api/alerts")
    @locked
    def list_alerts():
        return jsonify(success=True, data=store.list_alerts(request.args.get("status")))

    @app.post("/api/alerts")
    @locked
    def create_alert():
        return jsonify(success=True, data=store.create_alert(body())), 201

    @app.post("/api/alerts/<int:alert_id>/ack")
    @locked
    def ack_alert(alert_id: int):
        item = store.update_alert_status(alert_id, "acked")
        return jsonify(success=item is not None, data=item), (200 if item else 404)

    @app.post("/api/alerts/<int:alert_id>/resolve")
    @locked
    def resolve_alert(alert_id: int):
        item = store.update_alert_status(alert_id, "resolved")
        return jsonify(success=item is not None, data=item), (200 if item else 404)

    @app.get("/api/alert-rules")
    @locked
    def list_alert_rules():
        return jsonify(success=True, data=store.list_alert_rules())

    @app.post("/api/alert-rules")
    @locked
    def create_alert_rule():
        try:
            return jsonify(success=True, data=store.create_alert_rule(body())), 201
        except (ValueError, KeyError) as exc:
            return jsonify(success=False, error="bad_request", message=str(exc)), 400

    @app.put("/api/alert-rules/<int:rule_id>")
    @locked
    def update_alert_rule(rule_id: int):
        try:
            item = store.update_alert_rule(rule_id, body())
        except (ValueError, KeyError) as exc:
            return jsonify(success=False, error="bad_request", message=str(exc)), 400
        return jsonify(success=item is not None, data=item), (200 if item else 404)

    @app.delete("/api/alert-rules/<int:rule_id>")
    @locked
    def delete_alert_rule(rule_id: int):
        deleted = store.delete_alert_rule(rule_id)
        return jsonify(success=deleted), (200 if deleted else 404)

    @app.get("/api/prediction/features")
    @locked
    def prediction_features():
        return jsonify(success=True, data=store.prediction_features())

    @app.get("/api/prediction/runs")
    @locked
    def list_prediction_runs():
        return jsonify(
            success=True,
            data=store.list_prediction_runs(
                node_id=request.args.get("node_id"),
                metric=request.args.get("metric"),
                limit=int(request.args.get("limit", "50")),
            ),
        )

    @app.post("/api/prediction/runs")
    @locked
    def create_prediction_runs():
        payload = body()
        model_names = payload.get("models") or [payload["model_name"]]
        created = [store.create_prediction_run({**payload, "model_name": name}) for name in model_names]
        data = created[0] if "model_name" in payload else created
        return jsonify(success=True, data=data), 202

    @app.post("/api/prediction/runs/<run_id>/activate")
    @locked
    def activate_prediction_run(run_id: str):
        return jsonify(success=True, data=store.activate_prediction_run(run_id))

    @app.get("/api/prediction/metrics")
    @locked
    def prediction_metrics():
        horizon_arg = request.args.get("horizon_minutes")
        return jsonify(
            success=True,
            data=store.get_prediction_metrics(
                node_id=request.args.get("node_id"),
                metric=request.args.get("metric"),
                horizon_minutes=int(horizon_arg) if horizon_arg is not None else None,
            ),
        )

    @app.get("/api/prediction/latest")
    @locked
    def prediction_latest():
        node_id = request.args.get("node_id")
        metric = request.args.get("metric")
        if not node_id or not metric:
            return jsonify(success=False, error="bad_request", message="node_id and metric are required"), 400
        horizon_arg = request.args.get("horizon_minutes")
        item = store.get_latest_prediction(node_id, metric, int(horizon_arg) if horizon_arg is not None else None)
        return jsonify(success=item is not None, data=item), (200 if item is not None else 404)

    @app.get("/api/inference/health")
    def inference_health():
        return jsonify(success=True, data=inference.health())

    @app.get("/api/models")
    def list_models():
        return jsonify(success=True, data=inference.list_models(request.args.get("task")))

    @app.post("/api/models")
    def register_model():
        return jsonify(success=True, data=inference.register_model(body())), 201

    @app.post("/api/models/<model_id>/activate")
    def activate_model(model_id: str):
        return jsonify(success=True, data=inference.activate_model(model_id))

    @app.post("/api/inference/predict")
    def inference_predict():
        payload = body()
        result = inference.predict(payload)
        if result.get("task") in VISION_TASKS:
            if vision_sink is None:
                raise VisionEventSinkUnavailable("DATABASE_URL is required for PostgreSQL vision event storage")
            vision_sink.ingest_event({**payload, **result})
        return jsonify(success=True, data=result)

    @app.get("/api/vision/events")
    def list_vision_events():
        if vision_sink is None:
            raise VisionEventSinkUnavailable("DATABASE_URL is required for PostgreSQL vision event storage")
        return jsonify(
            success=True,
            data=vision_sink.list_events(
                camera_id=request.args.get("camera_id"),
                task=request.args.get("task"),
                limit=int(request.args.get("limit", "50")),
            ),
        )

    @app.post("/api/vision/events")
    def ingest_vision_event():
        if vision_sink is None:
            raise VisionEventSinkUnavailable("DATABASE_URL is required for PostgreSQL vision event storage")
        return jsonify(success=True, data=vision_sink.ingest_event(body())), 201

    @app.post("/api/chat/sessions")
    @locked
    def create_chat_session():
        from uuid import uuid4 as _uuid4

        return jsonify(success=True, data={"session_id": f"chat-{_uuid4().hex[:12]}"}), 201

    @app.get("/api/chat/sessions/<session_id>/messages")
    @locked
    def list_chat_messages(session_id: str):
        return jsonify(success=True, data=store.list_chat_messages(session_id))

    @app.post("/api/chat/sessions/<session_id>/messages")
    @locked
    def send_chat_message(session_id: str):
        payload = body()
        content = str(payload.get("content") or "").strip()
        if not content:
            return jsonify(success=False, error="bad_request", message="content is required"), 400
        settings = store.get_current_user_settings(current_user_id())
        chatbot = settings.get("chatbot") or {}
        llm = LLMClient(
            base_url=str(chatbot.get("base_url") or "http://127.0.0.1:11434/v1").strip(),
            model=str(chatbot.get("model") or "qwen2.5:7b").strip(),
        )
        # Prior transcript replayed as plain user/assistant turns; tool-call
        # internals are not persisted, so history stays provider-neutral.
        history = [
            {"role": item["role"], "content": item.get("content") or ""}
            for item in store.list_chat_messages(session_id)
        ]
        toolbox = ChatToolbox(store=store, vision_sink=vision_sink, inference=inference)
        result = run_agent_turn(llm, toolbox, history, content)
        store.append_chat_message(session_id, "user", content)
        saved = store.append_chat_message(session_id, "assistant", result["content"], tool_calls=result.get("tool_calls_made") or [])
        return jsonify(
            success=True,
            data={
                "message": saved,
                "tool_calls_made": result.get("tool_calls_made") or [],
                "error": result.get("error"),
            },
        )

    @app.get("/api/artifacts")
    def list_artifacts():
        return jsonify(
            success=True,
            data=store.list_artifacts(
                artifact_type=request.args.get("artifact_type"),
                limit=int(request.args.get("limit", "100")),
            ),
        )

    @app.post("/api/artifacts")
    def register_artifact():
        return jsonify(success=True, data=store.register_artifact(body())), 201

    @app.post("/api/artifacts/<int:artifact_id>/presign")
    def presign_artifact(artifact_id: int):
        return jsonify(success=True, data=store.presign_artifact_url(artifact_id))

    return app
