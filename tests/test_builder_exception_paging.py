"""One persisting builder fault must not bury every other ERROR.

MEASURED 2026-08-25, live ERROR+ feed (`/api/bot/logs?level=error&limit=400`,
157 rows returned): **131 of them — 83.4% of the entire operator
ERROR+/CRITICAL feed — were one leg**, `ict_scalp_mgc_15m: no candle data for
symbol=MGC`, repeating. Sharing that feed were 15 Bybit venue rejections, 2
over-cover CRITICALs and 8 target-naked pages, all of them buried.

Same shape and a worse ratio than the `ib_target_naked` flood
(202/376 = 53.7%, BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART).

STATE THE POPULATION: `mgc_trend_1h` trades the SAME symbol and is unaffected,
so this is not a gateway blackout for MGC — it is one leg's timeframe. That is
why the latch key is per STRATEGY, not per symbol or account.
"""
from __future__ import annotations

import pathlib

import pytest

import src.runtime.pipeline as pl


@pytest.fixture()
def pages(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir", lambda: tmp_path)
    sent = []
    monkeypatch.setattr(pl, "report", lambda *a, **k: sent.append(k))
    return sent


class TestTheRepeatIsDowngradedNotSuppressed:
    def test_the_first_occurrence_pages_at_ERROR(self, pages):
        pl._report_builder_exception("ict_scalp_mgc_15m",
                                     RuntimeError("no candle data for symbol=MGC"))
        assert pages[0]["level"] is pl.Level.ERROR

    def test_the_same_cause_again_is_WARN_not_silence(self, pages):
        """Suppressing outright would make a persisting fault invisible — the
        opposite failure, and just as bad. WARN still persists to
        outcomes.jsonl and still renders on the notifications banner; it just
        does not Telegram."""
        for _ in range(5):
            pl._report_builder_exception(
                "ict_scalp_mgc_15m", RuntimeError("no candle data for symbol=MGC"))
        assert len(pages) == 5, "every occurrence is still RECORDED"
        assert pages[0]["level"] is pl.Level.ERROR
        assert all(p["level"] is pl.Level.WARN for p in pages[1:])

    def test_the_downgraded_row_says_why_it_was_downgraded(self, pages):
        for _ in range(2):
            pl._report_builder_exception("s", RuntimeError("boom"))
        assert "repeat" in pages[1]["reason"]
        assert "boom" in pages[1]["reason"], "the cause is never dropped"

    def test_the_level_constant_is_WARN_not_WARNING(self, pages):
        """CLAUDE.md records this exact trap: outcomes.Level has `warn`, and a
        consumer filtering on "WARNING" silently dropped every WARN row
        (BL-20260813-OPERATOR-WARNING-BANNER-CANNOT-MATCH-WARN)."""
        pl._report_builder_exception("s", RuntimeError("x"))
        pl._report_builder_exception("s", RuntimeError("x"))
        assert pages[1]["level"].value == "warn"


class TestTheKeyCarriesTheCauseNotJustTheStrategy:
    def test_a_NEW_cause_on_a_latched_strategy_pages_immediately(self, pages):
        """A per-strategy-only latch would report a genuinely new failure as
        'already alerting' and say nothing — the silent_refusal_alert lesson."""
        pl._report_builder_exception("s", RuntimeError("no candle data"))
        pl._report_builder_exception("s", ValueError("something else entirely"))
        assert [p["level"] for p in pages] == [pl.Level.ERROR, pl.Level.ERROR]

    def test_a_different_strategy_is_not_muted(self, pages):
        pl._report_builder_exception("a", RuntimeError("boom"))
        pl._report_builder_exception("b", RuntimeError("boom"))
        assert [p["level"] for p in pages] == [pl.Level.ERROR, pl.Level.ERROR]

    def test_digits_are_normalised_out_of_the_cause(self):
        """Digits vary without the condition changing (a bar count, a price, a
        timestamp), so keying on the raw message would defeat the latch."""
        a = pl._builder_exception_cause(RuntimeError("only 12 bars, need 200"))
        b = pl._builder_exception_cause(RuntimeError("only 34 bars, need 200"))
        assert a == b

    def test_the_exception_CLASS_is_part_of_the_cause(self):
        assert (pl._builder_exception_cause(RuntimeError("x"))
                != pl._builder_exception_cause(ValueError("x")))

    def test_the_cause_is_bounded(self):
        """A latch key built from an unbounded exception message would grow the
        state file without limit."""
        assert len(pl._builder_exception_cause(RuntimeError("x" * 9999))) <= 200


class TestItFailsLoudNotQuiet:
    def test_an_unreachable_latch_pages_rather_than_downgrading(
        self, tmp_path, monkeypatch,
    ):
        sent = []
        monkeypatch.setattr(pl, "report", lambda *a, **k: sent.append(k))
        monkeypatch.setattr("src.runtime.alert_cooldown.cooldown_admits",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
        pl._report_builder_exception("s", RuntimeError("boom"))
        pl._report_builder_exception("s", RuntimeError("boom"))
        assert all(p["level"] is pl.Level.ERROR for p in sent), (
            "a broken latch must announce itself as noise, never as silence"
        )

    def test_it_shares_the_latch_rather_than_copying_it(self):
        """Copying the latch is how the per-process monotonic defect returns in
        the copy — the whole reason alert_cooldown is its own module."""
        # Repo-root-relative, not cwd-relative: a sibling test that chdirs
        # would otherwise make this fail for a reason that has nothing to do
        # with what it asserts (it passes alone and failed in the full run).
        src = (pathlib.Path(pl.__file__).resolve().parent / "pipeline.py").read_text()
        assert "from src.runtime.alert_cooldown import cooldown_admits" in src
        assert "time.monotonic()" not in src.split(
            "def _report_builder_exception")[1][:2000]


def test_the_state_file_is_readable_on_the_diag_surface():
    """#8778 shipped a writer with no allowlist entry. Not again: a latch that
    downgrades an ERROR and cannot be inspected leaves 'the latch is holding'
    and 'the latch is broken' indistinguishable from outside."""
    from src.web.api.routers.diag import _LOG_FILES
    assert "strategy_builder_exception_alert_state" in _LOG_FILES
    assert (_LOG_FILES["strategy_builder_exception_alert_state"].name
            == f"{pl._BUILDER_EXC_ALERT_KIND}_alert_state.json")
