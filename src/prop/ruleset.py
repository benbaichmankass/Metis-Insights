"""Prop-firm ruleset — load + validate a ruleset YAML into a dataclass.

A ruleset is *data, not code* (design §4): adding a second prop firm or
updating Breakout's numbers is a YAML edit, never a code change. This module
parses one of those YAML files into a typed :class:`PropRuleset` with sane
defaults and exposes the ``unconfirmed`` banner flag so every downstream
report can shout "these numbers are placeholders" until the operator verifies
them.

Tier-1 research tooling — no live-path imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


@dataclass
class PhaseRules:
    """Per-phase eval requirements (``phases.evaluation`` / ``phases.funded``)."""

    profit_target_pct: Optional[float] = None
    min_trading_days: int = 0
    max_eval_days: Optional[int] = None


#: Where the drawdown limit measures FROM. Three references, and they are a
#: different question from what a breach DOES (see ``DRAWDOWN_BREACHES``).
#:
#:   ``static``        — a fixed fraction below the STARTING balance. Never
#:                       moves. The prop-firm shape (Breakout's 6% floor).
#:   ``trailing``      — a fraction below the running PEAK equity.
#:   ``intraday_high`` — a fraction below TODAY'S high, reset at UTC midnight.
#:                       This is what ``accounts.yaml::risk.max_dd_pct`` has
#:                       always meant (``src/units/accounts/risk.py``: *"max
#:                       intra-day equity drawdown from today's high"*).
DRAWDOWN_REFERENCES = ("static", "trailing", "intraday_high")

#: What breaching the drawdown limit DOES. Orthogonal to the reference above,
#: and separating them is the entire point of this change.
#:
#:   ``terminal`` — the account is dead. No further trading, ever.
#:   ``refusal``  — ONE trade is refused; the account keeps trading and the
#:                  reference resets on its own schedule.
#:
#: ⚠️ **DO NOT COLLAPSE THESE BACK INTO ONE ENUM.** They were one enum, with
#: two members that both implied ``terminal``, and that is exactly how the
#: defect happened: ``_standard_ruleset`` needed an intraday resetting brake,
#: no member represented it, and the concept was absorbed into ``static`` —
#: a permanent terminal floor, the opposite on BOTH axes. For two months every
#: standard-arm ``p_breach``/``survival`` figure graded a per-trade brake as an
#: account-killer. The lesson, from
#: ``BL-20260827-COMPAT-MATRIX-STANDARD-ARM-BORROWED-A-TYPE``:
#:
#:   *A type system with no member for a new concept will silently absorb it
#:   into the nearest existing member. An enum that CANNOT represent the rule
#:   raises an error; one that APPROXIMATELY can returns a confident wrong
#:   number for two months.*
#:
#: Adding ``intraday_high`` to the reference enum alone would have repeated the
#: mistake one level down — it would have inherited ``terminal``.
DRAWDOWN_BREACHES = ("terminal", "refusal")


@dataclass
class LimitRules:
    """Account limits: where the drawdown measures from, and what a breach does.

    ⚠️ The class docstring used to read *"Account-killer limits — breaching any
    = instant fail"*, which is true of a PROP ruleset and false of a standard
    one. ``drawdown_breach`` is what makes the difference expressible instead
    of assumed.
    """

    daily_loss_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    drawdown_type: str = "static"          # one of DRAWDOWN_REFERENCES
    max_position_pct: Optional[float] = None
    #: One of DRAWDOWN_BREACHES. Defaults to ``terminal`` because every ruleset
    #: that predates this field is a prop ruleset, where terminal is correct —
    #: so the default preserves existing behaviour byte-for-byte. A ruleset
    #: declaring ``intraday_high`` must state it EXPLICITLY (the loader
    #: refuses otherwise), because that is the combination whose consequence
    #: nobody should have to infer.
    drawdown_breach: str = "terminal"

    @property
    def drawdown_is_terminal(self) -> bool:
        """True when breaching the drawdown limit kills the account.

        Read THIS rather than comparing ``drawdown_type`` to ``"static"``:
        the two Monte-Carlo call sites did the latter, so a new reference
        would have fallen into their ``else None`` branch and the limit would
        have been silently dropped from the model.
        """
        return self.drawdown_breach == "terminal"


@dataclass
class ConsistencyRules:
    enabled: bool = False
    max_single_day_profit_share: float = 0.40


@dataclass
class RestrictionRules:
    weekend_flat: bool = False
    overnight_flat: bool = False


@dataclass
class PayoutRules:
    """Firm-side funded-withdrawal rules (``economics.payout``)."""

    first_payout_after_days: float = 14.0
    payout_frequency_days: float = 7.0
    min_withdrawal_usd: float = 0.0
    min_trading_days_before_payout: int = 0
    processing_hours: float = 24.0


@dataclass
class WithdrawalPolicy:
    """OUR banking policy (``economics.withdrawal_policy``) — not a firm rule.

    The default (``bank_asap``) withdraws all equity above the starting balance
    at every allowed window: realised payouts are safe, and the static off-start
    drawdown floor is never raised on payout, so retained profit only adds
    breach exposure for zero upside.
    """

    mode: str = "above_start"
    buffer_usd: float = 0.0
    bank_asap: bool = True
    cadence_days: float = 7.0


@dataclass
class Economics:
    """Account economics for the cost-aware EV evaluation (``economics``)."""

    account_fee_usd: float = 0.0
    rebuy_fee_usd: float = 0.0
    payout: PayoutRules = field(default_factory=PayoutRules)
    withdrawal_policy: WithdrawalPolicy = field(default_factory=WithdrawalPolicy)


@dataclass
class PropRuleset:
    """A fully-parsed prop-firm ruleset.

    Built from the YAML in ``config/prop_rulesets/*.yaml``; see design §4 for
    the schema. Numeric percentage fields are *fractions* (0.10 == 10 %), as in
    the YAML.
    """

    ruleset: str
    plan: str = ""
    account_size_usd: float = 25_000.0
    profit_split: float = 0.80
    unconfirmed: bool = False
    evaluation: PhaseRules = field(default_factory=PhaseRules)
    funded: PhaseRules = field(default_factory=PhaseRules)
    limits: LimitRules = field(default_factory=LimitRules)
    consistency: ConsistencyRules = field(default_factory=ConsistencyRules)
    restrictions: RestrictionRules = field(default_factory=RestrictionRules)
    funded_soak_days: int = 30
    economics: Economics = field(default_factory=Economics)
    raw: Dict[str, Any] = field(default_factory=dict)

    # -- convenience accessors --------------------------------------------
    @property
    def drawdown_type(self) -> str:
        return self.limits.drawdown_type

    def to_dict(self) -> Dict[str, Any]:
        """Compact JSON-serializable view (for embedding in a report)."""
        return {
            "ruleset": self.ruleset,
            "plan": self.plan,
            "account_size_usd": self.account_size_usd,
            "profit_split": self.profit_split,
            "unconfirmed": self.unconfirmed,
            "evaluation": {
                "profit_target_pct": self.evaluation.profit_target_pct,
                "min_trading_days": self.evaluation.min_trading_days,
                "max_eval_days": self.evaluation.max_eval_days,
            },
            "funded": {
                "profit_target_pct": self.funded.profit_target_pct,
                "min_trading_days": self.funded.min_trading_days,
            },
            "limits": {
                "daily_loss_pct": self.limits.daily_loss_pct,
                "max_drawdown_pct": self.limits.max_drawdown_pct,
                "drawdown_type": self.limits.drawdown_type,
                "drawdown_breach": self.limits.drawdown_breach,
                "max_position_pct": self.limits.max_position_pct,
            },
            "consistency": {
                "enabled": self.consistency.enabled,
                "max_single_day_profit_share": self.consistency.max_single_day_profit_share,
            },
            "restrictions": {
                "weekend_flat": self.restrictions.weekend_flat,
                "overnight_flat": self.restrictions.overnight_flat,
            },
            "funded_soak_days": self.funded_soak_days,
            "economics": {
                "account_fee_usd": self.economics.account_fee_usd,
                "rebuy_fee_usd": self.economics.rebuy_fee_usd,
                "payout": {
                    "first_payout_after_days": self.economics.payout.first_payout_after_days,
                    "payout_frequency_days": self.economics.payout.payout_frequency_days,
                    "min_withdrawal_usd": self.economics.payout.min_withdrawal_usd,
                    "min_trading_days_before_payout": self.economics.payout.min_trading_days_before_payout,
                },
                "withdrawal_policy": {
                    "mode": self.economics.withdrawal_policy.mode,
                    "buffer_usd": self.economics.withdrawal_policy.buffer_usd,
                    "bank_asap": self.economics.withdrawal_policy.bank_asap,
                    "cadence_days": self.economics.withdrawal_policy.cadence_days,
                },
            },
        }


class RulesetValidationError(ValueError):
    """Raised when a ruleset YAML is structurally invalid."""


def _as_float(val: Any, ctx: str) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError) as exc:  # noqa: PERF203
        raise RulesetValidationError(f"{ctx}: expected a number, got {val!r}") from exc


def _as_int(val: Any, ctx: str, default: int = 0) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError) as exc:
        raise RulesetValidationError(f"{ctx}: expected an integer, got {val!r}") from exc


def parse_ruleset(data: Dict[str, Any]) -> PropRuleset:
    """Parse an already-loaded YAML mapping into a :class:`PropRuleset`.

    Validates required structure (``ruleset`` name + a ``limits`` block with a
    recognised ``drawdown_type``) and fills every optional field with its
    default. Kept separate from :func:`load_ruleset` so tests can build a
    ruleset from a dict without touching the filesystem.
    """
    if not isinstance(data, dict):
        raise RulesetValidationError("ruleset must be a YAML mapping")

    name = data.get("ruleset")
    if not name or not isinstance(name, str):
        raise RulesetValidationError("ruleset: a non-empty 'ruleset' name is required")

    phases = data.get("phases") or {}
    if not isinstance(phases, dict):
        raise RulesetValidationError("phases: must be a mapping")
    eval_block = phases.get("evaluation") or {}
    funded_block = phases.get("funded") or {}

    evaluation = PhaseRules(
        profit_target_pct=_as_float(eval_block.get("profit_target_pct"), "evaluation.profit_target_pct"),
        min_trading_days=_as_int(eval_block.get("min_trading_days"), "evaluation.min_trading_days"),
        max_eval_days=(
            _as_int(eval_block.get("max_eval_days"), "evaluation.max_eval_days", default=0)
            if eval_block.get("max_eval_days") is not None
            else None
        ),
    )
    funded = PhaseRules(
        profit_target_pct=_as_float(funded_block.get("profit_target_pct"), "funded.profit_target_pct"),
        min_trading_days=_as_int(funded_block.get("min_trading_days"), "funded.min_trading_days"),
    )

    limits_block = data.get("limits") or {}
    if not isinstance(limits_block, dict):
        raise RulesetValidationError("limits: must be a mapping")
    dd_type = str(limits_block.get("drawdown_type", "static")).lower()
    if dd_type not in DRAWDOWN_REFERENCES:
        raise RulesetValidationError(
            f"limits.drawdown_type: must be one of {DRAWDOWN_REFERENCES}, got {dd_type!r}"
        )
    dd_breach_raw = limits_block.get("drawdown_breach")
    if dd_breach_raw is None:
        # ⚠️ REQUIRED for `intraday_high`, defaulted for the other two.
        # An intraday resetting reference with an UNSTATED consequence is the
        # exact ambiguity this field exists to remove — defaulting it to
        # `terminal` would silently rebuild the original defect. static and
        # trailing default because every ruleset written before this field is
        # a prop ruleset, where terminal is what the firm actually does.
        if dd_type == "intraday_high":
            raise RulesetValidationError(
                "limits.drawdown_breach: REQUIRED when drawdown_type is "
                "'intraday_high' — an intraday resetting reference does not "
                "imply a terminal breach, and assuming one is the defect this "
                "field exists to prevent. State 'refusal' or 'terminal'."
            )
        dd_breach = "terminal"
    else:
        dd_breach = str(dd_breach_raw).lower()
        if dd_breach not in DRAWDOWN_BREACHES:
            raise RulesetValidationError(
                f"limits.drawdown_breach: must be one of {DRAWDOWN_BREACHES}, got {dd_breach!r}"
            )
    limits = LimitRules(
        daily_loss_pct=_as_float(limits_block.get("daily_loss_pct"), "limits.daily_loss_pct"),
        max_drawdown_pct=_as_float(limits_block.get("max_drawdown_pct"), "limits.max_drawdown_pct"),
        drawdown_type=dd_type,
        max_position_pct=_as_float(limits_block.get("max_position_pct"), "limits.max_position_pct"),
        drawdown_breach=dd_breach,
    )

    cons_block = data.get("consistency") or {}
    consistency = ConsistencyRules(
        enabled=bool(cons_block.get("enabled", False)),
        max_single_day_profit_share=_as_float(
            cons_block.get("max_single_day_profit_share", 0.40),
            "consistency.max_single_day_profit_share",
        )
        or 0.40,
    )

    restr_block = data.get("restrictions") or {}
    restrictions = RestrictionRules(
        weekend_flat=bool(restr_block.get("weekend_flat", False)),
        overnight_flat=bool(restr_block.get("overnight_flat", False)),
    )

    econ_block = data.get("economics") or {}
    payout_block = (econ_block.get("payout") or {}) if isinstance(econ_block, dict) else {}
    wd_block = (econ_block.get("withdrawal_policy") or {}) if isinstance(econ_block, dict) else {}
    economics = Economics(
        account_fee_usd=_as_float(econ_block.get("account_fee_usd", 0.0), "economics.account_fee_usd") or 0.0,
        rebuy_fee_usd=_as_float(econ_block.get("rebuy_fee_usd", 0.0), "economics.rebuy_fee_usd") or 0.0,
        payout=PayoutRules(
            first_payout_after_days=_as_float(payout_block.get("first_payout_after_days", 14.0), "economics.payout.first_payout_after_days") or 0.0,
            payout_frequency_days=_as_float(payout_block.get("payout_frequency_days", 7.0), "economics.payout.payout_frequency_days") or 7.0,
            min_withdrawal_usd=_as_float(payout_block.get("min_withdrawal_usd", 0.0), "economics.payout.min_withdrawal_usd") or 0.0,
            min_trading_days_before_payout=_as_int(payout_block.get("min_trading_days_before_payout", 0), "economics.payout.min_trading_days_before_payout"),
        ),
        withdrawal_policy=WithdrawalPolicy(
            mode=str(wd_block.get("mode", "above_start")),
            buffer_usd=_as_float(wd_block.get("buffer_usd", 0.0), "economics.withdrawal_policy.buffer_usd") or 0.0,
            bank_asap=bool(wd_block.get("bank_asap", True)),
            cadence_days=_as_float(wd_block.get("cadence_days", 7.0), "economics.withdrawal_policy.cadence_days") or 7.0,
        ),
    )

    return PropRuleset(
        ruleset=name,
        plan=str(data.get("plan", "")),
        account_size_usd=_as_float(data.get("account_size_usd", 25_000.0), "account_size_usd") or 25_000.0,
        profit_split=_as_float(data.get("profit_split", 0.80), "profit_split") or 0.80,
        unconfirmed=bool(data.get("unconfirmed", False)),
        evaluation=evaluation,
        funded=funded,
        limits=limits,
        consistency=consistency,
        restrictions=restrictions,
        funded_soak_days=_as_int(data.get("funded_soak_days", 30), "funded_soak_days", default=30),
        economics=economics,
        raw=dict(data),
    )


def load_ruleset(path: str | Path) -> PropRuleset:
    """Load + validate a ruleset YAML file into a :class:`PropRuleset`."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"ruleset not found: {p}")
    data = yaml.safe_load(p.read_text()) or {}
    return parse_ruleset(data)
