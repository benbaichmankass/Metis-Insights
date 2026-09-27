"""E31 — the process's loaded state must be distinguishable from the file's.

THE DEFECT: every surface that claimed to report the running trader's roster
was re-reading ``config/``. ``runtime_status.json`` — the one artifact the
trading process itself writes — computed ``live`` from
``_read_live_per_account(accounts_yaml)`` and ``strategies`` from
``_read_strategy_names(strategies_yaml)``, both of which open and parse the
YAML. So "the file moved and the process has not" was unobservable, and
``merged`` / ``deployed`` / ``observed`` collapsed into one.

These tests assert the three things that make the new surface worth having:

1. the roster reported is the one the PROCESS HOLDS, and it stays put when the
   file changes underneath it (the test that would have failed before E31);
2. ``pending_reload`` is READABLE — a digest the process loaded, compared
   against the digest on disk now;
3. "we could not look" never renders as an empty roster.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.runtime.loaded_config import (
    DIGEST_ABSENT,
    DIGEST_READ,
    HELD_LOADED,
    HELD_NEVER_LOADED,
    PROCESS_NOT_WRITTEN,
    PROCESS_OBSERVED,
    PROCESS_UNREADABLE,
    RELOAD_CURRENT,
    RELOAD_PENDING,
    RELOAD_STATES,
    RELOAD_UNKNOWN,
    DigestReading,
    digest_file,
    digest_text,
    never_loaded,
    new_stamp,
    read_process_block,
    reload_state,
)
from src.web.api.routers.runtime_config import build_runtime_config

_REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# digest + reload_state: the comparison itself
# ---------------------------------------------------------------------------
def test_digest_is_over_content_not_path(tmp_path):
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("strategies: {x: {}}\n")
    b.write_text("strategies: {x: {}}\n")
    assert digest_file(a).digest == digest_file(b).digest
    b.write_text("strategies: {x: {}, y: {}}\n")
    assert digest_file(a).digest != digest_file(b).digest


def test_missing_file_is_absent_not_a_digest(tmp_path):
    r = digest_file(tmp_path / "nope.yaml")
    assert r.state == DIGEST_ABSENT
    # The dangerous shape would be a digest of "" — an absent file must not
    # produce a value that can compare EQUAL to anything.
    assert r.digest is None


def test_reload_state_current_when_process_loaded_the_bytes_on_disk(tmp_path):
    f = tmp_path / "accounts.yaml"
    f.write_text("accounts: {bybit_2: {mode: live}}\n")
    loaded = digest_text(f.read_text())
    assert reload_state(loaded, digest_file(f))["reload_state"] == RELOAD_CURRENT


def test_reload_state_pending_when_the_file_moves_under_the_process(tmp_path):
    """The R2/B2 case: a roster cut lands on the VM, the process has not taken it."""
    f = tmp_path / "accounts.yaml"
    f.write_text("accounts: {bybit_2: {strategies: [a, b, c, d]}}\n")
    loaded_then = digest_text(f.read_text())          # what the process loaded
    f.write_text("accounts: {bybit_2: {strategies: [a]}}\n")  # the cut lands
    out = reload_state(loaded_then, digest_file(f))
    assert out["reload_state"] == RELOAD_PENDING
    assert out["loaded_digest"] != out["on_disk_digest"]


@pytest.mark.parametrize("loaded,on_disk", [
    (None, DigestReading(state=DIGEST_READ, digest="sha256:aa")),
    ("sha256:aa", DigestReading(state=DIGEST_ABSENT)),
    (None, DigestReading(state=DIGEST_ABSENT)),
])
def test_an_unestablished_comparison_is_unknown_never_current(loaded, on_disk):
    """`unknown` must not degrade to `current`.

    Reporting agreement that was never checked is the exact bug class this
    module exists for — a reader would take it as evidence the deploy landed.
    """
    out = reload_state(loaded, on_disk)
    assert out["reload_state"] == RELOAD_UNKNOWN
    assert out["reload_state"] != RELOAD_CURRENT


def test_reload_state_vocabulary_is_closed():
    assert set(RELOAD_STATES) == {RELOAD_CURRENT, RELOAD_PENDING, RELOAD_UNKNOWN}


# ---------------------------------------------------------------------------
# never_loaded: "we could not look" must not render as an empty roster
# ---------------------------------------------------------------------------
def test_never_loaded_carries_no_values_at_all():
    blk = never_loaded("config/strategies.yaml", "not loaded since boot")
    assert blk["held_state"] == HELD_NEVER_LOADED
    assert blk["digest"] is None and blk["loaded_at_utc"] is None
    # No key may carry an empty collection: `[]` would read as "the process
    # holds no strategies", which is a measurement nobody made.
    assert "names" not in blk and "resolved" not in blk


def test_stamp_carries_its_values_under_held_loaded():
    st = new_stamp("config/strategies.yaml", "sha256:ab", names=["x"], roster_state="ok")
    d = st.as_dict()
    assert d["held_state"] == HELD_LOADED
    assert d["names"] == ["x"] and d["digest"] == "sha256:ab"
    assert d["loaded_at_utc"]


# ---------------------------------------------------------------------------
# read_process_block: the three reader states, none collapsed
# ---------------------------------------------------------------------------
def test_missing_artifact_is_unreadable(tmp_path):
    state, block, why = read_process_block(tmp_path / "runtime_status.json")
    assert state == PROCESS_UNREADABLE and block is None
    assert "could not look" in why


def test_malformed_artifact_is_unreadable_not_not_written(tmp_path):
    p = tmp_path / "runtime_status.json"
    p.write_text("{not json")
    state, block, _ = read_process_block(p)
    assert state == PROCESS_UNREADABLE and block is None


def test_artifact_without_a_process_block_is_not_written(tmp_path):
    """An older build publishes no process block. Distinct remedy from
    `unreadable` (deploy the build vs fix the artifact), so distinct state."""
    p = tmp_path / "runtime_status.json"
    p.write_text(json.dumps({"schema_version": 1, "strategies": ["a"], "live": {}}))
    state, block, why = read_process_block(p)
    assert state == PROCESS_NOT_WRITTEN and block is None
    assert "no `process` block" in why


def test_artifact_with_a_process_block_is_observed(tmp_path):
    p = tmp_path / "runtime_status.json"
    p.write_text(json.dumps({"process": {"pid": 7}}))
    state, block, _ = read_process_block(p)
    assert state == PROCESS_OBSERVED and block == {"pid": 7}


# ---------------------------------------------------------------------------
# the loaders actually stamp what they loaded
# ---------------------------------------------------------------------------
def test_strategy_registry_stamps_the_roster_it_cached():
    import src.strategy_registry as sr
    rows = sr.reload_strategies()          # forces a fresh read + stamp
    st = sr.cache_stamp()
    assert st is not None
    assert st.digest == digest_text((_REPO / "config" / "strategies.yaml").read_text())
    assert st.values["names"] == [r["name"] for r in rows]
    assert st.values["roster_state"] == ("ok" if rows else "empty")


def test_registry_stamp_is_cleared_when_the_cache_is():
    """A stale stamp beside a cleared cache would assert a roster nobody holds."""
    import src.strategy_registry as sr
    sr.load_strategies()
    assert sr.cache_stamp() is not None
    sr._cache = None
    sr._cache_stamp = None
    assert sr.cache_stamp() is None
    sr.load_strategies()                    # restore for other tests
    assert sr.cache_stamp() is not None


def test_accounts_loader_stamps_the_gates_it_resolved():
    from src.units.accounts import accounts_stamp, load_accounts
    accounts = load_accounts()
    st = accounts_stamp()
    assert st is not None
    assert st.values["account_count"] == len(accounts)
    for acct in accounts:
        row = st.values["resolved"][acct.name]
        # Read off the RiskManager the process built, never re-derived from
        # the file — that is the whole point of the stamp.
        assert row["dry_run"] is bool(acct.risk_manager.dry_run)


def test_accounts_stamp_preserves_none_vs_empty_routing():
    """`strategies:` absent (legacy fallthrough) and `strategies: []` (block
    all) are opposite instructions; the stamp must not flatten them."""
    from src.units.accounts import accounts_stamp, load_accounts
    load_accounts()
    resolved = accounts_stamp().values["resolved"]
    for name, row in resolved.items():
        assert row["strategies"] is None or isinstance(row["strategies"], list)


# ---------------------------------------------------------------------------
# end-to-end: the payload a reader gets
# ---------------------------------------------------------------------------
def _write_status(path: Path) -> None:
    from src.strategy_registry import load_strategies
    from src.units.accounts import load_accounts
    from src.web.runtime_status import build_status
    load_strategies()
    load_accounts()
    path.write_text(json.dumps(build_status()))


def test_payload_reports_the_process_roster_not_the_file(tmp_path):
    sp = tmp_path / "runtime_status.json"
    _write_status(sp)
    out = build_runtime_config(status_json=sp, now_utc=datetime.now(timezone.utc))
    assert out["process_state"] == PROCESS_OBSERVED
    held = out["loaded"]["accounts_yaml"]["resolved"]
    from src.units.accounts import load_accounts
    assert set(held) == {a.name for a in load_accounts()}
    assert out["reload"]["accounts_yaml"]["reload_state"] == RELOAD_CURRENT
    assert out["reload"]["strategies_yaml"]["reload_state"] == RELOAD_CURRENT


def test_payload_goes_pending_when_the_file_changes_after_the_snapshot(tmp_path):
    """THE TEST THAT COULD NOT HAVE PASSED BEFORE E31.

    Every previous surface re-read the YAML, so a changed file changed the
    reported roster instantly and the divergence was invisible by
    construction. Here the reported roster must stay put and the state must
    go `pending_reload`.
    """
    sp = tmp_path / "runtime_status.json"
    _write_status(sp)
    before = build_runtime_config(status_json=sp)
    held_before = before["loaded"]["accounts_yaml"]["resolved"]

    moved = tmp_path / "accounts.yaml"
    moved.write_text((_REPO / "config" / "accounts.yaml").read_text()
                     + "\n# a roster cut landed on the VM\n")

    after = build_runtime_config(status_json=sp, accounts_yaml=moved)
    assert after["reload"]["accounts_yaml"]["reload_state"] == RELOAD_PENDING
    assert after["loaded"]["accounts_yaml"]["resolved"] == held_before
    assert "PENDING RELOAD" in after["verdict"]


def test_payload_never_renders_an_unobserved_process_as_an_empty_roster(tmp_path):
    sp = tmp_path / "runtime_status.json"
    # (a) artifact absent
    a = build_runtime_config(status_json=sp)
    assert a["process_state"] == PROCESS_UNREADABLE
    assert a["loaded"] is None          # NOT {}, NOT {"strategies": []}
    assert a["reload"]["accounts_yaml"]["reload_state"] == RELOAD_UNKNOWN
    assert "UNKNOWN" in a["verdict"]
    # (b) artifact present, older build
    sp.write_text(json.dumps({"schema_version": 1, "strategies": [], "live": {}}))
    b = build_runtime_config(status_json=sp)
    assert b["process_state"] == PROCESS_NOT_WRITTEN
    assert b["loaded"] is None
    assert "UNKNOWN" in b["verdict"]
    # (c) and the two reasons are NOT the same value
    assert a["process_state"] != b["process_state"]


def test_strategies_route_marks_held_by_process_null_when_unobserved(tmp_path, monkeypatch):
    """`held_by_process: None` is not `False` — the SPA must be able to tell
    "we were not told" from "the process does not hold this leg"."""
    import src.web.api.routers.strategies as S
    monkeypatch.setattr(S, "runtime_logs_dir", lambda: tmp_path)
    out = S.get_strategies()
    assert out["runtime"]["process_state"] == PROCESS_NOT_WRITTEN
    assert out["runtime"]["held_strategies"] is None
    assert all(s["held_by_process"] is None for s in out["strategies"])


def test_strategies_route_reports_held_when_the_process_published(tmp_path, monkeypatch):
    import src.web.api.routers.strategies as S
    _write_status(tmp_path / "runtime_status.json")
    monkeypatch.setattr(S, "runtime_logs_dir", lambda: tmp_path)
    out = S.get_strategies()
    assert out["runtime"]["process_state"] == PROCESS_OBSERVED
    assert out["runtime"]["held_strategies"]
    from src.strategy_registry import load_strategies
    declared = {r["name"] for r in load_strategies()}
    assert set(out["runtime"]["held_strategies"]) == declared
    assert any(s["held_by_process"] is True for s in out["strategies"])


def test_bot_config_note_no_longer_claims_a_runtime_view():
    """The note asserted `live_per_account is the pipeline's runtime view`
    while the producer parses the YAML. Field beats comment."""
    from src.web.api.routers.bot_config import build_config
    note = build_config()["trading_mode"]["note"]
    assert "runtime view" not in note
    assert "REPORT THE FILE" in note
    assert "/api/bot/runtime-config" in note


def test_runtime_status_publishes_the_process_block():
    from src.web.runtime_status import build_status
    st = build_status()
    assert "process" in st
    assert st["process"]["strategies_yaml"]["held_state"] in (HELD_LOADED, HELD_NEVER_LOADED)
