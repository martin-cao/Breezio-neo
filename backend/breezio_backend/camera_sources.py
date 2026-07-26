"""Camera source helpers backed by FFmpeg/FFprobe."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterator, Optional


SUPPORTED_CAMERA_PROTOCOLS = {"rtsp", "http", "https", "file"}
DEFAULT_PORTS = {"rtsp": 554, "http": 80, "https": 443}
DEFAULT_SNAPSHOT_DIR = Path("var/camera_snapshots")
DEFAULT_RTSP_TRANSPORT = "udp"


def normalize_camera_payload(payload: dict[str, Any], existing: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    existing = existing or {}
    normalized = dict(payload)
    protocol = str(normalized.get("protocol") or existing.get("protocol") or "rtsp").lower().strip()
    if protocol:
        if protocol not in SUPPORTED_CAMERA_PROTOCOLS:
            raise ValueError(f"unsupported camera protocol: {protocol}")
        normalized["protocol"] = protocol

    port = normalized.get("port", existing.get("port"))
    if port in ("", None):
        normalized["port"] = DEFAULT_PORTS.get(protocol)
    elif port is not None:
        normalized["port"] = int(port)

    source_uri = (normalized.get("source_uri") or existing.get("source_uri") or "").strip()
    ip_address = (normalized.get("ip_address") or existing.get("ip_address") or "").strip()
    if not source_uri and protocol in {"rtsp", "http", "https"} and ip_address:
        source_uri = build_source_uri(protocol, ip_address, normalized.get("port"))
    if source_uri:
        normalized["source_uri"] = source_uri
    return normalized


def build_source_uri(protocol: str, ip_address: str, port: Optional[int] = None) -> str:
    protocol = protocol.lower().strip()
    if protocol not in SUPPORTED_CAMERA_PROTOCOLS:
        raise ValueError(f"unsupported camera protocol: {protocol}")
    if protocol == "file":
        return ip_address
    host = ip_address.strip()
    if not host:
        raise ValueError("ip_address is required when source_uri is not provided")
    default_port = DEFAULT_PORTS.get(protocol)
    if port and port != default_port:
        return f"{protocol}://{host}:{port}"
    return f"{protocol}://{host}"


def safe_snapshot_name(device: dict[str, Any]) -> str:
    raw = str(device.get("client_id") or device.get("name") or device.get("id") or "camera")
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-") or "camera"


def display_cmd(cmd: list[str], source_uri: str) -> str:
    return " ".join("<source_uri>" if part == source_uri else part for part in cmd)


def rtsp_transport(device: Optional[dict[str, Any]] = None) -> str:
    value = (device or {}).get("rtsp_transport") or os.getenv("BREEZIO_RTSP_TRANSPORT") or DEFAULT_RTSP_TRANSPORT
    return str(value).lower().strip()


def append_rtsp_transport(command: list[str], source_uri: str, transport: str) -> None:
    if source_uri.startswith("rtsp://") and transport and transport != "default":
        command.extend(["-rtsp_transport", transport])


def mjpeg_preview_command(source_uri: str, fps: int = 30, width: int = 960, transport: str = DEFAULT_RTSP_TRANSPORT) -> list[str]:
    command = ["ffmpeg", "-nostdin", "-loglevel", "error"]
    append_rtsp_transport(command, source_uri, transport)
    command.extend(
        [
            "-i",
            source_uri,
            "-an",
            "-vf",
            f"fps={fps},scale={width}:-1",
            "-q:v",
            "5",
            "-f",
            "mjpeg",
            "pipe:1",
        ]
    )
    return command


def iter_mjpeg_frames(stdout, chunk_size: int = 65536) -> Iterator[bytes]:
    buffer = b""
    while True:
        chunk = stdout.read(chunk_size)
        if not chunk:
            break
        buffer += chunk
        while True:
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                buffer = buffer[-1:]
                break
            end = buffer.find(b"\xff\xd9", start + 2)
            if end < 0:
                if start > 0:
                    buffer = buffer[start:]
                break
            frame = buffer[start : end + 2]
            buffer = buffer[end + 2 :]
            yield frame


def stream_camera_mjpeg(device: dict[str, Any], fps: int = 30, width: int = 960) -> Iterator[bytes]:
    source_uri = device.get("source_uri")
    if not source_uri:
        normalized = normalize_camera_payload({}, existing=device)
        source_uri = normalized.get("source_uri")
    if not source_uri:
        raise ValueError("camera source_uri or ip_address is required")

    command = mjpeg_preview_command(str(source_uri), fps=fps, width=width, transport=rtsp_transport(device))
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is not installed or not available on PATH") from exc
    if process.stdout is None:
        process.kill()
        raise RuntimeError("ffmpeg stdout pipe was not opened")
    try:
        for frame in iter_mjpeg_frames(process.stdout):
            yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode("ascii") + b"\r\n\r\n" + frame + b"\r\n"
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


def probe_camera_source(device: dict[str, Any], snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR, timeout_seconds: int = 8) -> dict[str, Any]:
    source_uri = device.get("source_uri")
    if not source_uri:
        normalized = normalize_camera_payload({}, existing=device)
        source_uri = normalized.get("source_uri")
    if not source_uri:
        raise ValueError("camera source_uri or ip_address is required")

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"{safe_snapshot_name(device)}.jpg"
    transport = rtsp_transport(device)
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
    ]
    append_rtsp_transport(probe_cmd, source_uri, transport)
    probe_cmd.extend(
        [
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name,avg_frame_rate,r_frame_rate",
            "-of",
            "json",
            source_uri,
        ]
    )
    frame_cmd = [
        "ffmpeg",
        "-y",
    ]
    append_rtsp_transport(frame_cmd, source_uri, transport)
    frame_cmd.extend(
        [
            "-i",
            source_uri,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(snapshot_path),
        ]
    )
    try:
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except FileNotFoundError:
        return {
            "ok": False,
            "source_uri": source_uri,
            "error": "ffprobe is not installed or not available on PATH",
            "ffprobe_cmd": display_cmd(probe_cmd, source_uri),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "source_uri": source_uri,
            "error": f"ffprobe timed out after {timeout_seconds}s",
            "ffprobe_cmd": display_cmd(probe_cmd, source_uri),
        }
    if probe.returncode != 0:
        return {
            "ok": False,
            "source_uri": source_uri,
            "error": (probe.stderr or probe.stdout or "ffprobe failed").strip(),
            "ffprobe_cmd": display_cmd(probe_cmd, source_uri),
        }

    stream_info = json.loads(probe.stdout or "{}")
    try:
        frame = subprocess.run(frame_cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except FileNotFoundError:
        return {
            "ok": False,
            "source_uri": source_uri,
            "snapshot_path": None,
            "stream": (stream_info.get("streams") or [{}])[0],
            "error": "ffmpeg is not installed or not available on PATH",
            "ffprobe_cmd": display_cmd(probe_cmd, source_uri),
            "ffmpeg_cmd": display_cmd(frame_cmd, source_uri),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "source_uri": source_uri,
            "snapshot_path": None,
            "stream": (stream_info.get("streams") or [{}])[0],
            "error": f"ffmpeg snapshot timed out after {timeout_seconds}s",
            "ffprobe_cmd": display_cmd(probe_cmd, source_uri),
            "ffmpeg_cmd": display_cmd(frame_cmd, source_uri),
        }
    result = {
        "ok": frame.returncode == 0,
        "source_uri": source_uri,
        "snapshot_path": str(snapshot_path) if frame.returncode == 0 else None,
        "stream": (stream_info.get("streams") or [{}])[0],
        "ffprobe_cmd": display_cmd(probe_cmd, source_uri),
        "ffmpeg_cmd": display_cmd(frame_cmd, source_uri),
    }
    if frame.returncode != 0:
        result["error"] = (frame.stderr or frame.stdout or "ffmpeg snapshot failed").strip()
    return result
