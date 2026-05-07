"""S-013 M1 PR #1 — runtime_status.json producer."""
from __future__ import annotations

import json
import time
from pathlib import Path


from src.web import runtime_status as rs


def _write_yaml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_build_status_has_schema_v1_and_required_keys(tmp_path):
    accounts = _write_yaml(
        tmp_path / "accounts.yaml",
        "accounts:\n  bybit_1: {exchange: bybit}\n",
    )
    strategies = _write_yaml(
        tmp_path / "strategies.yaml",
        "strategies:\n  vwap: {enabled: true}\n",
    )
    payload = rs.build_status(
        accounts_yaml=accounts,
        strategies_yaml=strategies,
        dry_run_overrides={},
        git_sha="abc1234",
    )
    assert payload["schema_version"] == 1
    assert set(payload.keys()) == {
        "schema_version",
        "bot_uptime_s",
        "live",
        "strategies",
        "git_sha",
        "last_tick_utc",
    }
    assert payload["git_sha"] == "abc1234"
    assert payload["last_tick_utc"].endswith("Z")


def test_uptime_tracks_start_monotonic(tmp_path):
    payload = rs.build_status(
        accounts_yaml=tmp_path / "missing-accounts.yaml",
        strategies_yaml=tmp_path / "missing-strategies.yaml",
        dry_run_overrides={},
        git_sha="x",
        start_monotonic=time.monotonic() - 42.7,
    )
    # int() truncates toward zero, so 42.7s elapsed → 42 (allow ±1 for clock jitter).
    assert 41 <= payload["bot_uptime_s"] <= 44


def test_live_only_true_when_override_flips_account_to_live(tmp_path):
    accounts = _write_yaml(
        tmp_path / "accounts.yaml",
        "accounts:\n  a: {}\n  b: {}\n",
    )
    payload = rs.build_status(
        accounts_yaml=accounts,
        strategies_yaml=tmp_path / "missing.yaml",
        dry_run_overrides={"a": False, "b": True},
        git_sha="x",
    )
    # Override `False` means dry_run=False → account is live.
    # Override `True` means dry_run=True → account is NOT live.
    # Default for an absent override is dry_run=True (per S-012 PR E2).
    assert payload["live"] == {"a": True, "b": False}


def test_live_defaults_to_false_for_accounts_without_overrides(tmp_path):
    accounts = _write_yaml(
        tmp_path / "accounts.yaml",
        "accounts:\n  a: {}\n  b: {}\n",
    )
    payload = rs.build_status(
        accounts_yaml=accounts,
        strategies_yaml=tmp_path / "missing.yaml",
        dry_run_overrides={},
        git_sha="x",
    )
    assert payload["live"] == {"a": False, "b": False}


def test_strategies_only_returns_enabled_entries(tmp_path):
    strategies = _write_yaml(
        tmp_path / "strategies.yaml",
        (
            "strategies:\n"
            "  vwap: {enabled: true}\n"
            "  turtle_soup: {enabled: true}\n"
            "  killzone: {enabled: false}\n"
        ),
    )
    payload = rs.build_status(
        accounts_yaml=tmp_path / "missing-accounts.yaml",
        strategies_yaml=strategies,
        dry_run_overrides={},
        git_sha="x",
    )
    assert sorted(payload["strategies"]) == ["turtle_soup", "vwap"]


def test_missing_yaml_files_yield_empty_collections(tmp_path):
    payload = rs.build_status(
        accounts_yaml=tmp_path / "no-accounts.yaml",
        strategies_yaml=tmp_path / "no-strategies.yaml",
        dry_run_overrides={},
        git_sha="x",
    )
    assert payload["live"] == {}
    assert payload["strategies"] == []


def test_resolve_git_sha_falls_back_to_env(monkeypatch, tmp_path):
    def _fail(*a, **kw):
        raise FileNotFoundError("git not on PATH")

    monkeypatch.setattr(rs.subprocess, "run", _fail)
    monkeypatch.setenv("GIT_SHA", "deadbeef")
    assert rs._resolve_git_sha() == "deadbeef"


def test_resolve_git_sha_returns_unknown_when_no_git_and_no_env(monkeypatch):
    def _fail(*a, **kw):
        raise FileNotFoundError("git not on PATH")

    monkeypatch.setattr(rs.subprocess, "run", _fail)
    monkeypatch.delenv("GIT_SHA", raising=False)
    assert rs._resolve_git_sha() == "unknown"


def test_write_status_creates_directory_and_uses_atomic_replace(tmp_path):
    target = tmp_path / "deep" / "runtime_logs" / "runtime_status.json"
    rs.write_status(
        path=target,
        accounts_yaml=tmp_path / "missing-accounts.yaml",
        strategies_yaml=tmp_path / "missing-strategies.yaml",
        dry_run_overrides={},
        git_sha="x",
    )
    assert target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    # The atomic .tmp sibling must not survive a successful write.
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_write_status_swallows_unexpected_exceptions(tmp_path, monkeypatch):
    # Force build_status to blow up so we can verify the tick loop stays unaffected.
    def _boom(**_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(rs, "build_status", _boom)
    # Must not raise — the tick loop calls write_status() unconditionally.
    rs.write_status(path=tmp_path / "irrelevant.json")


def test_atomic_write_json_is_visible_only_after_replace(tmp_path):
    target = tmp_path / "x.json"
    captured = {}
    real_replace = rs.os.replace

    def _spy_replace(src, dst):
        captured["tmp_existed_before_replace"] = Path(src).exists()
        captured["dst_existed_before_replace"] = Path(dst).exists()
        real_replace(src, dst)

    rs.os.replace = _spy_replace
    try:
        rs._atomic_write_json(target, {"schema_version": 1})
    finally:
        rs.os.replace = real_replace
    assert captured["tmp_existed_before_replace"] is True
    assert captured["dst_existed_before_replace"] is False
    assert target.exists()
    assert json.loads(target.read_text())["schema_version"] == 1
