"""Tests for the S-022 PR6 bot/web silent-except sweep."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("dotenv",):
    sys.modules.setdefault(_mod, MagicMock())

# fastapi isn't a hard dep on every dev/CI host; stub for tests that
# import the web routers.
#
# S-045 T1: the original guard was `if "fastapi" not in sys.modules`,
# which checks whether fastapi has already been imported. On a fresh
# sandbox the check passes and the MagicMock stub is installed —
# but then `from fastapi.testclient import TestClient` in
# test_web_api_*.py later fails with "fastapi is not a package"
# because pytest has already cached our stub. The correct guard is
# "try a real import; only stub if truly absent" so CI (where
# fastapi IS installed via requirements-test.txt) keeps the real
# package and the web-API tests collect cleanly.
try:  # pragma: no cover — env-dependent guard
    import fastapi  # noqa: F401
    import fastapi.testclient  # noqa: F401
except ImportError:
    fastapi = MagicMock()

    class _APIRouter:
        def __init__(self, **_kw): pass
        def get(self, *_a, **_kw):
            def deco(f): return f
            return deco
        def post(self, *_a, **_kw):
            def deco(f): return f
            return deco

    class _HTTPException(Exception):
        def __init__(self, *_a, **_kw): pass

    fastapi.APIRouter = _APIRouter
    fastapi.HTTPException = _HTTPException
    fastapi.Depends = lambda x: x
    fastapi.status = MagicMock(HTTP_503_SERVICE_UNAVAILABLE=503,
                                HTTP_401_UNAUTHORIZED=401)
    sys.modules["fastapi"] = fastapi

# Stub src.web.api.auth so importing pnl doesn't drag in jwt/cryptography
# (not available in some test envs). Same try/except shape as fastapi above.
try:  # pragma: no cover — env-dependent guard
    import src.web.api.auth  # noqa: F401
except ImportError:
    auth_stub = MagicMock()
    auth_stub.require_session = lambda: {}
    sys.modules["src.web.api.auth"] = auth_stub


@pytest.fixture
def captured():
    """Capture every outcomes.report call across the codebase."""
    rec = []

    def fake_report(action, status, *, level, reason=None, **ctx):
        rec.append({"action": action, "status": status,
                    "level": getattr(level, "value", level),
                    "reason": reason, "ctx": ctx})
        return {}

    with patch("src.runtime.outcomes.report", side_effect=fake_report):
        yield rec


# ---------------------------------------------------------------------------
# src/web/runtime_status.py — strategies + accounts YAML failures
# ---------------------------------------------------------------------------


def test_runtime_status_strategies_yaml_failure_reports(captured, tmp_path):
    from src.web import runtime_status as rs
    bogus = tmp_path / "broken.yaml"
    bogus.write_text(": :: invalid yaml ::")  # malformed
    out = rs._read_strategy_names(bogus)
    assert out == []
    matches = [r for r in captured
               if r["action"] == "runtime_status"
               and r["status"] == "strategies_yaml_read_failed"]
    assert len(matches) == 1
    assert matches[0]["level"] == "warn"


def test_runtime_status_strategies_missing_file_reports(captured, tmp_path):
    from src.web import runtime_status as rs
    out = rs._read_strategy_names(tmp_path / "nope.yaml")
    assert out == []
    # FileNotFoundError is also routed through the swallow helper
    matches = [r for r in captured
               if r["status"] == "strategies_yaml_read_failed"]
    assert len(matches) == 1


def test_runtime_status_accounts_yaml_failure_reports(captured, tmp_path):
    from src.web import runtime_status as rs
    bogus = tmp_path / "broken.yaml"
    bogus.write_text(": :: invalid yaml ::")
    out = rs._read_live_per_account(bogus, {})
    assert out == {}
    matches = [r for r in captured
               if r["status"] == "accounts_yaml_read_failed"]
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# src/web/api/routers/pnl.py — accounts YAML failure
# ---------------------------------------------------------------------------


def test_pnl_accounts_yaml_failure_reports(captured, tmp_path):
    """Patch the inner local-import path: pnl uses local import too."""
    from src.web.api.routers import pnl
    bogus = tmp_path / "broken.yaml"
    bogus.write_text(": :: invalid ::")
    out = pnl._load_account_ids(bogus)
    assert out == []
    matches = [r for r in captured
               if r["action"] == "pnl_endpoint"
               and r["status"] == "accounts_yaml_read_failed"]
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# src/bot/data_loaders.py — PyYAML ImportError
# ---------------------------------------------------------------------------


def test_data_loaders_yaml_import_error_reports(captured):
    """Force a yaml ImportError by stashing an explosive 'yaml' in sys.modules."""
    from src.bot import data_loaders as dl

    saved = sys.modules.get("yaml")
    sys.modules.pop("yaml", None)

    class _ImportFailure:
        def __getattr__(self, _name):
            raise ImportError("PyYAML not installed")

    # Use a meta_path finder that raises ImportError when yaml is imported.
    import importlib.abc
    import importlib.machinery

    class _BlockYAMLFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname == "yaml":
                raise ImportError("PyYAML blocked for test")
            return None

    finder = _BlockYAMLFinder()
    sys.meta_path.insert(0, finder)
    try:
        out = dl._load_yaml_accounts()
    finally:
        sys.meta_path.remove(finder)
        if saved is not None:
            sys.modules["yaml"] = saved

    assert out == []
    matches = [r for r in captured
               if r["action"] == "data_loaders"
               and r["status"] == "pyyaml_missing"]
    assert len(matches) == 1
    assert matches[0]["level"] == "warn"
