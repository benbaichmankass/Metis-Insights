"""`src/runtime/portfolio_conflicts.py` — the question a single-trade lever cannot ask.

Every exit lever is a univariate cut on one trade's own path. These tests pin
the properties that make a BOOK-level reading trustworthy: correlation is never
assumed, an unreadable row is never counted as clean, and the conflict kinds
stay apart instead of blending into one score.
"""
import pytest

from src.runtime import portfolio_conflicts as pc


def pos(pid, symbol, side, account, pattern, entry=100.0, sl=None):
    return {"id": pid, "symbol": symbol, "side": side, "account": account,
            "pattern": pattern, "entryPrice": entry, "stopLoss": sl}


# --- side normalisation -----------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ("buy", "long"), ("BUY", "long"), (" long ", "long"),
    ("sell", "short"), ("Short", "short"),
    ("flat", None), ("", None), (None, None), (3, None), ("close", None),
])
def test_side_normalisation(raw, want):
    assert pc.norm_side(raw) == want


def test_unreadable_side_is_excluded_not_defaulted():
    """Defaulting an unreadable side would manufacture or mask an opposition."""
    rows = [pos(1, "X", "buy", "a", "s"), pos(2, "X", "wat", "b", "s")]
    assert pc.opposing_same_symbol(rows) == []
    assert pc.audit(rows)["rows_with_unreadable_side"] == [2]


# --- opposing / self-opposing -----------------------------------------------

def test_opposing_same_symbol_detected():
    rows = [pos(1, "ETHUSDT", "buy", "a", "s1"), pos(2, "ETHUSDT", "sell", "b", "s2")]
    (c,) = pc.opposing_same_symbol(rows)
    assert c.kind == pc.OPPOSING_SAME_SYMBOL and c.key == "ETHUSDT"
    assert len(c.positions) == 2


def test_same_direction_is_not_a_conflict():
    rows = [pos(1, "ETHUSDT", "buy", "a", "s1"), pos(2, "ETHUSDT", "buy", "b", "s2")]
    assert pc.opposing_same_symbol(rows) == []


def test_different_symbols_are_not_a_same_symbol_conflict():
    rows = [pos(1, "ETHUSDT", "buy", "a", "s"), pos(2, "XRPUSDT", "sell", "b", "s")]
    assert pc.opposing_same_symbol(rows) == []


def test_self_opposing_needs_the_same_strategy():
    """Two strategies disagreeing is an opinion; one strategy held both ways
    is the same opinion held both ways, and is strictly worse."""
    two = [pos(1, "E", "buy", "a", "s1"), pos(2, "E", "sell", "b", "s2")]
    one = [pos(1, "E", "buy", "a", "s1"), pos(2, "E", "sell", "b", "s1")]
    assert pc.self_opposing_strategy(two) == []
    (c,) = pc.self_opposing_strategy(one)
    assert c.kind == pc.SELF_OPPOSING_STRATEGY and c.key == "s1:E"


def test_self_opposing_is_reported_across_accounts():
    """The netting guard keys on (account, strategy, symbol) and cannot see this."""
    rows = [pos(1, "E", "buy", "bybit_1", "d"), pos(2, "E", "buy", "bybit_2", "d"),
            pos(3, "E", "sell", "bybit_portfolio", "d")]
    (c,) = pc.self_opposing_strategy(rows)
    assert "bybit_portfolio" in c.detail and len(c.positions) == 3


# --- mirror stop divergence -------------------------------------------------

def test_mirror_legs_with_equal_stops_are_clean():
    rows = [pos(1, "X", "sell", "live", "s", 10.0, 11.0),
            pos(2, "X", "sell", "mirror", "s", 10.0, 11.0)]
    assert pc.mirror_stop_divergence(rows) == []


def test_mirror_legs_with_different_stops_are_flagged_with_who_is_protected():
    rows = [pos(1, "X", "sell", "live", "s", 10.0, 9.0),      # trailed into profit
            pos(2, "X", "mirror_side", "mirror", "s", 10.0, 11.0)]
    rows[1]["side"] = "sell"
    (c,) = pc.mirror_stop_divergence(rows)
    assert c.kind == pc.MIRROR_STOP_DIVERGENCE
    assert "locked-profit" in c.detail and "at-original-risk" in c.detail


def test_a_different_entry_is_a_different_signal_not_a_mirror():
    rows = [pos(1, "X", "sell", "a", "s", 10.0, 11.0),
            pos(2, "X", "sell", "b", "s", 10.5, 12.0)]
    assert pc.mirror_stop_divergence(rows) == []


def test_a_lone_leg_is_never_a_divergence():
    assert pc.mirror_stop_divergence([pos(1, "X", "sell", "a", "s", 10.0, 11.0)]) == []


def test_an_unreadable_stop_in_a_mirror_group_is_reported_not_dropped():
    """A group silently reduced to one comparable leg would report agreement
    it never established."""
    rows = [pos(1, "X", "sell", "a", "s", 10.0, 11.0),
            pos(2, "X", "sell", "b", "s", 10.0, None)]
    (c,) = pc.mirror_stop_divergence(rows)
    assert "no readable stop" in c.detail


# --- nominal stops ----------------------------------------------------------

@pytest.mark.parametrize("entry,sl,flagged", [
    (100.0, 50.0, True),    # 50% away — the pairs-sleeve placeholder shape
    (100.0, 200.0, True),   # 100% away
    (100.0, 97.0, False),   # a working 3% stop
    (100.0, 49.9, True),
])
def test_nominal_stop_threshold(entry, sl, flagged):
    got = pc.nominal_stop([pos(1, "X", "buy", "a", "s", entry, sl)])
    assert bool(got) is flagged


def test_ungradeable_stop_is_reported_never_counted_clean():
    """'this stop is fine' and 'we could not read this stop' are opposites."""
    rows = [pos(1, "X", "buy", "a", "s", None, 5.0),
            pos(2, "X", "buy", "b", "s", 100.0, None)]
    assert pc.nominal_stop(rows) == []
    assert sorted(pc.audit(rows)["rows_with_ungradeable_stop"]) == [1, 2]


# --- correlation: measured, never assumed -----------------------------------

def test_correlation_not_supplied_is_not_no_conflict_found():
    """The whole hazard: reporting a clean book over an unstated denominator."""
    rows = [pos(1, "ETHUSDT", "buy", "a", "s"), pos(2, "XRPUSDT", "sell", "b", "s")]
    rep = pc.audit(rows)
    assert rep["correlation_state"] == "not_supplied"
    assert rep["counts"][pc.CORRELATED_OPPOSITION] == 0


def test_an_unmeasured_pair_is_surfaced_not_treated_as_uncorrelated():
    rows = [pos(1, "ETHUSDT", "buy", "a", "s"), pos(2, "XRPUSDT", "sell", "b", "s")]
    conflicts, unmeasured = pc.correlated_opposition(rows, {})
    assert conflicts == []
    assert unmeasured == [("ETHUSDT", "XRPUSDT")]


def test_measured_correlated_opposition_is_flagged():
    rows = [pos(1, "ETHUSDT", "buy", "a", "s"), pos(2, "XRPUSDT", "sell", "b", "s")]
    conflicts, unmeasured = pc.correlated_opposition(
        rows, {("ETHUSDT", "XRPUSDT"): 0.87})
    assert unmeasured == []
    (c,) = conflicts
    assert c.kind == pc.CORRELATED_OPPOSITION and "0.87" in c.detail


def test_measured_low_correlation_is_not_flagged():
    rows = [pos(1, "GLD", "buy", "a", "s"), pos(2, "XRPUSDT", "sell", "b", "s")]
    conflicts, unmeasured = pc.correlated_opposition(rows, {("GLD", "XRPUSDT"): 0.05})
    assert conflicts == [] and unmeasured == []


def test_correlation_key_order_does_not_matter():
    rows = [pos(1, "A", "buy", "x", "s"), pos(2, "B", "sell", "y", "s")]
    for key in (("A", "B"), ("B", "A")):
        conflicts, _ = pc.correlated_opposition(rows, {key: 0.9})
        assert len(conflicts) == 1, key


def test_same_direction_on_correlated_symbols_is_not_an_opposition():
    rows = [pos(1, "A", "buy", "x", "s"), pos(2, "B", "buy", "y", "s")]
    conflicts, unmeasured = pc.correlated_opposition(rows, {("A", "B"): 0.99})
    assert conflicts == [] and unmeasured == []


# --- the audit envelope -----------------------------------------------------

def test_kinds_are_counted_separately_never_blended():
    """Different questions, different remedies; a blended score would hide
    which one fired."""
    rows = [pos(1, "E", "buy", "a", "s1", 100.0, 99.0),
            pos(2, "E", "sell", "b", "s1", 100.0, 101.0),
            pos(3, "Z", "buy", "c", "s2", 100.0, 20.0)]
    counts = pc.audit(rows)["counts"]
    assert counts[pc.OPPOSING_SAME_SYMBOL] == 1
    assert counts[pc.SELF_OPPOSING_STRATEGY] == 1
    assert counts[pc.NOMINAL_STOP] == 1
    assert set(counts) == {
        pc.OPPOSING_SAME_SYMBOL, pc.SELF_OPPOSING_STRATEGY,
        pc.MIRROR_STOP_DIVERGENCE, pc.CORRELATED_OPPOSITION, pc.NOMINAL_STOP}


def test_conflicts_carry_the_rows_that_produced_them():
    """A conflict that reports only its conclusion cannot be contradicted."""
    rows = [pos(7, "E", "buy", "a", "s1"), pos(8, "E", "sell", "b", "s1")]
    (c,) = pc.self_opposing_strategy(rows)
    assert sorted(r["id"] for r in c.positions) == [7, 8]


def test_a_clean_book_reports_clean():
    rows = [pos(1, "A", "buy", "x", "s1", 100.0, 98.0),
            pos(2, "B", "buy", "y", "s2", 50.0, 49.0)]
    rep = pc.audit(rows, correlation={("A", "B"): 0.9})
    assert sum(rep["counts"].values()) == 0
    assert rep["correlation_state"] == "supplied"
    assert rep["correlated_pairs_unmeasured"] == []


def test_empty_book_is_not_an_error():
    rep = pc.audit([])
    assert rep["positions"] == 0 and sum(rep["counts"].values()) == 0


def test_no_order_path_caller():
    """Observe-only by contract: reading this to CLOSE a position is Tier-3."""
    import ast
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    callers = []
    for p in list((repo / "src/units").rglob("*.py")) + \
             list((repo / "src/core").rglob("*.py")) + [repo / "src/main.py"]:
        if not p.exists() or "portfolio_conflicts" not in p.read_text():
            continue
        for n in ast.walk(ast.parse(p.read_text())):
            names = ([a.name for a in n.names] if isinstance(n, ast.Import)
                     else [n.module or ""] if isinstance(n, ast.ImportFrom) else [])
            if any("portfolio_conflicts" in x for x in names):
                callers.append(str(p.relative_to(repo)))
    assert not callers, f"portfolio_conflicts gained an order-path caller: {callers}"
