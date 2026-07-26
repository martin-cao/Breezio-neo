from __future__ import annotations

import argparse
import os

from .api import make_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Breezio backend HTTP API")
    parser.add_argument("--host", default=os.getenv("BACKEND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("BACKEND_PORT", "8000")))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--token", default=os.getenv("API_TOKEN"))
    args = parser.parse_args()
    app = make_app(database_url=args.database_url, token=args.token)
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
