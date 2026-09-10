"""The orphan-adopt SIZE-PLAUSIBILITY gate — can the claimed strategy's own
history support the position we are about to hand it?

``BL-20260908-THE-ORPHAN-ADOPT-REATTACH-WRITES-A-STRATEGY-NAME-IT-CANNOT-
SUPPORT-AND-THAT-STRATEGY-THEN-ACTS-ON-THE-ROW``.

⚠️ **WHAT THIS SUITE CANNOT DO, STATED SO A GREEN RUN IS NOT MISREAD.** The
backlog row's ``resolution_criteria`` requires *"an observed adopt whose
re-attach DECLINED on a quantity mismatch and left the row bare, read from the
live journal — **not by a unit test, which cannot reproduce the venue state
that produces a candidate package**"*. Nothing here reaches a venue. These
tests establish that the DECISION is right on the measured population and that
the refusal cannot flatten a position; they do not establish that the mechanism
has ever fired on the fleet.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime import orphan_attribution as oa
from src.runtime.order_monitor import (
    _adopt_orphan_position,
    _recover_orphan_attribution,
    _recover_orphan_order_package,
)
from src.units.db.database import Database
from tests.fixtures.real_schema_db import (
    insert_order_package as _insert_package,
    insert_trade as _insert_trade,
    make_canonical_db,
)


# ---------------------------------------------------------------------------
# The pure decision, against the ONLY population that exists.
# ---------------------------------------------------------------------------

# The five judgeable adopts MI-204 scored, verified against the live journal on
# 2026-09-10 (`/api/diag/journal?table=trades&limit=1000`, ids 4651..5650).
# id, claimed strategy, adopted size, that strategy's own observed range, verdict
# ⚠️ CORRECTED 2026-09-10 BY ITS OWN AUTHOR, AND BOTH CORRECTIONS RUN AGAINST
# THE GATE RATHER THAN FOR IT. This table shipped in #11708 carrying an id
# transposition and an unstated denominator; neither changes a verdict, and
# both would have misled the next reader who checked it against the journal.
#
# (1) THE ID WAS TRANSPOSED, AND THE ROW IT NAMED IS NOT AN ADOPT AT ALL.
#     The entry read `(5569, "ict_scalp_eth_15m", 26.05, ...)`. Measured on the
#     live journal: **5568** is the size-26.05 row, and its `setup_type` is
#     `ict_scalp_eth_15m` with `exit_reason: netting_attributed` -- i.e. it is
#     the wrong-book netting close CLAUDE.md documents, NOT an adopted orphan.
#     The real **5569** is `setup_type: adopted_orphan`, size **0.47**. So the
#     hand-scored population did not merely mislabel a row, it INCLUDED A
#     NON-ADOPT, which means the "5 judgeable adopts" denominator was itself
#     off by one. Both rows are kept below, labelled for what they are.
#
# (2) "2 OF 5" HAD NO STATED POPULATION, WHICH IS THE ONE RULE THIS REPO
#     PROMOTED TO TOP LEVEL. Re-measured 2026-09-10 by grading EVERY named
#     adopt with this module -- population: the 1000 rows `/api/diag/journal`
#     returns for `trades` (ids 4653..5652), of which **15** carry
#     `setup_type='adopted_orphan'` and all 15 name a strategy (zero are bare).
#     Verdicts: **13 supported, 2 size_implausible (5448, 5453), 0 no_history,
#     0 unreadable**. So the refusal rate over a STATED population is 2 of 15,
#     not 2 of 5 -- the gate is ~3x RARER than the original figure implies,
#     which is the direction that matters: it makes "no refusals seen" even
#     weaker as evidence that the gate works.
#
#     ⚠️ THAT 2-OF-15 IS AN UPPER BOUND ON REFUSALS, and the bound has a
#     direction worth keeping: history entered only through `min` and `max`, so
#     MORE history can only WIDEN the band. A `supported` verdict is therefore
#     stable under more history, and a `size_implausible` one can only soften.
#     The 1000-row page truncates history, and the runtime gate reads its own
#     `limit=200` per (strategy, symbol) -- a THIRD basis again. None of the
#     three is "the" population; each must be named when its number is quoted.
#
#     ⚠️ AND IT IS NOT EVIDENCE THE ORIGINAL "2 OF 5" WAS WRONG. Its population
#     was never recorded, so the two figures cannot be compared -- that absence
#     IS the defect being fixed, not a disagreement being resolved.
#
# Fields: (trade_id, strategy, size, hand-scored (lo, hi), expected verdict).
# The bands are the HAND-SCORED ones from the original measurement and are
# deliberately NOT refreshed from the live journal: these cases pin the
# module's ARITHMETIC on frozen inputs, and re-deriving them from a moving
# table would make the test assert whatever the journal currently says.
MEASURED_ADOPTS = [
    (5453, "pairs_sol_eth_b", 17.67, (0.37, 2.10), "size_implausible"),
    (5448, "pairs_sol_eth_a", 1236.30, (2.0, 57.5), "size_implausible"),
    (5288, "pairs_sol_eth_a", 11.90, (2.0, 57.5), "supported"),
    (5555, "ict_scalp_sol_15m", 545.0, (9.1, 2511.7), "supported"),
    # 5568 is NOT an adopt (setup_type ict_scalp_eth_15m, exit_reason
    # netting_attributed). Retained because the arithmetic it pins is real and
    # was genuinely computed; relabelled so nobody re-derives "an adopt of size
    # 26.05" from it. See correction (1) above.
    (5568, "ict_scalp_eth_15m", 26.05, (5.31, 114.47), "supported"),
    # The REAL 5569 -- an adopted_orphan of size 0.47, graded supported against
    # ict_scalp_eth_15m's own ETHUSDT history on 2026-09-10.
    (5569, "ict_scalp_eth_15m", 0.47, (0.165, 228.9), "supported"),
]

#: The stated population behind correction (2). Quoted rather than recomputed,
#: so a reader knows WHICH denominator any "N of M" in this file refers to.
MEASURED_POPULATION = {
    "source": "/api/diag/journal?table=trades&limit=1000",
    "read_at": "2026-09-10T17:2xZ",
    "trades_rows": 1000,
    "trade_id_range": (4653, 5652),
    "adopted_orphan_rows": 15,
    "named_a_strategy": 15,
    "bare_orphan_adopt": 0,
    "verdicts": {"supported": 13, "size_implausible": 2,
                 "no_history": 0, "unreadable": 0},
    "refused_trade_ids": (5448, 5453),
}


@pytest.mark.parametrize(
    "trade_id,strategy,size,rng,expected", MEASURED_ADOPTS,
    ids=[f"{t}-{s}" for t, s, _, _, _ in MEASURED_ADOPTS],
)
def test_reproduces_every_measured_verdict(trade_id, strategy, size, rng, expected):
    """The gate agrees with every hand-scored case (see MEASURED_ADOPTS'
    correction header for what that population is, and is not)."""
    lo, hi = rng
    history = [lo, (lo + hi) / 2.0, hi]
    got = oa.assess_size_support(size=size, history_sizes=history)
    assert got.state == expected, (
        f"trade {trade_id} ({strategy}, size {size}, range {rng}): "
        f"expected {expected}, got {got.state} — {got.detail}"
    )
    assert got.refuses is (expected == "size_implausible")


@pytest.mark.parametrize("band", [1.5, 2.0, 3.0, 5.0, 8.0])
def test_the_verdicts_are_robust_to_the_band_choice(band):
    """DEFAULT_BAND is CHOSEN, not derived — so pin that the answer does not
    depend on the exact choice. The two mis-attributions sit 8.4x and 21.5x
    outside their range, so every band in this sweep separates them cleanly."""
    for trade_id, strategy, size, (lo, hi), expected in MEASURED_ADOPTS:
        got = oa.assess_size_support(
            size=size, history_sizes=[lo, (lo + hi) / 2.0, hi], band=band,
        )
        assert got.state == expected, (
            f"band={band} flips trade {trade_id} ({strategy}) to {got.state}"
        )


def test_a_thin_history_does_not_refuse_it_reports_no_history():
    """⚠️ The permissive branch, and it is deliberate. With 1-2 samples `min`
    and `max` collapse onto a point, so refusing would bare-orphan a young
    strategy's first adopt on no evidence."""
    got = oa.assess_size_support(size=999.0, history_sizes=[1.0, 2.0])
    assert got.state == "no_history"
    assert got.refuses is False, "an ungradeable attribution must NOT refuse"
    assert got.n_history == 2


def test_an_unreadable_history_does_not_refuse():
    """`None` is *we could not look* — never evidence of implausibility."""
    got = oa.assess_size_support(size=999.0, history_sizes=None)
    assert got.state == "unreadable"
    assert got.refuses is False


def test_an_unparseable_size_is_unreadable_not_a_refusal():
    for bad in (None, "", "abc", 0, -0.0):
        got = oa.assess_size_support(size=bad, history_sizes=[1.0, 2.0, 3.0])
        assert got.state == "unreadable", bad
        assert got.refuses is False, bad


def test_only_size_implausible_ever_refuses():
    """Pins the accessor callers must branch on. A caller testing
    `state == "supported"` would bare-orphan every ungradeable adopt."""
    seen = set()
    for size, hist in ((17.67, [0.3, 1.0, 2.1]), (1.0, [0.3, 1.0, 2.1]),
                       (1.0, [0.3]), (1.0, None), (None, [1.0, 2.0, 3.0])):
        v = oa.assess_size_support(size=size, history_sizes=hist)
        seen.add(v.state)
        assert v.refuses is (v.state == "size_implausible")
    assert seen == set(oa.STATES), f"not every state exercised: {seen}"


def test_a_degenerate_band_falls_back_rather_than_arming_a_total_refusal():
    """A band <= 1.0 would refuse anything but the exact observed extremes.
    A typo must not silently arm a near-total refusal on an order path."""
    for bad in (0.0, 1.0, -3.0, "nonsense", None):
        got = oa.assess_size_support(
            size=1.5, history_sizes=[1.0, 1.2, 2.0], band=bad,
        )
        assert got.state == "supported", bad


def test_history_excludes_adopted_rows_so_a_wrong_attribution_cannot_widen_the_band():
    """⚠️ Load-bearing, not tidiness. A previous mis-attribution writes its own
    size into the claimed strategy's history; counting it widens the band by
    exactly the outlier the gate exists to catch, so the SECOND identical
    mis-attribution would pass."""
    rows = [
        {"position_size": 0.4, "setup_type": "pairs_sol_eth_b", "is_backtest": 0},
        {"position_size": 2.1, "setup_type": "pairs_sol_eth_b", "is_backtest": 0},
        {"position_size": 1.0, "setup_type": "pairs_sol_eth_b", "is_backtest": 0},
        # the poisoned row — trade 5453 itself
        {"position_size": 17.67, "setup_type": "adopted_orphan", "is_backtest": 0},
    ]
    sizes = oa.sizes_from_trades(rows)
    assert 17.67 not in sizes
    assert oa.assess_size_support(size=17.67, history_sizes=sizes).refuses is True
    # ...and the control: had it been counted, the gate would have passed it.
    poisoned = [r["position_size"] for r in rows]
    assert oa.assess_size_support(size=17.67, history_sizes=poisoned).refuses is False


def test_sizes_from_trades_distinguishes_unreadable_from_empty():
    assert oa.sizes_from_trades(None) is None
    assert oa.sizes_from_trades([]) == []


# ---------------------------------------------------------------------------
# The wiring — and the safety property my first attempt got wrong.
# ---------------------------------------------------------------------------

def _db(tmp_path: Path) -> Database:
    path = tmp_path / "trade_journal.db"
    make_canonical_db(path)
    return Database(str(path))


def _seed_history(db: Database, strategy: str, symbol: str, sizes) -> None:
    for i, s in enumerate(sizes):
        _insert_trade(
            db.db_path, timestamp=f"2026-09-0{1 + i % 8}T00:00:00Z",
            symbol=symbol, direction="long", entry_price=2500.0,
            position_size=s, status="closed", is_backtest=0, is_demo=1,
            strategy_name=strategy, setup_type=strategy, account_id="bybit_1",
        )


def _seed_candidate(db: Database, strategy: str, symbol: str) -> str:
    opid = f"pkg-{strategy}"
    _insert_package(
        db.db_path, order_package_id=opid, strategy_name=strategy,
        symbol=symbol, direction="long", entry=2500.0, sl=2400.0, tp=2700.0,
        status="closed",
    )
    return opid


def test_the_gate_is_inert_when_position_size_is_omitted(tmp_path):
    """The back-compat wrapper takes no size, so it cannot refuse — which is
    what keeps every un-migrated caller byte-for-byte unchanged."""
    db = _db(tmp_path)
    _seed_candidate(db, "pairs_sol_eth_b", "ETHUSDT")
    _seed_history(db, "pairs_sol_eth_b", "ETHUSDT", [0.4, 1.0, 2.1])
    got = _recover_orphan_order_package(
        db=db, symbol="ETHUSDT", direction="long", entry_price=2500.0,
    )
    assert got is not None
    assert got.get("strategy_name") == "pairs_sol_eth_b"


def test_an_implausible_size_refuses_and_reports_why(tmp_path):
    """Trade 5453's shape end to end: 17.67 against a sleeve topping out at 2.10."""
    db = _db(tmp_path)
    _seed_candidate(db, "pairs_sol_eth_b", "ETHUSDT")
    _seed_history(db, "pairs_sol_eth_b", "ETHUSDT", [0.37, 1.0, 2.10])
    got = _recover_orphan_attribution(
        db=db, symbol="ETHUSDT", direction="long", entry_price=2500.0,
        position_size=17.67,
    )
    assert got.refused is True
    assert got.package is None, "a refusal must NEVER return a package"
    assert got.refused_strategy == "pairs_sol_eth_b"


def test_a_refusal_is_distinguishable_from_no_candidate(tmp_path):
    """⚠️ THE PROPERTY THAT PREVENTS A LIVE FLATTEN, and the one my first
    attempt at this change got wrong.

    `_reattach_adopted_orphans`' own docstring says an **un-recoverable** orphan
    is FLATTENED by the caller's per-account pass. If a refusal were reported as
    plain `None` — indistinguishable from "no candidate exists" — the size gate
    would convert a journal mis-attribution into a live reduce-only close, which
    is strictly worse than the defect it fixes."""
    db = _db(tmp_path)
    _seed_candidate(db, "pairs_sol_eth_b", "ETHUSDT")
    _seed_history(db, "pairs_sol_eth_b", "ETHUSDT", [0.37, 1.0, 2.10])

    refused = _recover_orphan_attribution(
        db=db, symbol="ETHUSDT", direction="long", entry_price=2500.0,
        position_size=17.67,
    )
    no_candidate = _recover_orphan_attribution(
        db=db, symbol="SOLUSDT", direction="long", entry_price=140.0,
        position_size=17.67,
    )

    assert refused.package is None and no_candidate.package is None
    assert refused.refused is True
    assert no_candidate.refused is False, (
        "no candidate is NOT a refusal — that row is legitimately flattenable"
    )


def test_a_supported_size_still_attributes(tmp_path):
    db = _db(tmp_path)
    _seed_candidate(db, "trend_donchian_eth", "ETHUSDT")
    _seed_history(db, "trend_donchian_eth", "ETHUSDT", [17.08, 25.0, 37.76])
    got = _recover_orphan_attribution(
        db=db, symbol="ETHUSDT", direction="long", entry_price=2500.0,
        position_size=17.67,
    )
    assert got.refused is False
    assert got.package is not None
    assert got.package.get("strategy_name") == "trend_donchian_eth"


def test_adopt_writes_a_bare_orphan_rather_than_an_unsupportable_name(tmp_path):
    """The end-to-end refusal: the adopted row keeps the honest bare state
    (`strategy_name='orphan_adopt'`, no synthesised stops) instead of naming a
    strategy whose `monitor()` would then act on it."""
    db = _db(tmp_path)
    _seed_candidate(db, "pairs_sol_eth_b", "ETHUSDT")
    _seed_history(db, "pairs_sol_eth_b", "ETHUSDT", [0.37, 1.0, 2.10])

    tid = _adopt_orphan_position(
        db=db, account_id="bybit_1", symbol="ETHUSDT", direction="long",
        size=17.67, entry_price=2500.0,
    )
    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT strategy_name, setup_type, stop_loss, take_profit_1 "
            "FROM trades WHERE id = ?", [int(tid)],
        ).fetchone()
    finally:
        conn.close()

    assert row["strategy_name"] == "orphan_adopt"
    assert row["setup_type"] == "adopted_orphan"
    assert row["stop_loss"] in (None, 0, 0.0)
    assert row["take_profit_1"] in (None, 0, 0.0)


def test_adopt_still_attributes_a_supported_position(tmp_path):
    """The control — without it, a gate that refused EVERYTHING would pass the
    test above and look correct."""
    db = _db(tmp_path)
    _seed_candidate(db, "trend_donchian_eth", "ETHUSDT")
    _seed_history(db, "trend_donchian_eth", "ETHUSDT", [17.08, 25.0, 37.76])

    tid = _adopt_orphan_position(
        db=db, account_id="bybit_1", symbol="ETHUSDT", direction="long",
        size=17.67, entry_price=2500.0,
    )
    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT strategy_name FROM trades WHERE id = ?", [int(tid)],
        ).fetchone()
    finally:
        conn.close()
    assert row["strategy_name"] == "trend_donchian_eth"


def test_a_strategy_with_no_history_is_still_attributed(tmp_path):
    """The permissive branch end to end: a strategy that has never traded this
    symbol cannot be scored, and an unscoreable attribution proceeds."""
    db = _db(tmp_path)
    _seed_candidate(db, "brand_new_strategy", "ETHUSDT")
    got = _recover_orphan_attribution(
        db=db, symbol="ETHUSDT", direction="long", entry_price=2500.0,
        position_size=17.67,
    )
    assert got.refused is False
    assert got.package is not None
