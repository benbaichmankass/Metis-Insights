"""Breakout POC — outbound "trade setup" ticket builder.

Turns a strategy signal into a single, self-contained, **agent-agnostic**
instruction block that the operator pastes into a supervised executor
(desktop browser-Claude / Comet / manual) to place a BRACKET order on the
Breakout DXTrade terminal. See
``docs/integrations/breakout-poc-manual-bridge-DESIGN.md``.

Tier-1: this module only *formats* a message. It places no live order and is
not wired into the live order path — the coordinator hook is a separate,
operator-gated step.

Hard invariants baked into every ticket (printed in the instruction block so
they travel with the message regardless of which agent reads it):
  * SL + TP attached at entry — never place without both.
  * Supervised confirm — review the filled order before submit.
  * Validity guards — abort if past ``valid_until`` or if live price is outside
    the entry band.
  * Do not manage the exit — the broker-side bracket is the exit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

# Bar duration in minutes per strategy timeframe — drives the default TTL.
_TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "1d": 1440,
}


@dataclass(frozen=True)
class BreakoutSignal:
    """Minimal signal shape the ticket builder needs."""
    strategy: str
    symbol: str                 # bot symbol, e.g. "BTCUSDT"
    direction: str              # "long" | "short"
    entry: float
    sl: float
    tp: float
    timeframe: str              # e.g. "4h" — drives TTL
    signal_time: datetime       # tz-aware UTC


@dataclass(frozen=True)
class TicketConfig:
    """Routing + sizing knobs (from breakout_routing.yaml)."""
    account_size_usd: float = 5000.0
    risk_pct: float = 0.6                 # PERCENT of balance per trade (0.6 = 0.6%)
    dxtrade_symbol: Optional[str] = None  # e.g. "BTCUSD"; None → show bot symbol + TODO
    contract_value_usd_per_point: float = 1.0  # $ risk per 1.0 unit per 1 price-point
                                               # (crypto-native default; VERIFY vs the
                                               # DXTrade instrument's contract size)
    entry_band_frac: float = 0.25         # band = ± this × (entry→SL distance)
    ttl_bars: float = 1.0                 # signal valid for this many bars after signal_time


@dataclass(frozen=True)
class Ticket:
    signal: BreakoutSignal
    cfg: TicketConfig
    side: str                   # "Buy" | "Sell"
    risk_usd: float
    qty_units: float            # size in instrument units (e.g. BTC); convert to DXTrade contracts
    rr: float                   # reward:risk
    entry_min: float
    entry_max: float
    valid_until: datetime


def _round(x: float, n: int = 6) -> float:
    return float(round(x, n))


def build_ticket(sig: BreakoutSignal, cfg: TicketConfig) -> Ticket:
    """Compute sizing, the entry band, and validity window for a signal."""
    if sig.direction not in ("long", "short"):
        raise ValueError(f"direction must be long|short, got {sig.direction!r}")
    sl_dist = abs(sig.entry - sig.sl)
    if sl_dist <= 0:
        raise ValueError("entry and sl must differ (non-zero stop distance)")
    tp_dist = abs(sig.tp - sig.entry)
    rr = tp_dist / sl_dist if sl_dist else 0.0

    risk_usd = (cfg.risk_pct / 100.0) * cfg.account_size_usd
    cvpp = cfg.contract_value_usd_per_point or 1.0
    qty_units = risk_usd / (sl_dist * cvpp)

    # Entry band — symmetric fraction of the stop distance, clamped so it never
    # crosses the SL (a fill past the SL would be nonsensical).
    band = cfg.entry_band_frac * sl_dist
    if sig.direction == "long":
        side = "Buy"
        entry_min = max(sig.entry - band, sig.sl + 1e-9)
        entry_max = sig.entry + band
    else:
        side = "Sell"
        entry_min = sig.entry - band
        entry_max = min(sig.entry + band, sig.sl - 1e-9)

    ttl_min = cfg.ttl_bars * _TF_MINUTES.get(sig.timeframe, 60)
    valid_until = sig.signal_time + timedelta(minutes=ttl_min)

    return Ticket(
        signal=sig, cfg=cfg, side=side,
        risk_usd=_round(risk_usd, 2), qty_units=_round(qty_units, 8),
        rr=_round(rr, 2), entry_min=_round(entry_min), entry_max=_round(entry_max),
        valid_until=valid_until,
    )


def render_ticket(t: Ticket, *, now: Optional[datetime] = None) -> str:
    """Render the paste-ready, agent-agnostic ticket message."""
    s = t.signal
    c = t.cfg
    sym = c.dxtrade_symbol or f"{s.symbol} (⚠ set DXTrade symbol)"
    daily_cap = 0.03 * c.account_size_usd
    dd_floor = 0.06 * c.account_size_usd
    contract_note = (
        f"{t.qty_units} {s.symbol.replace('USDT','')} "
        f"(= ${t.risk_usd:.2f} risk at the stop; "
        f"convert to DXTrade contracts per the instrument's contract size)"
    )
    lines = [
        "BREAKOUT TRADE SETUP — place a BRACKET order on the Breakout DXTrade terminal.",
        "RULES (do all): attach SL **and** TP at entry; never place without both. "
        "Pause for my confirmation before you submit. If now is past 'Valid until' "
        "OR the live price is outside the entry band, DO NOT place — reply "
        "'skipped: stale/out-of-range'. Do NOT manage the exit yourself — the "
        "bracket is the exit.",
        "",
        f"  Strategy : {s.strategy}",
        f"  Symbol   : {sym}",
        f"  Side     : {t.side} ({s.direction})",
        f"  Size     : {contract_note}",
        f"  Entry    : {s.entry}   (only if live price is within {t.entry_min} … {t.entry_max})",
        f"  Stop     : {s.sl}",
        f"  Target   : {s.tp}   (R:R ≈ {t.rr})",
        "",
        f"  Order type: LIMIT at entry with attached bracket + day/GTD expiry if "
        f"supported (so a stale signal simply doesn't fill); else market ONLY if "
        f"price is in-band.",
        f"  Signal time: {s.signal_time.astimezone(timezone.utc).isoformat()}",
        f"  Valid until: {t.valid_until.astimezone(timezone.utc).isoformat()}",
        "",
        f"  Prop context (${c.account_size_usd:.0f} 1-Step Classic): this risks "
        f"${t.risk_usd:.2f} ({c.risk_pct}% of balance). Account killers — daily "
        f"loss ${daily_cap:.0f} (3%), static drawdown floor ${dd_floor:.0f} (6% "
        f"off start). Breaching either permanently disables the account.",
    ]
    if now is not None and now > t.valid_until:
        lines.insert(2, "  ⚠ THIS TICKET IS ALREADY EXPIRED — do not place.")
    return "\n".join(lines)
