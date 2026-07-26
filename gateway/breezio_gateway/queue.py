from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


class JsonlQueue:
    def __init__(self, path: Path):
        self.path = path

    def append(self, item: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, separators=(",", ":"), ensure_ascii=False))
            handle.write("\n")

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def rewrite(self, rows: Iterable[dict]) -> None:
        rows = list(rows)
        if not rows:
            if self.path.exists():
                self.path.unlink()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False))
                handle.write("\n")
        tmp.replace(self.path)


def append_jsonl(path: Path, item: dict) -> None:
    JsonlQueue(path).append(item)


def load_jsonl(path: Path) -> list[dict]:
    return JsonlQueue(path).load()


def rewrite_jsonl(path: Path, rows: Iterable[dict]) -> None:
    JsonlQueue(path).rewrite(rows)
