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
from src.prop.standard_account_size import (
    load_balance_snapshots,
    resolve_standard_account_size,
)
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
    #: ⚠️ **NULLABLE, and ``None`` means the size could not be ESTABLISHED** —
    #: never "small", never "zero". A prop account always has one (the plan
    #: declares it); a standard account's comes from the balance snapshot and
    #: can legitimately refuse. Check :attr:`gradeable`, not this field.
    account_size_usd: Optional[float]
    account_class: str              # "paper" | "real_money"
    source: str                     # ruleset file path, or "standard:<account>"
    #: Provenance of ``account_size_usd`` — one of
    #: ``standard_account_size.{DECLARED,MEASURED,STALE,UNREADABLE}``. Prop
    #: units report ``declared`` (the plan states the size).
    size_state: str = "declared"
    size_source: str = ""
    size_as_of: Optional[str] = None
    size_age_hours: Optional[float] = None
    size_reason: str = ""

    @property
    def gradeable(self) -> bool:
        """True only when this account can actually be graded.

        ⚠️ **EVERY CONSUMER MUST CHECK THIS BEFORE READING A VERDICT.** Before
        2026-08-27 there was nothing to check: an unestablished size silently
        became ``_DEFAULT_STANDARD_SIZE`` ($10,000), so an ungraded account and
        a graded one produced identically confident rows. That is the whole
        defect — see ``_standard_ruleset``.
        """
        return self.account_size_usd is not None and self.account_size_usd > 0


def _standard_ruleset(account_id: str, risk_block: Dict[str, Any],
                      account_size: float) -> PropRuleset:
    """Synthesize a no-target/no-economics ruleset from an account's risk block.

    The account's own ``max_dd_pct`` / ``daily_loss_pct`` become its limits so
    a survival/sizing check can consult them; there is no profit target and
    ``economics`` stays at its zero default (a real account is not a
    disposable, re-buyable prop account).

    ⚠️ **THE ``max_dd_pct`` MAPPING WAS A SEMANTIC SUBSTITUTION. FIXED
    2026-08-27; the history is kept because the mechanism recurs.**
    ``BL-20260827-STANDARD-ARM-MISMODELS-INTRADAY-MAX-DD-AS-A-TERMINAL-FLOOR``
    and ``BL-20260827-COMPAT-MATRIX-STANDARD-ARM-BORROWED-A-TYPE-WITH-NO-MEMBER-FOR-ITS-CONCEPT``.

    This function used to build ``drawdown_type="static"``: a **permanent**
    floor a fixed fraction below the **starting balance**, which the evaluator
    treats as terminal — the prop-firm rule. What ``max_dd_pct`` actually means
    on a standard account is the opposite on both axes
    (``src/units/accounts/risk.py``, and the ``accounts.yaml`` header):
    *"max **INTRA-DAY** equity drawdown **from today's high**"* — it resets at
    UTC midnight, it measures from a rolling daily high rather than from the
    start, and breaching it **refuses one trade**; it never disables the
    account. So the arm graded a resetting per-trade brake as an
    account-killer, and every standard ``p_breach``/``survival`` figure
    inherited that.

    **Why it happened, which is the transferable part:** the 2026-06-17 design
    says twice that a standard account gets *"a no-breach ruleset"*.
    ``PB-20260618-012`` then deliberately added survival/breach so *"a
    positive-but-fragile cell can't route onto live capital"* — sound intent.
    But ``PropRuleset`` offered only ``static`` and ``trailing``, neither of
    which is an intraday resetting brake, so the new concept was absorbed into
    the nearest member.

      *A type system with no member for a new concept will silently absorb it
      into the nearest existing member. An enum that CANNOT represent the rule
      raises an error; one that APPROXIMATELY can returns a confident wrong
      number for two months.*

    **What it builds now:** ``drawdown_type="intraday_high"`` with an explicit
    ``drawdown_breach="refusal"``. The reference and the consequence are
    separate fields precisely so the second cannot be inherited by accident —
    adding ``intraday_high`` to the single old enum would have repeated the
    mistake one level down. See ``ruleset.DRAWDOWN_REFERENCES`` /
    ``DRAWDOWN_BREACHES``.

    ⚠️ **THE SURVIVAL GATE IS NOT RETIRED, AND MUST NOT BE.** An earlier
    suggestion in the 2026-08-27 session that it be dropped was withdrawn: the
    fragility question is the only thing standing between a
    positive-but-fragile cell and live capital. What was wrong was the MODEL,
    not the intent to model it.

    ⚠️ **A ``refusal`` LIMIT IS NOT MODELLED BY ``p_breach``.** The Monte Carlo
    reports ``dd_model_state: "not_terminal"`` for this shape, meaning the
    drawdown contributed nothing to that figure. Read the state beside the
    number; ``evaluate()``'s ``drawdown_refusals`` block is what measures this
    limit, as a refusal RATE rather than a breach probability.
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
            drawdown_type="intraday_high",
            drawdown_breach="refusal",
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


def unit_for_account(
    account_id: str,
    account: Dict[str, Any],
    *,
    snapshots: Optional[Dict[str, Dict[str, Any]]] = None,
    size_override: Optional[float] = None,
) -> AccountBacktestUnit:
    """Build the :class:`AccountBacktestUnit` for one parsed account mapping.

    ``snapshots`` is the balance-snapshot mapping used to size a STANDARD
    account. Passing ``None`` means *the caller could not read the store*,
    which refuses — deliberately distinct from ``{}`` (read, empty). Callers
    that grade many accounts should read it ONCE and pass it in, rather than
    letting each account re-open the DB.
    """
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

    # Grading size for a STANDARD account. This is a research sizing input,
    # NOT a live cap (the live notional cap pos_size was removed 2026-06-24).
    #
    # ⚠️ **THERE IS NO DEFAULT ANY MORE, DELIBERATELY.** This line used to read
    # ``_as_float(risk_block.get("account_size_usd")) or _DEFAULT_STANDARD_SIZE``
    # and 0 of 11 accounts declare that key, so EVERY standard account was
    # graded against a synthetic $10,000 while real balances spanned $200.10 to
    # $1,341,065.16 — a 6,700x range, wrong in both directions by one to two
    # orders of magnitude. Risk per trade is ``risk_pct x size`` and the
    # drawdown floor is a fraction of the same size, so the placeholder did not
    # nudge the survival figures, it decided them.
    #
    # An unresolvable size now REFUSES: the unit comes back with
    # ``account_size_usd=None`` and ``gradeable == False``. A default is what
    # made the original defect invisible — every row carried a confident
    # number, so nothing in the output distinguished a graded account from an
    # ungraded one.
    sz = resolve_standard_account_size(
        account_id, risk_block, snapshots=snapshots, override_usd=size_override
    )
    rs = _standard_ruleset(account_id, risk_block, sz.size_usd or 0.0)
    return AccountBacktestUnit(
        account_id=account_id, kind="standard", ruleset=rs,
        risk_pct=risk_pct, account_size_usd=sz.size_usd,
        account_class=account_class, source=f"standard:{account_id}",
        size_state=sz.size_state, size_source=sz.size_source,
        size_as_of=sz.size_as_of, size_age_hours=sz.size_age_hours,
        size_reason=sz.reason,
    )


def all_account_units(
    accounts_path: Optional[Path] = None,
    *,
    snapshots: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, AccountBacktestUnit]:
    """Resolve a backtest unit for EVERY account in ``accounts.yaml``.

    Reads through the canonical ``src.config.accounts_loader.load_accounts_dict``
    (the single source of truth — never a hand-rolled parser, per the
    ``canonical-config-loaders`` CI guard). The compat-matrix runner iterates
    this, so a new account is evaluated automatically and a new prop account is
    picked up with no code change.
    """
    accounts = load_accounts_dict(accounts_path)
    # ONE balance read for the whole roster, not one per account.
    snaps = load_balance_snapshots() if snapshots is None else snapshots
    out: Dict[str, AccountBacktestUnit] = {}
    for acct_id, acct in accounts.items():
        if isinstance(acct, dict):
            out[acct_id] = unit_for_account(acct_id, acct, snapshots=snaps)
    return out
