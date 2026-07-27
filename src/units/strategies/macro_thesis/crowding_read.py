"""M36 Track C · C3 — ``crowding_read``: the reductive positioning/over-extension
conditioner (Move B2).

The operator's "how & when other traders trade the move" dimension — **reframed
around the concluded signal research.** The M28 signal-research program found that
positioning/crowding as a **directional** signal is exhausted on free data (COT /
funding / OI all null OOS after cost). So this module does **not** predict a side.
It reads how **over-owned / over-extended** an already-justified thesis's move is,
and uses that **only reductively**: shrink an entry toward a floor, and tighten
(advance) the C2 progress-exit — never enlarge a bet, never pick a direction.

The read blends three **extremity/intensity** inputs (each ∈ [0,1], direction-free,
*injected* by the caller so this module stays pure — no feed fetch, no I/O):

  * **move_extension** — how far/fast price has already run (e.g. from C2's
    ``move_progress`` past target, or a price-stretch z-score → [0,1]).
  * **positioning_extremity** — |COT-spec-net percentile − 0.5|·2, funding
    magnitude, etc. — the *magnitude* of positioning extremity, NOT its sign.
  * **sentiment_intensity** — the M9 news layer's aggregate sentiment
    magnitude/velocity on the theme (a crowded narrative → fragile).

Outputs a ``crowding`` ∈ [0,1] (mean over present inputs, renormalized so a
missing feed strands nothing), a **reductive** ``size_multiplier`` ∈ [floor, 1]
(mirrors ``news_influence`` — 1.0 neutral, ``floor`` at max crowding), and an
``exit_tighten`` ∈ [0,1] the caller folds into the C2 exit. Observe-only; any live
sizing/exit effect is C4-backtest + Tier-3 gated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


def _unit(v: Any) -> Optional[float]:
    """A finite float clamped to [0,1], or None (rejects bool / NaN / inf)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return 0.0 if f < 0.0 else 1.0 if f > 1.0 else f


@dataclass(frozen=True)
class CrowdingRead:
    """The reductive crowding conditioner derived from extremity/intensity inputs."""

    crowding: Optional[float]  # [0,1] blend over present inputs (None if none present)
    size_multiplier: float  # [floor, 1.0] — reductive entry-size factor
    exit_tighten: float  # [0,1] — how much to advance the C2 progress-exit
    inputs: dict  # the present {name: value} that fed the blend
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "crowding": self.crowding,
            "size_multiplier": self.size_multiplier,
            "exit_tighten": self.exit_tighten,
            "inputs": self.inputs,
            "note": self.note,
        }


def crowding_read(
    *,
    move_extension: Any = None,
    positioning_extremity: Any = None,
    sentiment_intensity: Any = None,
    size_floor: float = 0.5,
) -> CrowdingRead:
    """Blend the present extremity inputs into a reductive crowding conditioner.

    Each input is optional and clamped to [0,1]; ``crowding`` is the mean over the
    inputs that are present (renormalize-over-present, so a missing feed doesn't
    drag the score toward 0). No inputs present → ``crowding=None``,
    ``size_multiplier=1.0`` (a no-op), ``exit_tighten=0.0``. ``size_floor`` bounds
    how far a crowded read may shrink an entry (default 0.5 = at most halve).
    """
    present: dict = {}
    for name, raw in (
        ("move_extension", move_extension),
        ("positioning_extremity", positioning_extremity),
        ("sentiment_intensity", sentiment_intensity),
    ):
        u = _unit(raw)
        if u is not None:
            present[name] = u

    if not present:
        return CrowdingRead(None, 1.0, 0.0, {}, note="no crowding inputs — neutral")

    crowding = sum(present.values()) / len(present)
    floor = 0.0 if size_floor < 0.0 else 1.0 if size_floor > 1.0 else size_floor
    # Reductive by construction: 1.0 at crowding 0 → floor at crowding 1.
    size_multiplier = 1.0 - crowding * (1.0 - floor)
    return CrowdingRead(
        crowding=crowding,
        size_multiplier=size_multiplier,
        exit_tighten=crowding,
        inputs=present,
    )


def conditioned_exit(
    progress_action_record: dict,
    read: CrowdingRead,
    *,
    crowded_at: float = 0.6,
    near_target: float = 0.7,
) -> dict:
    """Fold the crowding read into the C2 progress action (observe-only).

    B1 (C2) fires the exit on price PROGRESS; B2 (this) makes it fire **sooner when
    the move is over-owned**: a still-``hold`` thesis that is both **near** its
    target (``move_progress ≥ near_target``) AND **crowded**
    (``exit_tighten ≥ crowded_at``) is upgraded ``hold → trim`` (advance the exit).
    An already-``trim``/``exit`` action is left as-is (crowding never *relaxes* an
    exit — reductive-only). Returns a new record; never mutates the input.
    """
    out = dict(progress_action_record)
    action = out.get("action")
    mp = out.get("move_progress")
    if (
        action == "hold"
        and read.exit_tighten >= crowded_at
        and isinstance(mp, (int, float))
        and not isinstance(mp, bool)
        and mp >= near_target
    ):
        out["action"] = "trim"
        out["reason"] = "crowded + near target — advance exit (crowding conditioner)"
        out["crowding"] = read.crowding
    else:
        out["crowding"] = read.crowding
    return out
