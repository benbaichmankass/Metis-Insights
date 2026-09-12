"""Tests for the starved-account detector (MI-276).

This module is the READER for a signal that was measured correct and consumed
by nothing for twelve days (MI-274), so the properties pinned here are mostly
about not re-creating that failure in the reader:

  * the four states stay APART (`collapsed-state-guard` contract
    `starved_account_alert.state`, and this file is one of its consumers);
  * ``routed > 0`` is the discriminator — the ``rounds_applied`` / "elected"
    rule fires on healthy accounts and was measured doing so;
  * pre-``fanout_schema`` rows are UNGRADEABLE, never starvation;
  * ``no_winner`` never pools into the threshold;
  * an unreadable soak returns ``None``, never ``[]``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import alert_cooldown as ac
from src.runtime import starved_account_alert as S

NOW = datetime(2026, 9, 11, 17, 0, tzinfo=timezone.utc)


def _row(per_account, *, minutes_ago=5, schema=2, symbol="SOLUSDT",
         rounds_applied=None):
    row = {
        "logged_at_utc": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
        "symbol": symbol,
        "per_account": {a: {"state": s} for a, s in per_account.items()},
        "rounds_applied": rounds_applied or [],
    }
    if schema is not None:
        row["fanout_schema"] = schema
    return row


def _starved(account, n, **kw):
    return [_row({account: "starved"}, minutes_ago=i + 1, **kw)
            for i in range(n)]


# ── the state vocabulary stays apart ──────────────────────────────────────

def test_four_states_are_four_distinct_values():
    assert len(set(S.STARVED_STATES)) == 4
    assert S.STARVED_STATES == (
        S.STARVED_PERSISTENT, S.STARVED_ROUTING, S.STARVED_NOT_OBSERVED,
        S.STARVED_SOAK_UNREADABLE,
    )


def test_starved_persistent_is_the_finding():
    g = S.assess(_starved("breakout_1", 6), min_rows=4, now=NOW)
    assert g["breakout_1"]["state"] == S.STARVED_PERSISTENT
    assert g["breakout_1"]["starved"] == 6
    assert g["breakout_1"]["routed"] == 0


def test_routing_requires_positive_evidence_and_is_the_only_clean_negative():
    rows = _starved("a", 6) + [_row({"a": "routed"}, minutes_ago=1)]
    g = S.assess(rows, min_rows=4, now=NOW)
    assert g["a"]["state"] == S.STARVED_ROUTING
    assert g["a"]["routed"] == 1


def test_below_threshold_is_not_observed_never_routing():
    """Seen, never routed, but too few rows: we have NO positive evidence it
    can route, so crediting it as healthy would be the collapse."""
    g = S.assess(_starved("a", 2), min_rows=4, now=NOW)
    assert g["a"]["state"] == S.STARVED_NOT_OBSERVED
    assert g["a"]["state"] != S.STARVED_ROUTING
    assert g["a"]["below_min_rows"] is True


def test_an_unreadable_soak_is_its_own_state_not_a_quiet_fleet():
    g = S.assess(None, min_rows=4, now=NOW)
    assert g == {"__soak__": {
        "state": S.STARVED_SOAK_UNREADABLE, "starved": 0, "routed": 0,
        "no_winner": 0, "gradeable_rows": 0, "ungradeable_rows": 0,
        "window_rows": 0}}


def test_read_soak_tail_returns_none_not_empty_when_absent(tmp_path):
    """None is 'we did not look'; [] would read as a quiet fleet."""
    assert S.read_soak_tail(tmp_path / "nope.jsonl") is None


# ── the discriminator ─────────────────────────────────────────────────────

def test_routed_not_rounds_applied_is_the_discriminator():
    """THE MEASURED TRAP. `rounds_applied` contains ONLY allowlisted accounts,
    so a non-allowlisted account that holds the global winner still gets its
    order out and is graded `routed`. Measured 2026-09-11 over the complete
    soak: `bybit_2` and `bybit_portfolio` were each elected 66 times with
    `rounds_applied` empty and were NOT starved. Keying on `rounds_applied`
    would report two healthy accounts as starved and bury the one real finding.
    """
    rows = [_row({"bybit_2": "routed"}, minutes_ago=i + 1, rounds_applied=[])
            for i in range(8)]
    g = S.assess(rows, min_rows=4, now=NOW)
    assert g["bybit_2"]["state"] == S.STARVED_ROUTING
    assert all(not r["rounds_applied"] for r in rows)


def test_the_measured_bybit_1_shape_does_not_fire():
    """Starved occasionally, routed overwhelmingly — the normal contest loser.
    Measured over the complete soak: 1 starved against 391 routed."""
    rows = (_starved("bybit_1", 1)
            + [_row({"bybit_1": "routed"}, minutes_ago=i + 2) for i in range(40)])
    g = S.assess(rows, min_rows=4, now=NOW)
    assert g["bybit_1"]["state"] == S.STARVED_ROUTING


# ── schema: pre-v2 rows conflate and must never be graded ─────────────────

def test_pre_schema_rows_are_ungradeable_never_starvation():
    """`arbitration_fanout` states a row with no `fanout_schema` CONFLATES
    starvation with no-winner ticks — measured at a 6.5x overstatement.
    Restricting to v2 removed 13 of `bybit_1`'s 14 starved gradings."""
    rows = _starved("a", 8, schema=None)
    g = S.assess(rows, min_rows=4, now=NOW)
    assert "a" not in g, "a pre-schema row must not produce a verdict"


def test_pre_schema_rows_are_counted_in_the_denominator_not_dropped():
    rows = _starved("a", 5) + _starved("a", 3, schema=None)
    g = S.assess(rows, min_rows=4, now=NOW)
    assert g["a"]["state"] == S.STARVED_PERSISTENT
    assert g["a"]["starved"] == 5
    assert g["a"]["ungradeable_rows"] == 3
    assert g["a"]["gradeable_rows"] == 5
    assert g["a"]["window_rows"] == 8


# ── no_winner is a different population and never pools ───────────────────

def test_no_winner_never_reaches_the_threshold():
    """A no-winner tick has no other account to have lost to; its cause is
    upstream and a fan-out is not its remedy."""
    rows = [_row({"a": "no_winner"}, minutes_ago=i + 1) for i in range(20)]
    g = S.assess(rows, min_rows=4, now=NOW)
    assert g["a"]["no_winner"] == 20
    assert g["a"]["starved"] == 0
    assert g["a"]["state"] != S.STARVED_PERSISTENT


def test_no_winner_rides_as_context_beside_a_real_finding():
    rows = _starved("a", 5) + [_row({"a": "no_winner"}, minutes_ago=9)]
    g = S.assess(rows, min_rows=4, now=NOW)
    assert g["a"]["state"] == S.STARVED_PERSISTENT
    assert g["a"]["no_winner"] == 1
    assert "NOT counted here" in S.describe("a", g["a"], window_hours=24)


# ── the window ────────────────────────────────────────────────────────────

def test_rows_outside_the_window_are_excluded():
    old = [_row({"a": "starved"}, minutes_ago=60 * 48 + i) for i in range(9)]
    g = S.assess(old, min_rows=4, now=NOW, window_hours=24)
    assert "a" not in g


def test_an_unparseable_timestamp_is_skipped_not_counted():
    rows = _starved("a", 5)
    rows.append(_row({"a": "starved"}))
    rows[-1]["logged_at_utc"] = "not-a-date"
    g = S.assess(rows, min_rows=4, now=NOW)
    assert g["a"]["starved"] == 5


# ── the tail read is bounded and tolerant of one bad line ─────────────────

def test_tail_read_is_bounded_and_drops_the_partial_first_line(tmp_path):
    p = tmp_path / "soak.jsonl"
    p.write_text("".join(json.dumps({"n": i, "pad": "x" * 200}) + "\n"
                         for i in range(200)), encoding="utf-8")
    rows = S.read_soak_tail(p, max_bytes=4096)
    assert rows is not None and 0 < len(rows) < 200
    assert all(isinstance(r, dict) and "n" in r for r in rows)


def test_one_unparseable_line_is_not_an_unreadable_file(tmp_path):
    p = tmp_path / "soak.jsonl"
    p.write_text('{"n": 1}\nNOT JSON\n{"n": 2}\n', encoding="utf-8")
    rows = S.read_soak_tail(p, max_bytes=1 << 20)
    assert rows is not None and len(rows) == 2


# ── the latch ─────────────────────────────────────────────────────────────

def test_observation_file_does_not_collide_with_the_cooldown_file():
    assert ac.state_path(S.ALERT_KIND).name != S._STATE_FILENAME


def test_the_shared_durable_cooldown_is_used_not_a_copy(monkeypatch, tmp_path):
    assert S._alert_cooldown is ac
    seen = {}
    monkeypatch.setattr(S._alert_cooldown, "cooldown_admits",
                        lambda kind, key, c, **kw: seen.update(
                            kind=kind, key=key, kw=kw) or True)
    monkeypatch.setattr(S, "_state_path", lambda: tmp_path / "obs.json")
    monkeypatch.setattr(S, "_send_alert", lambda m: None)
    S.run_starved_account_check(rows=_starved("a", 6), force=True, now=NOW)
    assert seen["kind"] == S.ALERT_KIND and seen["key"] == "a"


def test_no_severity_is_passed_because_the_count_tracks_signal_volume(
        monkeypatch, tmp_path):
    """The deliberate asymmetry with `losing_streak_alert`. Severity there is
    the streak length, a monotone measure of the FAULT. Here the only candidate
    is the starvation count, which rises on a busy market day while the defect
    is unchanged — paging on that would call market activity deterioration."""
    seen = {}
    monkeypatch.setattr(S._alert_cooldown, "cooldown_admits",
                        lambda kind, key, c, **kw: seen.update(kw=kw) or True)
    monkeypatch.setattr(S, "_state_path", lambda: tmp_path / "obs.json")
    monkeypatch.setattr(S, "_send_alert", lambda m: None)
    S.run_starved_account_check(rows=_starved("a", 9), force=True, now=NOW)
    assert "severity" not in seen["kw"]


def test_alert_is_WARN_not_CRITICAL_and_states_its_population():
    g = S.assess(_starved("breakout_1", 6), min_rows=4, now=NOW)
    body = S.describe("breakout_1", g["breakout_1"], window_hours=24)
    assert "[WARN]" in body and "CRITICAL" not in body
    assert "Population:" in body
    assert "ARBITRATION_FANOUT_ACCOUNTS" in body


# ── knobs ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["", "  ", "banana", None])
def test_an_unparseable_knob_falls_back_to_the_default_never_to_zero(
        monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("STARVED_ACCOUNT_MIN_ROWS", raising=False)
    else:
        monkeypatch.setenv("STARVED_ACCOUNT_MIN_ROWS", raw)
    assert S._int_knob("STARVED_ACCOUNT_MIN_ROWS", 4, minimum=1) == 4


def test_paused_when_cadence_is_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("STARVED_ACCOUNT_CHECK_SECONDS", "0")
    monkeypatch.setattr(S, "_state_path", lambda: tmp_path / "obs.json")
    assert S.run_starved_account_check()["reason"] == "paused"


def test_skip_list_suppresses_an_account(monkeypatch, tmp_path):
    monkeypatch.setenv("STARVED_ACCOUNT_SKIP", "a")
    monkeypatch.setattr(S, "_state_path", lambda: tmp_path / "obs.json")
    monkeypatch.setattr(S._alert_cooldown, "cooldown_admits",
                        lambda *a, **k: True)
    sent = []
    monkeypatch.setattr(S, "_send_alert", lambda m: sent.append(m))
    S.run_starved_account_check(rows=_starved("a", 9), force=True, now=NOW)
    assert sent == []


# ── an unreadable soak must not retract a standing finding ────────────────

def test_an_unreadable_soak_latches_nothing_and_clears_nothing(
        monkeypatch, tmp_path):
    monkeypatch.setattr(S, "_state_path", lambda: tmp_path / "obs.json")
    monkeypatch.setattr(S._alert_cooldown, "cooldown_admits",
                        lambda *a, **k: True)
    sent = []
    monkeypatch.setattr(S, "_send_alert", lambda m: sent.append(m))
    S.run_starved_account_check(rows=_starved("a", 6), force=True, now=NOW)
    assert any("[WARN]" in m for m in sent)
    sent.clear()
    out = S.run_starved_account_check(rows=None, force=True, now=NOW)
    assert out["reason"] == "soak_unreadable"
    assert sent == [], "a blind window must not send a recovery"
    assert S.status()["a"]["state"] == S.STARVED_PERSISTENT


# ── recovery never claims something nobody measured ───────────────────────

def test_dropping_out_of_the_soak_is_not_reported_as_a_recovery(
        monkeypatch, tmp_path):
    monkeypatch.setattr(S, "_state_path", lambda: tmp_path / "obs.json")
    monkeypatch.setattr(S._alert_cooldown, "cooldown_admits",
                        lambda *a, **k: True)
    sent = []
    monkeypatch.setattr(S, "_send_alert", lambda m: sent.append(m))
    S.run_starved_account_check(rows=_starved("a", 6), force=True, now=NOW)
    sent.clear()
    S.run_starved_account_check(rows=[_row({"other": "routed"})], force=True,
                                now=NOW)
    assert len(sent) == 1 and "[OK]" in sent[0]
    assert "NOT a report that it started routing" in sent[0]


def test_a_real_recovery_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "_state_path", lambda: tmp_path / "obs.json")
    monkeypatch.setattr(S._alert_cooldown, "cooldown_admits",
                        lambda *a, **k: True)
    sent = []
    monkeypatch.setattr(S, "_send_alert", lambda m: sent.append(m))
    S.run_starved_account_check(rows=_starved("a", 6), force=True, now=NOW)
    sent.clear()
    S.run_starved_account_check(
        rows=_starved("a", 6) + [_row({"a": "routed"}, minutes_ago=1)],
        force=True, now=NOW)
    assert len(sent) == 1 and "[OK]" in sent[0]
    assert "routed in the last" in sent[0]
