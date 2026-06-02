"""Secret-safe structured logger for the Tradovate integration.

JSON-line output to ``stderr`` by default so a systemd unit captures it
in the journal. Drops keys whose name suggests a secret and truncates
token-like values to a short prefix so a leaked log line is still safe.
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from typing import Any

_SECRET_KEYS = {
    "password", "sec", "secret", "accessToken", "mdAccessToken",
    "authorization", "bearer", "deviceId",
}


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if k in _SECRET_KEYS else _scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    if isinstance(value, str) and len(value) > 96:
        return value[:8] + "…" + f"<len={len(value)}>"
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            }:
                continue
            out[key] = _scrub(val)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def get_logger(name: str = "tradovate") -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(JsonFormatter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]
