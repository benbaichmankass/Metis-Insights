"""Durable read surface for the stray-OCA group sweep.

``IBClient._sweep_stray_oca_groups`` computes a complete decision plan — which
non-keyed protective groups are strays, which sibling groups are preserved, and
under ``apply`` whether the cancels actually took — and its one call site
**discarded the returned dict**. The only output was a ``logger.warning`` into
journald: rolling, unstructured, and impossible to write a field-matching probe
against. So ``PROTECTION_STRAY_GROUP_MODE`` had no read surface at all, while
``CLAUDE.md`` told a Tier-2 reviewer to read rows before arming a path that
CANCELS a live position's protective legs. Those rows did not exist.

Tracked by two rows filed independently the same day and deliberately not
merged, because their criteria differ:
``BL-20260831-STRAY-OCA-SWEEP-ANNOTATE-COMPUTES-A-VERDICT-AND-DISCARDS-IT``
(one row per graded symbol, verified on the live VM) and
``BL-20260831-STRAY-OCA-APPLY-PATH-HAS-NO-SOAK-SO-ITS-CANCEL-IS-UNPROVABLE``
(per decision, carrying the five leg states and the cancel read-back).

⚠️ **THIS MODULE RE-DECIDES NOTHING.** It persists the plan the sweep already
returned. It deliberately does **not** re-read ``PROTECTION_STRAY_GROUP_MODE``:
a second resolution is a second source of truth, free to drift from the one that
actually governed the cancel, and the row would then describe a decision nobody
made. Same reasoning as ``exposure_soak``, which records the account's own
``report()["exposure"]`` verbatim rather than reconstructing it.

⚠️ **``decision`` IS NEVER COLLAPSED.** Three states, and the third is the one
that matters: ``stray_unkeyed`` (strays found — THE FINDING) · ``no_strays`` (we
read the book and nothing was stray) · ``could_not_look`` (the ``openTrades()``
read failed — emphatically **NOT** evidence that no strays rest, which is the
invariant the sweep's own ``test_read_failure_is_not_evidence_of_no_strays``
already defends one layer down).

⚠️ **A ``no_strays`` ROW IS STILL WRITTEN, DELIBERATELY — IT IS THE
DENOMINATOR.** Dropping it would leave a reader able to see findings and unable
to see how often the sweep ran and found nothing, so "the sweep is quiet" and
"the sweep is not running" would render identically. That is the unstated-
denominator error this repo keeps paying for, and it is the same mistake the
arbitration fan-out soak had to be corrected for on 2026-08-30.

⚠️ **``cancelled`` IS THE COUNT OF CANCEL CALLS MADE, NOT AN OUTCOME.** It is
carried under that name on purpose, because renaming it would hide the very
defect the verification envelope exists to make impossible
(``BL-20260825-PLACE-PROTECTIVE-COUNTS-THE-CANCEL-CALL-NOT-ITS-EFFECT``). Branch
on ``verify_state`` and ``still_resting``; never on ``cancelled``.

⚠️ **THE FIVE LEG STATES ARE EMITTED WITH EXPLICIT ZEROS.** The sweep's
``by_state`` only carries states it actually saw, so an absent key means "no leg
landed here" and is indistinguishable from "this state was never reachable".
``legs_by_state`` names all five every time and sums to ``legs_seen`` by
construction, so the partition is checkable rather than trusted.

At ``off`` this writes **nothing** — that mode stays byte-for-byte unchanged on
disk, the discipline ``prop_ticket_risk_soak`` already follows.
"""

from __future__ import annotations

import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from src.runtime import stray_oca_groups

logger = logging.getLogger(__name__)

SOAK_LOG_NAME = "stray_oca_soak.jsonl"

# The headline verdict for one graded sweep. Never collapsed — see the module
# docstring on why `could_not_look` may not fold into `no_strays`.
DECISION_STRAY = "stray_unkeyed"
DECISION_NO_STRAYS = "no_strays"
DECISION_COULD_NOT_LOOK = "could_not_look"
DECISIONS = (DECISION_STRAY, DECISION_NO_STRAYS, DECISION_COULD_NOT_LOOK)

# Emitted on every row, always all five, so a zero is a measurement.
_LEG_STATES = (
    stray_oca_groups.KEEP_TARGET,
    stray_oca_groups.SIBLING_KEYED,
    stray_oca_groups.STRAY_UNKEYED,
    stray_oca_groups.UNGROUPED,
    stray_oca_groups.NOT_PROTECTIVE,
)


def _log_path() -> pathlib.Path:
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / SOAK_LOG_NAME


def decision_for(plan: Mapping[str, Any]) -> str:
    """Grade one returned plan. See ``DECISIONS``.

    ``could_not_look`` is decided by the sweep's own ``read_state``, never
    inferred from an empty ``stray_groups`` — an unreadable book and a clean
    book both present as "no strays found" and must not share a verdict.
    """
    if str(plan.get("read_state") or "") == "could_not_look":
        return DECISION_COULD_NOT_LOOK
    return DECISION_STRAY if plan.get("stray_groups") else DECISION_NO_STRAYS


def record(
    plan: Optional[Mapping[str, Any]],
    *,
    symbol: str,
    keep_group: str = "",
    account_id: Any = None,
) -> Optional[Dict[str, Any]]:
    """Append one row for one graded sweep. Returns the row, or ``None``.

    Returns ``None`` — writing nothing — when *plan* is absent or the sweep ran
    at ``off``. Every other invocation writes, including the quiet ones.

    Best-effort throughout. This sits on the live IB order path inside
    ``place_protective``, so **no failure here may reach the caller**: silence
    costs one observation, a raise would cost a protective arm.
    """
    try:
        if not plan:
            return None
        global_mode = str(plan.get("global_mode") or "")
        if global_mode == stray_oca_groups.MODE_OFF:
            return None

        by_state: Mapping[str, Any] = plan.get("by_state") or {}
        legs_by_state = {s: int(by_state.get(s, 0) or 0) for s in _LEG_STATES}
        # Any state the classifier grows later would silently vanish from the
        # partition above, so the sum is taken from what was actually seen and
        # the two are reported side by side rather than assumed equal.
        legs_seen = sum(int(v or 0) for v in by_state.values())

        row: Dict[str, Any] = {
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
            "symbol": str(symbol or ""),
            "account_id": plan.get("account_id", account_id),
            "keep_group": plan.get("keep_group", keep_group),
            "decision": decision_for(plan),
            # The EFFECT, taken from the sweep rather than re-derived.
            "acted": bool(plan.get("acted")),
            # `mode` is what governed THIS sweep; `global_mode` is what was
            # requested; `apply_scope` says why they differ. A held-back row can
            # therefore never read as an applied one — the distinction
            # NETTING_ATTRIBUTION_MODE had to be corrected into existence.
            "mode": plan.get("mode"),
            "global_mode": global_mode,
            "apply_scope": plan.get("apply_scope"),
            # `could_not_look` here is the whole reason this field exists.
            "read_state": plan.get("read_state"),
            "legs_seen": legs_seen,
            "legs_by_state": legs_by_state,
            "stray_groups": list(plan.get("stray_groups") or []),
            "preserved_groups": list(plan.get("preserved_groups") or []),
            "ungrouped_seen": plan.get("ungrouped_seen"),
        }

        # The apply-side read-back. Present only when the sweep actually acted;
        # absent (not zeroed) otherwise, because a zero here would assert a
        # verification nobody performed.
        if row["acted"]:
            row["cancel_calls_made"] = plan.get("cancelled")
            row["verify_state"] = plan.get("verify_state")
            row["still_resting"] = plan.get("still_resting")
            row["confirmed_gone"] = plan.get("confirmed_gone")
            row["cancelled_groups"] = list(plan.get("stray_groups") or [])

        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        return row
    except Exception:  # noqa: BLE001 — observe-only must never break an arm
        logger.debug("stray_oca_soak: record failed", exc_info=False)
        return None


__all__ = ["DECISIONS", "SOAK_LOG_NAME", "decision_for", "record"]
