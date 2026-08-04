"""The ONE shared execution-realism cost model — fees + slippage + funding.

Design of record: docs/research/FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md § 3.B
(the execution-realism component). This is P1: the *measured* research→results gap
on `htf_pullback_trend_2h` BTC (+0.57R too optimistic vs live) was traced to the
harness modelling **fees only** — no funding (crypto perps pay funding every ~8h; a
multi-bar hold crosses several windows) and no slippage / real-fill cost, which the
real live PnL (exchange-fills / broker-truth) ate. This module is the one place both
the **backtest harness** and the **live sizer/EV scorer** resolve transaction cost, so
a backtest edge is net-of-real-cost *by construction* and can never silently drift
from what live charges (the § 2 fidelity-by-construction principle).

Consolidation (the § 3.B mandate — "one cost model both consult"):
- `DEFAULT_FEE_BPS_ROUNDTRIP` lives HERE now. `src.runtime.allocator_ev` and
  `src.runtime.trade_costs` re-export it, so there is exactly one owner of the
  round-trip fee constant (the `test_regime_debt_matrix_fee` "one owner" contract).
- The R-space cost (`roundtrip_cost_r`) is what the backtest harnesses consult (they
  work in R-multiples of the per-unit stop distance). The USD-space cost
  (`roundtrip_cost_usd`) is the close-path/EV analogue built on the SAME bps.

Cost math (all three terms are a bps-of-notional drag; qty cancels in R-space):
  fee_r       = (fee_bps_roundtrip      / 1e4) · avg_price / risk
  slippage_r  = (slippage_bps_roundtrip / 1e4) · avg_price / risk
  funding_r   = (funding_bps_per_window / 1e4) · n_windows · avg_price / risk
where `avg_price = (entry + exit)/2` (matches the harnesses' existing fee term),
`risk = |entry − sl|` (per-unit R), and `n_windows` = the number of 8h perp-funding
timestamps the hold crossed (exact from entry/exit datetimes, or `hold_hours /
window_hours` as the fractional fallback).

**Funding sign is modelled as a directionless DRAG at P1 (a magnitude cost), NOT a
signed pay/receive.** A signed funding term needs a point-in-time historical funding-
rate feed per (symbol, timestamp); until that feed exists, charging the magnitude is
the conservative, honest choice — it can only make a backtest look *worse* (never
fabricate an edge), the same safety posture the venue-fee resolver takes. A signed
funding feed is the documented P2 upgrade (`docs/research/FAITHFUL-BACKTEST-PLATFORM
-DESIGN-2026-08-04.md` § 3.B).

Pure + fail-permissive: anything un-derivable → a zeroed term (never raises), so a
cost model can never strand a harness row or a live close.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

# ---- the single-owner cost constants ----------------------------------------

# Round-trip taker fee, bps of entry notional. Crypto-perp / futures / fx default;
# commission-free venues resolve to 0 via `profile_loader.roundtrip_fee_bps_for`.
DEFAULT_FEE_BPS_ROUNDTRIP: float = 7.5

# Round-trip slippage (half-spread + market impact), bps of notional. Charged once
# per side (entry fill worse than mid, exit fill worse than mid) → this is the SUM
# of both sides. 5.0 ≈ 2.5 bps/side, a modest-conservative taker estimate for a
# liquid crypto perp; a harness flag / caller can override per venue.
DEFAULT_SLIPPAGE_BPS_ROUNDTRIP: float = 5.0

# Perp funding magnitude per 8h window, bps of notional. ~1 bps ≈ BTC's long-run
# average |funding| per 8h. Directionless drag (see module docstring).
DEFAULT_FUNDING_BPS_PER_WINDOW: float = 1.0

# Length of a perp funding window (hours). Bybit/Binance charge at 00:00/08:00/16:00
# UTC — 8h boundaries aligned to the Unix epoch, so a modulo count is exact.
FUNDING_WINDOW_HOURS: float = 8.0


# ---- venue-aware cost resolution (mandatory-cost policy) ---------------------
# A faithful backtest is net-of-real-cost, but the cost must be VENUE-CORRECT:
# perp funding applies ONLY to crypto perpetual swaps, NOT to dated futures
# (MES/MGC/MHG), equities/ETFs (SPY/GLD), or fx (EURUSD). Charging funding on a
# non-perp fabricates a cost — the same false-drag class as the equity fee bug
# (BL-20260730-RESEARCH-VENUE-FEE). The fee term is already venue-aware via
# ``profile_loader.roundtrip_fee_bps_for``; these mirror it for funding/slippage.

# Crypto PERPETUAL-swap categories in config/instruments.yaml (funding applies).
# Dated `futures` (MES/MGC/MHG), `spot` equities/ETFs, and fx never pay perp funding.
_PERP_CATEGORIES = {"linear", "inverse"}
_PERP_CATEGORY_CACHE: dict[str, str] | None = None


def is_perp(symbol: Any) -> bool:
    """True iff ``symbol`` is a crypto PERPETUAL swap (funding applies). Resolved
    authoritatively from ``config/instruments.yaml`` (``category ∈ {linear,
    inverse}``, mirroring ``profile_loader.roundtrip_fee_bps_for``); falls back to
    the ``*USDT`` heuristic only when the symbol is absent from instruments.yaml.
    Non-perps (futures/spot/equity/fx) → False → zero funding. Memoized process-wide
    (reset ``_PERP_CATEGORY_CACHE`` in tests to force a reload)."""
    global _PERP_CATEGORY_CACHE
    s = str(symbol or "").strip()
    if not s:
        return False
    if _PERP_CATEGORY_CACHE is None:
        try:
            from src.core.profile_loader import load_instrument_profiles
            profiles = load_instrument_profiles()
            _PERP_CATEGORY_CACHE = {
                sym: str(getattr(p, "category", "") or "").lower()
                for sym, p in (profiles or {}).items()
            }
        except Exception:  # allow-silent: best-effort; the *USDT heuristic below is the fallback — research cost model, not a live read-path
            _PERP_CATEGORY_CACHE = {}
    cat = _PERP_CATEGORY_CACHE.get(s)
    if cat is not None:
        return cat in _PERP_CATEGORIES  # authoritative — instruments.yaml wins
    return s.upper().endswith("USDT")  # unknown symbol: crypto perps end in USDT


def funding_bps_per_window_for(symbol: Any) -> float:
    """Venue-aware perp funding: the default per-8h magnitude for a crypto perp,
    ``0.0`` for every non-perp (futures/equity/fx never pay perp funding)."""
    return DEFAULT_FUNDING_BPS_PER_WINDOW if is_perp(symbol) else 0.0


def slippage_bps_roundtrip_for(symbol: Any) -> float:  # noqa: ARG001
    """Round-trip slippage default. Uniform across venues for now (spread + impact
    exists everywhere); a per-venue magnitude is a documented refinement. Takes the
    symbol so the signature is stable when that lands."""
    return DEFAULT_SLIPPAGE_BPS_ROUNDTRIP


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _epoch_seconds(t: Any) -> Optional[float]:
    """Best-effort UTC epoch seconds from a datetime / pandas Timestamp / epoch
    number / ISO string. None if un-parseable (fail-permissive)."""
    if t is None:
        return None
    # datetime / pandas.Timestamp (Timestamp is a datetime subclass).
    if isinstance(t, datetime):
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.timestamp()
    # epoch number (seconds).
    n = _f(t)
    if n is not None and not isinstance(t, str):
        return n
    # ISO-8601 string (accept trailing Z).
    s = str(t).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def funding_windows_crossed(
    entry_time: Any,
    exit_time: Any,
    *,
    window_hours: float = FUNDING_WINDOW_HOURS,
    hold_hours: Optional[float] = None,
) -> float:
    """Number of perp funding timestamps a hold crossed.

    Exact when `entry_time`/`exit_time` resolve to epoch seconds: the count of
    window boundaries `k·W` in the half-open interval `(entry, exit]`, i.e.
    `floor(exit/W) − floor(entry/W)` — funding boundaries are epoch-aligned 8h
    multiples (00:00/08:00/16:00 UTC), so this is precise. Falls back to the
    fractional `hold_hours / window_hours` (the *expected* window count) when the
    datetimes are missing/un-parseable but a hold duration is known. `0.0` when
    nothing is derivable (fail-permissive → no funding charged rather than a guess).
    """
    w = _f(window_hours)
    if w is None or w <= 0:
        return 0.0
    a = _epoch_seconds(entry_time)
    b = _epoch_seconds(exit_time)
    if a is not None and b is not None and b >= a:
        wsec = w * 3600.0
        return float(math.floor(b / wsec) - math.floor(a / wsec))
    hh = _f(hold_hours)
    if hh is not None and hh > 0:
        return hh / w
    return 0.0


def roundtrip_cost_r(
    *,
    entry: Any,
    exit_price: Any,
    risk: Any,
    entry_time: Any = None,
    exit_time: Any = None,
    hold_hours: Optional[float] = None,
    fee_bps_roundtrip: float = DEFAULT_FEE_BPS_ROUNDTRIP,
    slippage_bps_roundtrip: float = 0.0,
    funding_bps_per_window: float = 0.0,
    funding_window_hours: float = FUNDING_WINDOW_HOURS,
) -> dict[str, float]:
    """Per-trade round-trip cost in **R** (multiples of the per-unit stop distance),
    broken into fee / slippage / funding + the total.

    `slippage_bps_roundtrip` and `funding_bps_per_window` default to **0.0** so a
    caller that passes only the fee is byte-identical to the legacy fee-only term —
    execution realism is *opt-in per call* (the harness exposes flags), never a
    silent change to every existing sweep. Negative bps clamp to 0 (a cost never adds
    value). Any un-derivable input → all-zero terms (never raises).
    """
    e = _f(entry)
    x = _f(exit_price)
    rk = _f(risk)
    zero = {"fee_r": 0.0, "slippage_r": 0.0, "funding_r": 0.0,
            "total_cost_r": 0.0, "funding_windows": 0.0}
    if e is None or x is None or rk is None or rk <= 0:
        return zero
    avg_px = (abs(e) + abs(x)) / 2.0
    per_r = avg_px / rk  # price→R conversion of one unit of notional-fraction

    def _bps(v: Any) -> float:
        b = _f(v)
        return 0.0 if (b is None or b < 0) else b

    fee_r = (_bps(fee_bps_roundtrip) / 1.0e4) * per_r
    slip_r = (_bps(slippage_bps_roundtrip) / 1.0e4) * per_r
    n_win = funding_windows_crossed(
        entry_time, exit_time, window_hours=funding_window_hours,
        hold_hours=hold_hours,
    )
    fund_r = (_bps(funding_bps_per_window) / 1.0e4) * n_win * per_r
    return {
        "fee_r": fee_r,
        "slippage_r": slip_r,
        "funding_r": fund_r,
        "total_cost_r": fee_r + slip_r + fund_r,
        "funding_windows": n_win,
    }


def roundtrip_cost_usd(
    *,
    entry_price: Any,
    qty: Any,
    contract_value_usd: Any = 1.0,
    entry_time: Any = None,
    exit_time: Any = None,
    hold_hours: Optional[float] = None,
    fee_bps_roundtrip: float = DEFAULT_FEE_BPS_ROUNDTRIP,
    slippage_bps_roundtrip: float = 0.0,
    funding_bps_per_window: float = 0.0,
    funding_window_hours: float = FUNDING_WINDOW_HOURS,
) -> dict[str, Optional[float]]:
    """USD-space analogue of :func:`roundtrip_cost_r`, built on the SAME bps so the
    close-path/EV cost and the backtest cost can never diverge. `None` terms when
    un-derivable (matches `trade_costs.estimate_roundtrip_fee_usd`).

    `fee_usd = (bps/1e4)·|entry|·|qty|·|cvu|` on the entry notional; slippage the
    same shape; funding scales the notional by the window count. Fee-only default
    (slippage/funding 0.0) keeps `estimate_roundtrip_fee_usd` byte-identical.
    """
    e = _f(entry_price)
    q = _f(qty)
    cvu = _f(contract_value_usd)
    if e is None or q is None or e <= 0 or q <= 0:
        return {"fee_usd": None, "slippage_usd": None, "funding_usd": None,
                "total_cost_usd": None, "funding_windows": 0.0}
    if cvu is None or cvu <= 0:
        cvu = 1.0
    notional = abs(e) * abs(q) * abs(cvu)

    def _bps(v: Any) -> float:
        b = _f(v)
        return 0.0 if (b is None or b < 0) else b

    fee = (_bps(fee_bps_roundtrip) / 1.0e4) * notional
    slip = (_bps(slippage_bps_roundtrip) / 1.0e4) * notional
    n_win = funding_windows_crossed(
        entry_time, exit_time, window_hours=funding_window_hours,
        hold_hours=hold_hours,
    )
    fund = (_bps(funding_bps_per_window) / 1.0e4) * n_win * notional
    return {
        "fee_usd": fee,
        "slippage_usd": slip,
        "funding_usd": fund,
        "total_cost_usd": fee + slip + fund,
        "funding_windows": n_win,
    }
