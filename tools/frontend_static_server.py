from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


class FrontendRequestHandler(SimpleHTTPRequestHandler):
    backend_url = "http://127.0.0.1:8000"

    def end_headers(self) -> None:
        # Static assets have no content-hash in their names, so force browsers
        # to revalidate (If-Modified-Since) instead of serving a stale bundle
        # after a redeploy.
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self.proxy_api()
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if self.path.startswith("/api/"):
            self.proxy_api()
            return
        super().do_HEAD()

    def do_POST(self) -> None:
        self.proxy_api()

    def do_PUT(self) -> None:
        self.proxy_api()

    def do_DELETE(self) -> None:
        self.proxy_api()

    def do_OPTIONS(self) -> None:
        self.proxy_api()

    def proxy_api(self) -> None:
        body = None
        if self.command in {"POST", "PUT", "PATCH"}:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else None

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() in {"accept", "authorization", "content-type"}
        }
        request = urllib.request.Request(
            f"{self.backend_url}{self.path}",
            data=body,
            headers=headers,
            method=self.command,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content_type = response.headers.get("Content-Type", "")
                if content_type.startswith("text/event-stream") or content_type.startswith("multipart/x-mixed-replace"):
                    # Server-Sent Events and MJPEG previews are endless streams;
                    # relay chunks as they arrive instead of buffering the body.
                    self.send_response(response.status)
                    self.copy_response_headers(response.headers)
                    self.end_headers()
                    if self.command == "HEAD":
                        return
                    try:
                        while True:
                            chunk = response.read1(65536)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # browser closed the EventSource
                    return
                payload = response.read()
                self.send_response(response.status)
                self.copy_response_headers(response.headers)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            self.copy_response_headers(exc.headers)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except urllib.error.URLError as exc:
            payload = f'{{"success":false,"error":"backend_unavailable","message":"{exc.reason}"}}'.encode()
            self.send_response(HTTPStatus.BAD_GATEWAY)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

    def copy_response_headers(self, headers) -> None:
        for key, value in headers.items():
            if key.lower() in {"connection", "date", "server", "transfer-encoding"}:
                continue
            self.send_header(key, value)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Serve the Breezio frontend and proxy /api requests to the backend")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5173)
    p.add_argument("--backend-url", default="http://127.0.0.1:8000")
    p.add_argument("--directory", type=Path, default=ROOT / "frontend")
    return p


def main() -> int:
    args = parser().parse_args()
    handler = lambda *handler_args, **handler_kwargs: FrontendRequestHandler(
        *handler_args,
        directory=str(args.directory),
        **handler_kwargs,
    )
    FrontendRequestHandler.backend_url = args.backend_url.rstrip("/")
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"serving frontend from {args.directory} on http://{args.host}:{args.port}; "
        f"proxying /api to {FrontendRequestHandler.backend_url}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("frontend server stopped", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
