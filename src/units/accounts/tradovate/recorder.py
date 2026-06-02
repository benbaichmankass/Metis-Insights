"""NDJSON recorder for debugging.

Pipe quote/order/fill events through this and inspect the resulting
file with ``jq`` after the fact. Newline-delimited JSON is append-only
and crash-safe — partial writes only ever lose the last record.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Recorder:
    def __init__(self, path: str | os.PathLike):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fh = open(self._path, "a", encoding="utf-8")

    def record(self, kind: str, payload: Any) -> None:
        line = json.dumps(
            {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, "payload": payload},
            default=str,
        )
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.close()
            except Exception:
                pass

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
