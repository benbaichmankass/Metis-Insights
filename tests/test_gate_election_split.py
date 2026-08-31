"""The gate/election split: ``gate_intents`` + ``elect_from_gated``.

WHY THIS EXISTS. ``aggregate_intents`` elects ONE winner per **symbol,
globally**, before any account is consulted. Two accounts running the same
symbol therefore compete, and every account but the winner's is silently
starved — it produces no order package, so it is invisible to the journal AND
to every per-account detector. Measured on the live audit, SOLUSDT buy-side
winners 2026-08-01..08-27 (n=60): ``trend_donchian_sol`` won **0**, having
produced 120 buy signals and ZERO journal rows on ``bybit_1``
(``BL-20260827-PROP-ONLY-TWIN-WINS-THE-GLOBAL-SYMBOL-SLOT-AND-STARVES-ITS-PAPER-SIBLING``).
Measured from declared config 2026-08-31: **13 of 23** live symbols have >= 2
live accounts competing for that one global slot.

The fix is to elect per account. What blocked it was structural, and named in
``intent_multiplexer.py``: re-running ``aggregate_intents`` per account
re-enters ``_hard_regime_gate`` and re-emits a ``regime_hard_gate`` audit row
**per account per tick** — corrupting the one signal that cleanly partitions
"would have gated" from "did gate".

So the gate is split from the election: gate ONCE, elect N times.

⚠️ These tests assert the SPLIT's invariants, not the routing change. Routing
is unchanged — ``aggregate_intents`` is still exactly
``elect_from_gated(*gate_intents(...))``. If a future change makes
``elect_from_gated`` emit an audit row, load a policy, or read the env, the
split has lost the only property that makes per-account election possible and
these tests are the ones that must fail.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

from src.runtime.intents import (
    StrategyIntent,
    aggregate_intents,
    elect_from_gated,
    gate_intents,
)


def _capture_audit_rows() -> tuple:
    captured: List[Dict[str, Any]] = []

    def _spy(payload, *args, **kwargs):
        captured.append(dict(payload))

    return captured, _spy


def _clean_router_env(monkeypatch):
    monkeypatch.delenv("REGIME_ROUTER_DISABLED", raising=False)
    monkeypatch.delenv("REGIME_ROUTER_ENABLED", raising=False)


def _make_intent(strategy: str, side: str, regime: str | None,
                 symbol: str = "BTCUSDT") -> StrategyIntent:
    return StrategyIntent(
        strategy=strategy,
        symbol=symbol,
        side=side,
        target_qty=0.0,          # the live sentinel — RiskManager sizes
        regime=regime,
        adx_14=15.0,
        vol_regime=None,
        entry=70000.0,
        sl=69000.0,
        tp=72000.0,
    )


_POLICY = {
    "chop": {"vwap": {"long": "off", "short": "off"}},
    "transitional": {"vwap": {"long": "off", "short": "off"}},
    "trending": {"vwap": {"long": "off", "short": "off"}},
}


# --- the composition is exact ----------------------------------------------


def test_aggregate_is_exactly_gate_then_elect(monkeypatch):
    """``aggregate_intents`` must stay the composition of the two halves.

    This is the no-behaviour-change proof for the split itself.
    """
    _clean_router_env(monkeypatch)
    intents = [
        _make_intent("vwap", "long", "trending"),          # OFF cell -> gated
        _make_intent("trend_donchian", "long", "trending"),
        _make_intent("ict_scalp_5m", "long", "trending"),
    ]

    _, spy_a = _capture_audit_rows()
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy_a):
        via_aggregate = aggregate_intents(intents, symbol="BTCUSDT")

    _, spy_b = _capture_audit_rows()
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy_b):
        cands, pre = gate_intents(intents, symbol="BTCUSDT")
        via_split = elect_from_gated(cands, symbol="BTCUSDT",
                                     intents_before_gate=pre)

    assert via_aggregate.side == via_split.side
    assert via_aggregate.reason == via_split.reason
    assert via_aggregate.target_qty == via_split.target_qty
    win_a = via_aggregate.winning_intent
    win_b = via_split.winning_intent
    assert (win_a.strategy if win_a else None) == (win_b.strategy if win_b else None)


# --- the invariant the whole design rests on -------------------------------


def test_electing_many_times_emits_the_gate_audit_exactly_once(monkeypatch):
    """Gate ONCE, elect N times — the audit partition stays uncorrupted.

    This is THE reason the split exists. Re-running ``aggregate_intents`` per
    account would emit one ``regime_hard_gate`` row per account per tick, so a
    later analysis could not tell "the gate refused this once" from "the gate
    refused it four times". Here four accounts elect from one gated set and
    the gate is still recorded exactly once.
    """
    _clean_router_env(monkeypatch)
    intents = [
        _make_intent("vwap", "long", "trending"),          # OFF cell -> gated
        _make_intent("trend_donchian", "long", "trending"),
        _make_intent("ict_scalp_5m", "long", "trending"),
    ]
    captured, spy = _capture_audit_rows()

    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        candidates, pre_gate = gate_intents(intents, symbol="BTCUSDT")
        # Four accounts, each electing from its OWN roster subset of the SAME
        # gated candidate set — the shape the per-account fan-out needs.
        rosters = {
            "bybit_1": {"trend_donchian", "ict_scalp_5m"},
            "bybit_2": {"trend_donchian"},
            "bybit_portfolio": {"trend_donchian"},
            "breakout_1": {"ict_scalp_5m"},
        }
        elected = {
            acct: elect_from_gated(
                tuple(i for i in candidates if i.strategy in roster),
                symbol="BTCUSDT",
                intents_before_gate=pre_gate,
            )
            for acct, roster in rosters.items()
        }

    hard = [r for r in captured if r.get("event") == "regime_hard_gate"]
    assert len(hard) == 1, (
        f"gate emitted {len(hard)} rows across 4 elections; must be 1 — "
        "a per-account re-gate corrupts the would-gate/did-gate partition"
    )
    assert hard[0]["strategy"] == "vwap"
    assert hard[0]["enforced"] is True

    # And every account got its own winner instead of one global winner.
    assert elected["bybit_2"].winning_intent.strategy == "trend_donchian"
    assert elected["breakout_1"].winning_intent.strategy == "ict_scalp_5m"
    # The starvation is gone: no account holding a live candidate is flat.
    for acct, desired in elected.items():
        assert desired.side != "flat", f"{acct} starved despite holding a candidate"


def test_elect_from_gated_is_pure(monkeypatch):
    """No audit row, no policy load, no env read.

    Purity is what makes calling it once per account safe. If this fails, the
    election has grown a side effect and N accounts means N of whatever it is.
    """
    _clean_router_env(monkeypatch)
    intents = (
        _make_intent("trend_donchian", "long", "trending"),
        _make_intent("ict_scalp_5m", "long", "trending"),
    )
    captured, spy = _capture_audit_rows()
    policy_loads = []

    def _tracking_policy_load(*a, **k):
        policy_loads.append(1)
        return _POLICY

    with patch("src.runtime.intents._load_regime_policy",
               side_effect=_tracking_policy_load), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        for _ in range(5):
            elect_from_gated(intents, symbol="BTCUSDT", intents_before_gate=2)

    assert captured == [], f"election emitted audit rows: {captured}"
    assert policy_loads == [], "election loaded the regime policy"


# --- the collapsed state the pre-gate count exists to prevent ---------------


def test_pre_gate_count_separates_all_gated_from_nothing_wanted(monkeypatch):
    """``all_intents_gated`` and ``no_intents_for_symbol`` are opposite claims.

    Both produce an empty candidate tuple. Only the pre-gate count tells them
    apart — "everything that wanted to trade was refused" vs "nothing wanted
    to trade". Dropping ``intents_before_gate`` collapses them.
    """
    # Everything refused: 3 wanted in, 0 survived.
    all_gated = elect_from_gated((), symbol="BTCUSDT", intents_before_gate=3)
    assert all_gated.reason == "all_intents_gated"

    # Nothing wanted in at all.
    nothing = elect_from_gated((), symbol="BTCUSDT", intents_before_gate=0)
    assert nothing.reason == "no_intents_for_symbol"

    # The default is the honest one for a bare empty call, and it is NOT
    # "everything was refused" — we have no evidence anything was.
    defaulted = elect_from_gated((), symbol="BTCUSDT")
    assert defaulted.reason == "no_intents_for_symbol"


def test_gate_returns_the_pre_gate_count_not_the_survivor_count(monkeypatch):
    """The count must be taken BEFORE the gate, or a fully-gated tick reports
    "nothing wanted to trade" when the truth is "everything was refused"."""
    _clean_router_env(monkeypatch)
    _, spy = _capture_audit_rows()
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        candidates, pre_gate = gate_intents(
            [_make_intent("vwap", "long", "trending")], symbol="BTCUSDT"
        )
    assert candidates == ()      # the only candidate was refused
    assert pre_gate == 1         # ...but one DID want to trade
    assert elect_from_gated(
        candidates, symbol="BTCUSDT", intents_before_gate=pre_gate
    ).reason == "all_intents_gated"


def test_gate_filters_to_symbol_and_election_does_not_refilter(monkeypatch):
    """Symbol filtering belongs to the gate half.

    ``elect_from_gated`` deliberately does not re-filter — it trusts its
    caller. This is documented rather than defended, so it is pinned here: a
    caller that skips ``gate_intents`` gets an unfiltered, ungated election.
    """
    _clean_router_env(monkeypatch)
    mixed = [
        _make_intent("trend_donchian", "long", "trending", symbol="BTCUSDT"),
        _make_intent("trend_donchian_eth", "long", "trending", symbol="ETHUSDT"),
    ]
    _, spy = _capture_audit_rows()
    with patch("src.runtime.intents._load_regime_policy", return_value={}), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        candidates, _ = gate_intents(mixed, symbol="BTCUSDT")
    assert [i.strategy for i in candidates] == ["trend_donchian"]
