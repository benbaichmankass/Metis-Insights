"""Account → backtest ruleset resolver — the one map every prop-aware tool uses.

Each account is evaluated and sized against a ruleset (design:
``docs/integrations/prop-accounts-architecture-DESIGN.md``):

- **Prop accounts** (``exchange: breakout`` or an explicit
  ``backtest_ruleset: prop_rulesets/<file>``) → the prop ruleset file (breach
  rules + ``economics`` + target; e.g. ``config/prop_rulesets/breakout.yaml``).
  Evaluated by the cost-aware EV + survival gate.
- **Every other account** → a ``standard`` ruleset synthesized from the account's
  own ``risk`` block (``max_dd_pct`` / ``daily_loss_pct`` / ``risk_pct`` /
  ``pos_size``), with no profit target and no prop economics. Its "compatibility
  test" is the ordinary net-of-fee performance backtest.

This is deliberately **multi-account from day one**: callers iterate
``all_account_units()`` — nothing hardcodes "the prop account" or a single size.
Adding a prop account is an ``accounts.yaml`` entry (+ a ruleset file); zero code
change here.

Tier-1 research/eval tooling — no live order path.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from src.config.accounts_loader import load_accounts_dict
from src.prop.ruleset import (
    Economics,
    LimitRules,
    PhaseRules,
    PropRuleset,
    load_ruleset,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RULESETS_DIR = _REPO_ROOT / "config"

# Default notional for the synthesized `standard` ruleset when an account
# declares no `risk.pos_size` (real accounts size off the live balance at
# runtime; the backtest just needs a consistent notional to scale R-multiples).
_DEFAULT_STANDARD_SIZE = 10_000.0
_DEFAULT_RISK_PCT = 0.5  # percent


@dataclass
class AccountBacktestUnit:
    """How one account is backtested: its ruleset + sizing + which evaluator.

    ``kind`` drives the evaluator the compat matrix uses:
      - ``"prop"``     → cost-aware EV + survival (``montecarlo_prop``).
      - ``"standard"`` → net-of-fee performance backtest (the per-strategy harness).
    ``ruleset`` carries the account's constraints either way (so a standard
    account's daily-loss / max-DD caps are still available to a sizing check).
    """

    account_id: str
    kind: str                       # "prop" | "standard"
    ruleset: PropRuleset
    risk_pct: float                 # PERCENT per trade (e.g. 1.0 == 1%)
    account_size_usd: float
    account_class: str              # "paper" | "real_money"
    source: str                     # ruleset file path, or "standard:<account>"


def _standard_ruleset(account_id: str, risk_block: Dict[str, Any],
                      account_size: float) -> PropRuleset:
    """Synthesize a no-target/no-economics ruleset from an account's risk block.

    The account's own ``max_dd_pct`` / ``daily_loss_pct`` become the (static)
    drawdown + daily-loss limits so a survival/sizing check can still consult
    them; there is no profit target and ``economics`` stays at its zero default
    (a real account is not a disposable, re-buyable prop account).
    """
    return PropRuleset(
        ruleset=f"standard:{account_id}",
        plan="standard",
        account_size_usd=account_size,
        profit_split=1.0,
        evaluation=PhaseRules(profit_target_pct=None, min_trading_days=0),
        limits=LimitRules(
            daily_loss_pct=_as_float(risk_block.get("daily_loss_pct")),
            max_drawdown_pct=_as_float(risk_block.get("max_dd_pct")),
            drawdown_type="static",
        ),
        economics=Economics(),
    )


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _resolve_ruleset_path(spec: str) -> Path:
    """Resolve a ``backtest_ruleset`` spec to a file under config/."""
    p = Path(spec)
    if not p.is_absolute():
        p = _RULESETS_DIR / spec
    return p


def unit_for_account(account_id: str, account: Dict[str, Any]) -> AccountBacktestUnit:
    """Build the :class:`AccountBacktestUnit` for one parsed account mapping."""
    risk_block = account.get("risk") or {}
    risk_pct = _as_float(risk_block.get("risk_pct"))
    if risk_pct is None:
        # The live coordinator builds a FLAT account_cfg (risk_pct at the top
        # level, from account.risk_manager.risk_pct) — NOT nested under a "risk"
        # block like raw accounts.yaml. Without this fallback unit_for_account
        # saw no risk_pct on the runtime path and silently defaulted to
        # _DEFAULT_RISK_PCT (0.5%), so every emitted prop ticket was sized at
        # 0.5% instead of the configured 1.5% (~3x undersized; risk_usd $25 vs
        # the intended $75 on the $5k Breakout account). Tier-3 sizing fix —
        # the compat-matrix path (raw accounts.yaml, nested risk block) is
        # unchanged; this only adds the flat-dict fallback the runtime needs.
        risk_pct = _as_float(account.get("risk_pct"))
    risk_pct = (risk_pct * 100.0) if (risk_pct is not None and risk_pct <= 1.0) else (risk_pct or _DEFAULT_RISK_PCT)
    account_class = str(account.get("account_class") or ("paper" if account.get("demo") else "real_money"))

    # Prop binding: any prop signal (the canonical predicate) binds this
    # account to a prop ruleset; an explicit ``backtest_ruleset`` still names
    # WHICH one below. Single source of truth for the prop test —
    # BL-20260628-PROP-ISPROP-PREDICATE-DRIFT (was a local subset here: it
    # ignored account_class/type and was case-sensitive on exchange).
    from src.prop.prop_identity import is_prop_account
    spec = account.get("backtest_ruleset")
    is_prop = is_prop_account(account)

    if is_prop:
        if not spec or spec == "standard":
            spec = "prop_rulesets/breakout.yaml"
        path = _resolve_ruleset_path(spec)
        rs = load_ruleset(path)
        return AccountBacktestUnit(
            account_id=account_id, kind="prop", ruleset=rs,
            risk_pct=risk_pct, account_size_usd=rs.account_size_usd,
            account_class=account_class, source=str(path),
        )

    # Backtest/compat-matrix notional for a STANDARD account. This is a
    # research sizing input, NOT a live cap (the live notional cap pos_size
    # was removed 2026-06-24). Prefer an explicit ``account_size_usd`` if the
    # risk block declares one, else the standard default; never the removed
    # ``pos_size`` (a per-trade cap conflated with account equity).
    size = _as_float(risk_block.get("account_size_usd")) or _DEFAULT_STANDARD_SIZE
    rs = _standard_ruleset(account_id, risk_block, size)
    return AccountBacktestUnit(
        account_id=account_id, kind="standard", ruleset=rs,
        risk_pct=risk_pct, account_size_usd=size,
        account_class=account_class, source=f"standard:{account_id}",
    )


def all_account_units(accounts_path: Optional[Path] = None) -> Dict[str, AccountBacktestUnit]:
    """Resolve a backtest unit for EVERY account in ``accounts.yaml``.

    Reads through the canonical ``src.config.accounts_loader.load_accounts_dict``
    (the single source of truth — never a hand-rolled parser, per the
    ``canonical-config-loaders`` CI guard). The compat-matrix runner iterates
    this, so a new account is evaluated automatically and a new prop account is
    picked up with no code change.
    """
    accounts = load_accounts_dict(accounts_path)
    out: Dict[str, AccountBacktestUnit] = {}
    for acct_id, acct in accounts.items():
        if isinstance(acct, dict):
            out[acct_id] = unit_for_account(acct_id, acct)
    return out
