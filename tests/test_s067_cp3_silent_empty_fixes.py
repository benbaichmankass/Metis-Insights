"""S-067 CP-3 — regression tests for the remaining trust-corroding sites:

4. ``src/web/api/routers/bot_config.py::_read_yaml`` — was silently
   returning ``{}`` on malformed YAML. Now logs + surfaces per-file
   failure as a top-level ``config_load_errors`` array.
5. ``src/web/runtime_status.py::build_status`` — the
   ``dry_run_overrides`` block was silently catching
   ``except Exception`` and falling back to ``{}``, which would make
   the runtime-status file misreport every account as ``live``. Now
   pipes the failure through the existing
   ``_swallow_runtime_status`` helper.

See ``docs/audits/silent-empty-2026-05-10.md`` § 1 for the audit
rationale.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import inspect

import pytest
import yaml
from fastapi.testclient import TestClient

from src.web import runtime_status as runtime_status_mod
from src.web.api import main as api_main
from src.web.api.routers import bot_config as bot_config_router


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Site #4 — bot_config._read_yaml
# ---------------------------------------------------------------------------


def test_config_malformed_yaml_surfaces_load_error(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bad YAML → 200 (intent preserved: never 500 on missing config)
    BUT the per-file failure is surfaced in ``config_load_errors``.

    Pre-S-067 this was indistinguishable from "no strategies
    configured" — the Settings tab silently rendered empty.
    """
    bad = tmp_path / "strategies.yaml"
    bad.write_text("not: valid: yaml: at: all:")
    good_accounts = tmp_path / "accounts.yaml"
    good_accounts.write_text(
        yaml.safe_dump({
            "accounts": {
                "bybit_1": {
                    "type": "regular",
                    "exchange": "bybit",
                    "mode": "live",
                    "market_type": "spot",
                    "strategies": ["vwap"],
                },
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot_config_router, "_ACCOUNTS_YAML", good_accounts)
    monkeypatch.setattr(bot_config_router, "_STRATEGIES_YAML", bad)
    monkeypatch.setattr(
        bot_config_router, "_RUNTIME_STATUS_JSON", tmp_path / "absent.json"
    )
    monkeypatch.setattr(
        bot_config_router, "_HALT_FLAG_PATH", str(tmp_path / "absent.flag")
    )

    resp = client.get("/api/bot/config")
    assert resp.status_code == 200
    body = resp.json()
    # The good file populated.
    assert len(body["accounts"]) == 1
    # The bad file failed and is surfaced.
    errors = body["config_load_errors"]
    assert isinstance(errors, list)
    assert len(errors) == 1
    assert errors[0]["path"] == str(bad)
    # YAMLError or one of its subclasses (ScannerError, ParserError, …).
    assert "Error" in errors[0]["error"]
    # Strategies section is empty (the malformed file produced no data).
    assert body["strategies"] == {}


def test_config_load_errors_empty_when_all_files_load_cleanly(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: ``config_load_errors`` is present and empty when
    every YAML loaded successfully — the field shape is stable so the
    dashboard can render "all configs loaded" without branching on
    field presence."""
    accounts = tmp_path / "accounts.yaml"
    accounts.write_text(yaml.safe_dump({"accounts": {}}), encoding="utf-8")
    strategies = tmp_path / "strategies.yaml"
    strategies.write_text(yaml.safe_dump({"strategies": {}}), encoding="utf-8")
    monkeypatch.setattr(bot_config_router, "_ACCOUNTS_YAML", accounts)
    monkeypatch.setattr(bot_config_router, "_STRATEGIES_YAML", strategies)
    monkeypatch.setattr(
        bot_config_router, "_RUNTIME_STATUS_JSON", tmp_path / "absent.json"
    )
    monkeypatch.setattr(
        bot_config_router, "_HALT_FLAG_PATH", str(tmp_path / "absent.flag")
    )

    resp = client.get("/api/bot/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["config_load_errors"] == []


def test_config_load_errors_includes_both_files_when_both_corrupt(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both YAMLs corrupt → both surface in ``config_load_errors``.
    Confirms the collector accumulates across files rather than
    short-circuiting on the first failure."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: valid: yaml: at: all:")
    monkeypatch.setattr(bot_config_router, "_ACCOUNTS_YAML", bad)
    monkeypatch.setattr(bot_config_router, "_STRATEGIES_YAML", bad)
    monkeypatch.setattr(
        bot_config_router, "_RUNTIME_STATUS_JSON", tmp_path / "absent.json"
    )
    monkeypatch.setattr(
        bot_config_router, "_HALT_FLAG_PATH", str(tmp_path / "absent.flag")
    )

    resp = client.get("/api/bot/config")
    assert resp.status_code == 200
    body = resp.json()
    # Both files are the same path on disk — we get two entries (one
    # per call) so the dashboard can count failures, not files.
    assert len(body["config_load_errors"]) == 2
    for entry in body["config_load_errors"]:
        assert entry["path"] == str(bad)


def test_config_missing_files_do_not_count_as_load_errors(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing files are a normal case (fresh install / test env) —
    they must not surface as load errors. Only unreadable / malformed
    files do."""
    monkeypatch.setattr(
        bot_config_router, "_ACCOUNTS_YAML", tmp_path / "missing-a.yaml"
    )
    monkeypatch.setattr(
        bot_config_router, "_STRATEGIES_YAML", tmp_path / "missing-s.yaml"
    )
    monkeypatch.setattr(
        bot_config_router, "_RUNTIME_STATUS_JSON", tmp_path / "missing-r.json"
    )
    monkeypatch.setattr(
        bot_config_router, "_HALT_FLAG_PATH", str(tmp_path / "missing.flag")
    )

    resp = client.get("/api/bot/config")
    assert resp.status_code == 200
    assert resp.json()["config_load_errors"] == []




# ---------------------------------------------------------------------------
# Site #5 — runtime_status.build_status :: live map from accounts.yaml
# (the in-memory dry_run override layer was removed 2026-06-10; build_status
#  now derives per-account live/dry straight from accounts.yaml::mode).
# ---------------------------------------------------------------------------


def test_build_status_reads_live_map_from_accounts_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """build_status no longer takes a dry_run_overrides param and reads the
    per-account live map straight from accounts.yaml::mode (no override
    layer). Default-live, explicit dry_run honoured."""
    assert (
        "dry_run_overrides"
        not in inspect.signature(runtime_status_mod.build_status).parameters
    )
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(
        yaml.safe_dump(
            {"accounts": {"bybit_1": {"mode": "live"}, "bybit_2": {"mode": "dry_run"}}}
        ),
        encoding="utf-8",
    )
    payload = runtime_status_mod.build_status(
        now_utc=datetime(2026, 5, 10, 0, 0, 0, tzinfo=timezone.utc),
        start_monotonic=0.0,
        strategies_yaml=tmp_path / "missing-s.yaml",
        accounts_yaml=accounts_yaml,
        git_sha="deadbeef",
    )
    assert payload["live"] == {"bybit_1": True, "bybit_2": False}
