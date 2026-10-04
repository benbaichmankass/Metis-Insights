# `_unattributed/` — results of runs no queue unit dispatched

`research_unit: null` means **not queue-dispatched** (`research_result.py` docstring), a legal state. This
directory is not a to-do list, but it must not hide runs that DID have a unit. Audited 2026-10-04
(RESEARCH-PIPELINE-REPAIR): 17 files.

**Attributed (moved out):** `36434140504` -> `RQ-20260928-006`, `36434144288` -> `RQ-20260928-007`. Both were
fired by the dispatcher (14:11:46Z / 14:11:48Z, actor `github-actions[bot]`, 4 s after the units' own stamp
`2026-09-28T14:11:42Z`) but the units declared no `run.inputs.research_unit`, so the workflow stamped none.
The dispatcher now supplies it when the workflow declares the input, so this class cannot recur.

**Correctly unattributed (15 files) — no unit existed or applies:**

| files | what they are | why no unit |
|---|---|---|
| `36300749358` `36300753265` `36300760079` `36300763613` `36300772837` `36300777384` `36300782038` `36300787082` `36300791459` `36300796844` `36311520625` | E7 `research-harness-dispatch.yml` smoke dispatches of the ten wired harnesses on BTCUSDT (2026-09-27) and an xsec_momentum re-run | hand-dispatched to prove the wiring; the queue units that cover these harnesses (RQ-20260928-030/-031, RQ-20260929-101..108) were created 09-28/09-29, i.e. after |
| `37037846659` | `m20-exit-lever-sweep` run triggered by a **push** (M20-EXITS-4 re-sweep of pre-TP-fix stale cells, 2026-10-02) | checklist row M20-EXITS-4, not a queue unit |
| `b6-prop-ev-breakout_1-20260927` `w6-prop-candidates-session` `w6-prop-candidates-noncrypto-session` | session-written B6/W6 prop EV records (`run_attempt: null`) | produced in a lane session, not by a workflow |

Re-attribute a file only against a unit that existed at run time and whose inputs the run actually carried.
