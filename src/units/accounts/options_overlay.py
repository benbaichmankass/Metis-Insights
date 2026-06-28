"""Account-scoped options EXPRESSION — turn an equity order package into a vertical.

Phase-1 Slice-3b of the Alpaca L3 options build (docs/research/alpaca-options-PHASE1-spec.md).

This is the seam between the strategy-agnostic execution layer and the dormant
options pipeline. An Alpaca account may declare it *expresses* its orders as
defined-risk debit verticals (an account-level capability — NOT a strategy change;
the GDX/SLV equity strategies stay pure signal generators). When such an account
receives an order package, `execute_pkg` routes it here instead of the equity bracket:

    equity OrderPackage (symbol, direction, entry)
      → build chain (AlpacaOptionsData)
      → select_debit_vertical()            (options_selector)
      → size_debit_structure(budget)       (options_sizing)
      → place_spread(legs)                 (AlpacaOptionsExecutor)

`place_options_expression` does live I/O (it composes the tested pure pieces); the
pure parts — `account_expresses_options` (the gate) and `build_chain_from_responses`
(the contracts+snapshot join) — are unit-tested directly, and the live path is tested
with injected fake clients.

Scope (Slice 3b): OPEN a debit vertical in PAPER. Closing / expiry / assignment is the
poll-based Slice-4 monitor; until then a paper spread rides to expiry (acceptable for a
paper soak). Refusals (no chain / no fit / budget) place NOTHING and are reported back
so `execute_pkg` journals a rejection row — never a fabricated trade.
"""
from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.units.accounts.alpaca_options_data import AlpacaOptionsData
from src.units.accounts.alpaca_options_exec import AlpacaOptionsExecutor
from src.units.accounts.options_selector import (
    ChainContract,
    DebitVertical,
    select_debit_vertical,
    to_option_legs,
)
from src.units.accounts.options_sizing import size_debit_structure

logger = logging.getLogger(__name__)


_CANON_ACCOUNTS_CACHE: Optional[Dict[str, Any]] = None


def _canonical_options_block(account_id: Optional[str]) -> Any:
    """The ``options`` block for *account_id* from the canonical accounts.yaml.

    Cached for the process lifetime (accounts.yaml only changes via deploy+restart).
    Fail-safe: any load error → None (treated as not-expressing). Lets
    ``account_expresses_options`` stay correct even when handed a STRIPPED cfg that
    omitted the block.
    """
    if not account_id:
        return None
    global _CANON_ACCOUNTS_CACHE
    if _CANON_ACCOUNTS_CACHE is None:
        try:
            from src.config.accounts_loader import load_accounts_dict
            _CANON_ACCOUNTS_CACHE = load_accounts_dict() or {}
        except Exception:  # noqa: BLE001 — never let a config-load error gate a read
            _CANON_ACCOUNTS_CACHE = {}
    cfg = _CANON_ACCOUNTS_CACHE.get(str(account_id)) or {}
    return cfg.get("options")


def account_expresses_options(account_cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the options-expression config block for *account_cfg*, or None.

    An account opts in with an ``options:`` block whose ``express_as`` is a supported
    structure (today only ``debit_vertical``) and which is not explicitly disabled.
    None → the account uses the normal (equity) execution path. Never raises.

    ROBUSTNESS (INCIDENT 2026-06-27): several callers hand this a STRIPPED cfg with a
    fixed key set that omits ``options`` (the monitor's ``_load_account_cfgs_for_reconcile``
    and the coordinator's per-account cfg). When the ``options`` key is ABSENT we resolve
    it from the CANONICAL accounts.yaml by ``account_id`` — otherwise the reconciler
    mis-classified ``alpaca_options_paper`` as equity and adopted the shared paper
    login's equity positions as phantom orphans. An EXPLICIT ``options`` value (incl.
    ``None``) is honoured as-is, so a deliberate opt-out is never overridden.
    """
    opt = account_cfg.get("options")
    if opt is None and "options" not in account_cfg:
        opt = _canonical_options_block(account_cfg.get("account_id"))
    if not isinstance(opt, dict):
        return None
    if opt.get("enabled") is False:
        return None
    if str(opt.get("express_as") or "").strip().lower() != "debit_vertical":
        return None
    return opt


def build_chain_from_responses(
    contracts_payload: Dict[str, Any],
    snapshots_payload: Dict[str, Any],
) -> List[ChainContract]:
    """Join a ``/v2/options/contracts`` payload with a snapshots payload → ChainContracts.

    Pure. Each contract is matched to its snapshot by OCC symbol; ``mid`` comes from the
    snapshot's latest quote (None when unquotable → the selector skips it), ``delta``/``iv``
    from greeks when present. Contracts with no snapshot still appear with ``mid=None``.
    """
    snaps = (snapshots_payload or {}).get("snapshots") or {}
    out: List[ChainContract] = []
    for c in (contracts_payload or {}).get("option_contracts") or []:
        sym = c.get("symbol")
        if not sym:
            continue
        try:
            strike = float(c.get("strike_price"))
        except (TypeError, ValueError):
            continue
        snap = snaps.get(sym) or {}
        greeks = snap.get("greeks") or {}
        try:
            oi = int(c.get("open_interest")) if c.get("open_interest") is not None else None
        except (TypeError, ValueError):
            oi = None
        out.append(
            ChainContract(
                symbol=sym,
                type=str(c.get("type") or "").lower(),
                strike=strike,
                expiration=str(c.get("expiration_date") or ""),
                mid=AlpacaOptionsData.quote_mid(snap),
                delta=greeks.get("delta"),
                iv=snap.get("impliedVolatility"),
                open_interest=oi,
            )
        )
    return out


def options_structure_dict(result: "OptionsExpressionResult") -> Dict[str, Any]:
    """The persisted structure detail for a placed expression (Slice-5 surfacing).

    Pure — turns an :class:`OptionsExpressionResult` into the compact JSON block stored
    in the trade row's notes (``notes.options``) and surfaced on ``/api/bot/positions``
    so the dashboard + Android can render the legs / strikes / defined risk without a
    live broker call. Per-leg live greeks/PnL are a documented follow-up (the positions
    endpoint is connection-free by contract); this captures the decision-time geometry.
    """
    v = result.vertical
    legs_out: List[Dict[str, Any]] = []
    strike_by_symbol: Dict[str, Any] = {}
    type_by_symbol: Dict[str, Any] = {}
    if v is not None:
        for cc in (v.long_leg, v.short_leg):
            if cc is not None:
                strike_by_symbol[cc.symbol] = cc.strike
                type_by_symbol[cc.symbol] = cc.type
    for leg in result.legs or []:
        sym = getattr(leg, "symbol", None)
        legs_out.append({
            "symbol": sym,
            "side": getattr(leg, "side", None),
            "intent": getattr(leg, "position_intent", None),
            "ratio": getattr(leg, "ratio_qty", 1),
            "strike": strike_by_symbol.get(sym),
            "type": type_by_symbol.get(sym),
        })
    out: Dict[str, Any] = {
        "structure": "debit_vertical",
        "contracts": int(result.contracts or 0),
        "net_debit": round(float(result.net_debit or 0.0), 4),
        "max_loss_usd": round(float(result.max_loss_usd or 0.0), 2),
        "legs": legs_out,
    }
    if v is not None:
        out["width"] = v.width
        out["max_gain_usd"] = v.max_gain_usd
        out["breakeven"] = v.breakeven
        out["expiration"] = v.expiration
    return out


@dataclass
class OptionsExpressionResult:
    """Outcome of an options expression attempt."""

    refused: bool
    reason: Optional[str] = None
    trade_id: Optional[str] = None
    contracts: int = 0
    net_debit: float = 0.0
    max_loss_usd: float = 0.0
    vertical: Optional[DebitVertical] = None
    legs: List[Any] = field(default_factory=list)


def _strike_band(entry: float, opt_type: str, span_pct: float = 0.12) -> Dict[str, float]:
    """A modest strike window around the underlying for contract discovery."""
    lo = round(entry * (1.0 - span_pct), 2)
    hi = round(entry * (1.0 + span_pct), 2)
    return {"strike_price_gte": lo, "strike_price_lte": hi}


def place_options_expression(
    pkg: Any,
    options_cfg: Dict[str, Any],
    *,
    exchange_client: Any = None,
    data_client: Optional[AlpacaOptionsData] = None,
    exec_client: Optional[AlpacaOptionsExecutor] = None,
    today: Optional[_dt.date] = None,
    is_dry: bool = False,
) -> OptionsExpressionResult:
    """Express *pkg*'s direction as a debit vertical and (unless dry) place it.

    Returns an :class:`OptionsExpressionResult`. ``refused=True`` (with a reason) means
    nothing was placed — a clean no-trade the caller journals as a rejection. On a dry
    run the spread is selected + sized but not placed (``trade_id`` is a ``dry-`` marker).

    Clients are derived from the equity ``exchange_client``'s resolved creds/env so a
    dedicated ``alpaca_options_paper`` account uses its own key pair; they can be injected
    for tests.
    """
    # Resolve creds/env from the equity client (so the account's own key pair is reused).
    env = getattr(exchange_client, "env", None) or "paper"
    api_key = getattr(exchange_client, "api_key", None)
    api_secret = getattr(exchange_client, "api_secret", None)
    if data_client is None:
        data_client = AlpacaOptionsData(api_key=api_key, api_secret=api_secret, env=env,
                                        feed=str(options_cfg.get("data_feed") or "indicative"))
    if exec_client is None:
        exec_client = AlpacaOptionsExecutor(api_key=api_key, api_secret=api_secret, env=env)

    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    direction = str(getattr(pkg, "direction", "") or "").lower()
    underlying = str(getattr(pkg, "symbol", "") or "")
    entry = float(getattr(pkg, "entry", 0.0) or 0.0)
    if entry <= 0 or not underlying or direction not in ("long", "short"):
        return OptionsExpressionResult(True, reason=f"bad_package:{underlying}/{direction}/{entry}")

    opt_type = "call" if direction == "long" else "put"
    target_dte = int(options_cfg.get("target_dte", 35))
    min_dte = int(options_cfg.get("min_dte", 21))
    max_dte = int(options_cfg.get("max_dte", 60))
    budget = float(options_cfg.get("max_loss_per_trade_usd", 60.0))
    max_iv_rank = options_cfg.get("max_iv_rank")  # optional; None → ungated

    # 1. Discover contracts + snapshot the chain.
    contracts_env = data_client.list_option_contracts(
        underlying,
        expiration_date_gte=today.isoformat(),
        expiration_date_lte=(today + _dt.timedelta(days=max_dte)).isoformat(),
        contract_type=opt_type,
        **_strike_band(entry, opt_type),
        limit=200,
    )
    if contracts_env.get("retCode") != 0:
        return OptionsExpressionResult(True, reason=f"contracts_read_failed:{contracts_env.get('retMsg')}")
    snap_env = data_client.snapshots(underlying, limit=200)
    if snap_env.get("retCode") != 0:
        return OptionsExpressionResult(True, reason=f"snapshot_read_failed:{snap_env.get('retMsg')}")

    chain = build_chain_from_responses(contracts_env.get("result") or {}, snap_env.get("result") or {})
    if not chain:
        return OptionsExpressionResult(True, reason="empty_chain")

    # 2. Select the debit vertical.
    vertical = select_debit_vertical(
        chain, direction=direction, underlying_price=entry, today=today,
        target_dte=target_dte, min_dte=min_dte, max_dte=max_dte,
        max_iv_rank=max_iv_rank,
    )
    if not vertical.ok:
        return OptionsExpressionResult(True, reason=f"no_selection:{vertical.reason}")

    # 3. Size by max-loss budget.
    sized = size_debit_structure(net_debit=vertical.net_debit, max_loss_budget_usd=budget)
    if sized.refused:
        return OptionsExpressionResult(True, reason=f"sizing:{sized.reason}", vertical=vertical)

    legs = to_option_legs(vertical)

    # 4. Place (or simulate on dry).
    if is_dry:
        return OptionsExpressionResult(
            False, reason="dry_run", trade_id=None, contracts=sized.contracts,
            net_debit=vertical.net_debit, max_loss_usd=sized.total_max_loss_usd,
            vertical=vertical, legs=legs,
        )

    resp = exec_client.place_spread(
        legs, qty=sized.contracts, order_type="limit", limit_price=vertical.net_debit,
    ) or {}
    if resp.get("retCode") != 0:
        return OptionsExpressionResult(True, reason=f"place_failed:{resp.get('retMsg')}", vertical=vertical)
    order_id = (resp.get("result") or {}).get("orderId")
    logger.info(
        "options expression placed: %s %s vertical %s/%s x%d net_debit=%.2f max_loss=$%.2f exp=%s",
        underlying, direction, vertical.long_leg.symbol, vertical.short_leg.symbol,
        sized.contracts, vertical.net_debit, sized.total_max_loss_usd, vertical.expiration,
    )
    return OptionsExpressionResult(
        False, reason=None, trade_id=str(order_id or ""), contracts=sized.contracts,
        net_debit=vertical.net_debit, max_loss_usd=sized.total_max_loss_usd,
        vertical=vertical, legs=legs,
    )
