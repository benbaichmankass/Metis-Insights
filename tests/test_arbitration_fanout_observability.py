"""Can a future review session SEE whether the per-account fan-out ran?

WHY THIS FILE EXISTS. The apply path shipped 2026-08-31 while
``arbitration_fanout_soak.record`` still hardcoded ``mode: "annotate"`` and
``apply_implemented: False``. Those were true for the whole life of the
annotate-only build and became a LIE the moment apply landed — a review session
reading the log would have concluded the fan-out was never built. That is this
repo's signature failure (a mechanism ships, nothing reads it back — the reason
``provenance-consumer-guard`` exists), and it is worse than a missing field
because the field was present and confidently wrong.

⚠️ THESE TESTS ASSERT THE OBSERVABILITY, NOT THE ROUTING. Routing is asserted
in ``test_arbitration_fanout_apply.py``. The question here is the one the
operator asked: *is the mechanism monitored going forward, and will a future
session know what to look for?*
"""
from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from src.runtime import arbitration_fanout_soak as soak


@pytest.fixture()
def log(tmp_path, monkeypatch):
    path = tmp_path / "arbitration_fanout_soak.jsonl"
    monkeypatch.setattr(soak, "_log_path", lambda: path)
    monkeypatch.setenv("ARBITRATION_FANOUT_MODE", "annotate")
    monkeypatch.setenv("ARBITRATION_FANOUT_ACCOUNTS", "")
    return path


_ROSTER = {
    "bybit_1": {"strategies": ["trend_donchian_sol"]},
    "breakout_1": {"strategies": ["trend_donchian_sol_prop"]},
}


def _rows(path):
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _plan(applied: bool) -> Dict[str, Any]:
    rounds = [{"strategy": "trend_donchian_sol", "accounts": ["bybit_1"],
               "side": "long", "entry": 100.0, "sl": 95.0, "tp": 115.0}]
    p: Dict[str, Any] = {
        "roster_state": "read",
        "rounds": rounds,
        "accounts_planned": 2,
        "accounts_elected": 2,
        "per_account": {
            "bybit_1": {"elected": "trend_donchian_sol", "state": "elected"},
            "breakout_1": {"elected": "trend_donchian_sol_prop", "state": "elected"},
        },
    }
    if applied:
        p["apply_rounds"] = rounds
    return p


# --- the two fields that were confidently wrong -----------------------------


def test_the_row_no_longer_claims_apply_is_unimplemented(log):
    """`apply_implemented: False` would tell a reviewer the capability is absent."""
    soak.record(["trend_donchian_sol", "trend_donchian_sol_prop"],
                "trend_donchian_sol_prop", symbol="SOLUSDT",
                accounts=_ROSTER, plan=_plan(applied=False))
    row = _rows(log)[-1]
    assert row["apply_implemented"] is True


def test_effective_mode_reflects_what_happened_not_what_was_asked(log, monkeypatch):
    """`mode` is the EFFECT; `global_mode` the REQUEST. They must not collapse."""
    monkeypatch.setenv("ARBITRATION_FANOUT_MODE", "apply")
    soak.record(["trend_donchian_sol"], "trend_donchian_sol_prop",
                symbol="SOLUSDT", accounts=_ROSTER, plan=_plan(applied=True))
    row = _rows(log)[-1]
    assert row["global_mode"] == "apply"
    assert row["mode"] == "apply"
    assert row["applied"] is True
    assert row["rounds_applied"] and row["rounds_applied"][0]["accounts"] == ["bybit_1"]


def test_a_held_back_row_can_never_read_as_an_applied_one(log, monkeypatch):
    """The staged state: the fan-out DECIDED and the allowlist held it back.

    `rounds_planned` non-empty with `applied: False` is exactly what a reviewer
    must be able to see before widening the allowlist — the correction
    NETTING_ATTRIBUTION_ACCOUNTS needed on 2026-08-09.
    """
    monkeypatch.setenv("ARBITRATION_FANOUT_MODE", "apply")
    soak.record(["trend_donchian_sol"], "trend_donchian_sol_prop",
                symbol="SOLUSDT", accounts=_ROSTER, plan=_plan(applied=False))
    row = _rows(log)[-1]
    assert row["global_mode"] == "apply"     # what was asked
    assert row["applied"] is False           # what happened
    assert row["mode"] == "annotate"
    assert row["rounds_planned"]             # ...but it DID decide
    assert row["rounds_applied"] == []


# --- the collapsed state ----------------------------------------------------


def test_plan_state_separates_did_not_look_from_elected_nothing(log):
    """`absent` is *we did not look*, NOT *the fan-out elected nothing*."""
    soak.record(["trend_donchian_sol"], "trend_donchian_sol_prop",
                symbol="SOLUSDT", accounts=_ROSTER, plan=None)
    absent = _rows(log)[-1]
    assert absent["plan_state"] == "absent"
    assert absent["accounts_planned"] is None     # never 0 — we did not look
    assert absent["accounts_elected"] is None

    soak.record(["trend_donchian_sol"], "trend_donchian_sol_prop",
                symbol="SOLUSDT", accounts=_ROSTER, plan=_plan(applied=False))
    planned = _rows(log)[-1]
    assert planned["plan_state"] == "planned"
    assert planned["accounts_planned"] == 2


def test_the_denominator_ships_beside_the_count(log):
    """`accounts_elected` over `accounts_planned` — never a bare count."""
    soak.record(["trend_donchian_sol"], "trend_donchian_sol_prop",
                symbol="SOLUSDT", accounts=_ROSTER, plan=_plan(applied=False))
    row = _rows(log)[-1]
    assert row["accounts_elected"] <= row["accounts_planned"]
    assert row["elected_by_account"]["bybit_1"] == "trend_donchian_sol"


# --- an applied tick must never be dropped by the quiet-tick gate -----------


def test_an_applied_tick_is_always_recorded(log, monkeypatch):
    """The quiet-tick gate must not swallow the one record that it ran.

    A tick where nothing was starved is ordinarily dropped as noise. If the
    fan-out ACTED on that tick, dropping the row would leave the only durable
    evidence silently incomplete.
    """
    monkeypatch.setenv("ARBITRATION_FANOUT_MODE", "apply")
    # Winner belongs to the only account holding a candidate -> nothing starved.
    row = soak.record(["trend_donchian_sol"], "trend_donchian_sol",
                      symbol="SOLUSDT",
                      accounts={"bybit_1": {"strategies": ["trend_donchian_sol"]}},
                      plan=_plan(applied=True))
    assert row is not None, "an applied tick was dropped as a quiet tick"
    assert row["applied"] is True
    assert _rows(log)


def test_a_genuinely_quiet_unapplied_tick_is_still_dropped(log):
    """The gate still works — this must not become a per-tick firehose."""
    row = soak.record(["trend_donchian_sol"], "trend_donchian_sol",
                      symbol="SOLUSDT",
                      accounts={"bybit_1": {"strategies": ["trend_donchian_sol"]}},
                      plan=_plan(applied=False))
    assert row is None
    assert _rows(log) == []


# --- the soak must never break a tick ---------------------------------------


def test_a_malformed_plan_never_raises_into_the_tick(log):
    for bad in ("not-a-dict", 42, {"apply_rounds": "nonsense"}):
        soak.record(["trend_donchian_sol"], "x", symbol="SOLUSDT",
                    accounts=_ROSTER, plan=bad)  # must not raise


# --- the doc a future session reads -----------------------------------------


def test_the_module_docstring_no_longer_says_apply_is_unimplemented():
    """The prose is monitoring too — a session reads it before the rows.

    It asserted `apply IS NOT IMPLEMENTED` for the whole annotate-only build.
    """
    doc = soak.__doc__ or ""
    # The old sentence is deliberately QUOTED as history so nobody re-quotes it
    # as fact, so its mere presence is not the defect — the defect would be it
    # standing as the current claim. Assert the affirmative leads instead.
    assert "IS IMPLEMENTED AS OF 2026-08-31" in doc
    if "IS NOT IMPLEMENTED" in doc:
        assert doc.index("IS IMPLEMENTED AS OF 2026-08-31") < doc.index(
            "IS NOT IMPLEMENTED"
        ), "the retracted claim precedes the current one; a reader meets it first"
    # And it must tell a reviewer what to look for.
    for cue in ("apply_implemented", "plan_state", "applied", "starved_count"):
        assert cue in doc, f"the docstring does not tell a reviewer to read {cue}"
