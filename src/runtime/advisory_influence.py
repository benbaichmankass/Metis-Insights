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

Non-negotiables enforced by construction:

- **Reductive-only.** The operator can only make the bot trade *less* —
  veto a trade (``qty → 0``) or leave it unchanged. It can never create a
  trade, enlarge ``qty``, widen the stop, move the take-profit, or flip
  side. The closing guard asserts ``final.qty <= intended.qty`` and that
  every other order field is untouched.
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
from dataclasses import dataclass
from typing import Any, Mapping

from src.core.order_contract import OrderPackage

# Modes. v1 ships `off` (default), `annotate` (attach scores, no order
# effect) and `veto` (suppress when a quorum of advisory models is
# bearish). `size_scale` is reserved for a later, separately-gated step.
_VALID_MODES = frozenset({"off", "annotate", "veto"})


@dataclass(frozen=True)
class AdvisoryPolicy:
    """Per-strategy advisory influence policy (parsed from YAML)."""

    mode: str = "off"
    veto_threshold: float = 0.35
    quorum: int = 1

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(
                f"advisory_policy.mode must be one of {sorted(_VALID_MODES)}; "
                f"got {self.mode!r}"
            )
        if self.quorum < 1:
            raise ValueError(f"advisory_policy.quorum must be >= 1; got {self.quorum}")


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
    return AdvisoryPolicy(
        mode=str(raw.get("mode", "off")),
        veto_threshold=float(raw.get("veto_threshold", 0.35)),
        quorum=int(raw.get("quorum", 1)),
    )


@dataclass(frozen=True)
class InfluenceResult:
    """Outcome of applying advisory influence to one order package."""

    package: OrderPackage
    action: str  # "none" | "annotate" | "veto"
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
    input unchanged or a veto'd copy (``qty == 0``). ``record`` is the
    intended-vs-final audit payload the caller writes to
    ``advisory_decisions.jsonl``.

    The function is total and side-effect-free; the caller decides what to
    do with a veto'd (``is_flat``) package (the execution layer already
    no-ops a flat package).
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
        rec = {**base_record, "action": "annotate"}
        return _identity(annotated, "annotate", rec)

    # mode == "veto": suppress when >= quorum advisory models score below
    # the bearish threshold.
    bearish = [
        mid for mid, score in advisory_scores.items()
        if float(score) < policy.veto_threshold
    ]
    rec = {
        **base_record,
        "veto_threshold": policy.veto_threshold,
        "quorum": policy.quorum,
        "bearish_models": sorted(bearish),
        "bearish_count": len(bearish),
    }
    if len(bearish) >= policy.quorum:
        vetoed = dataclasses.replace(
            package,
            qty=0.0,
            attribution={
                **package.attribution,
                "advisory_veto": {
                    "bearish_models": sorted(bearish),
                    "veto_threshold": policy.veto_threshold,
                    "quorum": policy.quorum,
                    "scores": dict(advisory_scores),
                },
            },
        )
        rec["action"] = "veto"
        rec["final_qty"] = 0.0
        result = InfluenceResult(package=vetoed, action="veto", record=rec)
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
    for fld in ("strategy_id", "symbol", "account_id", "side",
                "entry_price", "stop_loss", "take_profit", "order_type"):
        assert getattr(final, fld) == getattr(intended, fld), (
            f"advisory influence altered '{fld}': "
            f"{getattr(intended, fld)!r} -> {getattr(final, fld)!r}"
        )
