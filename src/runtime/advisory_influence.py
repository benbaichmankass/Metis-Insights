"""Advisory influence operator (WS7 "act" layer — DESIGN sign-off, 2026-05-25).

The one piece the deployment ladder was missing: the mechanism by which
an ``advisory``-stage model actually changes a live order. Until now the
ladder could *observe* (shadow logging) and *decide* (gate-check /
stage-guard) but could not *act* — `log_advisory_scores` is explicitly
"no order action taken."

This module is **rollout step 1** of
`docs/sprint-plans/ai-traders/ws7-advisory-influence-operator-DESIGN.md`:
the pure operator + its config contract + the default-off gate. It is
**not yet wired into any live strategy** — wiring onto a specific
model/strategy is the separately operator-gated enablement step (rollout
step 2+). With the flag off (the default) this code is inert.

Operator decisions (2026-05-25):

- **Downsize, not hard veto.** When a quorum of advisory models is
  bearish, the position is shrunk to a *floor* fraction of the intended
  size — never zeroed. (A hard veto is just ``size_floor = 0``; we do not
  default to it.)
- **Quorum = majority.** By default a *majority* of the wired
  advisory-stage models must be bearish before any downsize applies — not
  a single model.

Non-negotiables enforced by construction:

- **Reductive-only.** The operator can only make the bot trade *less* —
  shrink ``qty`` toward the floor or leave it unchanged. It can never
  create a trade, enlarge ``qty``, widen the stop, move the take-profit,
  or flip side. The closing guard asserts ``final.qty <= intended.qty``
  and that every other order field is untouched.
- **Default off.** Active only when ``ADVISORY_MODE`` is on AND the
  strategy supplies an ``advisory_policy`` with a non-``off`` mode AND at
  least one advisory-stage score is present. Any of those absent =
  identity passthrough.
- **Deterministic fallback.** Empty/`None` scores → identity. The caller
  wraps score collection in the existing per-predictor ``try/except`` so
  a broken model yields no score and the package passes through.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from typing import Any, Mapping

from src.core.order_contract import OrderPackage

# Modes. v1 ships `off` (default), `annotate` (attach scores, no order
# effect) and `downsize` (shrink to a floor when a quorum of advisory
# models is bearish). A graded score→size curve is a later option.
_VALID_MODES = frozenset({"off", "annotate", "downsize"})

# Quorum sentinel: a majority of the advisory models that produced a score.
_MAJORITY = "majority"


@dataclass(frozen=True)
class AdvisoryPolicy:
    """Per-strategy advisory influence policy (parsed from YAML).

    ``quorum`` is either a positive int (exact count of bearish models
    required) or the string ``"majority"`` (default — more than half of
    the advisory models that scored this signal).

    ``size_floor`` is the smallest fraction of the intended size a
    downsize may leave (``0.5`` = at most halve the position; ``0.0`` =
    hard veto, which we do not default to).
    """

    mode: str = "off"
    bearish_threshold: float = 0.35
    size_floor: float = 0.5
    quorum: int | str = _MAJORITY

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(
                f"advisory_policy.mode must be one of {sorted(_VALID_MODES)}; "
                f"got {self.mode!r}"
            )
        if not (0.0 <= self.size_floor <= 1.0):
            raise ValueError(
                f"advisory_policy.size_floor must be in [0, 1]; got {self.size_floor}"
            )
        if isinstance(self.quorum, str):
            if self.quorum != _MAJORITY:
                raise ValueError(
                    f"advisory_policy.quorum string must be {_MAJORITY!r}; "
                    f"got {self.quorum!r}"
                )
        elif self.quorum < 1:
            raise ValueError(f"advisory_policy.quorum must be >= 1; got {self.quorum}")

    def resolve_quorum(self, n_scored: int) -> int:
        """Concrete number of bearish models required, given how many
        advisory models produced a score this tick."""
        if self.quorum == _MAJORITY:
            return n_scored // 2 + 1
        return int(self.quorum)


def parse_policy(cfg: Mapping[str, Any] | None) -> AdvisoryPolicy:
    """Build an :class:`AdvisoryPolicy` from a strategy cfg block.

    A missing or empty ``advisory_policy`` yields the default (mode
    ``off``) — i.e. omitting it opts the strategy out, no influence.
    """
    if not cfg:
        return AdvisoryPolicy()
    raw = cfg.get("advisory_policy")
    if not isinstance(raw, Mapping):
        return AdvisoryPolicy()
    quorum_raw = raw.get("quorum", _MAJORITY)
    quorum: int | str = (
        quorum_raw if isinstance(quorum_raw, str) else int(quorum_raw)
    )
    return AdvisoryPolicy(
        mode=str(raw.get("mode", "off")),
        bearish_threshold=float(raw.get("bearish_threshold", 0.35)),
        size_floor=float(raw.get("size_floor", 0.5)),
        quorum=quorum,
    )


@dataclass(frozen=True)
class InfluenceResult:
    """Outcome of applying advisory influence to one order package."""

    package: OrderPackage
    action: str  # "none" | "annotate" | "downsize"
    record: dict[str, Any]


def _identity(package: OrderPackage, action: str, record: dict[str, Any]) -> InfluenceResult:
    return InfluenceResult(package=package, action=action, record=record)


def apply_advisory_influence(
    package: OrderPackage,
    advisory_scores: Mapping[str, float] | None,
    policy: AdvisoryPolicy,
    *,
    flag_enabled: bool,
) -> InfluenceResult:
    """Apply reductive-only advisory influence to ``package``.

    Returns an :class:`InfluenceResult` whose ``package`` is either the
    input unchanged or a downsized copy (``qty`` scaled to
    ``size_floor * qty``). ``record`` is the intended-vs-final audit
    payload the caller writes to ``advisory_decisions.jsonl``.

    The function is total and side-effect-free.
    """
    if not isinstance(package, OrderPackage):
        raise TypeError(
            f"apply_advisory_influence operates on OrderPackage; "
            f"got {type(package).__name__}"
        )
    base_record: dict[str, Any] = {
        "action": "none",
        "mode": policy.mode,
        "flag_enabled": flag_enabled,
        "model_scores": dict(advisory_scores or {}),
        "intended_qty": package.qty,
        "final_qty": package.qty,
    }
    # Any gate absent → identity passthrough.
    if not flag_enabled or policy.mode == "off" or not advisory_scores:
        return _identity(package, "none", base_record)

    if policy.mode == "annotate":
        annotated = dataclasses.replace(
            package,
            attribution={
                **package.attribution,
                "advisory_scores": dict(advisory_scores),
            },
        )
        return _identity(annotated, "annotate", {**base_record, "action": "annotate"})

    # mode == "downsize": shrink to the floor when >= quorum advisory
    # models score below the bearish threshold.
    n_scored = len(advisory_scores)
    quorum_n = policy.resolve_quorum(n_scored)
    bearish = sorted(
        mid for mid, score in advisory_scores.items()
        if float(score) < policy.bearish_threshold
    )
    rec = {
        **base_record,
        "bearish_threshold": policy.bearish_threshold,
        "size_floor": policy.size_floor,
        "quorum": policy.quorum,
        "quorum_resolved": quorum_n,
        "n_scored": n_scored,
        "bearish_models": bearish,
        "bearish_count": len(bearish),
    }
    if len(bearish) >= quorum_n:
        new_qty = package.qty * policy.size_floor
        downsized = dataclasses.replace(
            package,
            qty=new_qty,
            attribution={
                **package.attribution,
                "advisory_downsize": {
                    "bearish_models": bearish,
                    "bearish_threshold": policy.bearish_threshold,
                    "size_floor": policy.size_floor,
                    "quorum_resolved": quorum_n,
                    "scores": dict(advisory_scores),
                    "intended_qty": package.qty,
                },
            },
        )
        rec["action"] = "downsize"
        rec["final_qty"] = new_qty
        result = InfluenceResult(package=downsized, action="downsize", record=rec)
    else:
        result = _identity(package, "none", rec)

    _assert_reductive(package, result.package)
    return result


def _assert_reductive(intended: OrderPackage, final: OrderPackage) -> None:
    """Defence-in-depth: a model may only make the bot trade LESS.

    Raises ``AssertionError`` if the influence ever increased size or
    altered any risk-bearing field. This is a hard invariant — better to
    crash the tick (deterministic fallback upstream) than to ship an
    order the model enlarged.
    """
    assert abs(final.qty) <= abs(intended.qty) + 1e-12, (
        f"advisory influence increased qty {intended.qty} -> {final.qty}"
    )
    assert math.isfinite(final.qty), f"advisory influence produced non-finite qty {final.qty}"
    for fld in ("strategy_id", "symbol", "account_id", "side",
                "entry_price", "stop_loss", "take_profit", "order_type"):
        assert getattr(final, fld) == getattr(intended, fld), (
            f"advisory influence altered '{fld}': "
            f"{getattr(intended, fld)!r} -> {getattr(final, fld)!r}"
        )
