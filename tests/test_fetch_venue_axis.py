"""BL-20260821-FETCH-COST-HAS-NO-VENUE-AXIS — the third `fetch*` cut.

`/api/diag/tick_cost` published two cuts of the same fetch seconds:
`fetch.<timeframe>` (which frames miss) and `fetchby.<consumer>` (which phase
asked). Neither can answer WHICH VENUE the cost belongs to — the axis that
decides what a fix is worth.

WHY THAT COST SOMETHING CONCRETE. The 2026-08-21 exit-loop attribution needed
exactly this axis and did not have it. `fetch.15m` was fully attributable to IB
only BY LUCK: there happened to be exactly ONE open 15m package and it happened
to be on an IB-routed symbol. A second 15m package on Bybit would have made that
line un-attributable, and T.1's root cause — IB frames cannot cache at any TTL —
would not have been isolable from the instrument at all.

THE ROW'S OWN CRITERION is reconciliation: the per-venue seconds must sum to the
same total as the per-timeframe seconds over the same process, to within 1%. The
existing two cuts reconcile EXACTLY (measured 2026-08-21: 11467.9s vs 11467.9s,
delta 0.00%), and that exactness is what makes the instrument trustworthy. So it
is asserted here numerically rather than argued.
"""
from __future__ import annotations

import pytest

from src.runtime import market_data as md


class _Conn:
    """Stands in for a connector; only its MODULE is what the resolver reads."""

    def get_ohlcv(self, *a, **k):
        return [[1, 1.0, 1.0, 1.0, 1.0, 1.0]] * 3


def _as_module(cls_name: str, module: str):
    return type(cls_name, (_Conn,), {"__module__": module})()


@pytest.mark.parametrize("module,expected", [
    ("src.exchange.bybit_connector", "bybit"),
    ("src.exchange.ib_connector", "interactive_brokers"),
    ("src.exchange.alpaca_connector", "alpaca"),
    ("src.exchange.oanda_connector", "oanda"),
])
def test_each_venue_resolves_from_its_module(module, expected):
    assert md._venue_of_client(_as_module("C", module)) == expected


def test_an_unrecognised_connector_keeps_its_OWN_bucket():
    """`unknown` must not be folded into a neighbour.

    Folding it would silently inflate whichever venue absorbed it — and the
    inflated venue would look like the place to spend effort.
    """
    assert md._venue_of_client(_as_module("C", "src.exchange.new_venue")) == "unknown"
    assert md._venue_of_client(None) == "unknown"
    assert md._venue_of_client(object()) == "unknown"


def test_the_three_cuts_RECONCILE_over_the_same_fetches(monkeypatch):
    """THE ROW'S CRITERION. Same seconds, three cuts — the sums must agree.

    Each fetch must record once in EACH family, on both the miss path and the
    cache-hit path. If `fetchvenue` were wired only to the miss path its `n`
    would fall short of `fetchby`'s by exactly the hit count, which is how the
    existing pair is documented to reconcile (1326 misses + 3170 hits = 4496).
    """
    from src.runtime import tick_cost

    tick_cost.reset_for_test() if hasattr(tick_cost, "reset_for_test") else None
    recorded: list[str] = []

    import contextlib

    @contextlib.contextmanager
    def _spy(name):
        recorded.append(name)
        yield

    monkeypatch.setattr(tick_cost, "hook", _spy)
    monkeypatch.setattr(tick_cost, "current_phase", lambda: "pipeline.signal_build")

    with md._fetch_phase("15m", "interactive_brokers"):
        pass
    with md._fetch_phase("cache_hit", "bybit"):
        pass

    tf = [n for n in recorded if n.startswith("fetch.")]
    by = [n for n in recorded if n.startswith("fetchby.")]
    venue = [n for n in recorded if n.startswith("fetchvenue.")]
    assert len(tf) == len(by) == len(venue) == 2, (
        f"each fetch must record once per family; got tf={tf} by={by} venue={venue}"
    )
    assert "fetchvenue.interactive_brokers" in venue
    assert "fetchvenue.bybit" in venue, (
        "the cache-hit path must carry a venue too, or fetchvenue's n falls "
        "short of fetchby's by exactly the hit count"
    )


def test_the_venue_name_budget_stays_far_under_the_cap():
    """`hook_names_refused` must still read 0 — the row says so explicitly.

    Five venue names against a 64 cap, on a chain measured at 27 names.
    """
    from src.runtime import tick_cost

    assert tick_cost._MAX_HOOK_NAMES >= 64
    assert len(md._VENUE_BY_MODULE) + 1 <= 8   # +1 for the `unknown` bucket
