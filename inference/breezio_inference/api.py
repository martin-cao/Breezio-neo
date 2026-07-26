from __future__ import annotations

from http import HTTPStatus
from typing import Optional
from urllib.parse import parse_qs, urlparse

from flask import Flask, jsonify, request

from .service import InferenceService


def dispatch_request(
    service: InferenceService,
    method: str,
    raw_path: str,
    body: Optional[dict] = None,
) -> tuple[HTTPStatus, dict]:
    parsed = urlparse(raw_path)
    path = parsed.path
    query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
    payload = body or {}

    try:
        if method == "GET" and path == "/api/inference/health":
            return HTTPStatus.OK, service.health()
        if method == "GET" and path == "/api/inference/models":
            return HTTPStatus.OK, {"success": True, "data": service.list_models(query.get("task"))}
        if method == "POST" and path == "/api/inference/models":
            return HTTPStatus.CREATED, {"success": True, "data": service.register_model(payload)}
        if method == "POST" and path.startswith("/api/inference/models/") and path.endswith("/activate"):
            model_id = path.split("/")[4]
            return HTTPStatus.OK, {"success": True, "data": service.activate_model(model_id)}
        if method == "GET" and path == "/api/inference/identities":
            return HTTPStatus.OK, {"success": True, "data": service.list_identities()}
        if method == "POST" and path == "/api/inference/identities":
            return HTTPStatus.CREATED, {"success": True, "data": service.enroll_identity(payload)}
        if method == "POST" and path == "/api/inference/predict":
            return HTTPStatus.OK, {"success": True, "data": service.predict(payload)}
        return HTTPStatus.NOT_FOUND, {"success": False, "error": "not_found"}
    except KeyError as exc:
        return HTTPStatus.NOT_FOUND, {"success": False, "error": "not_found", "message": str(exc)}
    except (TypeError, ValueError) as exc:
        return HTTPStatus.BAD_REQUEST, {"success": False, "error": "bad_request", "message": str(exc)}


def make_app(service: Optional[InferenceService] = None) -> Flask:
    app = Flask(__name__)
    inference_service = service or InferenceService()

    def body() -> dict:
        payload = request.get_json(silent=False) if request.data else {}
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def flask_dispatch():
        status, payload = dispatch_request(
            inference_service,
            request.method,
            request.full_path if request.query_string else request.path,
            body() if request.method in {"POST", "PUT", "PATCH"} else {},
        )
        return jsonify(payload), int(status)

    @app.errorhandler(ValueError)
    @app.errorhandler(TypeError)
    def _bad_request(exc: Exception):
        return jsonify(success=False, error="bad_request", message=str(exc)), 400

    @app.errorhandler(400)
    def _bad_json(exc: Exception):
        return jsonify(success=False, error="bad_request", message=str(exc)), 400

    @app.route("/api/inference/health", methods=["GET"])
    @app.route("/api/inference/models", methods=["GET", "POST"])
    @app.route("/api/inference/models/<path:_model_action>", methods=["POST"])
    @app.route("/api/inference/identities", methods=["GET", "POST"])
    @app.route("/api/inference/predict", methods=["POST"])
    def _routes(_model_action: str | None = None):
        return flask_dispatch()

    @app.errorhandler(404)
    def _not_found(exc: Exception):
        return jsonify(success=False, error="not_found"), 404

    return app
