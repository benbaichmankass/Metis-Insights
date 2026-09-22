"""E21 — the exit half must not be gated on the strategy roster.

Each test here FAILS against the code as it stood at ``f3746ab``. That is the
deliverable's proof, not decoration, so every test names the pre-fix behaviour
it would have caught.

THE MEASUREMENT THIS EXISTS FOR (MEASURED 2026-09-22 by parsing
``config/strategies.yaml`` — 55 declared — and ``config/accounts.yaml`` — 11
accounts — at commit ``f3746ab``; reproduce with
``tests/test_e21_exit_roster_resolution.py::test_the_retired_fallback_covered_no_live_leg``):
**52 distinct strategies are routed to at least one ``mode: live`` account, and
the retired hardcoded fallback ``["turtle_soup", "vwap"]`` covered 0 of them.**
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.runtime import order_monitor as om  # noqa: E402
from src.runtime.strategy_roster import (  # noqa: E402
    ROSTER_EMPTY, ROSTER_OK, ROSTER_UNREADABLE, resolve_roster,
)


# ---------------------------------------------------------------------------
# 1 — the population, re-measured here rather than inherited from the review
# ---------------------------------------------------------------------------

def test_the_retired_fallback_covered_no_live_leg():
    """Re-measure the claim the fix rests on, from the config files themselves.

    Pre-fix this measured the same thing; what it proves is that the deleted
    fallback's comment ("matches the production roster … after PR B1") was
    false. *Field beats comment.*
    """
    yaml = pytest.importorskip("yaml")
    strategies = yaml.safe_load((_REPO / "config" / "strategies.yaml").read_text())["strategies"]
    accounts = yaml.safe_load((_REPO / "config" / "accounts.yaml").read_text())["accounts"]

    routed_live = set()
    for cfg in accounts.values():
        if (cfg or {}).get("mode") == "live":
            routed_live |= set((cfg or {}).get("strategies") or [])

    assert len(routed_live) > 0, "no strategy is routed to a live account — check the parse"
    retired_fallback = {"turtle_soup", "vwap"}
    assert retired_fallback & routed_live == set(), (
        "the retired fallback now covers a live leg; re-open the E21 argument "
        f"before trusting this test: {sorted(retired_fallback & routed_live)}"
    )
    # Both names exist in the registry — so the fallback was not merely stale,
    # it named legs that are deliberately NOT executing.
    assert strategies["turtle_soup"]["execution"] == "shadow"
    assert strategies["vwap"]["enabled"] is False


def test_pipeline_has_no_hardcoded_strategy_value_fallback():
    """FAILS pre-fix: ``pipeline.py`` returned ``["turtle_soup", "vwap"]``.

    A source-level assertion on purpose — the defect was a literal VALUE, and
    importing ``pipeline`` here would drag in pandas/ccxt for no benefit.
    """
    src = (_REPO / "src" / "runtime" / "pipeline.py").read_text()
    offending = [
        ln for ln in src.splitlines()
        if ln.strip().startswith("return [") and "turtle_soup" in ln and "vwap" in ln
    ]
    assert not offending, (
        "pipeline.py resolves a failed registry read to a hardcoded strategy "
        f"list again: {offending}"
    )
    assert "STRATEGY_ROSTER = resolve_roster()" in src


# ---------------------------------------------------------------------------
# 2 — "we could not look" is distinguishable from "there is nothing"
# ---------------------------------------------------------------------------

def test_unreadable_and_empty_rosters_do_not_collapse(tmp_path):
    """FAILS pre-fix: both conditions produced the same two-name list."""
    missing = tmp_path / "does-not-exist.yaml"
    unreadable = resolve_roster(str(missing))
    assert unreadable.state == ROSTER_UNREADABLE
    assert unreadable.names == ()
    assert unreadable.error, "an unreadable roster must carry WHY"
    assert unreadable.readable is False

    empty = tmp_path / "empty.yaml"
    empty.write_text("strategies: {}\n", encoding="utf-8")
    got_empty = resolve_roster(str(empty))
    assert got_empty.state == ROSTER_EMPTY
    assert got_empty.names == ()
    assert got_empty.readable is True

    good = tmp_path / "good.yaml"
    good.write_text("strategies:\n  alpha: {}\n  beta: {}\n", encoding="utf-8")
    got_ok = resolve_roster(str(good))
    assert got_ok.state == ROSTER_OK
    assert got_ok.names == ("alpha", "beta")

    # The whole point: names alone cannot tell the first two apart.
    assert unreadable.names == got_empty.names
    assert unreadable.state != got_empty.state


def test_broken_yaml_is_unreadable_not_empty(tmp_path):
    """A SYNTAX error is the live trigger — ict-git-sync pulls main every ~5 min."""
    broken = tmp_path / "broken.yaml"
    broken.write_text("strategies:\n  alpha: {\n", encoding="utf-8")
    res = resolve_roster(str(broken))
    assert res.state == ROSTER_UNREADABLE
    assert res.names == ()


# ---------------------------------------------------------------------------
# 3 — the exit population is open-package-driven
# ---------------------------------------------------------------------------

class _FakeDB:
    def __init__(self, names, raises=False):
        self._names = list(names)
        self._raises = raises

    def get_open_package_strategy_names(self):
        if self._raises:
            raise RuntimeError("journal unavailable")
        return list(self._names)


def test_exit_population_includes_a_leg_that_left_the_registry(monkeypatch):
    """FAILS pre-fix: the exit half iterated the roster verbatim.

    The concrete case: a leg is retired from ``config/strategies.yaml`` while it
    still holds an open position. Row R2 cuts four more legs, so this is the
    next instance, not a hypothetical.
    """
    monkeypatch.setattr(om, "_roster_names", lambda: (["alpha", "beta"], ROSTER_OK))
    db = _FakeDB(["beta", "tlt_pullback_1h"])
    population = om.exit_population(None, db)
    assert "tlt_pullback_1h" in population, (
        "an OPEN package whose leg is not on the roster received no monitor() call"
    )
    assert population[:2] == ["alpha", "beta"], "roster order (multiplexer priority) lost"
    assert population.count("beta") == 1, "union duplicated a name"


def test_exit_population_survives_an_unreadable_roster(monkeypatch):
    """FAILS pre-fix: an unreadable roster yielded ``["turtle_soup", "vwap"]``,
    so every open package outside those two names went unmonitored."""
    monkeypatch.setattr(om, "_roster_names", lambda: ([], ROSTER_UNREADABLE))
    db = _FakeDB(["gld_pullback_1h", "tlt_pullback_1h"])
    population = om.exit_population(None, db)
    assert sorted(population) == ["gld_pullback_1h", "tlt_pullback_1h"]
    assert "turtle_soup" not in population and "vwap" not in population


def test_exit_population_does_not_invent_names_when_the_db_read_fails(monkeypatch):
    """A failed journal read must not silently look like a flat book."""
    monkeypatch.setattr(om, "_roster_names", lambda: (["alpha"], ROSTER_OK))
    assert om.exit_population(None, _FakeDB([], raises=True)) == ["alpha"]


def test_caller_supplied_scope_still_wins(monkeypatch):
    monkeypatch.setattr(om, "_roster_names", lambda: (["alpha"], ROSTER_OK))
    assert om.exit_population(["only_me"], _FakeDB(["other"])) == ["only_me"]


# ---------------------------------------------------------------------------
# 4 — end to end through run_exit_evaluation_tick against a real journal
# ---------------------------------------------------------------------------

def test_exit_tick_scans_an_open_package_off_the_roster(tmp_path, monkeypatch):
    """FAILS pre-fix. The strongest form: a real sqlite journal, a real tick.

    ``strat_off_roster`` is not in the roster and has no strategy module, so
    ``monitor()`` cannot run — but the package must still be SCANNED, which is
    what the summary key proves. A package that is never scanned cannot be
    surfaced as monitor-blind either.
    """
    from src.units.db.database import Database

    db_path = tmp_path / "journal.db"
    db = Database(db_path=str(db_path))
    db.insert_order_package({
        "order_package_id": "e21-off-roster",
        "strategy_name": "strat_off_roster",
        "symbol": "TLT",
        "direction": "long",
        "entry": 88.0,
        "sl": 81.2425,
        "tp": 89.21682,
        "status": "open",
        "meta": {"timeframe": "1h"},
    })
    assert db.get_open_package_strategy_names() == ["strat_off_roster"]

    monkeypatch.setattr(om, "_roster_names", lambda: (["alpha"], ROSTER_OK))
    summaries = om.run_exit_evaluation_tick(db_path=str(db_path), ohlcv_fetcher=None)

    assert "strat_off_roster" in summaries, (
        "the exit tick never looked at a strategy holding an open package"
    )
    assert summaries["strat_off_roster"]["open"] == 1
    assert json.dumps(summaries)  # summaries stay JSON-serialisable


# ---------------------------------------------------------------------------
# 5 — the health snapshot can go red on it
# ---------------------------------------------------------------------------

def test_health_check_grades_the_three_roster_states_distinctly(tmp_path):
    """FAILS pre-fix: no check existed, so the snapshot read ``7/7 checks ok``
    while the roster covered 0 of the 52 live-routed legs."""
    from src.runtime.health import _DEFAULT_CHECKS, check_strategy_roster

    assert check_strategy_roster in _DEFAULT_CHECKS, (
        "the check exists but nothing runs it — a check nobody runs is not a surface"
    )

    missing = check_strategy_roster(path=str(tmp_path / "gone.yaml"))
    assert missing.status == "critical"
    assert missing.ctx["state"] == ROSTER_UNREADABLE

    empty_path = tmp_path / "empty.yaml"
    empty_path.write_text("strategies: {}\n", encoding="utf-8")
    empty = check_strategy_roster(path=str(empty_path))
    assert empty.status == "critical"
    assert empty.ctx["state"] == ROSTER_EMPTY

    # Same status, different state: the remedies differ, so the states must too.
    assert missing.ctx["state"] != empty.ctx["state"]
    assert missing.detail != empty.detail

    live = check_strategy_roster()
    assert live.status == "ok", live.detail
    assert live.ctx["resolved"] == live.ctx["declared"]


def test_health_check_rereads_the_file_rather_than_a_cached_roster(tmp_path):
    """A cached registry would make the check structurally unable to fire."""
    from src.runtime.health import check_strategy_roster

    path = tmp_path / "strategies.yaml"
    path.write_text("strategies:\n  alpha: {}\n", encoding="utf-8")
    assert check_strategy_roster(path=str(path)).status == "ok"
    path.write_text("strategies:\n  alpha: {\n", encoding="utf-8")
    after = check_strategy_roster(path=str(path))
    assert after.status == "critical", "the check answered from a cached read"
    assert after.ctx["state"] == ROSTER_UNREADABLE
