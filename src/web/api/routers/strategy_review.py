"""M7 strategy-review reads — TWO routes over TWO DIFFERENT RECORDS.

- ``GET /api/bot/strategies/{name}/review`` — one strategy's newest packet from
  ``runtime_logs/strategy_reviews/`` (the LIVE VM path: today's run, ephemeral,
  ``.gitignore:29``, gone on re-provision).
- ``GET /api/bot/strategy-reviews`` — the COMMITTED fleet index from
  ``comms/strategy_reviews/`` (the durable record, with history).

⚠️ **They are not two views of one thing and must not be read as such.** They can
legitimately disagree: the VM holds today's run before it is committed, and the
repo keeps days the VM has dropped. Every response from the second stamps
``source`` so a reader never has to infer which record answered.

Both are Tier 1 — no auth, read-only. A Tier-3 action is *read* here, never
enacted.

----

``GET /api/bot/strategies/{name}/review`` serves the latest packet emitted by
``scripts/ml/strategy_review_packet.py`` for a given strategy.

Response shape (when a packet is present):

    {
      "present": true,
      "packet_path": "/.../runtime_logs/strategy_reviews/2026-06-09/vwap.json",
      "summary_md_path": "/.../runtime_logs/strategy_reviews/2026-06-09/vwap.md",
      "packet": { ... full packet JSON ... }
    }

When no packet exists yet (the strategy has never been reviewed by the
gate), the route returns HTTP 200 with ``present: false`` so the
dashboard can render an empty card without a crash.

``GET /api/bot/strategy-reviews`` is documented at its own definition below,
with the reasoning for each field it refuses to collapse.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter

from src.utils.paths import repo_root, runtime_logs_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

# Strategy names in this repo are [a-z0-9_]+; reject anything else to keep
# the path traversal-safe (mirrors the insights router's strategy-name guard).
_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def _reviews_root() -> Path:
    return runtime_logs_dir() / "strategy_reviews"


def _latest_packet(name: str) -> Optional[Path]:
    """Return the most recent <UTC-date>/<name>.json packet path, or None."""
    root = _reviews_root()
    if not root.exists():
        return None
    # Each UTC date is its own subdir; iterate newest-first.
    try:
        day_dirs = sorted(
            (p for p in root.iterdir() if p.is_dir()),
            reverse=True,
        )
    except OSError:
        return None
    for day_dir in day_dirs:
        candidate = day_dir / f"{name}.json"
        if candidate.exists():
            return candidate
    return None


@router.get("/strategies/{name}/review")
def get_strategy_review(name: str) -> Dict[str, Any]:
    if not _NAME_RE.match(name):
        return {"present": False, "error": "invalid_strategy_name"}
    path = _latest_packet(name)
    if not path:
        return {"present": False, "packet_path": None, "summary_md_path": None}
    try:
        packet = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.exception("strategy_review: failed to read packet at %s", path)
        return {"present": False, "error": "read_failed", "packet_path": str(path)}
    md_path = path.with_suffix(".md")
    return {
        "present": True,
        "packet_path": str(path),
        "summary_md_path": str(md_path) if md_path.exists() else None,
        "packet": packet,
    }


# ---------------------------------------------------------------------------
# The COMMITTED decision record — GET /api/bot/strategy-reviews
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS (Phase F / C3). The cron and the committed path shipped in
# PR #10649; measured 2026-09-01, `comms/strategy_reviews/` had **zero
# readers** — grep over *.py/*.ts/*.svelte/*.yml returned the writer and the
# docs and nothing else. A record that is written and never read is the shape
# `provenance-consumer-guard` exists to catch, and here it is the C3 failure one
# level up: the packet becomes durable and still reaches no decision.
#
# ⚠️ THIS SERVES A DIFFERENT RECORD FROM THE ROUTE ABOVE, DELIBERATELY.
#   * `/api/bot/strategies/{name}/review` reads `runtime_logs/strategy_reviews/`
#     — the LIVE VM path. Today's packet, ephemeral, `.gitignore:29`, and gone
#     when the VM is re-provisioned.
#   * This route reads `comms/strategy_reviews/` — the COMMITTED record a later
#     session or cycle reads.
# They can legitimately disagree (the VM has today's run before it is committed;
# the repo keeps history the VM has dropped). Serving one under the other's name
# would be sub-class **A** of the diagnostic-provenance defect — the label naming
# a quantity the accessor does not return — so every response stamps `source`.
#
# ⚠️ WHAT IS COMMITTED IS NOT EVERYTHING, AND THAT IS THE POINT OF `packet_committed`.
# The workflow commits `INDEX.json` ALWAYS and full packets only where an action
# is proposed (52 strategies × daily would be ~19,000 files a year). So a row
# without a packet is the ORDINARY case for a HOLD, not a gap — but "no packet
# because nothing was proposed" and "no packet because something broke" are
# different facts, and the flag is what keeps them apart.

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The generator's cadence is DAILY, so one missed day is tolerated and two is a
# finding. ⚠️ This threshold is CHOSEN, not measured — there is no distribution
# of packet ages behind it, and it should not be quoted as if there were. It is
# deliberately generous: this grades a REPORT's age, and an over-eager `stale`
# on a working daily cron is the desensitized-alarm shape.
_STALE_AFTER_HOURS = 48.0


def _committed_reviews_root() -> Path:
    return Path(repo_root()) / "comms" / "strategy_reviews"


def _committed_dates() -> list[str]:
    """Every committed UTC-date dir, newest-first. `[]` on an unreadable root.

    The caller distinguishes "no dates" from "could not look" via the root's
    own existence check, which is why this does not signal failure itself.
    """
    root = _committed_reviews_root()
    if not root.exists():
        return []
    try:
        return sorted(
            (p.name for p in root.iterdir() if p.is_dir() and _DATE_RE.match(p.name)),
            reverse=True,
        )
    except OSError:
        return []


def _grade_freshness(generated_at: Optional[str]) -> tuple[str, Optional[float]]:
    """Grade the record's age. Four states, never collapsed.

    ``fresh`` · ``stale`` · ``undateable`` (*a timestamp we could not parse — a
    record that cannot be dated cannot be shown to be current, so it fails SAFE
    to not-fresh*) · ``absent`` (no record at all).

    ⚠️ Age is the load-bearing field here because this record's whole purpose is
    to be read BEFORE a decision. A three-week-old packet rendered beside a
    confident action badge is indistinguishable from a current one — the same
    defect `/api/bot/prop/status` grew `status_freshness` for. `age_hours` is
    ``None`` for BOTH `absent` and `undateable`; read the verdict, never the null.
    """
    if not generated_at:
        freshness = "undateable"
        return freshness, None
    try:
        parsed = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        freshness = "undateable"
        return freshness, None
    if parsed.tzinfo is None:
        # A naive stamp is treated as UTC — the generator writes UTC — rather
        # than as local, which would shift the age by the host's offset.
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0
    freshness = "fresh" if age_hours <= _STALE_AFTER_HOURS else "stale"
    return freshness, round(age_hours, 2)



def _evidence_block(index: Dict[str, Any], rows: list) -> Dict[str, Any]:
    """What `actionable: 0` actually means for this run.

    ⚠️ **A ZERO ACTIONABLE COUNT IS TWO DIFFERENT FACTS AND THEY LOOK
    IDENTICAL.** It can mean *the fleet was graded and nothing needs
    attention*, or *nothing could be graded at all*. Measured on the
    2026-09-01 run — population: all 52 enabled strategies, window 7 days —
    `n_closed` was 0 for 34 legs and never exceeded 8, so **52/52 sat under
    the generator's own n>=20 floor** and the run could not have proposed an
    action whatever the PnL. Rendering that as "0 actionable" and stopping is
    the unstated-denominator error, and on a decision surface it is the
    expensive direction: it reports a clean bill of health for a fleet nobody
    could grade.

    ⚠️ **DERIVED FROM THE GENERATOR'S PUBLISHED NUMBERS, NEVER FROM `reasons`.**
    The evidence floor is stated in English in every held row, and matching
    that English is sub-class A of the diagnostic-provenance defect — the same
    reasoning that defers C4. An index written before the generator published
    the field therefore grades `unknown`; it is not re-derived and not guessed.
    """
    floor = index.get("min_closed_for_action")
    if floor is None:
        # ⚠️ `unknown` is WE COULD NOT LOOK — the index predates the field.
        # Never `none_below_floor`: that would assert every row had enough
        # evidence, the opposite of what was actually measured on this run.
        return {"floor_state": "unknown", "min_closed_for_action": None,
                "below_floor": None, "gradeable": None}

    below = sum(1 for r in rows if r.get("below_evidence_floor") is True)
    ungraded = sum(1 for r in rows if r.get("below_evidence_floor") is None)
    gradeable = len(rows) - below - ungraded
    if not rows:
        state = "unknown"
    elif gradeable == 0:
        # Nothing could have been proposed. `actionable: 0` says nothing here.
        state = "none_gradeable"
    elif below == 0:
        state = "all_gradeable"
    else:
        state = "partly_gradeable"
    return {
        "floor_state": state,
        "min_closed_for_action": floor,
        "below_floor": below,
        "gradeable": gradeable,
        # Rows whose n_closed itself was unknown — not folded into either.
        "floor_unknown_rows": ungraded,
        # ⚠️ AND WHAT WOULD HAVE TO CHANGE. `below_floor` says the run could
        # not act; it does not say whether waiting would ever help. See
        # `_horizon_block`.
        "horizon": _horizon_block(index, rows),
    }


def _horizon_block(index: Dict[str, Any], rows: list) -> Dict[str, Any]:
    """How far the fleet is from gradeable — and whether waiting reaches it.

    ⚠️ **`below_floor: 52` IS ONE NUMBER COVERING FOUR DIFFERENT PROBLEMS, AND
    ONLY ONE OF THEM IS A WINDOW PROBLEM.** A leg closing 8 trades a week
    reaches n=20 in a few more weeks; a leg that closed nothing has no
    measurable rate to project from at all; a leg in `execution: shadow` does
    not fill by design and so accumulates NO closed-trade evidence at any
    window whatsoever. Rendering those as one count invites the one remedy that
    is a trap — widen the window until something clears the floor — which fires
    a KILL off an evidence base assembled to make a KILL fireable, the same
    low-n hazard the floor exists to prevent, one level up.

    ⚠️ **READ FROM THE GENERATOR'S PUBLISHED PER-ROW BLOCK, NEVER RECOMPUTED
    HERE.** The horizon needs the window length and the funnel counts, and a
    route holding its own copy of that arithmetic is how the two spellings of
    a rule drift apart — the reasoning `min_closed_for_action` is published
    for. An index whose rows predate the field therefore grades
    ``horizon_state: "unknown"``, exactly as `floor_state` does: *we could not
    look*, never "no leg has a horizon problem".
    """
    published = [
        r.get("evidence_horizon") for r in rows
        if isinstance(r.get("evidence_horizon"), dict)
    ]
    if not rows or not published:
        return {
            # ⚠️ `unknown` here is WE COULD NOT LOOK — the committed index
            # predates the generator publishing the block. It is emphatically
            # not "every leg is reachable".
            "horizon_state": "unknown",
            "why_unknown": (
                "no row carries the generator's evidence_horizon block — this "
                "index predates it. NOT a reading that every leg is reachable."
            ),
            "window_days": index.get("window_days"),
            "rows_with_horizon": 0,
            "rows_total": len(rows),
            "by_horizon_class": None,
            "by_funnel_stage": None,
            "days_to_grade_all_reachable_point": None,
            "structurally_ungradeable": None,
        }

    by_class: Dict[str, int] = {}
    by_stage: Dict[str, int] = {}
    reachable_days: list = []
    for h in published:
        cls = str(h.get("horizon_class") or "unknown")
        by_class[cls] = by_class.get(cls, 0) + 1
        stage = str(h.get("funnel_stage") or "unknown")
        by_stage[stage] = by_stage.get(stage, 0) + 1
        # Only a `reachable` leg HAS a point projection; `unbounded_no_closes`
        # and `structurally_ungradeable` carry None for opposite reasons and
        # pooling them into one "days" figure is the collapse this block
        # exists to undo.
        if cls == "reachable" and h.get("days_to_floor_point") is not None:
            reachable_days.append(float(h["days_to_floor_point"]))

    reachable = by_class.get("reachable", 0)
    structural = by_class.get("structurally_ungradeable", 0)
    unbounded = by_class.get("unbounded_no_closes", 0)
    gradeable_now = by_class.get("gradeable_now", 0)

    if gradeable_now == len(published):
        state = "all_gradeable_now"
    elif reachable == 0 and gradeable_now == 0:
        # Nothing a longer window reaches. THE FINDING: the floor is not a
        # window away, and proposing a wider one would buy nothing.
        state = "none_reachable_by_waiting"
    elif structural or unbounded:
        state = "partly_reachable"
    else:
        state = "all_reachable"

    return {
        "horizon_state": state,
        "why_unknown": None,
        "window_days": index.get("window_days"),
        # ⚠️ THE DENOMINATOR. A distribution over 3 of 52 rows is not a
        # statement about the fleet, and `rows_total` is what says so.
        "rows_with_horizon": len(published),
        "rows_total": len(rows),
        "by_horizon_class": by_class,
        "by_funnel_stage": by_stage,
        # The window that would grade every leg with a finite projection —
        # AND NO OTHER LEG. Read it beside `structurally_ungradeable` and
        # `unbounded_no_closes`, or it reads as a window that grades the fleet.
        "days_to_grade_all_reachable_point": max(reachable_days) if reachable_days else None,
        "reachable_legs": reachable,
        "unbounded_no_closes": unbounded,
        # Legs no window reaches under their current configuration. These need
        # a different disposition mechanism, not a bigger number.
        "structurally_ungradeable": structural,
    }


@router.get("/strategy-reviews")
def get_committed_strategy_reviews(
    date: Optional[str] = None,
    actionable_only: bool = False,
) -> Dict[str, Any]:
    """The committed M7 decision record — the fleet's proposed actions.

    ``date`` selects a committed UTC date (default: newest). ``actionable_only``
    filters the rows to those asking for a decision.

    ⚠️ **THE DENOMINATOR SURVIVES THE FILTER.** `graded` always counts every row
    in the index, so a filtered response can never be read as the whole fleet —
    "4 actionable" over an unstated population is the unstated-denominator error
    this repo has a top-level rule against. `returned` says how many rows this
    response actually carries.
    """
    root = _committed_reviews_root()
    dates = _committed_dates()

    base: Dict[str, Any] = {
        # Names WHICH record answered — never inferable, and the two disagree.
        "source": "comms/strategy_reviews",
        "available_dates": dates,
        "stale_after_hours": _STALE_AFTER_HOURS,
    }

    if date is not None and not _DATE_RE.match(date):
        return {
            **base,
            "present": False,
            "read_state": "unreadable",
            "freshness": "absent",
            "error": "invalid_date",
            "graded": None,
            "actionable": None,
            "rows": [],
            "returned": 0,
        }

    if not root.exists():
        return {
            **base,
            "present": False,
            # No record has ever been committed. Distinct from a failed read.
            "read_state": "absent",
            "freshness": "absent",
            # ⚠️ None, never 0 — zero graded is a REAL reading (a run that
            # graded nothing), and asserting it here would fabricate an
            # observation nobody made.
            "graded": None,
            "actionable": None,
            "rows": [],
            "returned": 0,
        }

    target = date or (dates[0] if dates else None)
    if target is None:
        return {
            **base, "present": False, "read_state": "absent",
            "freshness": "absent", "graded": None, "actionable": None,
            "rows": [], "returned": 0,
        }

    index_path = root / target / "INDEX.json"
    if not index_path.exists():
        return {
            **base, "present": False, "read_state": "absent", "utc_date": target,
            "freshness": "absent", "graded": None, "actionable": None,
            "rows": [], "returned": 0,
        }

    try:
        index = json.loads(index_path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("strategy_reviews: unreadable index %s: %s", index_path, exc)
        # ⚠️ FOUND-BUT-UNREADABLE IS NOT "NOTHING GRADED". Returning empty rows
        # with a zero count here would turn a read failure into a clean-looking
        # negative — sub-class C of the diagnostic-provenance defect, and the
        # consumer side of `silent-empty-guard`.
        return {
            **base, "present": False, "read_state": "unreadable",
            "utc_date": target, "freshness": "undateable",
            "graded": None, "actionable": None, "rows": [], "returned": 0,
            "error": "index_unreadable",
        }

    rows = index.get("rows") or []
    freshness, age_hours = _grade_freshness(index.get("generated_at"))

    day_dir = root / target
    enriched: list[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("strategy")
        # Whether the FULL packet rode along into the repo. For a HOLD its
        # absence is the designed behaviour, not a gap — see the header.
        committed = bool(name) and (day_dir / f"{name}.json").exists()
        enriched.append({**row, "packet_committed": committed})

    selected = [r for r in enriched if r.get("actionable")] if actionable_only else enriched

    return {
        **base,
        "present": True,
        "read_state": "index_read",
        "utc_date": index.get("utc_date", target),
        "generated_at": index.get("generated_at"),
        "age_hours": age_hours,
        "freshness": freshness,
        # The denominator — every row the run graded, filter or no filter.
        "graded": index.get("graded", len(enriched)),
        "actionable": index.get("actionable", sum(1 for r in enriched if r.get("actionable"))),
        # Published by the generator so no consumer re-derives (and mis-spells)
        # the rule — the lowercase/uppercase guess that produced the 105-file PR.
        "no_action_verdict": index.get("no_action_verdict"),
        "by_action": index.get("by_action", {}),
        "actionable_only": actionable_only,
        # Read this BESIDE `actionable` — never `actionable` alone.
        "evidence": _evidence_block(index, enriched),
        "returned": len(selected),
        "rows": selected,
    }
