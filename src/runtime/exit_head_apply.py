"""M20 E3 — the SHARED exit-head APPLY decision, used by every strategy unit
that consumes the exit head.

⚠️ **THIS IS A MOVE, NOT A NEW POLICY.** The body below was factored out of
``trend_donchian._exit_head_verdict`` (live since 2026-07-12, #6211/#6216/
#6217) **verbatim**, so that the second consumer — ``ict_scalp``, MI-150 —
mirrors the donchian decision instead of re-implementing it. A second copy of
this logic is how the two drift, and this repo has paid for that class
repeatedly (the ``_classify_broker_exit`` / ``bracket_outcome`` duplication
MI-144 had to pin with a test; the ``_stale_stop_verdict`` /
``_giveback_verdict`` bodies already moved to ``exit_levers.py`` for the same
reason, and that shim is the precedent this module copies).

``trend_donchian._exit_head_verdict`` is now a thin delegation to
:func:`exit_head_verdict`, so the move is reviewable **as a move** — the
donchian unit's behaviour is unchanged — and
``tests/test_exit_head_apply_parity.py`` runs BOTH call sites over the SAME
case table so they cannot diverge silently.

---

## What this function is, and what it is NOT

It is the **decision**: given a fresh score record and the leg's declared
config, should this trade close now? It is a **pure function** — no I/O, no
clock, no broker — so the policy is arguable in tests rather than against a
live position. That is the same discipline ``protection_reassert`` and
``stray_oca_groups`` adopted after
``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG``.

It is **NOT** the in-distribution guard. That lives one layer up, inside
``exit_head_shadow.maybe_score_exit_head``, which refuses to produce a ``rec``
at all for a leg outside the artifact's declared ``(tf, symbols, family)``.
A consumer that calls the shared scorer inherits that guard **by
construction** — which is the single most important reason MI-150 wires
``ict_scalp`` through ``maybe_score_exit_head`` rather than scoring itself.

## Every gate, all required

- ``rec`` — a fresh score from ``maybe_score_exit_head`` (``None`` on any
  scoring skip, incl. the once-per-closed-bar dedup, so the decision is
  evaluated once per bar, matching the trained policy's cadence).
- ``exit_head_action: close`` declared in meta (new packages) or cfg (live
  YAML — covers already-open packages via the monitor's live-cfg default).
- artifact ``stage == "advisory"`` — the operator promotion gate; a
  shadow-stage artifact NEVER closes anything.
- optional ``exit_head_model`` pin must match the artifact's model_id.
- the conditional policy fires, per the artifact's declared SHAPE.

Fail-closed on anything missing or malformed (returns ``None``); **never
raises**. A spurious close is the expensive failure here, so every ambiguity
resolves to *do nothing*.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

__all__ = ["exit_head_verdict"]


def _coerce_float(value: Any) -> Optional[float]:
    """Finite float or ``None``.

    NaN and ±inf are rejected rather than propagated: a NaN threshold makes
    every ``<``/``>`` comparison False, which would silently read as *the
    policy did not fire* — a collapsed state, not a refusal.
    """
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def exit_head_verdict(
    rec: Optional[Dict[str, Any]],
    meta: Dict[str, Any],
    cfg_dict: Dict[str, Any],
    current_price: float,
) -> Optional[Dict[str, Any]]:
    """Return ``{"action": "close", "reason": "exit_head", ...}`` or ``None``.

    See the module docstring for the full gate list. Behaviour is byte-for-byte
    the pre-MI-150 ``trend_donchian._exit_head_verdict``.
    """
    try:
        if not rec:
            return None
        action = str(meta.get("exit_head_action")
                     or cfg_dict.get("exit_head_action") or "").lower()
        if action != "close":
            return None
        if str(rec.get("stage") or "") != "advisory":
            return None
        pin = meta.get("exit_head_model") or cfg_dict.get("exit_head_model")
        if pin and str(pin) != str(rec.get("model_id")):
            return None
        tau = _coerce_float(meta.get("exit_head_threshold")
                            or cfg_dict.get("exit_head_threshold"))
        if tau is None:
            tau = _coerce_float(rec.get("tau"))
        below_r = _coerce_float(rec.get("below_r"))
        score = _coerce_float(rec.get("score"))
        open_r = _coerce_float((rec.get("feature_row") or {}).get("open_r"))
        if None in (tau, below_r, score) or open_r is None:
            return None
        # The firing rule follows the artifact's declared SHAPE (mirrors
        # exit_head_shadow.py's would_exit): the below_half_r head fires LOW
        # scores on losers (score < tau AND open_r < below_r); the peak_*
        # heads fire HIGH scores when the peak is in (score > tau [AND
        # open_r >= below_r for peak_winner]). Hardcoding the below_half_r
        # rule would fire a peak head on exactly the wrong condition
        # (MB-20260716 / M20 P4.2 graduation). `exit_head_threshold` still
        # overrides tau on either branch.
        policy = str(rec.get("policy") or "below_half_r")
        if policy.startswith("peak"):
            fires = score > tau and (
                policy != "peak_winner" or open_r >= below_r)
        else:
            fires = score < tau and open_r < below_r
        if not fires:
            return None
        return {"action": "close", "reason": "exit_head",
                "exit_price": current_price}
    except Exception:  # noqa: BLE001 — fail-closed, never a spurious close
        return None


# ---------------------------------------------------------------------------
# MI-150 — the staged MODE gate for a NEW consuming unit
# ---------------------------------------------------------------------------
#
# ⚠️ THIS GATE IS NOT ON THE DONCHIAN PATH AND MUST NOT BE PUT THERE. The
# donchian consumer has been live since 2026-07-12 gated on (YAML declare +
# advisory stage); wrapping a second gate around it would change a live
# money path for no reason. This is the staging knob for the SECOND consumer,
# `ict_scalp`, whose arming is a Tier-3 decision that has NOT been taken.

#: `*_MODE`, never a default-off `*_ENABLED` gate (Prime Directive; the same
#: shape as NEWS_INFLUENCE_MODE / CONVICTION_SIZING_MODE / PROTECTION_*_MODE).
_MODE_ENV = "ICT_SCALP_EXIT_HEAD_MODE"
_MODES = ("off", "annotate", "apply")


def resolve_mode(env: Optional[Dict[str, str]] = None) -> str:
    """`off` / `annotate` (default) / `apply` for the ict_scalp exit head.

    ⚠️ **An unparseable value falls back to `annotate` — never `off` and never
    `apply`.** A typo must not silently switch the observation off, and
    certainly must not switch a live order path on. Read at call time, so a
    flip needs no redeploy (it does need a trader restart to reach the
    process — confirm from `/proc/<MainPID>/environ` via `get-env`, never from
    the `.env`, which says only what the NEXT restart will pick up).
    """
    import os

    raw = (env or os.environ).get(_MODE_ENV)
    val = str(raw or "").strip().lower()
    return val if val in _MODES else "annotate"


def staged_exit_head_decision(
    rec: Optional[Dict[str, Any]],
    meta: Dict[str, Any],
    cfg_dict: Dict[str, Any],
    current_price: float,
    mode: str,
) -> Dict[str, Any]:
    """Run the FULL decision, then say what the mode did with it.

    Returns ``{"verdict": <verdict|None>, "would_close": bool, "mode": str,
    "apply_scope": str, "acted": bool, "decision_state": str}``.

    ⚠️ **At `annotate` the decision runs in full and the verdict is
    DISCARDED, not skipped.** Computing a plan and throwing it away without
    recording it is an annotate mode that annotates nothing. The caller
    writes `would_close` to the soak. Prior art, id kept WHOLE on one line
    because a wrapped id resolves to nothing:
    BL-20260831-STRAY-OCA-SWEEP-ANNOTATE-COMPUTES-A-VERDICT-AND-DISCARDS-IT

    ``decision_state`` is four states, never collapsed:

    - ``not_scored`` — no ``rec``. **We did not look**, never "the head did
      not fire". Today this is the ONLY value any ict_scalp leg can produce,
      because the live mirror publishes 1h artifacts only and the scorer's
      tf guard refuses every 5m/15m leg. That is the honest state and it must
      not read as a quiet negative.
    - ``scored_no_fire`` — scored, policy did not fire.
    - ``scored_would_close`` — scored and the policy fired.
    - ``mode_off`` — the decision was not run at all.
    """
    if mode == "off":
        return {"verdict": None, "would_close": False, "mode": mode,
                "apply_scope": "mode_off", "acted": False,
                "decision_state": "mode_off"}
    if not rec:
        return {"verdict": None, "would_close": False, "mode": mode,
                "apply_scope": "not_apply" if mode != "apply" else "applied",
                "acted": False, "decision_state": "not_scored"}
    verdict = exit_head_verdict(rec, meta, cfg_dict, current_price)
    would = verdict is not None
    acted = bool(would and mode == "apply")
    return {
        "verdict": verdict if acted else None,
        "would_close": would,
        "mode": mode,
        "apply_scope": "applied" if mode == "apply" else "not_apply",
        "acted": acted,
        "decision_state": "scored_would_close" if would else "scored_no_fire",
    }
