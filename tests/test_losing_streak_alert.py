"""Tests for the sustained-losing-streak detector (MI-276).

The load-bearing properties, each pinned because it is a defect this repo has
already paid for at least once:

  * the four states stay APART (`collapsed-state-guard` contract
    `losing_streak_alert.state`, and this file is one of its consumers);
  * the SIZE GATE, not the day count, is what suppresses a dollar-scale noise
    book — re-measured from the live journal, ``bybit_2``'s longest losing run
    is 10 days at −$56, so raising N never separates it;
  * the latch is the SHARED DURABLE one, never a copy and never monotonic
    (BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART);
  * the observation file does NOT collide with the cooldown's file;
  * a recovery message never claims a recovery nobody measured.
"""
from __future__ import annotations

import json

import pytest

from src.runtime import alert_cooldown as ac
from src.runtime import losing_streak_alert as L


def _row(account, day, pnl, *, status="closed", backtest=0, notes=None):
    return {
        "account_id": account, "status": status, "is_backtest": backtest,
        "pnl": pnl, "closed_at": f"{day}T12:00:00+00:00",
        "created_at": f"{day}T09:00:00+00:00", "timestamp": f"{day}T12:00:00Z",
        "notes": json.dumps(notes) if notes is not None else None,
    }


def _losing(account, days, per_day=-1000.0):
    return [_row(account, d, per_day) for d in days]


# ── the state vocabulary stays apart ──────────────────────────────────────

def test_four_states_are_four_distinct_values():
    assert len(set(L.STREAK_STATES)) == 4
    assert L.STREAK_STATES == (
        L.STREAK_ACTIVE, L.STREAK_NONE, L.STREAK_INSUFFICIENT,
        L.STREAK_UNREADABLE,
    )


def test_streak_active_is_the_finding():
    rows = _losing("a", ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"])
    g = L.assess(rows, min_days=4, min_loss_usd=500.0, max_gap_days=3)
    assert g["a"]["state"] == L.STREAK_ACTIVE
    assert g["a"]["streak_days"] == 4
    assert g["a"]["streak_loss_usd"] == pytest.approx(-4000.0)


def test_no_streak_when_the_newest_graded_day_won():
    rows = _losing("a", ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"])
    rows += [_row("a", "2026-09-05", +10.0)]
    g = L.assess(rows, min_days=4, min_loss_usd=500.0, max_gap_days=3)
    assert g["a"]["state"] == L.STREAK_NONE
    assert g["a"]["streak_days"] == 0


def test_insufficient_days_is_not_a_clean_negative():
    """Fewer gradeable days than the threshold: the streak CANNOT exist yet.

    A book with too few closes to grade is not a book that is winning, and
    reporting it as `no_streak` would be the clean-negative-over-no-denominator
    error.
    """
    rows = _losing("a", ["2026-09-03", "2026-09-04"])
    g = L.assess(rows, min_days=4, min_loss_usd=500.0, max_gap_days=3)
    assert g["a"]["state"] == L.STREAK_INSUFFICIENT
    assert g["a"]["state"] != L.STREAK_NONE
    assert g["a"]["graded_days"] == 2


def test_unreadable_when_rows_exist_but_none_carry_a_usable_pnl():
    """We looked and could not read it — distinct from both negatives."""
    rows = [_row("a", "2026-09-0%d" % d, None) for d in (1, 2, 3, 4, 5)]
    g = L.assess(rows, min_days=4, min_loss_usd=500.0, max_gap_days=3)
    assert g["a"]["state"] == L.STREAK_UNREADABLE
    assert g["a"]["state"] not in (L.STREAK_NONE, L.STREAK_INSUFFICIENT)
    assert g["a"]["rows"] == 5          # the rows were SEEN
    assert g["a"]["graded_days"] == 0   # and none was gradeable


def test_an_account_with_no_rows_is_absent_not_graded_healthy():
    g = L.assess(_losing("a", ["2026-09-01"]), min_days=4, min_loss_usd=1.0,
                 max_gap_days=3)
    assert "b" not in g


# ── the size gate is the load-bearing term ────────────────────────────────

def test_size_gate_suppresses_a_dollar_scale_noise_book():
    """The measured `bybit_2` shape: a LONG run of trivial daily losses.

    Re-measured 2026-09-11 over the live journal, `bybit_2`'s longest losing
    run is 10 days totalling −$56.15 at a median absolute daily PnL of $5.10.
    This is the regression that MI-271 § 6's "N>=4 has zero false positives"
    claim would not have caught.
    """
    days = [f"2026-09-{d:02d}" for d in range(1, 11)]
    rows = _losing("bybit_2_shaped", days, per_day=-5.6)
    g = L.assess(rows, min_days=4, min_loss_usd=500.0, max_gap_days=3)
    assert g["bybit_2_shaped"]["streak_days"] == 10
    assert g["bybit_2_shaped"]["state"] == L.STREAK_NONE
    assert g["bybit_2_shaped"]["below_min_loss"] is True
    assert g["bybit_2_shaped"]["below_min_days"] is False


@pytest.mark.parametrize("n", [4, 5, 6, 8, 10])
def test_raising_n_never_separates_the_noise_book(n):
    """The correction to MI-271 § 6, pinned: only the size gate removes it."""
    days = [f"2026-09-{d:02d}" for d in range(1, 11)]
    rows = _losing("noise", days, per_day=-5.6)
    ungated = L.assess(rows, min_days=n, min_loss_usd=0.0, max_gap_days=3)
    gated = L.assess(rows, min_days=n, min_loss_usd=500.0, max_gap_days=3)
    if n <= 10:
        assert ungated["noise"]["state"] == L.STREAK_ACTIVE, (
            "without a size gate the noise book fires at every N it is long "
            "enough to reach — raising N is not the remedy")
    assert gated["noise"]["state"] == L.STREAK_NONE


# ── gaps ──────────────────────────────────────────────────────────────────

def test_a_short_silence_does_not_break_a_run():
    """A day with no closes is 'we did not look', not a winning day."""
    rows = _losing("a", ["2026-09-01", "2026-09-02", "2026-09-04", "2026-09-05"])
    run = L.current_run(L.daily_pnl(rows)["a"], max_gap_days=3)
    assert run["length"] == 4
    assert run["start"] == "2026-09-01"


def test_a_long_silence_ends_the_run():
    rows = _losing("a", ["2026-09-01", "2026-09-02", "2026-09-10", "2026-09-11"])
    run = L.current_run(L.daily_pnl(rows)["a"], max_gap_days=3)
    assert run["length"] == 2, "a run resuming after 8 days is not consecutive"
    assert run["start"] == "2026-09-10"


# ── provenance ────────────────────────────────────────────────────────────

def test_fabricated_pnl_is_counted_but_never_summed():
    """A streak must not be manufacturable from reconstructed rows."""
    fab = {"exit_price_source": "local_markprice"}
    rows = [_row("a", f"2026-09-0{d}", -9999.0, notes=fab) for d in (1, 2, 3, 4)]
    by_day = L.daily_pnl(rows)["a"]
    assert all(c["pnl"] == 0.0 for c in by_day.values()), \
        "fabricated pnl must not reach the daily sum"
    assert sum(c["fabricated"] for c in by_day.values()) == 4, \
        "...but it must still be counted, or the exclusion is invisible"
    g = L.assess(rows, min_days=4, min_loss_usd=500.0, max_gap_days=3)
    assert g["a"]["state"] == L.STREAK_UNREADABLE


def test_measured_rows_are_summed_and_coverage_is_reported():
    notes = {"exit_price_source": "exchange_fill"}
    rows = [_row("a", f"2026-09-0{d}", -1000.0, notes=notes)
            for d in (1, 2, 3, 4)]
    g = L.assess(rows, min_days=4, min_loss_usd=500.0, max_gap_days=3)
    assert g["a"]["state"] == L.STREAK_ACTIVE
    assert g["a"]["pnl_coverage"] == pytest.approx(1.0)
    assert g["a"]["provenance"]["measured"] == 4


def test_coverage_is_none_not_zero_when_nothing_is_gradeable():
    """None and 0.0 are different facts — 'no denominator' vs 'none measured'."""
    rows = [_row("a", "2026-09-01", None)]
    g = L.assess(rows, min_days=4, min_loss_usd=500.0, max_gap_days=3)
    assert g["a"]["pnl_coverage"] is None


# ── population hygiene ────────────────────────────────────────────────────

def test_backtest_and_open_rows_are_excluded():
    rows = (_losing("a", ["2026-09-01"])
            + [_row("a", "2026-09-02", -5000.0, backtest=1),
               _row("a", "2026-09-03", -5000.0, status="open")])
    by_day = L.daily_pnl(rows)["a"]
    assert set(by_day) == {"2026-09-01"}


def test_the_day_is_the_CLOSE_day_not_the_open_day():
    row = _row("a", "2026-09-05", -1.0)
    row["created_at"] = "2026-09-01T09:00:00+00:00"
    by_day = L.daily_pnl([row])["a"]
    assert set(by_day) == {"2026-09-05"}


# ── the latch ─────────────────────────────────────────────────────────────

def test_observation_file_does_not_collide_with_the_cooldown_file():
    """Two writers, incompatible shapes, one path = silent total failure.

    `alert_cooldown.state_path` resolves ALERT_KIND to
    `<kind>_alert_state.json`; the detector's own observation state must not
    be given that same name. The cooldown's TTL prune drops every non-numeric
    value, so one pass would delete every observation — and both writers
    swallow their own exceptions, so the detector would keep looking installed.
    """
    assert ac.state_path(L.ALERT_KIND).name != L._STATE_FILENAME


def test_the_shared_durable_cooldown_is_used_not_a_copy(monkeypatch, tmp_path):
    """A copied latch is how the per-PROCESS defect returns in the copy."""
    # The identity check IS the anti-copy assertion: the detector must bind the
    # shared module itself, not a private re-implementation that happens to
    # have the same name.
    assert L._alert_cooldown is ac

    seen = {}

    def spy(kind, key, cooldown_s, **kw):
        seen["kind"], seen["key"], seen["kw"] = kind, key, kw
        return True

    monkeypatch.setattr(L._alert_cooldown, "cooldown_admits", spy)
    monkeypatch.setattr(L, "_state_path", lambda: tmp_path / "obs.json")
    monkeypatch.setattr(L, "_send_alert", lambda msg: None)
    rows = _losing("a", ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"])
    L.run_losing_streak_check(rows=rows, force=True)
    assert seen["kind"] == L.ALERT_KIND
    assert seen["key"] == "a"
    assert seen["kw"].get("severity") == 4


def test_severity_is_the_streak_length_so_a_lengthening_streak_pages(
        monkeypatch, tmp_path):
    """Severity must be monotone in the FAULT, so the shared one-directional
    rule pages on a deepening streak and stays silent on one that persists."""
    seen = []
    monkeypatch.setattr(L._alert_cooldown, "cooldown_admits",
                        lambda k, key, c, **kw: seen.append(kw.get("severity")) or True)
    monkeypatch.setattr(L, "_state_path", lambda: tmp_path / "obs.json")
    monkeypatch.setattr(L, "_send_alert", lambda msg: None)
    L.run_losing_streak_check(
        rows=_losing("a", [f"2026-09-0{d}" for d in (1, 2, 3, 4, 5)]),
        force=True)
    assert seen == [5]


def test_alert_is_WARN_not_CRITICAL():
    """CRITICAL is reserved for a position UNPROTECTED or REVERSED."""
    rows = _losing("a", ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"])
    g = L.assess(rows, min_days=4, min_loss_usd=500.0, max_gap_days=3)
    body = L.describe("a", g["a"])
    assert "[WARN]" in body and "CRITICAL" not in body
    assert "2026-09-01" in body and "2026-09-04" in body   # states the window
    assert "gradeable day(s)" in body                      # states the population


# ── knobs ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["", "  ", "banana", "4.5.6", None])
def test_an_unparseable_knob_falls_back_to_the_default_never_to_zero(
        monkeypatch, raw):
    """A typo must not silently switch the only watcher for this class off."""
    if raw is None:
        monkeypatch.delenv("LOSING_STREAK_MIN_DAYS", raising=False)
    else:
        monkeypatch.setenv("LOSING_STREAK_MIN_DAYS", raw)
    assert L._int_knob("LOSING_STREAK_MIN_DAYS", 4, minimum=2) == 4


def test_paused_when_cadence_is_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("LOSING_STREAK_CHECK_SECONDS", "0")
    monkeypatch.setattr(L, "_state_path", lambda: tmp_path / "obs.json")
    assert L.run_losing_streak_check()["reason"] == "paused"


def test_skip_list_suppresses_an_account(monkeypatch, tmp_path):
    monkeypatch.setenv("LOSING_STREAK_SKIP", "a")
    monkeypatch.setattr(L, "_state_path", lambda: tmp_path / "obs.json")
    monkeypatch.setattr(L._alert_cooldown, "cooldown_admits",
                        lambda *a, **k: True)
    sent = []
    monkeypatch.setattr(L, "_send_alert", lambda m: sent.append(m))
    rows = _losing("a", ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"])
    L.run_losing_streak_check(rows=rows, force=True)
    assert sent == []


# ── recovery never claims something nobody measured ───────────────────────

def test_going_quiet_is_not_reported_as_a_recovery(monkeypatch, tmp_path):
    """The `silent_refusal_alert` 2026-08-24 correction, pinned here."""
    monkeypatch.setattr(L, "_state_path", lambda: tmp_path / "obs.json")
    monkeypatch.setattr(L._alert_cooldown, "cooldown_admits",
                        lambda *a, **k: True)
    sent = []
    monkeypatch.setattr(L, "_send_alert", lambda m: sent.append(m))
    days = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
    L.run_losing_streak_check(rows=_losing("a", days), force=True)
    assert any("[WARN]" in m for m in sent)
    sent.clear()
    L.run_losing_streak_check(rows=[], force=True)   # account went silent
    assert len(sent) == 1
    assert "[OK]" in sent[0]
    assert "NOT a report that it stopped losing" in sent[0]


def test_a_real_recovery_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "_state_path", lambda: tmp_path / "obs.json")
    monkeypatch.setattr(L._alert_cooldown, "cooldown_admits",
                        lambda *a, **k: True)
    sent = []
    monkeypatch.setattr(L, "_send_alert", lambda m: sent.append(m))
    days = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
    L.run_losing_streak_check(rows=_losing("a", days), force=True)
    sent.clear()
    L.run_losing_streak_check(rows=_losing("a", days) + [_row("a", "2026-09-05", 10.0)],
                              force=True)
    assert len(sent) == 1 and "[OK]" in sent[0]
    assert "was not a loss" in sent[0]


def test_a_read_failure_latches_nothing_and_clears_nothing(monkeypatch, tmp_path):
    """'We could not look' must never render as 'no account is bleeding'."""
    monkeypatch.setattr(L, "_state_path", lambda: tmp_path / "obs.json")
    monkeypatch.setattr(L, "_read_window",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    out = L.run_losing_streak_check(force=True)
    assert out == {"checked": False, "reason": "read_failed"}
