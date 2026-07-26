from __future__ import annotations

import argparse
import os
from pathlib import Path


DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def migration_files(migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> list[Path]:
    return sorted(path for path in migrations_dir.glob("*.sql") if path.is_file())


def apply_migrations(database_url: str, migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> list[Path]:
    """Apply each migration file exactly once, tracked in `schema_migrations`.

    Earlier versions re-executed every file on every startup, relying on each
    migration being idempotent (CREATE ... IF NOT EXISTS). That breaks once a
    migration does something non-idempotent in effect — e.g. 0005 drops and
    replaces a unique index with different semantics; blindly re-running 0001
    afterwards would try to recreate the old (now-incompatible) index and fail.
    """
    import psycopg

    files = migration_files(migrations_dir)
    if not files:
        raise FileNotFoundError(f"no SQL migrations found in {migrations_dir}")

    applied: list[Path] = []
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  filename VARCHAR(255) PRIMARY KEY,
                  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("SELECT filename FROM schema_migrations")
            already_applied = {row[0] for row in cur.fetchall()}
            for path in files:
                if path.name in already_applied:
                    continue
                cur.execute(path.read_text())
                cur.execute("INSERT INTO schema_migrations(filename) VALUES (%s)", (path.name,))
                applied.append(path)
        conn.commit()
    return applied


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Apply Breezio PostgreSQL migrations")
    p.add_argument("--database-url", default=os.getenv("DATABASE_URL"), help="PostgreSQL connection URL")
    p.add_argument("--migrations-dir", type=Path, default=DEFAULT_MIGRATIONS_DIR)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    applied = apply_migrations(args.database_url, args.migrations_dir)
    for path in applied:
        print(f"applied {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
