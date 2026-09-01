"""ONE owner for "is the WIP ceiling enforced, and what was carried in when?".

WHY THIS MODULE EXISTS, and it is not hypothetical. These facts had TWO homes:
``src/web/api/routers/work.py`` (the SPA's Work view) and
``scripts/ops/work_digest.py`` (the daily digest). Phase C (#10657) shipped
``scripts/ci/check_wip_ceiling.py`` and migrated the carried rows, and updated
NEITHER. For ~20 minutes the deployed SPA told the operator "Declared, not
enforced. Nothing checks this yet" beside 584 migrated objects — i.e. it said
the ceiling was advisory when it would in fact fail their CI.

Fixing the route alone then left the DIGEST still saying it, which is how the
same sentence gets corrected once and stays wrong somewhere else. The repo's
rule is that a vocabulary gets one owner; ``work_digest`` already applies it a
few lines up ("ONE owner for 'what counts as an event' — imported, never
re-derived"). This is the same move for the same reason.

⚠️ STDLIB ONLY, DELIBERATELY. The route imports FastAPI and the digest is a
cron-invoked script that must not; neither can import the other, which is
exactly why the shared fact needs a third home rather than a cross-import.

⚠️ WHEN THE CEILING CHANGES, CHANGE IT HERE. A second copy anywhere is the bug
this module was created to end — and the test that pins route and digest to the
same value is what makes a re-divergence fail CI rather than reach the operator.
"""
from __future__ import annotations

#: The WIP ceiling on `in_flight` work objects.
WIP_CEILING = 8

#: Enforced in CI by `scripts/ci/check_wip_ceiling.py` since Phase C
#: (#10657, 2026-09-01). Exceeding it needs a written justification at
#: docs/claude/work/wip-ceiling-exception.yaml that becomes an operator
#: decision — and a *filed* justification still fails until it is *approved*.
CEILING_ENFORCED = True

#: The state string both surfaces publish. Never collapsed to a bare boolean:
#: "declared but unchecked" and "enforced" are different facts to a reader
#: deciding whether they may open a ninth object.
CEILING_STATE = "enforced_in_ci"

#: Carried backlog rows MIGRATED IN on 2026-09-01 (Phase C). History now, not a
#: pending gap — but the coverage note still has to say what was carried and
#: when, so the number keeps a name.
CARRIED_ROWS_MIGRATED = 572

#: What the coverage note says about that migration. Past tense on purpose: it
#: read "Phase C" (i.e. *pending*) after Phase C had already landed.
CARRIED_ROWS_MIGRATED_IN = "Phase C — COMPLETE 2026-09-01"
