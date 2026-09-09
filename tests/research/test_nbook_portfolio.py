"""The N-book engine must not be a SECOND portfolio engine that drifts.

WHY A PARITY TEST AND NOT A CODE REVIEW
---------------------------------------
`scripts/research/nbook_portfolio.py` adds the one thing
`scripts/backtest_system.py` cannot express — many books over one tick stream —
and to do that it necessarily carries its own tick loop. Two engines are free to
drift, and this repo has already paid for exactly that with two expressions of
"the same" election ordering that had silently come to disagree about the same
pair (`src/runtime/intents.py`, 2026-08-31).

Co-location would not have caught it; an assertion does. So: at **N=1 with the
full roster**, the N-book engine must reproduce `run_system_backtest`'s trades
exactly — same count, same owners, same entries/exits, same PnL.

⚠️ THE DENOMINATOR IS ASSERTED. A parity test over a run that produced ZERO
trades passes trivially and proves nothing — "green while measuring nothing",
which `docs/CLAUDE-RULES-CANONICAL.md` § "Green is not evidence" names as the
same sin as red while measuring nothing. So the test FAILS if the fixture stops
producing trades, rather than quietly going vacuous.

⚠️ THE TWO ENGINES ARE GIVEN ONE TIER-3 DEFINITION. `_election_sort_key`'s
recent-PnL term reads `trade_journal.db` live, and the N-book engine
deliberately repoints it at the replay's own closes. Left alone, the parity run
would compare an engine reading a (probably absent) journal against one reading
its own trades, and any mismatch would be unattributable. Both are therefore run
under ONE neutral track record, so what is compared is the ENGINE.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "scripts" / "research")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("pandas")

import backtest_system as bs  # noqa: E402
import nbook_portfolio as nb  # noqa: E402
import ranking_keys as rk  # noqa: E402

DATA = ROOT / "data" / "backtest_candles.csv"

# The one committed fixture that actually TRADES on this short file (5,000 1m
# bars ≈ 3.5 days). Chosen by measurement, not by taste: the 1h/4h members need
# more warmup than the file contains and produce zero trades, which would make
# every assertion below vacuous.
FIXTURE = dict(roster=["ict_scalp_5m", "hf_vwap_revert"], clock_tf="5m",
               signal_ttl_bars=8)


class _NeutralTrackRecord(rk.ReplayTrackRecord):
    """Tier-3 term made inert for BOTH engines.

    0.0 for every strategy is precisely what the LIVE `_track_record_rank`
    returns when the journal is unreadable, and its docstring gives the reason:
    "if the module cannot be read at all, every candidate must receive the same
    value so this term reorders nothing". Reusing that state is what makes the
    parity comparison about the engine rather than about the journal.
    """

    def rank(self, strategy: str) -> float:  # noqa: D401
        return 0.0


@pytest.mark.skipif(not DATA.is_file(), reason="committed candle fixture absent")
def test_nbook_at_n1_reproduces_the_one_book_harness_trade_for_trade():
    base5m = bs._load_candles(str(DATA))

    # Capture the one-book harness's closed trades. `run_system_backtest`
    # returns a summary, not the trades, so the comparison rides on the shared
    # `_ClosedTrade` list that both engines append to.
    captured = []
    real_summarize = bs._summarize

    def _spy(closed, *a, **k):
        captured.extend(closed)
        return real_summarize(closed, *a, **k)

    tr_one = _NeutralTrackRecord()
    bs._summarize = _spy
    try:
        with rk.install_ranking_key(rk.SHIPPED_ARM, track_record=tr_one):
            bs.run_system_backtest(
                base5m, roster=list(FIXTURE["roster"]), start=None, end=None,
                initial_balance=10_000.0, risk_pct=0.3, daily_loss_pct=3.0,
                signal_ttl_bars=FIXTURE["signal_ttl_bars"], overrides={},
                refresh=False, clock_tf=FIXTURE["clock_tf"],
                flip_policy="reverse", symbol="BTCUSDT")
    finally:
        bs._summarize = real_summarize

    out = nb.run_nbook(
        base5m,
        books=[nb.Book(name="main", roster=tuple(FIXTURE["roster"]),
                       initial_balance=10_000.0, risk_pct=0.3,
                       daily_loss_pct=3.0)],
        start=None, end=None, signal_ttl_bars=FIXTURE["signal_ttl_bars"],
        clock_tf=FIXTURE["clock_tf"], symbol="BTCUSDT",
        arbitration="per_account", ranking_key=rk.SHIPPED_ARM,
        flip_policy="reverse", folds=3, track_record=_NeutralTrackRecord(),
        attach_trades=True)

    # THE DENOMINATOR. Without this the parity below is 0 == 0.
    assert len(captured) > 0, (
        "the fixture stopped producing trades — this parity assertion has gone "
        "vacuous and is no longer evidence of anything; fix the fixture, do not "
        "delete the check")
    assert out["books"][0]["total_trades"] == len(captured)

    # TRADE-FOR-TRADE, not just a matching total: two engines can reach the
    # same net PnL through different trades, and that would be a drift this
    # test was written to catch rather than to average away.
    def _cmp(d):
        return (d["owner"], d["side"], d["entry_ts"], d["exit_ts"],
                round(d["entry"], 6), round(d["exit"], 6),
                round(d["qty"], 10), round(d["pnl"], 6), d["reason"])

    assert [_cmp(d) for d in out["books"][0]["trades"]] == \
        [_cmp(nb.trade_digest(t)) for t in captured], (
        "the N-book engine at N=1 no longer reproduces the one-book harness "
        "trade-for-trade — the two engines have drifted, which is exactly what "
        "this assertion exists to catch")
    assert out["books"][0]["net_pnl_usd"] == pytest.approx(
        round(sum(float(t.pnl) for t in captured), 2), abs=0.02)


@pytest.mark.skipif(not DATA.is_file(), reason="committed candle fixture absent")
def test_a_book_that_does_not_carry_the_global_winner_stands_aside_and_it_is_COUNTED():
    """The starvation shape, made visible rather than rendering as 'no signal'.

    `BL-20260827-PROP-ONLY-TWIN-WINS-THE-GLOBAL-SYMBOL-SLOT-AND-STARVES-ITS-PAPER-SIBLING`
    measured 0-of-60 live and was read as small-sample bad luck. Under `global`
    arbitration a book that does not carry the winning strategy trades nothing,
    and that must be a counted state — not silence.
    """
    base5m = bs._load_candles(str(DATA))
    books = [nb.Book(name="a", roster=("ict_scalp_5m",), initial_balance=10_000.0,
                     risk_pct=0.3, daily_loss_pct=3.0),
             nb.Book(name="b", roster=("hf_vwap_revert",), initial_balance=10_000.0,
                     risk_pct=0.3, daily_loss_pct=3.0)]
    g = nb.run_nbook(base5m, books=books, start=None, end=None,
                     signal_ttl_bars=FIXTURE["signal_ttl_bars"],
                     clock_tf=FIXTURE["clock_tf"], symbol="BTCUSDT",
                     arbitration="global", ranking_key=rk.SHIPPED_ARM, folds=3,
                     track_record=_NeutralTrackRecord())
    p = nb.run_nbook(base5m, books=books, start=None, end=None,
                     signal_ttl_bars=FIXTURE["signal_ttl_bars"],
                     clock_tf=FIXTURE["clock_tf"], symbol="BTCUSDT",
                     arbitration="per_account", ranking_key=rk.SHIPPED_ARM,
                     folds=3, track_record=_NeutralTrackRecord())
    # A denominator first: if neither mode elected anything the comparison is
    # vacuous and must fail rather than pass quietly.
    assert p["population"]["elections_held"] > 0
    assert g["population"]["stood_aside_global"] > 0, (
        "with two disjoint single-strategy books, a global election MUST leave "
        "one book standing aside on every tick it decides")
    assert p["population"]["stood_aside_global"] == 0, (
        "per-account arbitration cannot strand a book — each elects over its own "
        "roster, so standing aside is not reachable")


def test_book_spec_refuses_what_it_cannot_honour():
    kw = dict(default_roster=["ict_scalp_5m"], default_balance=1.0,
              default_risk_pct=0.3, default_daily_loss_pct=3.0)
    b = nb.parse_book_spec("name=x:roster=ict_scalp_5m|hf_vwap_revert:balance=500", **kw)
    assert b.name == "x" and b.roster == ("ict_scalp_5m", "hf_vwap_revert")
    assert b.initial_balance == 500.0
    # An unknown key is REFUSED, not ignored: silently dropping it would report
    # a full result under a narrowed label.
    with pytest.raises(ValueError):
        nb.parse_book_spec("name=x:rooster=a", **kw)
    # A strategy the harness has no module for cannot be replayed at all.
    with pytest.raises(ValueError):
        nb.parse_book_spec("name=x:roster=not_a_strategy", **kw)
    with pytest.raises(ValueError):
        nb.parse_book_spec("roster=ict_scalp_5m", **kw)


def test_unknown_arm_or_arbitration_is_refused_before_any_bar_is_replayed():
    import pandas as pd
    empty = pd.DataFrame({"timestamp": [], "open": [], "high": [], "low": [], "close": []})
    book = [nb.Book(name="m", roster=("ict_scalp_5m",), initial_balance=1.0,
                    risk_pct=0.3, daily_loss_pct=3.0)]
    with pytest.raises(ValueError):
        nb.run_nbook(empty, books=book, start=None, end=None, arbitration="whatever")
    with pytest.raises(ValueError):
        nb.run_nbook(empty, books=book, start=None, end=None, ranking_key="whatever")


def test_r_is_none_when_risk_is_unreadable_never_a_fabricated_zero():
    class _T:
        entry, sl, qty, pnl = 100.0, 100.0, 1.0, 5.0     # zero stop distance
    assert nb._trade_r(_T()) is None
    class _U:
        entry, sl, qty, pnl = 100.0, None, 1.0, 5.0      # unreadable stop
    assert nb._trade_r(_U()) is None
    class _V:
        entry, sl, qty, pnl = 100.0, 99.0, 2.0, 4.0      # risk = 2.0
    assert nb._trade_r(_V()) == pytest.approx(2.0)


def test_contested_stats_reports_ungradeable_R_rather_than_averaging_over_it():
    class _T:
        def __init__(self, pnl, sl):
            self.entry, self.qty, self.pnl, self.sl = 100.0, 1.0, pnl, sl
            self.exit_ts = "t"
    stats = nb._contested_stats([_T(10.0, 99.0), _T(-5.0, 100.0)])
    assert stats["trades"] == 2
    assert stats["r_gradeable"] == 1 and stats["r_ungradeable"] == 1
    # The ungradeable trade contributes to net PnL but NOT to expectancy — an
    # unmeasurable R must not become a 0.0 that drags the mean toward a value
    # nobody observed.
    assert stats["net_pnl_usd"] == 5.0
    assert stats["expectancy_r"] == pytest.approx(10.0)
