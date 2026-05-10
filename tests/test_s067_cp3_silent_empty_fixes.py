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
from typing import Any, Dict, List

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
# Site #5 — runtime_status.build_status :: dry_run_overrides block
# ---------------------------------------------------------------------------


def test_build_status_swallows_overrides_failure_via_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``get_dry_run_overrides()`` raises, ``build_status``
    must:
      1. not propagate the exception (tick-loop never-raise contract);
      2. fall back to ``dry_run_overrides = {}`` so the rest of the
         payload still builds;
      3. report the failure via ``_swallow_runtime_status`` so the
         operator gets a Telegram alert (deduplicated by
         ``outcomes.report``).

    Pre-S-067 the failure was silently caught and the fallback was
    ``{}`` with no log — every account would then be reported as
    ``live`` (since the dry/live flip in ``_read_live_per_account``
    keys off the override dict).
    """
    # Capture every _swallow_runtime_status call so we can assert the
    # specific status string.
    captured: List[Dict[str, Any]] = []

    def spy(status: str, exc: BaseException, **ctx: Any) -> None:
        captured.append({"status": status, "exc_type": type(exc).__name__, "ctx": ctx})

    monkeypatch.setattr(runtime_status_mod, "_swallow_runtime_status", spy)

    # Make the get_dry_run_overrides import inside build_status raise.
    import src.units.accounts as accounts_mod

    def boom() -> Dict[str, bool]:
        raise RuntimeError("synthetic dry_run_overrides failure")

    monkeypatch.setattr(accounts_mod, "get_dry_run_overrides", boom)

    # Use absent yaml paths so the rest of the function builds quickly
    # without depending on real config.
    payload = runtime_status_mod.build_status(
        now_utc=datetime(2026, 5, 10, 0, 0, 0, tzinfo=timezone.utc),
        start_monotonic=0.0,
        strategies_yaml=tmp_path / "missing-s.yaml",
        accounts_yaml=tmp_path / "missing-a.yaml",
        git_sha="deadbeef",
    )

    # 1. No exception propagated (the call returned).
    # 2. Fallback to empty live map.
    assert payload["live"] == {}
    # 3. The helper was called with the right status fingerprint.
    statuses = [c["status"] for c in captured]
    assert "dry_run_overrides_read_failed" in statuses
    # And it carried a useful exception type for the dedup fingerprint.
    overrides_call = next(
        c for c in captured if c["status"] == "dry_run_overrides_read_failed"
    )
    assert overrides_call["exc_type"] == "RuntimeError"


def test_build_status_passes_overrides_through_when_helper_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity: when ``get_dry_run_overrides()`` returns normally the
    overrides flow through to ``_read_live_per_account`` and shape
    the live map. Guards against the fix accidentally always
    falling back to ``{}``."""
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(
        yaml.safe_dump({
            "accounts": {"bybit_1": {"mode": "live"}, "bybit_2": {"mode": "live"}},
        }),
        encoding="utf-8",
    )

    # bybit_1 → dry_run False → live True; bybit_2 → dry_run True → live False.
    import src.units.accounts as accounts_mod
    monkeypatch.setattr(
        accounts_mod, "get_dry_run_overrides",
        lambda: {"bybit_1": False, "bybit_2": True},
    )

    payload = runtime_status_mod.build_status(
        now_utc=datetime(2026, 5, 10, 0, 0, 0, tzinfo=timezone.utc),
        start_monotonic=0.0,
        strategies_yaml=tmp_path / "missing-s.yaml",
        accounts_yaml=accounts_yaml,
        git_sha="deadbeef",
    )

    assert payload["live"] == {"bybit_1": True, "bybit_2": False}
