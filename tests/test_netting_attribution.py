"""Netting partial-close attribution (BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED).

Under Bybit one-way netting a symbol is ONE exchange position holding N journal
rows. A position-level exit shrinks that single position, but close detection is
per-ORDER (`_reconcile_open_trades` reconciles each row against *its own* Bybit
order), so a sibling row whose order filled long ago has no order event to
observe and keeps its full `position_size` forever. Measured 2026-08-06:
`bybit_1` SOLUSDT journal 2075.2 vs exchange 4.6 — **451x**.

Design: `docs/netting-partial-close-attribution-DESIGN.md`. These tests pin the
three things most likely to go wrong quietly:

1. **The provenance ladder.** `anchored` → ESTIMATED; `no_anchor` → declared
   UNMEASURED, never a substituted price; `deferred` → nothing written at all.
   Collapsing any two of those is the defect class that produced the phantom
   -$6,358 leak.
2. **The selection order** — leg-id evidence first, FIFO for the residual.
3. **The fail-safes** — unreadable exchange read, pairs-owned rows, and the
   2-observation confirm.
"""
from __future__ import annotations

import sqlite3

from src.runtime import order_monitor as om


# --------------------------------------------------------------- row selection
class _Row(dict):
    """dict that also indexes like a sqlite3.Row (the reconciler uses [])."""


def _row(tid, qty, created, sl_order_id=None):
    return _Row(
        id=tid, position_size=qty, created_at=created, sl_order_id=sl_order_id,
        symbol="XRPUSDT", direction="long", strategy_name="s", entry_price=1.0,
        notes=None,
    )


def test_selection_prefers_leg_gone_then_fifo():
    """A row whose tracked leg is no longer resting is named before FIFO.

    `leg_gone` is EVIDENCE (the leg fired or was cancelled — absence cannot
    tell those apart), which is why it still yields an ESTIMATED close. It is
    preferred over FIFO because it names a specific trade instead of assuming
    an ordering.
    """
    rows = [
        _row(1, 10.0, "2026-01-01", sl_order_id="LIVE"),   # leg still resting
        _row(2, 10.0, "2026-01-03", sl_order_id="GONE"),   # leg gone → first
        _row(3, 10.0, "2026-01-02"),                        # no leg → FIFO
    ]
    picked = om._netting_rows_to_attribute(rows, excess=15.0, live_leg_ids={"LIVE"})
    # Row 2 first (leg gone). Then row 3 — even though row 1 is OLDER, row 1's
    # leg is still RESTING, which is evidence the exchange still holds its qty.
    # A live leg outranks age.
    assert [(r["id"], q, b) for r, q, b in picked] == [
        (2, 10.0, "leg_gone"),
        (3, 5.0, "fifo"),
    ]


def test_a_still_resting_leg_is_picked_LAST(dummy=None):
    """Age alone would pick the tracked-and-protected row first. It must not."""
    rows = [
        _row(1, 10.0, "2026-01-01", sl_order_id="LIVE"),  # oldest, still protected
        _row(2, 10.0, "2026-01-09"),                       # newest, untracked
    ]
    picked = om._netting_rows_to_attribute(rows, excess=10.0, live_leg_ids={"LIVE"})
    assert [(r["id"], b) for r, _q, b in picked] == [(2, "fifo")]


def test_selection_stops_at_the_excess():
    """Never attribute more than the exchange actually gave back."""
    rows = [_row(1, 100.0, "2026-01-01")]
    picked = om._netting_rows_to_attribute(rows, excess=3.0, live_leg_ids=set())
    assert len(picked) == 1 and picked[0][1] == 3.0


def test_selection_empty_when_no_excess():
    assert om._netting_rows_to_attribute(
        [_row(1, 10.0, "2026-01-01")], excess=0.0, live_leg_ids=set()) == []


# ------------------------------------------------------------------ mode gate
def test_mode_defaults_to_annotate(monkeypatch):
    monkeypatch.delenv("NETTING_ATTRIBUTION_MODE", raising=False)
    assert om._netting_attribution_mode() == "annotate"
    monkeypatch.setenv("NETTING_ATTRIBUTION_MODE", "apply")
    assert om._netting_attribution_mode() == "apply"
    # Anything unrecognised must NOT silently become apply.
    monkeypatch.setenv("NETTING_ATTRIBUTION_MODE", "yes")
    assert om._netting_attribution_mode() == "annotate"


# --------------------------------------------------------- the provenance ladder
class _FakeDB:
    def __init__(self):
        self.updates = []

    def update_trade(self, tid, updates):
        self.updates.append((tid, updates))


def _apply(anchor_status, anchor_price, qty=10.0, take=10.0):
    db = _FakeDB()
    row = _Row(
        id=7, position_size=qty, entry_price=1.0, direction="long",
        symbol="XRPUSDT", notes=None,
    )
    outcome = om._netting_apply_close(
        db, row=row, take=take, basis="fifo",
        anchor_price=anchor_price, anchor_status=anchor_status,
        anchored_at="2026-08-06T12:00:00+00:00",
        anchor_source="candle_at_close", unmeasured_marker="__unmeasured__",
    )
    return outcome, db.updates


def test_anchored_close_is_ESTIMATED_not_measured():
    """An anchored bar gives a real estimate — priced, and stamped as estimated.

    This is the operator directive 2026-08-06: prefer a stated estimate over
    declaring nothing. The stamp is what keeps it honest — a bar close says
    where the MARKET was, not where THIS order filled.
    """
    outcome, updates = _apply("anchored", 1.10)
    assert outcome == "closed"
    (_tid, up), = updates
    assert up["status"] == "closed"
    assert up["exit_price"] == 1.10
    assert up["pnl"] is not None
    import json
    notes = json.loads(up["notes"])
    assert notes["exit_price_source"] == "candle_at_close"
    assert notes["pnl_source"] == "local_compute"
    assert "unmeasured_reason" not in notes
    # The attribution basis is recorded, so nobody mistakes an INFERRED close
    # for a broker-confirmed one.
    assert notes["netting_attribution_basis"] == "fifo"
    assert notes["netting_anchor_basis"] == "divergence_first_observed"


def test_no_anchor_DECLARES_unmeasured_and_prices_nothing():
    """The venue was asked and has no bar. Declare — never substitute a price.

    An anchorless "estimate" is FABRICATED by the repo's own vocabulary, which
    is the exact class behind the phantom -$6,358 leak. So this path must NOT
    write an exit_price or a pnl.
    """
    outcome, updates = _apply("no_anchor", None)
    assert outcome == "unmeasured"
    (_tid, up), = updates
    assert up["status"] == "closed"
    assert "exit_price" not in up and "pnl" not in up
    import json
    notes = json.loads(up["notes"])
    assert notes["pnl_source"] == "__unmeasured__"
    assert notes["unmeasured_reason"] == "netting_attribution_no_anchor"


def test_partial_take_reduces_and_leaves_the_row_OPEN():
    """The exchange gave back only part of this row — reduce, don't invent a close.

    Closing the whole row here would fabricate an exit for qty the exchange
    still backs.
    """
    outcome, updates = _apply("anchored", 1.10, qty=10.0, take=4.0)
    assert outcome == "closed"
    (_tid, up), = updates
    assert up["position_size"] == 6.0
    assert "status" not in up        # still open
    assert "pnl" not in up           # nothing realised


# ------------------------------------------------------------------ fail-safes
class _SweepDB:
    def __init__(self, path):
        self.path = str(path)
        # Build the REAL schema, never a hand-written stand-in. A fixture that
        # invents its own column names cannot catch a column-name bug in the
        # code under test — this file originally declared `strategy` where the
        # journal has `strategy_name`, so 13 green tests sat on top of a SELECT
        # that raises on every live tick. CI caught it; the fixture had not.
        # (Same class as the phantom `entry_ts` that hid the trust-map crash,
        # noted in tests/test_backtest_fidelity_calibrate.py::_seed.)
        from src.units.db.database import Database

        Database(self.path)
        self.updates = []

    def connect(self):
        return sqlite3.connect(self.path)

    def update_trade(self, tid, updates):
        self.updates.append((tid, updates))


def _seed(db, **kw):
    conn = sqlite3.connect(db.path)
    conn.execute(
        # `timestamp` is NOT NULL in the real schema — default it to created_at
        # rather than dropping the constraint, so the fixture keeps matching
        # production. The reconciler never reads it.
        "INSERT INTO trades (id,account_id,symbol,direction,position_size,"
        "entry_price,created_at,timestamp,setup_type,strategy_name,sl_order_id,"
        "status,is_backtest) "
        "VALUES (:id,:account_id,:symbol,:direction,:position_size,:entry_price,"
        ":created_at,:created_at,:setup_type,:strategy_name,:sl_order_id,'open',0)",
        kw,
    )
    conn.commit()
    conn.close()


_ACC = {"account_id": "bybit_1", "exchange": "bybit", "market_type": "linear"}


def _patch(monkeypatch, protection):
    monkeypatch.setattr("src.bot.data_loaders.list_accounts", lambda: [_ACC])
    monkeypatch.setattr(
        "src.units.accounts.clients.bybit_client_for", lambda acc: object())
    monkeypatch.setattr(
        om, "_bybit_position_protection", lambda *a, **k: protection)


def _base_row(**over):
    row = dict(
        id=1, account_id="bybit_1", symbol="XRPUSDT", direction="long",
        position_size=100.0, entry_price=1.0, created_at="2026-01-01",
        setup_type="normal", strategy_name="ict_scalp", sl_order_id=None,
    )
    row.update(over)
    return row


def test_unreadable_exchange_read_is_SKIPPED_never_attributed(tmp_path, monkeypatch):
    """`None` = could-not-read. Never attribute on an unconfirmed read."""
    db = _SweepDB(tmp_path / "j.db")
    _seed(db, **_base_row())
    _patch(monkeypatch, None)
    s = om._reconcile_netting_partial_closes(db)
    assert s["skipped_unreadable"] == 1
    assert s["divergent"] == 0 and db.updates == []


def test_pairs_rows_are_EXCLUDED(tmp_path, monkeypatch):
    """The pairs sleeve owns its own state — never close its rows behind it."""
    db = _SweepDB(tmp_path / "j.db")
    _seed(db, **_base_row(strategy_name="pairs_sol_btc", setup_type="pairs"))
    _patch(monkeypatch, {"size": 0.0, "side": "", "covered_qty": 0.0,
                         "source": "flat", "sl_leg_ids": set(),
                         "unknown_qty_sl_legs": 0})
    s = om._reconcile_netting_partial_closes(db)
    assert s["skipped_pairs"] == 1
    assert s["checked"] == 0 and db.updates == []


def test_first_observation_only_PENDS_never_closes(tmp_path, monkeypatch):
    """One reading of a smaller position is not proof — 2 observations required.

    A just-placed order can read absent while pending fill; that is the
    RECONCILER_SNAPSHOT_MIN_FILL_AGE_S incident shape.
    """
    om._NETTING_DIVERGENCE_SEEN.clear()
    db = _SweepDB(tmp_path / "j.db")
    _seed(db, **_base_row())
    # Exchange flat while the journal claims 100 → maximal divergence.
    _patch(monkeypatch, {"size": 0.0, "side": "", "covered_qty": 0.0,
                         "source": "flat", "sl_leg_ids": set(),
                         "unknown_qty_sl_legs": 0})
    s = om._reconcile_netting_partial_closes(db)
    assert s["divergent"] == 1
    assert s["pending_confirm"] == 1
    assert s["rows_selected"] == 0 and db.updates == []


def test_annotate_mode_never_writes_the_money_db(tmp_path, monkeypatch):
    """Default mode does all the work and records it — without a DB write."""
    om._NETTING_DIVERGENCE_SEEN.clear()
    monkeypatch.delenv("NETTING_ATTRIBUTION_MODE", raising=False)
    monkeypatch.setenv("RECONCILER_CLOSE_CONFIRM_SECONDS", "0")
    db = _SweepDB(tmp_path / "j.db")
    _seed(db, **_base_row())
    _patch(monkeypatch, {"size": 0.0, "side": "", "covered_qty": 0.0,
                         "source": "flat", "sl_leg_ids": set(),
                         "unknown_qty_sl_legs": 0})
    monkeypatch.setattr(om, "_netting_soak_row", lambda **kw: None)
    # Inject the anchor so no network is touched. NOTE the un-patched run
    # returns `deferred` (the sandbox 403s the kline fetch) and correctly
    # writes NOTHING — that is the contract working, not a test bug.
    monkeypatch.setattr(
        "src.runtime.exit_anchor.bar_close_at",
        lambda *a, **k: (1.10, "anchored"))
    om._reconcile_netting_partial_closes(db)          # 1st: pends
    s = om._reconcile_netting_partial_closes(db)      # 2nd: confirmed
    assert s["rows_selected"] == 1
    assert s["annotated"] == 1
    assert s["closed"] == 0
    assert db.updates == []          # the money DB is untouched


def test_opposite_side_rows_are_fully_attributed(tmp_path, monkeypatch):
    """A LONG position backs nothing on the SHORT side — those rows are phantom."""
    om._NETTING_DIVERGENCE_SEEN.clear()
    monkeypatch.setenv("RECONCILER_CLOSE_CONFIRM_SECONDS", "0")
    db = _SweepDB(tmp_path / "j.db")
    _seed(db, **_base_row(id=1, direction="short", position_size=40.0))
    _patch(monkeypatch, {"size": 100.0, "side": "long", "covered_qty": 100.0,
                         "source": "full_position_stop", "sl_leg_ids": set(),
                         "unknown_qty_sl_legs": 0})
    monkeypatch.setattr(om, "_netting_soak_row", lambda **kw: None)
    monkeypatch.setattr(
        "src.runtime.exit_anchor.bar_close_at",
        lambda *a, **k: (1.10, "anchored"))
    om._reconcile_netting_partial_closes(db)
    s = om._reconcile_netting_partial_closes(db)
    assert s["divergent"] == 1 and s["rows_selected"] == 1


# ── the full-close branch must write a column `trades` actually has ──────────
#
# Live-caught 2026-08-08, the first time NETTING_ATTRIBUTION_MODE=apply was
# ever enabled: every full close raised `no such column: close_reason`.
# close_reason is on `order_packages`; `trades` has `exit_reason`. The partial
# branch only touches position_size/notes, so it worked and masked this, and
# annotate mode never calls update_trade at all — so no soak could surface it.
# This test drives a REAL sqlite trades table rather than a mock, because a
# mocked update_trade accepts any column name and would have passed throughout.

def test_full_close_writes_a_column_the_trades_table_has(tmp_path):
    import inspect

    from src.units.db import database as dbmod

    src = inspect.getsource(dbmod)
    i = src.find("CREATE TABLE IF NOT EXISTS trades")
    assert i != -1, "trades DDL not found — schema moved, update this test"
    ddl = src[i:i + 3000]
    assert "exit_reason" in ddl
    assert "close_reason" not in ddl, (
        "close_reason now exists on trades — this test encodes the opposite; "
        "re-check _netting_apply_close before relaxing it"
    )

    # And the writer must use it.
    from src.runtime import order_monitor
    body = inspect.getsource(order_monitor._netting_apply_close)
    assert '"exit_reason": "netting_attributed"' in body
    assert '"close_reason"' not in body, (
        "_netting_apply_close writes order_packages' column name onto trades"
    )


# ── a divergence nobody can fix must not print as one that will be fixed ─────
#
# bybit_1/BNBUSDT fired JOURNAL/BROKER QTY DIVERGENCE at ERROR every ~5 minutes
# indefinitely (live-confirmed 2026-08-08). BNBUSDT is a pairs-sleeve leg, the
# netting reconciler refuses pairs rows by design (`skipped_pairs`), so no
# attribution pass will ever reduce them. An ERROR nobody can action is how
# alarm fatigue starts, and CLAUDE.md makes the desensitised alarm its own P1.

def test_one_shared_predicate_decides_who_owns_a_pairs_row():
    """The reconciler's skip and the sweep's label must never disagree."""
    import inspect

    from src.runtime import order_monitor

    recon = inspect.getsource(order_monitor._reconcile_netting_partial_closes)
    assert "_is_pairs_sleeve_row(row)" in recon, (
        "the reconciler must use the shared predicate, not an inline copy"
    )
    sweep = inspect.getsource(
        order_monitor._check_broker_naked_bybit_positions
    )
    assert "_is_pairs_sleeve_row(row)" in sweep


def test_pairs_predicate_matches_setup_type_or_strategy_name():
    from src.runtime.order_monitor import _is_pairs_sleeve_row

    assert _is_pairs_sleeve_row({"setup_type": "pairs_open", "strategy_name": ""})
    assert _is_pairs_sleeve_row({"setup_type": "", "strategy_name": "pairs_sol_btc"})
    assert not _is_pairs_sleeve_row(
        {"setup_type": "ict_scalp", "strategy_name": "ict_scalp_avax_5m"}
    )
    # A row missing the keys entirely must not raise — the sweep runs per tick.
    assert not _is_pairs_sleeve_row({})


def test_wholly_pairs_divergence_is_warning_not_error():
    """It names the owner and says no reconciler is coming."""
    import inspect

    from src.runtime import order_monitor

    src = inspect.getsource(order_monitor._check_broker_naked_bybit_positions)
    assert "PAIRS-SLEEVE" in src
    assert "pairs_executor" in src
    # The un-actionable case must NOT claim a phantom row that something
    # will reconcile — that is the sentence operators learned to ignore.
    i = src.find("PAIRS-SLEEVE")
    branch = src[i:i + 900]
    assert "logger.warning" in src[max(0, i - 300):i + 100]
    assert "At least one open row is a phantom" not in branch


# ── the allowlist scopes the WRITE, never the measurement ────────────────────
# Until 2026-08-09 `NETTING_ATTRIBUTION_ACCOUNTS` intersected the account set at
# the top of the pass (`bybit_ids &= allow`). Operator decision 4 asked to stage
# the WRITE on bybit_1 before real-money bybit_2 — correct — but scoping the
# whole pass also switched off OBSERVATION of every other account. So while the
# allowlist was set, bybit_2 was not merely un-written, it was invisible: no
# divergence check, no soak row, nothing to review before widening the allowlist
# to it. It had been measured non-clean on 2026-08-06.
#
# There was no test on the allowlist at all, which is why that went unnoticed.

def test_may_write_requires_apply_mode_and_membership():
    """The one place the allowlist is consulted. Both conditions, or no write."""
    # No allowlist => every account may be written, but only under apply.
    assert om._netting_may_write("bybit_2", "apply", set()) is True
    assert om._netting_may_write("bybit_2", "annotate", set()) is False
    # Allowlist set => membership decides, and apply is still required.
    assert om._netting_may_write("bybit_1", "apply", {"bybit_1"}) is True
    assert om._netting_may_write("bybit_2", "apply", {"bybit_1"}) is False
    assert om._netting_may_write("bybit_1", "annotate", {"bybit_1"}) is False


def test_a_non_allowlisted_account_is_still_OBSERVED(tmp_path, monkeypatch):
    """The regression this exists to prevent.

    bybit_2 is not on the allowlist, so nothing may be written for it — but its
    divergence must still be detected and annotated, because that soak row is
    the entire evidence base for deciding whether to widen the allowlist. An
    account you cannot see is an account you can never safely promote.
    """
    om._NETTING_DIVERGENCE_SEEN.clear()
    monkeypatch.setenv("NETTING_ATTRIBUTION_MODE", "apply")
    monkeypatch.setenv("NETTING_ATTRIBUTION_ACCOUNTS", "bybit_1")
    monkeypatch.setenv("RECONCILER_CLOSE_CONFIRM_SECONDS", "0")
    db = _SweepDB(tmp_path / "j.db")
    _seed(db, **_base_row(id=7, account_id="bybit_2"))
    monkeypatch.setattr(
        "src.bot.data_loaders.list_accounts",
        lambda: [{"account_id": "bybit_2", "exchange": "bybit",
                  "market_type": "linear"}])
    monkeypatch.setattr(
        "src.units.accounts.clients.bybit_client_for", lambda acc: object())
    monkeypatch.setattr(
        om, "_bybit_position_protection",
        lambda *a, **k: {"size": 0.0, "side": "", "covered_qty": 0.0,
                         "source": "flat", "sl_leg_ids": set(),
                         "unknown_qty_sl_legs": 0})
    captured = []
    monkeypatch.setattr(om, "_netting_soak_row", lambda **kw: captured.append(kw))
    monkeypatch.setattr(
        "src.runtime.exit_anchor.bar_close_at", lambda *a, **k: (1.10, "anchored"))

    om._reconcile_netting_partial_closes(db)
    s = om._reconcile_netting_partial_closes(db)

    assert s["checked"] == 1, "bybit_2 must be reached by the pass at all"
    assert s["annotated"] == 1, "and its divergence recorded"
    assert db.updates == [], "but the money DB must NOT be written"
    # And the suppression is COUNTED, so 'held back by the allowlist' stays
    # distinguishable from 'there was nothing to do'.
    assert s["apply_suppressed_by_allowlist"] == 1
    assert captured, "a soak row is the evidence; it must exist"


def test_the_soak_row_records_the_EFFECTIVE_mode_not_the_global_one(
    tmp_path, monkeypatch
):
    """An audit trail must not describe an action the code did not take.

    Under a global `apply`, a non-allowlisted account's rows are annotated only.
    Stamping the global `mode: apply` on them would claim a money-DB write that
    never happened — the sub-class-A diagnostic-provenance defect, in the one
    log an operator reads to decide whether applying is safe.

    The three fields together say: what happened (annotate), what was asked for
    (apply), and why they differ (not_allowlisted).
    """
    om._NETTING_DIVERGENCE_SEEN.clear()
    monkeypatch.setenv("NETTING_ATTRIBUTION_MODE", "apply")
    monkeypatch.setenv("NETTING_ATTRIBUTION_ACCOUNTS", "bybit_1")
    monkeypatch.setenv("RECONCILER_CLOSE_CONFIRM_SECONDS", "0")
    db = _SweepDB(tmp_path / "j.db")
    _seed(db, **_base_row(id=8, account_id="bybit_2"))
    monkeypatch.setattr(
        "src.bot.data_loaders.list_accounts",
        lambda: [{"account_id": "bybit_2", "exchange": "bybit",
                  "market_type": "linear"}])
    monkeypatch.setattr(
        "src.units.accounts.clients.bybit_client_for", lambda acc: object())
    monkeypatch.setattr(
        om, "_bybit_position_protection",
        lambda *a, **k: {"size": 0.0, "side": "", "covered_qty": 0.0,
                         "source": "flat", "sl_leg_ids": set(),
                         "unknown_qty_sl_legs": 0})
    captured = []
    monkeypatch.setattr(om, "_netting_soak_row", lambda **kw: captured.append(kw))
    monkeypatch.setattr(
        "src.runtime.exit_anchor.bar_close_at", lambda *a, **k: (1.10, "anchored"))

    om._reconcile_netting_partial_closes(db)
    om._reconcile_netting_partial_closes(db)

    assert len(captured) == 1
    row = captured[0]
    assert row["mode"] == "annotate", "what actually happened to this row"
    assert row["global_mode"] == "apply", "what was asked for"
    assert row["apply_scope"] == "not_allowlisted", "why they differ"
