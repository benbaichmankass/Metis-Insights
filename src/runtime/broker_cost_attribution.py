"""Attribute broker-truth round-trip FEES to trades via FIFO over the fills stream.

Slice B / B2 (MB-20260629-ALLOC-COSTCAP). Slice A stamped a fixed-model round-trip
cost *estimate* on every closed trade; this upgrades the trades we can attribute
EXACTLY to broker truth by joining `trade_journal.db::trades` to the exchange-fills
store (`runtime_state/exchange_fills.sqlite`).

The join is not a naive one-to-one:

* The trade's ENTRY order id is exact (`trades.broker_order_id`, the Slice-B/B0
  column = Bybit `orderId` = `exchange_fills.order_id`), so the entry fill(s) are
  matched exactly.
* Broker SL/TP EXITS fill under an order id the bot never sees, so the exit leg
  is matched by walking each `(account, symbol)` fills stream in time order and
  FIFO-pairing opposite-side fills against the open entry lots — the exit fill's
  fee is attributed to the trade whose lot it closes.
* On a **netted** account two strategies can share one exchange position; when the
  inventory holds lots from more than one distinct trade at once, per-trade fee
  attribution is ambiguous. Those trades are flagged `ambiguous` and are NOT
  written broker-truth (they keep their estimate) — a wrong money-label is worse
  than an approximate one.

This module is pure (no DB / no I/O). The sweep driver
(`scripts/ops/backfill_broker_truth_costs.py`) reads the two stores and applies
the result.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional


def normalize_symbol(sym: Optional[str]) -> str:
    """Fold a ccxt (`BTC/USDT:USDT`) or plain (`BTCUSDT`) symbol to a bare key."""
    if not sym:
        return ""
    return str(sym).split(":")[0].replace("/", "").upper()


def _fill_side(side: Optional[str]) -> str:
    return "buy" if str(side or "").strip().lower() in ("buy", "long") else "sell"


@dataclass
class TradeCost:
    trade_id: int
    fee_taker_usd: float = 0.0
    fee_maker_usd: float = 0.0
    entry_matched: bool = False
    exit_matched: bool = False
    ambiguous: bool = False
    fee_currencies: set = field(default_factory=set)

    @property
    def clean(self) -> bool:
        """A broker-truth-writable round trip: both legs matched, unambiguous."""
        return self.entry_matched and self.exit_matched and not self.ambiguous


@dataclass
class _Lot:
    trade_id: Optional[int]
    qty: float
    side: str  # 'buy' / 'sell'


def attribute_roundtrip_fees(
    trades: Iterable[Mapping],
    fills: Iterable[Mapping],
) -> dict[int, TradeCost]:
    """Return {trade_id: TradeCost} of FIFO-attributed broker fees.

    ``trades`` rows need: ``id``, ``account_id``, ``symbol``, ``broker_order_id``.
    ``fills`` rows need: ``account_id``, ``symbol``, ``side``, ``qty``, ``fee``,
    ``is_maker``, ``order_id``, ``exec_time`` (ISO-8601 sortable).
    """
    # Entry-order → trade map (exact leg). Skip trades with no join key.
    entry_of: dict[tuple[str, str], int] = {}
    costs: dict[int, TradeCost] = {}
    for t in trades:
        oid = t.get("broker_order_id")
        tid = t.get("id")
        if tid is None:
            continue
        costs[int(tid)] = TradeCost(trade_id=int(tid))
        if oid:
            entry_of[(str(t.get("account_id")), str(oid))] = int(tid)

    # Group fills by (account, normalized symbol), each walked in time order.
    groups: dict[tuple[str, str], list[Mapping]] = {}
    for f in fills:
        key = (str(f.get("account_id")), normalize_symbol(f.get("symbol")))
        groups.setdefault(key, []).append(f)

    def _add_fee(tid: int, fee: float, is_maker: bool, cur: Optional[str]) -> None:
        c = costs.get(tid)
        if c is None:  # a fill's trade isn't in the trades set — ignore
            return
        if is_maker:
            c.fee_maker_usd += fee
        else:
            c.fee_taker_usd += fee
        if cur:
            c.fee_currencies.add(str(cur))

    for (acct, _sym), fs in groups.items():
        fs.sort(key=lambda r: (str(r.get("exec_time") or ""), str(r.get("exec_id") or "")))
        inventory: deque[_Lot] = deque()  # all lots share one net side

        def _distinct_open_trades() -> set:
            return {lot.trade_id for lot in inventory if lot.trade_id is not None}

        def _flag_ambiguous(extra_tid: Optional[int]) -> None:
            # Inventory currently holds >1 distinct trade (or is about to) → the
            # positions are netted and per-trade fee split is not trustworthy.
            tids = _distinct_open_trades()
            if extra_tid is not None:
                tids = tids | {extra_tid}
            if len(tids) > 1:
                for tid in tids:
                    if tid in costs:
                        costs[tid].ambiguous = True

        for f in fs:
            side = _fill_side(f.get("side"))
            qty = abs(float(f.get("qty") or 0.0))
            if qty <= 0:
                continue
            fee = float(f.get("fee") or 0.0)
            is_maker = bool(f.get("is_maker"))
            cur = f.get("fee_currency")
            entry_tid = entry_of.get((acct, str(f.get("order_id"))))

            inv_side = inventory[0].side if inventory else None

            if inv_side is None or side == inv_side:
                # OPEN / add to the net position. Whole fill is an entry leg.
                _flag_ambiguous(entry_tid)
                inventory.append(_Lot(trade_id=entry_tid, qty=qty, side=side))
                if entry_tid is not None:
                    costs[entry_tid].entry_matched = True
                    _add_fee(entry_tid, fee, is_maker, cur)
                # A fill with no known entry trade opens an untracked lot; its
                # fee has no trade to land on (dropped).
            else:
                # CLOSE the net position FIFO; this fill's fee splits across the
                # lots it consumes (the EXIT leg of each). Any residual flips.
                remaining = qty
                fee_per_qty = fee / qty if qty else 0.0
                while remaining > 1e-12 and inventory:
                    lot = inventory[0]
                    take = min(remaining, lot.qty)
                    if lot.trade_id is not None:
                        costs[lot.trade_id].exit_matched = True
                        _add_fee(lot.trade_id, fee_per_qty * take, is_maker, cur)
                    lot.qty -= take
                    remaining -= take
                    if lot.qty <= 1e-12:
                        inventory.popleft()
                if remaining > 1e-12:
                    # Over-close → flips to a new position on the opposite side.
                    _flag_ambiguous(entry_tid)
                    inventory.append(_Lot(trade_id=entry_tid, qty=remaining, side=side))
                    if entry_tid is not None:
                        costs[entry_tid].entry_matched = True
                        _add_fee(entry_tid, fee_per_qty * remaining, is_maker, cur)

    return costs


def attribute_funding_to_trades(
    trades: Iterable[Mapping],
    funding_events: Iterable[Mapping],
    clean_trade_ids: Iterable[int],
) -> dict[int, float]:
    """Sum perp funding COST per CLEAN trade over its open→close window.

    Slice B / B1. Funding is charged to the NET (account, symbol) position, so it
    is only cleanly attributable to a trade that was the SOLE position holder for
    its life — i.e. the trades B2 flagged ``clean`` (unambiguous, both legs
    matched). For each such trade, funding_paid_usd = −Σ(signed funding_usd) for
    funding events on the same (account, normalized-symbol) whose time falls in
    ``[open_time, close_time]`` (positive = net paid = a cost, matching the fee
    convention).

    ``trades`` rows need: ``id``, ``account_id``, ``symbol``, ``open_time``
    (ISO), ``close_time`` (ISO). ``funding_events`` need: ``account_id``,
    ``symbol``, ``funding_time`` (ISO), ``funding_usd`` (signed).
    """
    clean = set(int(t) for t in clean_trade_ids)
    # Bucket funding by (account, norm_symbol) → sorted [(time, usd)].
    by_key: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for f in funding_events:
        key = (str(f.get("account_id")), normalize_symbol(f.get("symbol")))
        t = str(f.get("funding_time") or "")
        if not t:
            continue
        by_key.setdefault(key, []).append((t, float(f.get("funding_usd") or 0.0)))

    out: dict[int, float] = {}
    for tr in trades:
        tid = tr.get("id")
        if tid is None or int(tid) not in clean:
            continue
        open_t = str(tr.get("open_time") or "")
        close_t = str(tr.get("close_time") or "")
        if not open_t or not close_t:
            continue
        key = (str(tr.get("account_id")), normalize_symbol(tr.get("symbol")))
        total = 0.0
        for (ft, usd) in by_key.get(key, ()):  # ISO strings sort chronologically
            if open_t <= ft <= close_t:
                total += usd
        # Store as a COST (positive = paid), mirroring the fee sign convention.
        out[int(tid)] = round(-total, 8)
    return out
