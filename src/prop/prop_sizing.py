"""Per-ticket SIZING MODE for a prop account — ``flat`` or ``room``.

Declared in the account's ruleset (``config/prop_rulesets/breakout.yaml``
``sizing:``) and applied by :func:`src.prop.breakout_executor.emit_prop_ticket`.

OPERATOR DECISION 2026-09-27 ~11:12Z ("Current flat, room sizing next"):

* ``flat`` — the CURRENT breakout_1 account instance. The ticket keeps its
  existing size, ``risk_pct x declared account_size_usd`` (1.5% x $5,000 =
  $75). :func:`resolve` returns ``risk_usd=None`` and reads NOTHING, so the
  ticket is built by exactly the code path it was built by before this module
  existed (asserted byte-for-byte by ``tests/test_prop_sizing_mode.py``).
* ``room`` — every FRESH account instance::

      risk_usd = min(risk_pct x live balance, k x binding cushion)

  skipped below ``min_risk_usd``. The binding cushion is the smaller of the
  DD-floor and daily-loss distances AFTER open positions' loss-to-stop —
  ``prop_reconcile.compute_rule_distance``'s ``*_after_open_risk_usd``, the
  same figures ``prop_risk_gate`` grades a ticket against. It is the live
  counterpart of ``scripts/research/prop_ev_sim.py --sizing room``, whose
  output is the evidence (PR #13084,
  docs/research/b6-prop-ev/live-state-2026-09-27T0723Z.json).

⚠️ **AN UNKNOWN CUSHION IS NOT A CUSHION.** A stale or unreadable snapshot, an
open position with no journaled stop, or a missing daily-loss term makes the
room size unknowable. ``on_cushion_unknown: skip`` (the declared default)
skips the ticket and says why; it never falls back to the flat $75 that room
sizing exists to replace, and never sizes from a number nobody measured.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

FLAT = "flat"
ROOM = "room"
MODES = (FLAT, ROOM)

# The after-open-risk states whose distances are numbers (prop_reconcile E66).
_MEASURED_OPEN_RISK = ("no_open_positions", "measured")


@dataclass(frozen=True)
class SizingConfig:
    mode: str = FLAT
    k: float = 0.33
    min_risk_usd: float = 10.0
    on_cushion_unknown: str = "skip"
    #: flat mode's declared cap (``sizing.flat.max_risk_usd``); ``None`` = not declared.
    flat_max_risk_usd: Optional[float] = None
    #: flat mode's per-LEG risk overrides (``sizing.flat.per_leg_risk_usd``),
    #: ``{strategy: usd}``. A leg not listed sizes at the account value. Every
    #: value is validated at load to be in ``(0, flat_max_risk_usd]``.
    per_leg_risk_usd: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SizingDecision:
    """What the executor does with one ticket.

    ``risk_usd`` is ``None`` in ``flat`` mode (the ticket sizes itself exactly
    as before). In ``room`` mode it is the sized risk, or ``None`` together
    with a ``skip_reason``.
    """

    mode: str
    risk_usd: Optional[float] = None
    skip_reason: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None
    #: The configured per-ticket cap the ENFORCE risk gate holds a ticket to:
    #: flat -> ``sizing.flat.max_risk_usd``; room -> the room-formula risk.
    #: ``None`` = no cap known (enforce then refuses).
    cap_usd: Optional[float] = None


def load_sizing_config(ruleset_path: str | Path) -> SizingConfig:
    """Read the ``sizing:`` block of a prop ruleset file.

    An absent block is ``flat`` (the behaviour every prop account had before
    the block existed). An unrecognised mode RAISES: a typo must not silently
    pick a sizing formula for a live account.
    """
    import yaml

    data = yaml.safe_load(Path(ruleset_path).read_text()) or {}
    block = data.get("sizing") or {}
    mode = str(block.get("mode") or FLAT).strip().lower()
    if mode not in MODES:
        raise ValueError(f"{ruleset_path}: sizing.mode must be one of {MODES}, got {mode!r}")
    room = block.get("room") or {}
    flat = block.get("flat") or {}
    cfg = SizingConfig(
        mode=mode,
        k=float(room.get("k", SizingConfig.k)),
        min_risk_usd=float(room.get("min_risk_usd", SizingConfig.min_risk_usd)),
        on_cushion_unknown=str(room.get("on_cushion_unknown") or "skip").strip().lower(),
        flat_max_risk_usd=(float(flat["max_risk_usd"])
                           if flat.get("max_risk_usd") is not None else None),
    )
    per_leg = _parse_per_leg(ruleset_path, mode, flat)
    if per_leg:
        cfg = SizingConfig(
            mode=cfg.mode, k=cfg.k, min_risk_usd=cfg.min_risk_usd,
            on_cushion_unknown=cfg.on_cushion_unknown,
            flat_max_risk_usd=cfg.flat_max_risk_usd, per_leg_risk_usd=per_leg)
    if not (0.0 < cfg.k <= 1.0):
        raise ValueError(f"{ruleset_path}: sizing.room.k must be in (0, 1], got {cfg.k}")
    if cfg.on_cushion_unknown != "skip":
        raise ValueError(
            f"{ruleset_path}: sizing.room.on_cushion_unknown supports only 'skip', "
            f"got {cfg.on_cushion_unknown!r}")
    return cfg


def _parse_per_leg(ruleset_path: Any, mode: str, flat: Dict[str, Any]) -> Dict[str, float]:
    """Validate ``sizing.flat.per_leg_risk_usd``. FAIL-CLOSED: any doubt RAISES.

    A per-leg value may never exceed ``sizing.flat.max_risk_usd`` (the ruleset's
    per-ticket cap), so the load-time check holds even when the runtime risk
    gate is ``off``/``annotate``. No declared cap, a non-flat mode, a
    non-positive or non-numeric value are all refused: a typo must not size a
    live prop ticket.
    """
    raw = flat.get("per_leg_risk_usd")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{ruleset_path}: sizing.flat.per_leg_risk_usd must be a mapping")
    if not raw:
        return {}
    if mode != FLAT:
        raise ValueError(
            f"{ruleset_path}: sizing.flat.per_leg_risk_usd is declared but sizing.mode is "
            f"{mode!r} — per-leg sizing is flat-mode only (it would be silently ignored)")
    cap = flat.get("max_risk_usd")
    if cap is None:
        raise ValueError(
            f"{ruleset_path}: sizing.flat.per_leg_risk_usd needs sizing.flat.max_risk_usd "
            "(the per-ticket cap each per-leg value is bounded by)")
    cap = float(cap)
    out: Dict[str, float] = {}
    for leg, val in raw.items():
        try:
            v = float(val)
        except (TypeError, ValueError):
            raise ValueError(
                f"{ruleset_path}: sizing.flat.per_leg_risk_usd[{leg!r}] is not a number: {val!r}")
        if isinstance(val, bool) or not (v > 0.0) or v != v:
            raise ValueError(
                f"{ruleset_path}: sizing.flat.per_leg_risk_usd[{leg!r}] must be > 0, got {val!r}")
        if v > cap:
            raise ValueError(
                f"{ruleset_path}: sizing.flat.per_leg_risk_usd[{leg!r}]=${v:,.2f} exceeds the "
                f"ruleset per-ticket cap sizing.flat.max_risk_usd=${cap:,.2f}")
        out[str(leg)] = v
    return out


def room_risk_usd(
    *,
    risk_pct_frac: float,
    balance_usd: Optional[float],
    cushion_usd: Optional[float],
    k: float,
    min_risk_usd: float,
) -> tuple[Optional[float], Optional[str]]:
    """Pure room-sizing formula. Returns ``(risk_usd, skip_reason)``.

    Mirrors ``prop_ev_sim.simulate_life``'s ``room`` branch:
    ``min(risk_pct x balance, k x max(0, cushion))``, skipped below the
    minimum. ``None`` inputs are unknown, never zero.
    """
    if balance_usd is None or cushion_usd is None:
        missing = "balance" if balance_usd is None else "cushion"
        return None, f"room sizing: live {missing} unknown — cannot size"
    risk = min(risk_pct_frac * float(balance_usd), k * max(0.0, float(cushion_usd)))
    risk = round(risk, 2)
    if risk < min_risk_usd:
        return None, (f"room sizing: ${risk:,.2f} (= min({risk_pct_frac:.2%} x "
                      f"${float(balance_usd):,.2f}, {k} x ${float(cushion_usd):,.2f} "
                      f"cushion)) is below the ${min_risk_usd:,.2f} minimum — skipped")
    return risk, None


def binding_cushion(rd: Dict[str, Any]) -> tuple[Optional[float], str]:
    """The binding cushion from a ``compute_rule_distance`` dict, or ``None``.

    Both limits must be known: the sim takes the min of the two, and a missing
    daily-loss term is "we could not look", not "no daily limit".
    """
    fresh = rd.get("status_freshness")
    if fresh != "ok":
        return None, f"account-status snapshot is {fresh or 'unreadable'}"
    ors = rd.get("after_open_risk_state") or "unreadable"
    if ors not in _MEASURED_OPEN_RISK:
        return None, f"open-position risk not measurable ({ors})"
    dd = rd.get("distance_to_dd_floor_after_open_risk_usd")
    daily = rd.get("distance_to_daily_loss_after_open_risk_usd")
    if dd is None or daily is None:
        which = "DD-floor" if dd is None else "daily-loss"
        return None, f"the {which} distance could not be derived"
    return min(float(dd), float(daily)), ("dd_floor" if float(dd) <= float(daily) else "daily_loss")


def resolve(
    account_id: str,
    *,
    ruleset_path: str | Path,
    risk_pct: float,
    rule_distance: Optional[Dict[str, Any]] = None,
    strategy: Optional[str] = None,
) -> SizingDecision:
    """Decide how one ticket for ``account_id`` is sized.

    ``risk_pct`` is in PERCENT (the ``AccountBacktestUnit.risk_pct`` unit,
    e.g. 1.5). ``rule_distance`` is an injection seam for tests; ``None`` reads
    the live ``compute_rule_distance`` — ONLY in ``room`` mode.

    ``strategy`` selects a ``sizing.flat.per_leg_risk_usd`` override (flat mode
    only). A leg with no entry — and every call without ``strategy`` — gets the
    pre-existing decision (``risk_usd=None``: the account's own risk_pct), so
    an unlisted leg is byte-identical to before. A listed leg gets
    ``risk_usd`` = its value and ``cap_usd`` = the same value.
    """
    cfg = load_sizing_config(ruleset_path)
    if cfg.mode == FLAT:
        # Deliberately reads nothing else: the flat ticket must be the
        # pre-existing ticket, byte for byte.
        leg_usd = cfg.per_leg_risk_usd.get(strategy) if strategy else None
        if leg_usd is not None:
            return SizingDecision(
                mode=FLAT, risk_usd=leg_usd, cap_usd=leg_usd,
                detail={"per_leg_risk_usd": leg_usd, "strategy": strategy,
                        "ruleset_cap_usd": cfg.flat_max_risk_usd})
        return SizingDecision(mode=FLAT, cap_usd=cfg.flat_max_risk_usd)

    if rule_distance is None:
        try:
            from src.prop import prop_reconcile
            rule_distance = prop_reconcile.compute_rule_distance(account_id) or {}
        except Exception as exc:  # noqa: BLE001 — a read failure is not a cushion
            logger.warning("prop_sizing: rule-distance read failed for %s (%s)", account_id, exc)
            rule_distance = {"status_freshness": "unreadable"}

    cushion, why = binding_cushion(rule_distance)
    balance = rule_distance.get("balance")
    detail = {
        "k": cfg.k, "min_risk_usd": cfg.min_risk_usd, "risk_pct": risk_pct,
        "balance_usd": balance, "cushion_usd": cushion,
        "binding_limit": why if cushion is not None else None,
        "status_freshness": rule_distance.get("status_freshness"),
        "after_open_risk_state": rule_distance.get("after_open_risk_state"),
    }
    if cushion is None:
        return SizingDecision(
            mode=ROOM, skip_reason=f"room sizing: cushion unknown — {why}", detail=detail)
    risk, skip = room_risk_usd(
        risk_pct_frac=risk_pct / 100.0, balance_usd=balance, cushion_usd=cushion,
        k=cfg.k, min_risk_usd=cfg.min_risk_usd,
    )
    return SizingDecision(mode=ROOM, risk_usd=risk, skip_reason=skip, detail=detail,
                          cap_usd=risk)
