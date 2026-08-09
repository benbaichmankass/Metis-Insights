"""The exposure soak must accumulate a DISTRIBUTION without inventing readings.

`gross-exposure-governance-DESIGN.md` § 6 requires the ceiling to sit above
normal operation and below the venue limit; § 7 forbids shipping a value with no
observation soak behind it. Both need a per-account distribution over time, and
the statistic that matters is the **max** — a ceiling clearing the average but
not the peak silently clamps correctly-risk-sized trades at the peak, which § 6
names as worse than no ceiling at all.

Two failure modes are asserted dead here, because both would corrupt that max:

1. **A fabricated zero.** An unmeasured account must never be recorded as
   `0.0`. "We could not look" and "the account is flat" are opposite statements,
   and a fake zero drags a per-account minimum to a value never observed.
2. **An unstated denominator.** A max over 2 samples and a max over 200 are
   different claims, so `measured_n` / `rows` ride beside every max.
"""

from __future__ import annotations

import json

import pytest

from src.runtime import exposure_soak as es


@pytest.fixture(autouse=True)
def _isolate_log(tmp_path, monkeypatch):
    monkeypatch.setattr(es, "soak_log_path", lambda: tmp_path / "exposure_soak.jsonl")
    es._last_emit_ts = None
    yield


def _exp(*, measured=True, multiple=1.5, policy=False, reason=None):
    return {
        "policy_declared": policy,
        "max_gross_exposure_pct": 2.0 if policy else None,
        "open_gross_notional": 3000.0 if measured else None,
        "equity": 2000.0 if measured else None,
        "exposure_multiple": multiple if measured else None,
        "headroom_usd": None,
        "measured": measured,
        "unmeasured_reason": reason,
    }


# ---------------------------------------------------------------------------
# The two corruption modes
# ---------------------------------------------------------------------------

def test_an_unmeasured_account_is_never_recorded_as_a_zero_multiple():
    rec = es.build_exposure_soak_record(
        account_id="ib_live", exposure=_exp(measured=False, reason="equity_unavailable")
    )
    assert rec["measured"] is False
    assert rec["exposure_multiple"] is None, "a fake 0.0 would assert FLAT, not unknown"
    assert rec["unmeasured_reason"] == "equity_unavailable"


def test_unmeasured_rows_do_not_move_the_max_or_the_min():
    es.record_exposure_soak(es.build_exposure_soak_record(
        account_id="a", exposure=_exp(multiple=1.5)))
    es.record_exposure_soak(es.build_exposure_soak_record(
        account_id="a", exposure=_exp(measured=False, reason="no_equity")))
    es.record_exposure_soak(es.build_exposure_soak_record(
        account_id="a", exposure=_exp(multiple=2.4)))

    slot = es.read_soak_records()["summary"]["by_account"]["a"]
    assert slot["max_multiple"] == 2.4
    assert slot["min_multiple"] == 1.5, "an unmeasured row must not pull the min to 0"
    assert slot["rows"] == 3
    assert slot["measured_n"] == 2
    assert slot["unmeasured_n"] == 1


def test_the_max_always_ships_with_its_denominator():
    """A max over 2 samples is not the claim a max over 200 is."""
    for m in (0.4, 1.9):
        es.record_exposure_soak(es.build_exposure_soak_record(
            account_id="bybit_2", exposure=_exp(multiple=m)))
    slot = es.read_soak_records()["summary"]["by_account"]["bybit_2"]
    assert slot["max_multiple"] == 1.9
    assert {"rows", "measured_n"} <= set(slot), "the denominator must never be omitted"
    assert slot["measured_n"] == 2


# ---------------------------------------------------------------------------
# The 2026-08-09 lesson: quiet-because-shut vs quiet-because-refusing
# ---------------------------------------------------------------------------

def test_the_venue_session_is_recorded_so_quiet_is_not_read_as_refused():
    rec = es.build_exposure_soak_record(
        account_id="alpaca_paper", exposure=_exp(multiple=2.02), venue_session="closed"
    )
    assert rec["venue_session_us_equity"] == "closed"


def test_the_summary_reports_how_much_of_the_window_was_closed():
    for s in ("closed", "closed", "rth"):
        es.record_exposure_soak(es.build_exposure_soak_record(
            account_id="alpaca_paper", exposure=_exp(multiple=2.0), venue_session=s))
    sessions = es.read_soak_records()["summary"]["venue_sessions"]
    assert sessions == {"closed": 2, "rth": 1}, (
        "a soak that is mostly `closed` has not observed normal operation, and "
        "the reader must be able to see that without re-deriving the calendar"
    )


# ---------------------------------------------------------------------------
# Cadence is a knob, not an enable gate
# ---------------------------------------------------------------------------

def test_cadence_defaults_on_and_is_not_an_enable_gate(monkeypatch):
    monkeypatch.delenv(es._CADENCE_ENV, raising=False)
    assert es.cadence_seconds() == es._DEFAULT_CADENCE_S > 0


def test_a_non_positive_cadence_pauses_without_a_redeploy(monkeypatch):
    monkeypatch.setenv(es._CADENCE_ENV, "0")
    assert es.emit_exposure_soak() == 0


def test_a_garbage_cadence_falls_back_to_the_default_rather_than_pausing(monkeypatch):
    """Fail-ON: a typo must not silently switch observability off."""
    monkeypatch.setenv(es._CADENCE_ENV, "not-a-number")
    assert es.cadence_seconds() == es._DEFAULT_CADENCE_S


# ---------------------------------------------------------------------------
# Never perturb the tick
# ---------------------------------------------------------------------------

def test_emit_never_raises_when_accounts_cannot_be_loaded(monkeypatch):
    import src.units.accounts as accounts_mod
    monkeypatch.setattr(
        accounts_mod, "load_accounts",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert es.emit_exposure_soak(force=True) == 0  # swallowed, tick unharmed


def test_one_bad_account_does_not_stop_the_sweep(monkeypatch):
    class _RM:
        def __init__(self, exp): self._e = exp
        def report(self):
            if self._e is None:
                raise RuntimeError("bad account")
            return {"exposure": self._e}

    class _A:
        def __init__(self, name, exp):
            self.name, self.exchange, self.account_class = name, "bybit", "paper"
            self.risk_manager = _RM(exp)

    import src.units.accounts as accounts_mod
    monkeypatch.setattr(accounts_mod, "load_accounts",
                        lambda *a, **k: [_A("good1", _exp()), _A("bad", None), _A("good2", _exp())])
    assert es.emit_exposure_soak(force=True) == 2
    ids = {r["account_id"] for r in es.read_soak_records()["records"]}
    assert ids == {"good1", "good2"}


def test_a_malformed_record_is_dropped_not_written():
    assert es.build_exposure_soak_record(account_id="", exposure=_exp()) is None
    assert es.record_exposure_soak(None) is False


# ---------------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------------

def test_absent_log_returns_present_false_not_an_empty_success():
    env = es.read_soak_records()
    assert env["present"] is False
    assert env["count"] == 0
    assert env["summary"]["total_scanned"] == 0


def test_records_are_newest_first_and_the_summary_spans_the_whole_file():
    for m in (0.1, 0.2, 0.3):
        es.record_exposure_soak(es.build_exposure_soak_record(
            account_id="a", exposure=_exp(multiple=m)))
    env = es.read_soak_records(limit=1)
    assert env["count"] == 1
    assert env["records"][0]["exposure_multiple"] == 0.3, "newest first"
    assert env["summary"]["total_scanned"] == 3, (
        "the summary must span the file, not the truncated page — otherwise the "
        "max silently becomes a max-over-the-page"
    )


def test_a_corrupt_line_is_skipped_without_killing_the_read(tmp_path):
    p = es.soak_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"account_id": "a", "measured": True, "exposure_multiple": 1.0}) + "\n"
        + "{not json\n"
        + json.dumps({"account_id": "a", "measured": True, "exposure_multiple": 2.0}) + "\n"
    )
    env = es.read_soak_records()
    assert env["summary"]["by_account"]["a"]["max_multiple"] == 2.0
    assert env["summary"]["total_scanned"] == 2
