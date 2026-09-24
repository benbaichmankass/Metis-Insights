"""M39(B): the pairs close must stamp provenance, and stamp the RIGHT thing.

BL-20260824-THE-DECIDED-EXIT-PATH-IS-THE-UNMEASURED-ONE

`_close_pair` persisted status/exit_price/exit_reason/closed_at/pnl and NO
provenance key, so every pairs close classified UNVERIFIED -- "we don't know" --
while carrying a real price and a real pnl. Measured on the live journal
2026-08-25 over the newest 500 trades: of 107 DECIDED closes, 82 were
unverified and **all 82 were pairs**. After the M39(A) monitor fix this one
site was 100% of the remaining decided-provenance gap; every non-pairs decided
path measured 0% unverified.

What these tests hold, and why each one rather than a comment:

* The stamp is ESTIMATED, not MEASURED. `last_px` is `closes_a[-1]` -- the
  close of the bar the decision was made on -- and `close_open_position` is
  called for the flatten but only its `ok` flag is read, so no fill price is
  ever known. Stamping broker truth here would be the provenance lie that
  demoted `recorded_exit_price`. The test asserts the BUCKET through the
  canonical `classify_pnl`, not the string, so it fails if the vocabulary ever
  reclassifies the source out from under it.

* The stamp is at the WRITE SITE, so every `pairs_*` outcome is covered by
  construction. Track B was scoped as "pairs_revert / pairs_stop" while the
  live journal also carries `pairs_timeout` -- a per-reason fix would have left
  a third silently unstamped. Parametrised over all three plus an invented
  future reason.

* It never overwrites a more specific existing stamp (the sibling rule in
  `_apply_update` and `_sweep_local_pnl_for_unpriced`).

* It changes NOTHING else the close writes. A provenance change that quietly
  moved a price or a pnl would be far worse than the gap it closes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.runtime import provenance as prov  # noqa: E402


# --------------------------------------------------------------------------
# A stand-in for the two collaborators the close site touches.
# --------------------------------------------------------------------------
class _FakeDb:
    def __init__(self, rows):
        self._rows = rows
        self.updates = []          # [(trade_id, payload), ...]

    def get_trades(self, filters=None, limit=None):
        strat = (filters or {}).get("strategy_name")
        return [r for r in self._rows if r.get("strategy_name") == strat][: (limit or 99)]

    def update_trade(self, trade_id, payload):
        self.updates.append((trade_id, payload))


def _run_close(monkeypatch, *, outcome, notes=None, entry=100.0, last_px=110.0):
    """Drive `_close_pair` over one leg and return its update payload."""
    from src.units.strategies import pairs_executor as px

    row = {
        "id": 4242, "strategy_name": "pairs_x_a", "direction": "long",
        "position_size": 2.0, "entry_price": entry,
        "notes": json.dumps(notes) if notes is not None else None,
    }
    db = _FakeDb([row])
    monkeypatch.setattr(px, "Database" if hasattr(px, "Database") else "_noop",
                        lambda *a, **k: db, raising=False)
    # `Database` and `close_open_position` are imported INSIDE the function, so
    # patch them at their source modules.
    import src.units.db.database as dbmod
    import src.units.accounts.execute as execmod
    monkeypatch.setattr(dbmod, "Database", lambda *a, **k: db, raising=False)
    monkeypatch.setattr(execmod, "close_open_position",
                        lambda *a, **k: {"ok": True}, raising=False)
    monkeypatch.setattr(px, "_cascade_close_pair_package",
                        lambda *a, **k: True, raising=False)

    pair = {"symbol_a": "AAA", "symbol_b": "BBB"}
    monkeypatch.setattr(px, "_leg_strats", lambda p: ("pairs_x_a", "pairs_x_b"),
                        raising=False)
    px._close_pair(object(), {"account_id": "acct"}, pair, outcome, last_px, last_px)
    assert db.updates, "the close wrote nothing at all"
    return db.updates[0][1]


ALL_PAIRS_OUTCOMES = ["revert", "stop", "timeout", "some_future_outcome"]


@pytest.mark.parametrize("outcome", ALL_PAIRS_OUTCOMES)
def test_every_pairs_outcome_is_stamped(monkeypatch, outcome):
    """The stamp is at the write site, so a new outcome is covered for free."""
    payload = _run_close(monkeypatch, outcome=outcome)
    notes = json.loads(payload["notes"])
    assert notes.get("exit_price_source"), f"pairs_{outcome} close carries no stamp"
    assert payload["exit_reason"] == f"pairs_{outcome}"


def test_the_stamp_classifies_estimated_not_measured(monkeypatch):
    """A bar close is not a fill. Asserted through the canonical classifier."""
    payload = _run_close(monkeypatch, outcome="revert")
    bucket, _key = prov.classify_pnl({"notes": payload["notes"], "pnl": payload["pnl"]})
    assert bucket == prov.ESTIMATED, (
        f"expected ESTIMATED (a decision-bar close), got {bucket!r} — "
        f"MEASURED here would claim broker truth this path never reads"
    )


def test_without_the_stamp_the_row_would_be_unverified(monkeypatch):
    """The control: this is the state the fix exists to remove.

    Not a tautology — it pins that the bucket genuinely turns on the stamp, so
    `test_the_stamp_classifies_estimated_not_measured` cannot be passing for
    some unrelated reason.
    """
    payload = _run_close(monkeypatch, outcome="revert")
    stripped = {k: v for k, v in json.loads(payload["notes"]).items()
                if k not in prov.PROVENANCE_KEYS}
    bucket, _ = prov.classify_pnl({"notes": json.dumps(stripped), "pnl": payload["pnl"]})
    assert bucket == prov.UNVERIFIED


def test_a_more_specific_existing_stamp_is_never_overwritten(monkeypatch):
    payload = _run_close(monkeypatch, outcome="stop",
                         notes={"exit_price_source": "bybit_closed_pnl"})
    notes = json.loads(payload["notes"])
    assert notes["exit_price_source"] == "bybit_closed_pnl"
    bucket, _ = prov.classify_pnl({"notes": payload["notes"], "pnl": payload["pnl"]})
    assert bucket == prov.MEASURED, "a broker source was laundered to an estimate"


def test_an_unusable_price_declares_unmeasured_rather_than_a_bar_close(monkeypatch):
    """Three states: we did not get a price is not the same as we got a bar."""
    payload = _run_close(monkeypatch, outcome="revert", last_px=float("nan"))
    notes = json.loads(payload["notes"])
    assert notes["exit_price_source"] == prov.UNMEASURED_MARKER


def test_pnl_source_is_absent_when_there_is_no_pnl(monkeypatch):
    """`local_compute` describes arithmetic; claiming it over a NULL pnl would
    describe arithmetic that never ran."""
    payload = _run_close(monkeypatch, outcome="revert", entry=0.0)
    notes = json.loads(payload["notes"])
    assert payload["pnl"] is None
    assert "pnl_source" not in notes
    assert notes.get("exit_price_source") == "candle_at_close", (
        "the exit PRICE is still a real bar close even when pnl is uncomputable"
    )


def test_the_close_writes_nothing_different_apart_from_notes(monkeypatch):
    """A provenance change that moved a price or a pnl would be worse than the
    gap it closes."""
    payload = _run_close(monkeypatch, outcome="revert", entry=100.0, last_px=110.0)
    assert payload["status"] == "closed"
    assert payload["exit_price"] == 110.0
    # ⚠️ THIS USED TO ASSERT `pnl == 20.0` — (110-100) * 2.0 long — AND THAT
    # ASSERTION WAS PINNING THE DEFECT E30 FOUND. Writing a local pnl here put
    # the row OUTSIDE both broker-truth sweeps, which select on `pnl IS NULL`,
    # so it could never be refined to a fill. See
    # `test_the_close_leaves_pnl_null_so_the_broker_sweeps_can_see_the_row`.
    assert payload["pnl"] is None
    assert payload["pnl_percent"] is None
    assert set(payload) == {"status", "exit_price", "exit_reason", "closed_at",
                            "pnl", "pnl_percent", "notes"}


# ==========================================================================
# E30 — the pairs close must not pre-empt the PnL-resolution contract.
# Checklist row E30; observation PI-20260922-54WQNHJX-0001 in PIPELINE.jsonl.
# ==========================================================================

def test_the_close_leaves_pnl_null_so_the_broker_sweeps_can_see_the_row(
    monkeypatch,
):
    """The behavioural half: a pairs close is written UNPRICED.

    MEASURED on the live journal 2026-09-22 (full `trades` table, n=6,059, via
    /api/diag/journal): **0 of 365** `pairs_*` closes have ever classified
    MEASURED, while 48 non-pairs closes on the SAME account (`bybit_1`), the
    same venue and the same fills store resolved to `exchange_fill` in the
    preceding 14 days. The venue serves these fills; this path wrote a pnl at
    close time and thereby removed its own rows from the sweep that reads them.
    """
    payload = _run_close(monkeypatch, outcome="revert", entry=100.0, last_px=110.0)
    assert payload["pnl"] is None, (
        "a non-NULL pnl at close time puts the row outside both broker-truth "
        "sweeps FOREVER — they select on `pnl IS NULL`"
    )
    assert payload["pnl_percent"] is None
    # The price is still recorded, and still honestly stamped: the broker sweep
    # overwrites BOTH together if a fill lands, and if none ever does the local
    # fallback prices the row from this exact value — today's outcome. So the
    # change can upgrade a row and can never downgrade one.
    assert payload["exit_price"] == 110.0
    assert json.loads(payload["notes"])["exit_price_source"] == "candle_at_close"


def test_both_broker_truth_sweeps_still_gate_on_pnl_is_null():
    """The coupling half, stated so a future edit meets it.

    The behavioural test above is only meaningful because `pnl IS NULL` is what
    admits a row to the sweeps. That predicate lives in `order_monitor`, not
    here, so a change there would silently strand this fix. Read the source
    rather than trusting the comment (*field beats comment*).
    """
    import re

    om = (REPO / "src" / "runtime" / "order_monitor.py").read_text()
    for fn in ("_sweep_pending_pnl_from_bybit", "_sweep_local_pnl_for_unpriced"):
        start = om.index(f"def {fn}(")
        body = om[start:om.index("\ndef ", start + 1)]
        select = body[body.index('"SELECT'):body.index("LIMIT")]
        assert re.search(r"AND pnl IS NULL", select), (
            f"{fn} no longer selects on `pnl IS NULL`. The pairs close leaves "
            f"pnl NULL precisely so this sweep can see it — re-check "
            f"pairs_executor._close_pair before changing this scope."
        )


def test_a_priced_row_is_excluded_by_that_predicate_and_an_unpriced_one_is_not(
    tmp_path,
):
    """A positive AND a negative control, run against real sqlite.

    A negative result needs a denominator: asserting only that the unpriced row
    is selected would pass against a predicate that selects everything.
    """
    import sqlite3

    db = sqlite3.connect(tmp_path / "t.sqlite")
    db.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT, "
        "is_backtest INTEGER, pnl REAL)"
    )
    db.execute("INSERT INTO trades VALUES (1, 'closed', 0, NULL)")   # as written now
    db.execute("INSERT INTO trades VALUES (2, 'closed', 0, 20.0)")   # as written before
    got = {r[0] for r in db.execute(
        "SELECT id FROM trades WHERE status = 'closed' "
        "AND COALESCE(is_backtest, 0) = 0 AND pnl IS NULL"
    )}
    db.close()
    assert got == {1}, (
        f"expected only the unpriced row to be sweepable, got {got} — "
        f"the predicate must EXCLUDE the priced row or the fix is vacuous"
    )


def test_notes_go_through_the_capped_writer_not_a_char_slice():
    """json-notes-cap: a `json.dumps(...)[:N]` slice persists invalid JSON."""
    src = (REPO / "src" / "units" / "strategies" / "pairs_executor.py").read_text()
    assert "dump_capped(" in src
    assert "json.dumps(_notes)[" not in src


def test_the_notes_decoder_has_exactly_one_implementation():
    """order_monitor's private twin delegates rather than duplicating.

    A fourth copy is how the existing three drift apart — this repo's own
    words about the ticker map. The decode and the encode now share a home.
    """
    om = (REPO / "src" / "runtime" / "order_monitor.py").read_text()
    assert "def _decode_notes" in om
    assert "_load_notes(notes_raw)" in om, "order_monitor grew its own decode again"
