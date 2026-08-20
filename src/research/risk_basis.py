"""ONE definition of the per-trade risk basis a backtest should use.

WHY THIS EXISTS
---------------
The operator's own example of the modularity failure this repo keeps
paying for: *"the backtest risk and the live config don't match ... either
the way the backtesting is done needs to be independent of what the risk
is set to, or it needs to be made up to date. It needs to, in any case,
check various different risk percentages."*

Measured 2026-08-20, and it is worse than a stale number — **the same
parameter name carries two different UNITS**:

===============  ==========================================  ==============
where            formula                                     unit
===============  ==========================================  ==============
LIVE             ``risk_usdt = balance_usdt * risk_pct``      **fraction**
                 (``src/units/accounts/risk.py``)             0.015 = 1.5%
BACKTEST FLEET   ``(bal * (rpct / 100.0)) / stop_dist``       **percent**
                 (``scripts/backtest_system.py::_risk_qty``)  0.3 = 0.3%
===============  ==========================================  ==============

So ``--risk-pct 0.3`` — the default of ``backtest_system.py``,
``build_backtest_panel.py``, ``walkforward_flip_policy.py``,
``walkforward_netting_guard.py`` and ``allocator_multisymbol_backtest.py``
— is **0.3%**, against a live ``risk_pct: 0.015`` = **1.5%**. Every
default-invocation backtest has been sizing at **one fifth of live risk**.

⚠️ And the comment directly above that formula claims it *"mirrors the
live RiskManager.position_size math: risk_usd = balance * risk_pct"* — it
does not, it inserts a ``/ 100.0``. Field beats comment, at the sizing
basis. A third file, ``scripts/research/pairs_dollar_lots.py``, uses the
FRACTION convention (``risk_pct=0.015``) in the same directory as the
percent ones. Three conventions, one name.

WHAT THIS MODULE GUARANTEES
---------------------------
1. **The live value is READ, never transcribed.** ``live_risk()`` parses
   ``config/accounts.yaml`` — the same field the live sizer reads — so a
   harness default cannot silently drift from production again.
2. **The unit is never implicit.** Callers convert through
   :func:`to_percent` / :func:`to_fraction`; nobody writes another bare
   ``/ 100.0``. That division is the bug, not the fix.
3. **Resolution is three-state, never collapsed** (CLAUDE.md § "Collapsed
   states"). ``resolved`` / ``account_absent`` / ``unreadable`` — a
   harness must be able to tell *"we could not look"* from *"we looked and
   the account declares X"*. There is deliberately **NO fallback constant**:
   a silent default is exactly how 0.3 came to sit five-fold below live and
   stay there.
4. **A grid, not a point.** :func:`risk_grid_percent` brackets live rather
   than asserting it, because the operator's ask is that a backtest report
   how a strategy behaves ACROSS risk settings — a result that only holds
   at one risk_pct is a result about that setting, not about the strategy.

This module reads config and returns numbers. It has no side effects, opens
no socket, and is never on the order path.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

# The two unit conventions in the repo, named so a caller cannot be vague.
UNIT_FRACTION = "fraction"   # live: config/accounts.yaml + RiskManager
UNIT_PERCENT = "percent"     # the backtest fleet: `rpct / 100.0`

# Resolution states. Never collapse these — see the module docstring.
STATE_RESOLVED = "resolved"              # we read the account's declared value
STATE_ACCOUNT_ABSENT = "account_absent"  # we read the file; no such account
STATE_UNREADABLE = "unreadable"          # we could not look

#: The account a backtest should default to reasoning about: the live
#: real-money book. Named rather than inlined so one edit moves every caller.
DEFAULT_REFERENCE_ACCOUNT = "bybit_2"

#: Multipliers applied to the live basis to form the default sweep. Chosen to
#: BRACKET live (below, at, above) rather than to explore exhaustively — the
#: point is to show whether a conclusion survives a change in risk, not to
#: tune risk (which is Tier-3 and not a harness's decision to make).
DEFAULT_GRID_MULTIPLIERS: Tuple[float, ...] = (0.5, 1.0, 2.0)


def to_percent(fraction: float) -> float:
    """fraction (live convention) -> percent (harness convention)."""
    return float(fraction) * 100.0


def to_fraction(percent: float) -> float:
    """percent (harness convention) -> fraction (live convention)."""
    return float(percent) / 100.0


@dataclass(frozen=True)
class LiveRisk:
    """The live per-trade risk basis, with its provenance attached.

    ``fraction`` / ``percent`` are ``None`` unless ``state`` is
    ``resolved`` — a caller that reads the number without checking the
    state gets a ``None`` it must handle, rather than a plausible constant
    it will silently trust.
    """

    state: str
    account_id: str
    fraction: Optional[float] = None
    percent: Optional[float] = None
    source: Optional[str] = None
    detail: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.state == STATE_RESOLVED

    def describe(self) -> str:
        """One line naming the value AND where it came from.

        Harnesses print this beside their results, so a reader can tell a
        run sized at live risk from one sized at a hardcoded default
        without reading the invocation.
        """
        if not self.ok:
            return (f"live risk basis: UNKNOWN ({self.state}"
                    f"{'; ' + self.detail if self.detail else ''}) — "
                    f"account {self.account_id!r}")
        return (f"live risk basis: {self.percent:g}% "
                f"(fraction {self.fraction:g}) from {self.source} "
                f"[account {self.account_id!r}]")


def _accounts_path(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path(__file__).resolve().parents[2] / "config" / "accounts.yaml"


def live_risk(
    account_id: str = DEFAULT_REFERENCE_ACCOUNT,
    *,
    accounts_path: Optional[Path] = None,
) -> LiveRisk:
    """Read the account's declared ``risk.risk_pct`` from accounts.yaml.

    Returns a :class:`LiveRisk` whose ``state`` distinguishes the three
    outcomes. Never raises, and never substitutes a default — a harness
    that cannot read live risk must say so, not quietly proceed.
    """
    path = _accounts_path(accounts_path)

    # PARSE THROUGH THE CANONICAL LOADER, never a hand-rolled yaml.safe_load.
    # `canonical-config-loaders` caught this module doing exactly that on its
    # first guard run — a module written to fix "two definitions of one thing"
    # had itself become a ninth parser of accounts.yaml. Recorded rather than
    # quietly corrected, because it is the same reflex the module is about.
    from src.config.accounts_loader import load_accounts_dict

    # ⚠️ load_accounts_dict returns {} for THREE different reasons and only
    # populates `errors` for one of them (a parse exception): a MISSING file
    # and a non-dict `accounts:` block both return {} silently. So existence is
    # checked here separately — otherwise "we could not look" would collapse
    # into "the account is not declared", which is the distinction this
    # function exists to preserve. Filed as
    # BL-20260820-ACCOUNTS-LOADER-EMPTY-IS-THREE-STATES.
    if not path.exists():
        return LiveRisk(state=STATE_UNREADABLE, account_id=account_id,
                        detail=f"accounts config not found at {path}")

    errors: list = []
    accounts = load_accounts_dict(path, errors=errors)
    if errors:
        return LiveRisk(state=STATE_UNREADABLE, account_id=account_id,
                        detail="; ".join(str(e.get("error")) for e in errors))
    if not accounts:
        # The file exists and parsed, yet yielded no accounts — the config is
        # not usable as a risk reference. Not "this account is absent".
        return LiveRisk(state=STATE_UNREADABLE, account_id=account_id,
                        detail="config parsed but declares no accounts")

    block = accounts.get(account_id)
    if not isinstance(block, dict):
        return LiveRisk(state=STATE_ACCOUNT_ABSENT, account_id=account_id,
                        detail=f"declared accounts: {sorted(accounts)}")

    value = (block.get("risk") or {}).get("risk_pct")
    if not isinstance(value, (int, float)) or float(value) <= 0:
        return LiveRisk(state=STATE_ACCOUNT_ABSENT, account_id=account_id,
                        detail=f"account has no positive risk.risk_pct (got {value!r})")

    frac = float(value)
    return LiveRisk(
        state=STATE_RESOLVED,
        account_id=account_id,
        fraction=frac,
        percent=to_percent(frac),
        source=f"{path.name}::accounts.{account_id}.risk.risk_pct",
    )


def risk_grid_percent(
    account_id: str = DEFAULT_REFERENCE_ACCOUNT,
    *,
    multipliers: Sequence[float] = DEFAULT_GRID_MULTIPLIERS,
    accounts_path: Optional[Path] = None,
) -> Tuple[Optional[Tuple[float, ...]], LiveRisk]:
    """The default risk sweep, in the HARNESS unit (percent).

    Returns ``(grid, live)``. ``grid`` is ``None`` when live risk could not
    be resolved — deliberately, so a caller cannot sweep around a basis
    that was never read. Always returns the :class:`LiveRisk` too, so the
    caller can report WHY there is no grid.
    """
    live = live_risk(account_id, accounts_path=accounts_path)
    if not live.ok or live.percent is None:
        return None, live
    grid = tuple(sorted({round(live.percent * float(m), 6)
                         for m in multipliers if float(m) > 0}))
    return grid, live


def compare_to_live(
    harness_percent: float,
    account_id: str = DEFAULT_REFERENCE_ACCOUNT,
    *,
    accounts_path: Optional[Path] = None,
    tolerance: float = 1e-9,
) -> Dict[str, Any]:
    """Grade a harness's risk setting against live. Reporting only.

    ``verdict`` is ``matches_live`` / ``differs_from_live`` /
    ``live_unknown`` — the third is NOT ``matches``. ``ratio`` is
    harness ÷ live, so 0.2 reads as "this backtest sized at one fifth of
    live risk", which is the sentence the default 0.3 vs 1.5 deserves.
    """
    live = live_risk(account_id, accounts_path=accounts_path)
    out: Dict[str, Any] = {
        "harness_percent": float(harness_percent),
        "live_percent": live.percent,
        "live_state": live.state,
        "account_id": account_id,
        "describe": live.describe(),
    }
    if not live.ok or not live.percent:
        out["verdict"] = "live_unknown"
        out["ratio"] = None
        return out
    ratio = float(harness_percent) / live.percent
    out["ratio"] = ratio
    out["verdict"] = ("matches_live" if abs(ratio - 1.0) <= tolerance
                      else "differs_from_live")
    return out
