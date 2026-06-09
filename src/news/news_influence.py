"""News influence operator — the graduated "act" layer for the M9 news layer.

Today the live path only acts on the news **veto** (a hard skip). This module
is the graduated, *reductive* alternative the operator asked for: instead of a
blunt block, reason about whether the news — and any imminent high-impact event
— **supports the trade's direction or threatens to knock it off course**, and
shrink the position accordingly.

It mirrors `src/runtime/advisory_influence.py` exactly in posture:

- **Reductive-only.** Returns a size *factor* in ``[size_floor, 1.0]``. It can
  only ever make the bot trade *less* — never enlarge a position, never create
  one, never touch entry/SL/TP/side. A closing guard asserts ``0 <= factor <= 1``.
- **Default off + inert.** Gated by ``NEWS_INFLUENCE_MODE`` (off / annotate /
  downsize, default off). With the gate off it returns ``1.0`` before doing any
  work. **Not wired into the order path yet** — wiring it into
  ``Coordinator.multi_account_execute`` (alongside the advisory downsize) is the
  separately operator-gated next step (Tier-3).
- **Never raises in the caller's hot path** — the wiring layer wraps it.

Decision model (the operator's reframe — a *consideration*, not a blackout)
---------------------------------------------------------------------------
``adjustment`` from ``score_news`` is net news sentiment in ``[-1, 1]`` (positive
= bullish). Combine it with the trade side to get **directional alignment**:

    side_sign  = +1 for a buy, -1 for a sell
    alignment  = adjustment * side_sign     # +1 = news fully backs the trade,
                                            # -1 = news fully against it
    opposition = max(0, -alignment)         # 0 when aligned/neutral, up to 1 opposed

An optional ``event_risk`` in ``[0, 1]`` expresses how much an imminent scheduled
event (CPI, FOMC, NFP, EIA, …) could knock this trade off course. It is
**discounted when the trade is already aligned** with the prevailing news
direction (the event likely pushes our way) and counts in full when the trade is
opposed or the direction is unclear:

    threat = clamp(opposition + event_risk_weight * event_risk * (1 - max(0, alignment)), 0, 1)
    factor = 1.0 - threat * (1.0 - size_floor)        # in [size_floor, 1.0]

So: opposed news → downsize; imminent event + not-aligned → downsize more;
trade aligned with both news and event → ``factor == 1.0`` (left untouched).

``event_risk`` is an **injected scalar** here — computing it from a real
economic-calendar feed (impact × proximity, direction-aware) is the documented
follow-up; this module stays pure and unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

_VALID_MODES = frozenset({"off", "annotate", "downsize"})


@dataclass(frozen=True)
class NewsInfluencePolicy:
    """Per-strategy news influence policy (parsed from a cfg block).

    ``size_floor`` is the smallest fraction of the intended size a downsize may
    leave (``0.5`` = at most halve; ``0.0`` = may zero out — we do not default
    to that; the hard veto is a separate, existing gate).
    ``oppose_threshold`` is the dead-band: opposition below it is treated as
    neutral so faint noise never resizes a trade.
    ``event_risk_weight`` scales how strongly an imminent event downsizes.
    """

    mode: str = "off"
    size_floor: float = 0.5
    oppose_threshold: float = 0.05
    event_risk_weight: float = 0.5

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(
                f"news_influence.mode must be one of {sorted(_VALID_MODES)}; got {self.mode!r}"
            )
        if not (0.0 <= self.size_floor <= 1.0):
            raise ValueError(f"news_influence.size_floor must be in [0, 1]; got {self.size_floor}")
        if not (0.0 <= self.oppose_threshold <= 1.0):
            raise ValueError(
                f"news_influence.oppose_threshold must be in [0, 1]; got {self.oppose_threshold}"
            )
        if self.event_risk_weight < 0.0:
            raise ValueError(
                f"news_influence.event_risk_weight must be >= 0; got {self.event_risk_weight}"
            )


def parse_policy(cfg: Optional[Dict[str, Any]]) -> NewsInfluencePolicy:
    """Build a :class:`NewsInfluencePolicy` from a cfg block.

    A missing/empty ``news_influence`` block yields the default (mode ``off``) —
    omitting it opts out, no influence.
    """
    if not cfg:
        return NewsInfluencePolicy()
    raw = cfg.get("news_influence")
    if not isinstance(raw, dict):
        return NewsInfluencePolicy()
    return NewsInfluencePolicy(
        mode=str(raw.get("mode", "off")),
        size_floor=float(raw.get("size_floor", 0.5)),
        oppose_threshold=float(raw.get("oppose_threshold", 0.05)),
        event_risk_weight=float(raw.get("event_risk_weight", 0.5)),
    )


def _side_sign(side: Optional[str]) -> int:
    return 1 if str(side or "").lower() == "buy" else -1


def news_size_factor(
    adjustment: float,
    side: Optional[str],
    policy: NewsInfluencePolicy,
    *,
    flag_enabled: bool,
    event_risk: float = 0.0,
) -> tuple[float, Dict[str, Any]]:
    """Reductive size multiplier in ``[size_floor, 1.0]`` for ``(adjustment, side)``.

    ``1.0`` = no change (returned whenever the flag is off, the mode isn't
    ``downsize``, or the trade is aligned/neutral with no event threat).
    Returns ``(factor, record)``; ``record`` is the audit payload. Total and
    side-effect-free.
    """
    record: Dict[str, Any] = {
        "action": "none",
        "mode": policy.mode,
        "flag_enabled": flag_enabled,
        "adjustment": adjustment,
        "side": side,
        "event_risk": event_risk,
        "factor": 1.0,
    }
    if not flag_enabled or policy.mode == "off":
        return 1.0, record
    if policy.mode == "annotate":
        record["action"] = "annotate"
        return 1.0, record

    try:
        adj = float(adjustment)
    except (TypeError, ValueError):
        adj = 0.0
    ev = max(0.0, min(1.0, float(event_risk or 0.0)))

    alignment = max(-1.0, min(1.0, adj * _side_sign(side)))
    opposition = max(0.0, -alignment)
    if opposition < policy.oppose_threshold:
        opposition = 0.0

    # Event risk counts most when the trade is NOT aligned with the news.
    event_component = policy.event_risk_weight * ev * (1.0 - max(0.0, alignment))
    threat = max(0.0, min(1.0, opposition + event_component))

    factor = 1.0 - threat * (1.0 - policy.size_floor)
    factor = _clamp_reductive(factor)

    record.update(
        {
            "action": "downsize" if factor < 1.0 else "none",
            "alignment": round(alignment, 6),
            "opposition": round(opposition, 6),
            "event_component": round(event_component, 6),
            "threat": round(threat, 6),
            "size_floor": policy.size_floor,
            "factor": round(factor, 6),
        }
    )
    return factor, record


def _clamp_reductive(factor: float) -> float:
    """Defence-in-depth: the factor can only ever shrink the order.

    Raises ``AssertionError`` on a non-finite factor; clamps to ``[0, 1]`` so a
    miscalibrated policy can never enlarge a live position.
    """
    import math

    assert math.isfinite(factor), f"news influence produced non-finite factor {factor}"
    return max(0.0, min(1.0, factor))
