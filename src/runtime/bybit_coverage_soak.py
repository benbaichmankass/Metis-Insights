"""Durable read surface for the Bybit graded-book coverage basis.

``BYBIT_GRADED_COVERAGE_MODE`` decides whether the naked sweep's re-arm
comparison reads the GRADED-book quantity or the pre-2026-09-02 side-blind sum,
and ``BYBIT_GRADED_COVERAGE_ACCOUNTS`` stages that per account. The operator's
Tier-2 decision was **stage it on ``bybit_1`` (demo) first** — which is only a
decision if the evidence for widening it later actually accrues. These rows are
that evidence.

⚠️ **THIS IS THE HALF THE REPO KEEPS FORGETTING, AND IT IS SHIPPED IN THE SAME
COMMIT AS THE WRITER FOR THAT REASON.** ``PROTECTION_STRAY_GROUP_MODE``
promised a Tier-2 reviewer rows to read and its call site discarded the plan
(``BL-20260831-STRAY-OCA-SWEEP-ANNOTATE-COMPUTES-A-VERDICT-AND-DISCARDS-IT``);
``prop_risk_gate`` promised the same and had no file write at all; five alert
and cadence state files shipped with no read surface
(``BL-20260825-ALERT-AND-CADENCE-STATE-FILES-SHIP-WITHOUT-A-READ-SURFACE``).
``bybit_coverage_soak`` is registered in ``diag``'s ``log_file`` allowlist in
the commit that adds this module.

⚠️ **THE FIELD TO READ BEFORE WIDENING THE ALLOWLIST IS ``verdicts_differ``.**
It is the analogue of ``cash_settlement_soak``'s ``would_have_reduced_usd``: it
says how often arming would actually have changed the outcome, rather than how
often the code ran. A soak full of rows where the two bases agree is evidence
that arming is *safe*, not evidence that it is *needed*; a row where they
differ is the first sighting of the masking this gate exists for — which,
⚠️ **as of 2026-09-02 has NEVER been observed live. The defect was CONSTRUCTED
from the 2026-09-02T03:30:33Z read, n = 1.**

⚠️ **A ROW IS WRITTEN FOR EVERY BYBIT ACCOUNT, NOT ONLY THE ALLOWLISTED ONE.**
The allowlist scopes the BINDING, never the MEASUREMENT — otherwise the account
being staged TOWARD (``bybit_2``, real money) would be invisible in exactly the
rows a reviewer needs in order to widen to it. That is the correction
``NETTING_ATTRIBUTION_ACCOUNTS`` needed on 2026-08-09.

⚠️ **``mode`` IS THE EFFECTIVE MODE, ``global_mode`` THE REQUESTED ONE, AND
``apply_scope`` EXPLAINS ANY DIFFERENCE.** A held-back row can therefore never
read as an applied one — the distinction ``NETTING_ATTRIBUTION_MODE`` had to be
corrected into existence. Read ``basis`` to see which figure actually decided.

⚠️ **A ROW RECORDS THE COVERAGE DECISION, NOT AN ORDER OUTCOME.**
``decision: rearm_indicated`` means the comparison measured a hole — whether a
top-up or a Full-mode re-arm was then attempted, and whether it succeeded, is
the sweep's own summary and log, not this file. Reading these rows as placed
orders is the same error as reading ``cancel_calls_made`` as a cancellation.

⚠️ **ONE ROW PER (ACCOUNT, SYMBOL) PER SWEEP, NOT ONE PER JOURNAL ROW.** A
netted symbol holds MANY journal rows against ONE exchange position, and after
a successful re-arm the sweep rewrites its per-tick cache to say "fully
covered" so the siblings do not each fire again. Writing every row would
therefore both inflate the counts and persist that synthetic cache marker as
if it were a venue reading. The FIRST decision for a symbol in a sweep is the
one recorded; a reader must not treat the row count as a decision count.

At ``off`` this writes **nothing** — that mode stays byte-for-byte unchanged on
disk, the discipline ``prop_ticket_risk_soak`` and ``stray_oca_soak`` follow.
"""

from __future__ import annotations

import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from src.runtime import bybit_coverage_basis as _basis

logger = logging.getLogger(__name__)

SOAK_LOG_NAME = "bybit_coverage_soak.jsonl"

#: Copied onto the row verbatim from the decision. Never re-derived here: a
#: second resolution of the mode is a second source of truth, free to drift
#: from the one that actually governed the sweep, and the row would then
#: describe a decision nobody made (``stray_oca_soak``'s reasoning, and
#: ``exposure_soak``'s before it).
_DECISION_FIELDS = (
    "account_id", "symbol", "source",
    "mode", "global_mode", "apply_scope",
    "basis", "binding", "coverage_state",
    "position_size", "eps",
    "side_blind_qty", "graded_qty", "bound_qty",
    "verdict_side_blind", "verdict_graded", "verdicts_differ",
    "decision",
)


def _log_path() -> pathlib.Path:
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / SOAK_LOG_NAME


def record(
    decision: Optional[Mapping[str, Any]],
    *,
    position_side: Any = None,
    position_idx: Any = None,
    sl_leg_count: Any = None,
    other_book_qty: Any = None,
    other_book_legs: Any = None,
    other_book_state: Any = None,
) -> Optional[Dict[str, Any]]:
    """Append one row for one graded coverage decision. Returns it, or ``None``.

    Returns ``None`` — writing nothing — when *decision* is absent or the sweep
    ran at ``off``.

    Best-effort throughout. This sits on the live monitor's naked sweep, so no
    failure here may reach the caller: silence costs one observation, a raise
    would cost a protective re-arm.
    """
    try:
        if not decision:
            return None
        if str(decision.get("global_mode") or "") == _basis.MODE_OFF:
            return None

        row: Dict[str, Any] = {
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        for key in _DECISION_FIELDS:
            row[key] = decision.get(key)

        # Context for a reviewer reading a `verdicts_differ` row cold: WHY the
        # two figures disagreed. `other_book_state` is `bybit_leg_sides`' own
        # three-state verdict, whose `unknown` is *we could not look* and must
        # not be read as `impossible_one_way`.
        row["position_side"] = None if position_side is None else str(position_side)
        row["position_idx"] = position_idx
        row["sl_leg_count"] = sl_leg_count
        row["other_book_qty"] = other_book_qty
        row["other_book_legs"] = other_book_legs
        row["other_book_state"] = (
            None if other_book_state is None else str(other_book_state))

        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        return row
    except Exception:  # noqa: BLE001 — observe-only must never break a re-arm
        logger.debug("bybit_coverage_soak: record failed", exc_info=False)
        return None


__all__ = ["SOAK_LOG_NAME", "record"]
