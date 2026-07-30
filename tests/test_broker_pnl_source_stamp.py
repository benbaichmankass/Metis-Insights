"""A broker closed-pnl record must carry — and callers must stamp — its OWN source.

Until 2026-07-30 all four monitor sites that persist a broker close hardcoded
``exit_price_source = "bybit_closed_pnl"``. That was accurate while
``BROKER_PNL_READER_EXCHANGES`` held only ``bybit``. The moment a second reader
exists (IBKR), the literal labels an IBKR execution as a Bybit closed-pnl
record — a provenance lie written by the very subsystem this workstream added to
make provenance trustworthy.

Structural, not behavioural: reproducing all four call paths would need a live
Bybit client, an IB gateway and the netted-cascade reconciler. What actually
matters is that no literal survives at a stamp site, and a literal is exactly
what a future edit would reintroduce.
"""
from __future__ import annotations

import inspect
import re

from src.runtime import order_monitor as om
from src.runtime.provenance import (
    FABRICATED, MEASURED, UNVERIFIED, classify,
)
from src.units.accounts import clients


# --------------------------------------------------------------- the record
def test_the_bybit_record_declares_its_own_source():
    src = inspect.getsource(clients.account_closed_pnl_for_trade)
    assert '"source": "bybit_closed_pnl"' in src


def test_the_ib_branch_is_dispatched_before_the_bybit_category_check():
    """`_bybit_category` is called unconditionally after the capability gate;
    an IB account reaching it would fall out as 'unsupported category' and the
    reader would be silently dead."""
    src = inspect.getsource(clients.account_closed_pnl_for_trade)
    assert src.index("interactive_brokers") < src.index("_bybit_category")


def test_interactive_brokers_is_declared_in_the_capability_set():
    assert "interactive_brokers" in clients.BROKER_PNL_READER_EXCHANGES
    assert clients.exchange_has_broker_pnl_reader("interactive_brokers")
    assert clients.account_has_broker_pnl_reader(
        {"exchange": "interactive_brokers", "account_id": "ib_paper"}
    )


# ---------------------------------------------------------------- the helper
def test_helper_reads_the_records_source():
    assert om._broker_pnl_source({"source": "ib_execution"}) == "ib_execution"


def test_helper_falls_back_for_a_pre_source_record():
    """A record with no `source` can only have come from the Bybit reader —
    that was the only one that existed."""
    assert om._broker_pnl_source({"closed_pnl": 1.0}) == "bybit_closed_pnl"
    assert om._broker_pnl_source(None) == "bybit_closed_pnl"
    assert om._broker_pnl_source({"source": ""}) == "bybit_closed_pnl"


def test_note_key_preserves_the_bybit_key_but_separates_others():
    """Existing readers of `notes.bybit_closed_pnl` and every historical row
    must be unaffected; a non-Bybit figure must not land in that field."""
    assert om._broker_pnl_note_key({"source": "bybit_closed_pnl"}) == "bybit_closed_pnl"
    assert om._broker_pnl_note_key(None) == "bybit_closed_pnl"
    assert om._broker_pnl_note_key({"source": "ib_execution"}) == "ib_execution"


# ------------------------------------------------------------ no literals left
_STAMP_SITES = (
    "_recover_close_from_broker_pnl",
    "_close_trade_from_order_status",
    "_sweep_pending_pnl_from_bybit",
    "_cascade_close_netted_siblings",
)


def _code_only(fn) -> str:
    """Source minus comment lines — check what executes, not what is said."""
    return "\n".join(
        ln for ln in inspect.getsource(fn).splitlines()
        if not ln.lstrip().startswith("#")
    )


def test_no_stamp_site_hardcodes_the_bybit_provenance_literal():
    pattern = re.compile(r'"exit_price_source"\s*:\s*"bybit')
    for name in _STAMP_SITES:
        src = _code_only(getattr(om, name))
        assert not pattern.search(src), (
            f"{name} stamps a hardcoded Bybit provenance literal — an IBKR "
            f"execution persisted through it would be labelled Bybit truth"
        )


def test_every_stamp_site_routes_through_the_helper():
    for name in _STAMP_SITES:
        assert "_broker_pnl_source" in _code_only(getattr(om, name)), name


# ------------------------------------------------------- prorated is fabricated
def test_ib_execution_is_measured():
    assert classify("ib_execution") == MEASURED


def test_a_prorated_source_is_fabricated_whatever_its_base():
    """A proration splits a netted record across siblings by qty share — an
    assumption about ATTRIBUTION, true or false independently of how measured
    the underlying record was. Before this, `bybit_closed_pnl_prorated` fell
    through to UNVERIFIED, so a manufactured number read as merely unrecorded."""
    assert classify("bybit_closed_pnl_prorated") == FABRICATED
    assert classify("ib_execution_prorated") == FABRICATED
    assert classify("netted_prorated") == FABRICATED


def test_the_bare_suffix_is_not_a_source():
    assert classify("_prorated") == UNVERIFIED


def test_the_unprorated_bases_are_unaffected():
    assert classify("bybit_closed_pnl") == MEASURED
    assert classify("ib_execution") == MEASURED
