"""An account that reads UP while refusing every signal must alert.

WHY (`docs/research/WORKPLAN-2026-08-14.md` Lane 0). `account_reachability_alert`
probes `positions()`; an account whose `positions()` answers while `balance()`
returns None reads reachable and places nothing. `/health-review`'s
strategy-silence check measures `*_eval` events, and such a leg is not silent —
it is loudly failing at the last step. Measured on the live journal 2026-08-14:
`alpaca_live` produced 120 refusals across 16 separate days with zero alerts.

These tests pin the three things that make this alert trustworthy rather than
noisy: the states are not collapsed, the alert is per-ACCOUNT (not per-leg), and
the latch cannot swallow a second, different cause.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    return tmp_path


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    msgs: List[str] = []
    from src.runtime import silent_refusal_alert

    monkeypatch.setattr(silent_refusal_alert, "_send_alert", msgs.append)
    return msgs


def _row(account: str, status: str, reason: str = "", strategy: str = "s") -> Dict[str, Any]:
    return {"account_id": account, "status": status,
            "entry_reason": reason, "strategy_name": strategy}


# ── the classification ────────────────────────────────────────────────

def test_all_refused_is_the_finding(isolated) -> None:
    from src.runtime.silent_refusal_alert import assess

    rows = [_row("alpaca_live", "rejected", "REJECTED: zero_balance")] * 6
    a = assess(rows, min_rows=5)["alpaca_live"]

    assert a["verdict"] == "signalled_never_placed"
    assert a["alerting"] is True
    assert a["cause"] == "zero_balance"


def test_an_account_with_no_rows_is_absent_not_healthy(isolated) -> None:
    """"We observed nothing" must never render as "it is fine".

    An account routed nothing, or trading a quiet market, produces no rows —
    grading that healthy would be an all-clear derived from an empty read.
    """
    from src.runtime.silent_refusal_alert import assess

    assert assess([], min_rows=5) == {}
    assert "quiet_acct" not in assess([_row("busy", "closed")], min_rows=5)


def test_some_placed_is_not_an_outage(isolated) -> None:
    """A partial refusal rate is a tuning question, not "placing nothing"."""
    from src.runtime.silent_refusal_alert import assess

    rows = [_row("bybit_2", "rejected", "zero_balance")] * 9 + [
        _row("bybit_2", "closed")]
    a = assess(rows, min_rows=5)["bybit_2"]

    assert a["verdict"] == "partially_refused"
    assert a["alerting"] is False


def test_below_threshold_is_reported_but_does_not_alert(isolated) -> None:
    """One bad order is not a pattern — but it is still visible in the
    assessment, so a reader is never told it did not happen."""
    from src.runtime.silent_refusal_alert import assess

    a = assess([_row("x", "rejected", "zero_balance")] * 2, min_rows=5)["x"]
    assert a["verdict"] == "signalled_never_placed"
    assert a["alerting"] is False
    assert a["refused"] == 2


def test_unrecognised_status_does_not_read_as_refused(isolated) -> None:
    """A status nobody has seen must not be graded as a venue refusal.

    Otherwise a new status the reconciler starts writing would fire this alert
    across every account at once — the desensitized-alarm shape.
    """
    from src.runtime.silent_refusal_alert import assess

    a = assess([_row("x", "some_new_status")] * 9, min_rows=5)["x"]
    assert a["other"] == 9 and a["refused"] == 0
    assert a["verdict"] == "no_placed_rows_unrecognised_status_only"
    assert a["alerting"] is False


def test_verdict_matches_the_offline_audit(isolated) -> None:
    """The live alert and `dead_leg_audit` must grade one row the same way.

    Both import `src.runtime.dead_leg`; this asserts the shared module is
    actually what decides, rather than two copies that happen to agree today.
    """
    from src.runtime.dead_leg import verdict_for
    from src.runtime.silent_refusal_alert import assess

    counts = {"placed": 0, "refused": 7, "other": 0}
    assert verdict_for(counts) == "signalled_never_placed"
    rows = [_row("x", "exchange_rejected", "110007")] * 7
    assert assess(rows, min_rows=5)["x"]["verdict"] == verdict_for(counts)


# ── cause extraction ──────────────────────────────────────────────────

@pytest.mark.parametrize("reason,expected", [
    ("REJECTED: sizing_failed: RuntimeError: balance() returned None for "
     "alpaca_live (exchange=alpaca): the balance fetch was attempted",
     "balance_unreadable"),
    ("REJECTED: zero_balance | fvg entry", "zero_balance"),
    ("EXCHANGE_REJECTED: 110007 qty exceeds max", "venue_max_qty"),
    ("REJECTED: rejected_too_small", "below_min_qty"),
])
def test_causes_are_distinguished(reason, expected) -> None:
    """Each cause is a DIFFERENT operator action, so they are not one bucket."""
    from src.runtime.silent_refusal_alert import classify_cause

    assert classify_cause(reason) == expected


def test_missing_reason_is_unknown_not_other() -> None:
    """No reason text at all is a journal gap; an unmatched reason is a string
    worth adding to the patterns. Collapsing them would hide the first."""
    from src.runtime.silent_refusal_alert import classify_cause

    assert classify_cause(None) == "unknown"
    assert classify_cause("   ") == "unknown"
    assert classify_cause("REJECTED: something nobody has seen") == "other"


# ── the latch ─────────────────────────────────────────────────────────

def _refusals(n=6, reason="zero_balance", account="alpaca_live"):
    return [_row(account, "rejected", reason, f"leg_{i}") for i in range(n)]


def test_alerts_once_then_latches(isolated, sent) -> None:
    from src.runtime.silent_refusal_alert import run_silent_refusal_check

    now = datetime.now(timezone.utc)
    run_silent_refusal_check(now=now, rows=_refusals())
    assert len(sent) == 1
    # a second window with the same condition must NOT re-ping
    run_silent_refusal_check(now=now + timedelta(hours=2), rows=_refusals())
    assert len(sent) == 1


def test_recovery_pings_once(isolated, sent) -> None:
    from src.runtime.silent_refusal_alert import run_silent_refusal_check

    now = datetime.now(timezone.utc)
    run_silent_refusal_check(now=now, rows=_refusals())
    run_silent_refusal_check(
        now=now + timedelta(hours=2),
        rows=_refusals(3) + [_row("alpaca_live", "closed")] * 3)
    assert len(sent) == 2
    assert "[OK]" in sent[1]


def test_a_new_cause_is_not_swallowed_by_the_old_latch(isolated, sent) -> None:
    """An account that stops refusing for `zero_balance` and starts refusing
    for a venue cap has a NEW problem. One latch per account would report it
    as "already alerting" and say nothing — so the latch key is
    (account, cause)."""
    from src.runtime.silent_refusal_alert import run_silent_refusal_check

    now = datetime.now(timezone.utc)
    run_silent_refusal_check(now=now, rows=_refusals(reason="zero_balance"))
    run_silent_refusal_check(now=now + timedelta(hours=2),
                             rows=_refusals(reason="110007 qty exceeds max"))

    assert len(sent) == 2
    assert "zero_balance" in sent[0]
    assert "venue_max_qty" in sent[1]


def test_one_ping_per_account_not_per_leg(isolated, sent) -> None:
    """16 dead legs on one account is ONE alert.

    `alpaca_live` routes 16 live strategies; a per-leg alert would have fired
    16 pings for a single cause, and a 16-at-a-time alarm is the desensitized
    alarm this repo has already paid for once.
    """
    from src.runtime.silent_refusal_alert import run_silent_refusal_check

    rows = [_row("alpaca_live", "rejected", "zero_balance", f"leg_{i}")
            for i in range(16)]
    run_silent_refusal_check(now=datetime.now(timezone.utc), rows=rows)

    assert len(sent) == 1
    assert "leg_0" in sent[0]          # the legs are still named…
    assert "more" in sent[0]           # …and the overflow is declared


def test_skip_list_suppresses(isolated, sent, monkeypatch) -> None:
    from src.runtime.silent_refusal_alert import run_silent_refusal_check

    monkeypatch.setenv("SILENT_REFUSAL_SKIP", "alpaca_live")
    run_silent_refusal_check(now=datetime.now(timezone.utc), rows=_refusals())
    assert sent == []


def test_latched_accounts_are_readable_for_the_review_skills(isolated, sent) -> None:
    from src.runtime.silent_refusal_alert import (
        run_silent_refusal_check, silent_accounts)

    run_silent_refusal_check(now=datetime.now(timezone.utc), rows=_refusals())
    latched = silent_accounts()
    assert "alpaca_live" in latched
    assert latched["alpaca_live"]["cause"] == "zero_balance"


# ── knobs ─────────────────────────────────────────────────────────────

def test_cadence_gate_skips_a_too_soon_read(isolated, sent) -> None:
    from src.runtime.silent_refusal_alert import run_silent_refusal_check

    # `rows=` bypasses the cadence gate (it is the test seam), so drive the
    # real path: no rows, and a state stamp written a moment ago.
    now = datetime.now(timezone.utc)
    run_silent_refusal_check(now=now, rows=[])
    out = run_silent_refusal_check(now=now + timedelta(seconds=5))
    assert out["checked"] is False and out["reason"] == "cadence"


def test_unparseable_knob_falls_back_to_default_not_off(isolated, monkeypatch) -> None:
    """A typo must not silently switch off the only thing watching this class."""
    from src.runtime.silent_refusal_alert import _int_knob

    monkeypatch.setenv("SILENT_REFUSAL_CHECK_SECONDS", "not-a-number")
    assert _int_knob("SILENT_REFUSAL_CHECK_SECONDS", 3600) == 3600


def test_pause_knob(isolated, sent, monkeypatch) -> None:
    from src.runtime.silent_refusal_alert import run_silent_refusal_check

    monkeypatch.setenv("SILENT_REFUSAL_CHECK_SECONDS", "0")
    out = run_silent_refusal_check(now=datetime.now(timezone.utc),
                                   rows=_refusals())
    assert out["checked"] is False and out["reason"] == "paused"
    assert sent == []


def test_journal_read_failure_is_not_read_as_no_refusals(
        isolated, sent, monkeypatch) -> None:
    """A failed read must latch nothing and recover nothing.

    Reading "the DB was unavailable" as "no account is refusing" is the
    unasserted-denominator shape — a clean all-clear derived from nothing.
    """
    from src.runtime import silent_refusal_alert

    def _boom(*_a, **_kw):
        raise OSError("journal unreadable")

    monkeypatch.setattr(silent_refusal_alert, "_read_window", _boom)
    out = silent_refusal_alert.run_silent_refusal_check(
        now=datetime.now(timezone.utc), force=True)

    assert out["checked"] is False and out["reason"] == "read_failed"
    assert sent == []


def test_reads_a_real_journal_end_to_end(isolated, sent) -> None:
    """Positive control: the SQL actually matches rows in a real schema.

    Every other latch test injects `rows=`, so without this a broken query
    returning nothing would leave the file green.
    """
    import sqlite3

    from src.runtime.silent_refusal_alert import run_silent_refusal_check
    from src.units.db import database
    from src.utils.paths import trade_journal_db_path

    database.Database().create_tables()
    conn = sqlite3.connect(str(trade_journal_db_path()))
    for i in range(6):
        conn.execute(
            # The real DDL, not a convenient subset: `trades` declares
            # timestamp/symbol/direction/entry_price/position_size NOT NULL,
            # and a test that invented its own looser schema would pass against
            # a table production does not have (the shape that let the pairs
            # `order_packages` bug through, BL-20260810).
            "INSERT INTO trades (account_id, strategy_name, status, is_backtest,"
            " created_at, timestamp, symbol, direction, entry_price,"
            " position_size, entry_reason)"
            " VALUES (?,?,?,0,datetime('now'),datetime('now'),'SPY','long',"
            "1.0,1.0,?)",
            ("alpaca_live", f"leg_{i}", "rejected", "REJECTED: zero_balance"),
        )
    conn.commit()
    conn.close()

    out = run_silent_refusal_check(now=datetime.now(timezone.utc), force=True)
    assert out["checked"] is True, out
    assert out["alerted"] == ["alpaca_live"]
    assert len(sent) == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))


# ── the DECLARED-dry_run state (2026-08-24) ──────────────────────────
#
# BL-20260824-SILENT-REFUSAL-CANNOT-SEE-A-DECLARED-DRY-RUN. "Refused because
# BROKEN" and "refused because deliberately SWITCHED OFF" produce byte-identical
# `trades.status` rows, and this detector graded both `signalled_never_placed`.
# Live consequence: `alpaca_live` (mode: dry_run, account_class real_money, 16
# legs) latched `alerting: true` from 2026-08-21 and held it for three days on
# correct behaviour — the desensitized-alarm P1, self-inflicted on the newest
# detector, while `execution_diagnostics.EXPECTED_DISPATCH_SKIP_REASONS` had
# already ruled on this exact account by operator directive 2026-07-15.

_DRY = "REJECTED: dry_run_sizing_skip: risk_refused: sized_qty=0 with balance=0.10"


def test_a_declared_dry_run_refusal_is_not_the_finding(isolated) -> None:
    from src.runtime.silent_refusal_alert import assess

    a = assess([_row("alpaca_live", "rejected", _DRY)] * 6, min_rows=5)["alpaca_live"]

    assert a["verdict"] == "refusing_by_declaration"
    assert a["alerting"] is False
    assert a["policy_skipped"] == 6
    assert a["refused"] == 0


def test_a_declared_dry_run_account_never_pings(isolated, sent) -> None:
    from src.runtime.silent_refusal_alert import run_silent_refusal_check

    run_silent_refusal_check(rows=[_row("alpaca_live", "rejected", _DRY)] * 6)

    assert sent == []


def test_a_real_refusal_alongside_policy_skips_still_alerts(isolated) -> None:
    """The suppression is per-ROW, never per-account.

    An account that is switched off AND separately hitting a venue cap has a
    real problem; folding its genuine refusals into the declared ones would
    silence exactly the case this detector exists for.
    """
    from src.runtime.silent_refusal_alert import assess

    rows = ([_row("alpaca_live", "rejected", _DRY)] * 6
            + [_row("alpaca_live", "rejected", "REJECTED: venue_max_qty")] * 5)
    a = assess(rows, min_rows=5)["alpaca_live"]

    assert a["verdict"] == "signalled_never_placed"
    assert a["alerting"] is True
    assert a["refused"] == 5


def test_an_unrecognised_reason_still_counts_as_a_real_refusal(isolated) -> None:
    """Fail-SAFE, the opposite polarity to `account_side_filter`.

    That module gates an ORDER and is fail-permissive. This one gates an ALARM,
    so a reason the classifier cannot read must stay a refusal — the failure we
    refuse is a genuine outage silenced by a predicate that could not parse it.
    """
    from src.runtime.silent_refusal_alert import assess

    a = assess([_row("alpaca_live", "rejected", "REJECTED: something_new")] * 6,
               min_rows=5)["alpaca_live"]

    assert a["verdict"] == "signalled_never_placed"
    assert a["alerting"] is True


def test_why_we_are_not_alerting_is_itself_a_state(isolated) -> None:
    """A bare `alerting: False` collapses three different facts."""
    from src.runtime.silent_refusal_alert import assess

    off = assess([_row("a", "rejected", _DRY)] * 6, min_rows=5)["a"]
    thin = assess([_row("b", "rejected", "REJECTED: venue_max_qty")] * 2, min_rows=5)["b"]
    fine = assess([_row("c", "closed")] * 3, min_rows=5)["c"]
    bad = assess([_row("d", "rejected", "REJECTED: venue_max_qty")] * 6, min_rows=5)["d"]

    assert off["alert_disposition"] == "suppressed_declared_dry_run"
    assert thin["alert_disposition"] == "below_min_rows"
    assert fine["alert_disposition"] == "not_a_finding"
    assert bad["alert_disposition"] == "alerting"


def test_the_predicate_is_imported_never_re_derived() -> None:
    """One module owns "is this refusal deliberate?".

    A second copy is free to drift from the first, and the two would then
    disagree about whether to wake the operator.
    """
    from src.runtime import dead_leg
    from src.runtime.execution_diagnostics import EXPECTED_DISPATCH_SKIP_REASONS

    src = (Path(dead_leg.__file__)).read_text()
    assert "is_expected_dispatch_skip" in src
    for token in EXPECTED_DISPATCH_SKIP_REASONS:
        assert token not in src.split('"""', 2)[2], (
            f"{token!r} is re-declared in dead_leg — import the predicate instead")


def test_recovery_into_declared_dry_run_does_not_claim_orders_were_placed(
    isolated, sent
) -> None:
    """The recovery ping must name the reason it recovered.

    An account can leave the alerting state WITHOUT placing anything — a
    declared dry_run refuses everything, correctly — and the old wording
    ("is placing orders again (0 reached the exchange)") contradicts its own
    numbers in exactly that case.
    """
    from src.runtime.silent_refusal_alert import run_silent_refusal_check

    real = [_row("alpaca_live", "rejected", "REJECTED: venue_max_qty")] * 6
    run_silent_refusal_check(rows=real)
    assert len(sent) == 1 and "[ALERT]" in sent[0]

    run_silent_refusal_check(rows=[_row("alpaca_live", "rejected", _DRY)] * 6, force=True)

    assert len(sent) == 2
    msg = sent[1]
    assert "[OK]" in msg
    assert "DECLARED policy skip" in msg
    assert "placing orders again" not in msg


# ── A latched account that stops producing rows must be able to clear ───────
#
# BL: measured on the live VM 2026-08-25 — `alpaca_live` was latched
# `alerting: true` with `updated_at` frozen at 2026-08-21T12:38:38Z while
# `__last_check__` advanced to the same day, 3.85 days later. The loop iterated
# only `assessed`, which is built from rows INSIDE the window, so an account
# that goes quiet can never re-enter it and the latch stands forever.
# `silent_accounts()` — what the review skills read — kept reporting it.

def _quiet_state(tmp_path, monkeypatch, *, alerting: bool):
    import json
    from src.runtime import silent_refusal_alert as S
    p = tmp_path / "silent_refusal_alert_state.json"
    p.write_text(json.dumps({
        "__last_check__": "2026-08-25T09:04:05+00:00",
        "alpaca_live": {
            "alerting": alerting, "cause": "risk_refused", "refused": 5,
            "placed": 0, "verdict": "signalled_never_placed",
            "updated_at": "2026-08-21T12:38:38+00:00",
        },
    }))
    monkeypatch.setattr(S, "_state_path", lambda: p)
    return S, p


def test_a_latched_account_with_no_rows_is_released(tmp_path, monkeypatch):
    import json
    S, p = _quiet_state(tmp_path, monkeypatch, alerting=True)
    sent = []
    monkeypatch.setattr(S, "_send_alert", lambda m: sent.append(m))
    out = S.run_silent_refusal_check(rows=[], force=True)
    assert out["checked"] is True
    assert "alpaca_live" in out["recovered"]
    assert "alpaca_live" not in json.loads(p.read_text())
    assert len(sent) == 1


def test_the_release_message_does_NOT_claim_the_account_started_placing(tmp_path, monkeypatch):
    """`assess()` never grades an account with no rows, so claiming recovery
    would assert something nobody measured."""
    S, _ = _quiet_state(tmp_path, monkeypatch, alerting=True)
    sent = []
    monkeypatch.setattr(S, "_send_alert", lambda m: sent.append(m))
    S.run_silent_refusal_check(rows=[], force=True)
    msg = sent[0]
    assert "NO order rows at all" in msg
    assert "NOT a report that it started placing orders" in msg
    assert "reached the exchange" not in msg, "that is the OTHER recovery path's wording"


def test_a_quiet_NON_alerting_row_is_pruned_silently(tmp_path, monkeypatch):
    """Nothing was claimed about it, so nothing needs retracting — but it must
    not accumulate in the latch file forever either."""
    import json
    S, p = _quiet_state(tmp_path, monkeypatch, alerting=False)
    sent = []
    monkeypatch.setattr(S, "_send_alert", lambda m: sent.append(m))
    out = S.run_silent_refusal_check(rows=[], force=True)
    assert sent == []
    assert out["recovered"] == []
    assert "alpaca_live" not in json.loads(p.read_text())


def test_the_last_check_key_is_never_treated_as_an_account(tmp_path, monkeypatch):
    import json
    S, p = _quiet_state(tmp_path, monkeypatch, alerting=True)
    monkeypatch.setattr(S, "_send_alert", lambda m: None)
    S.run_silent_refusal_check(rows=[], force=True)
    assert "__last_check__" in json.loads(p.read_text())


# ── per-cause alert floors (2026-08-25, operator decision) ────────────
#
# `BL-20260825-BALANCE-UNREADABLE-CAN-NEVER-REACH-ITS-OWN-ALERT-THRESHOLD`.
# The global floor separates a pattern from one bad order and is right in
# general. For a cause that arrives in ones and twos weeks apart it does not
# mean "wait for a pattern", it means "never fire" — and that cause is Lane 0
# item 0.3's own condition.

# The exact string the sizer emits, taken from a live journal row rather than
# invented, so a change to the message breaks this test loudly.
_BAL_NONE = ("REJECTED: sizing_failed: RuntimeError: balance() returned None "
             "for ib_paper (exchange=interactive_brokers): API error or "
             "unreachable")


def test_a_single_balance_unreadable_row_alerts(isolated) -> None:
    from src.runtime.silent_refusal_alert import assess

    a = assess([_row("ib_paper", "rejected", _BAL_NONE)], min_rows=5)["ib_paper"]
    assert a["cause"] == "balance_unreadable"
    assert a["alerting"] is True
    assert a["alerting_basis"] == "per_cause_floor"
    assert a["priority_causes"] == ["balance_unreadable"]


def test_positive_control_the_old_semantics_would_not_have_fired(isolated) -> None:
    """The probe must be shown to detect a change, not just to pass.

    One row is FAR below the global floor of 5, so the pre-2026-08-25
    condition (`refused >= min_rows`) is false on this exact input. If a future
    edit collapses the per-cause path back into the global one, the assertion
    above starts passing for the wrong reason — this pins that it cannot.
    """
    from src.runtime.silent_refusal_alert import assess

    a = assess([_row("ib_paper", "rejected", _BAL_NONE)], min_rows=5)["ib_paper"]
    assert a["refused"] == 1
    assert a["refused"] < 5, "the old total-floor path must NOT explain this alert"


def test_the_lowered_floor_is_scoped_to_its_cause(isolated) -> None:
    """A different rare cause at one row still does NOT alert."""
    from src.runtime.silent_refusal_alert import assess

    a = assess([_row("b", "rejected", "REJECTED: venue_max_qty")], min_rows=5)["b"]
    assert a["alerting"] is False
    assert a["priority_causes"] == []
    assert a["alert_disposition"] == "below_min_rows"


def test_a_rare_cause_is_not_buried_behind_a_louder_dominant_one(isolated) -> None:
    """The mgc_trend_1h shape, measured live 2026-08-25.

    7 `risk_refused` (the risk manager working correctly) against 3
    `balance_unreadable` (the genuine defect). The dominant-cause line names
    the HEALTHY mechanism, so without the priority line a reader triages the
    wrong thing.
    """
    from src.runtime.silent_refusal_alert import _describe, assess

    rows = ([_row("ib_paper", "rejected", "REJECTED: RiskBreach: INTRADAY_DRAWDOWN")] * 7
            + [_row("ib_paper", "rejected", _BAL_NONE)] * 3)
    a = assess(rows, min_rows=5)["ib_paper"]
    assert a["cause"] == "risk_refused"          # dominant, and healthy
    assert a["priority_causes"] == ["balance_unreadable"]
    assert a["alerting_basis"] == "both"
    body = _describe(a, 24)
    assert "balance_unreadable (3)" in body
    assert "OWN lower floor" in body


def test_the_map_can_only_add_alerting_never_suppress(isolated, monkeypatch) -> None:
    """A per-cause floor ABOVE the global one must not silence anything.

    Otherwise this map becomes a quiet way to disarm a cause, which is a
    decision that belongs somewhere visible.
    """
    from src.runtime import silent_refusal_alert as s

    monkeypatch.setitem(s.CAUSE_MIN_ROWS, "venue_max_qty", 99)
    a = s.assess([_row("b", "rejected", "REJECTED: venue_max_qty")] * 6,
                 min_rows=5)["b"]
    assert a["alerting"] is True
    assert a["alerting_basis"] == "total_floor"
    assert a["priority_causes"] == []


def test_a_malformed_override_does_not_disarm_the_cause(isolated, monkeypatch) -> None:
    from src.runtime import silent_refusal_alert as s

    monkeypatch.setitem(s.CAUSE_MIN_ROWS, "balance_unreadable", "not-a-number")
    a = s.assess([_row("ib_paper", "rejected", _BAL_NONE)] * 6, min_rows=5)["ib_paper"]
    assert a["alerting"] is True, "a typo must fall back to the global floor"


def test_a_pre_existing_latch_does_not_spuriously_refire(isolated, sent) -> None:
    """The deploy that ships this must not re-page every latched account.

    A latch written before `priority_causes` existed has no such key. If the
    comparison read it as None the first tick after deploy would re-fire every
    currently-latched account — the desensitized-alarm failure, self-inflicted.
    """
    from src.runtime.silent_refusal_alert import _save_state, run_silent_refusal_check

    _save_state({"b": {"alerting": True, "cause": "venue_max_qty",
                       "refused": 6, "placed": 0,
                       "verdict": "signalled_never_placed",
                       "updated_at": "2026-08-24T00:00:00+00:00"}})
    rows = [_row("b", "rejected", "REJECTED: venue_max_qty")] * 6
    run_silent_refusal_check(rows=rows, force=True)
    assert sent == [], f"a pre-existing latch must stay latched silently: {sent}"


def test_the_verdict_gate_still_applies_and_this_is_a_known_residual(isolated) -> None:
    """⚠️ MEASURED RESIDUAL, pinned so it is not mistaken for coverage.

    The floor is only HALF the gate — `verdict == signalled_never_placed` must
    also hold, so an account that placed even one order in the window does not
    alert however rare its cause. Measured live 2026-08-25 over the three
    accounts carrying `balance_unreadable` on 2026-08-13: `ib_paper` and
    `alpaca_portfolio` both graded `signalled_never_placed` (so the floor
    change reaches them), but `alpaca_paper` graded `partially_refused`
    (1 placed / 4 refused) and is STILL not covered.

    So "0.3 is covered by this detector" is true of two of the three accounts
    in the very event the row is about, and must not be stated unqualified.
    Widening to `partially_refused` is a separate decision, not this one.
    """
    from src.runtime.silent_refusal_alert import assess

    rows = [_row("alpaca_paper", "closed")] + [_row("alpaca_paper", "rejected", _BAL_NONE)] * 4
    a = assess(rows, min_rows=5)["alpaca_paper"]
    assert a["verdict"] == "partially_refused"
    assert a["priority_causes"] == ["balance_unreadable"]
    assert a["alerting"] is False, "documents the residual — not an endorsement"
    assert a["alert_disposition"] == "not_a_finding"
