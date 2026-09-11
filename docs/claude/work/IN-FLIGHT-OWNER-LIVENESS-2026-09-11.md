# `in_flight` goes false by time passing — what the two registers actually say

> **Doc status:** `live` · category `evidence` · last verified `2026-09-11` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)

MI-236. Everything below was measured on `main` @ `4d8b30109`, **2026-09-11**,
by `scripts/ops/owner_liveness.py` — the module this evidence justifies. Every
figure names the population it was counted over.

## 1. The finding was measured over thirteen owners. Graded over all of them, it is more than twice as large.

The row records *six of nineteen* `in_flight` checklist rows stale, found by
`get_session` over **thirteen** owners on 2026-09-10. Grading **every**
`in_flight` row in **both** registers against `SESSIONS.json`:

| register | `in_flight` | supported | **unsupported** | could-not-establish |
|---|---:|---:|---:|---:|
| `MANAGER-CHECKLIST.json` (267 items) | 27 | 8 | **13** | 6 |
| `docs/claude/work/objects/*.yaml` (159 files) | 8 | 2 | **4** | 2 |
| **total** | **35** | **10** | **17** | **8** |

⚠️ **All six rows the finding named are STILL `in_flight` today** — the one-off
state correction never happened, which is exactly why the row's done-condition
forbids a manual sweep.

## 2. The two registers are different sizes, and only one of them binds

`check_wip_ceiling.py` counts **work objects** with `lifecycle: in_flight`, not
checklist rows — read from the guard, not inferred. So:

- A stale **checklist** row misprices capacity. It refuses nothing.
- A stale **object** row **refuses a ninth object at the ceiling**.

The objects register sits at **8 of a ceiling of 8** — full. **Of those eight
slots, two have an owner the registry shows active, four are held by owners the
registry itself records as `idle`, and two name no session at all** (`owner:
null`, `owner: claude`). That is the "binding, not cosmetic" claim measured on
the population it actually applies to.

## 3. The obvious build catches 1 of 6

Grading owners against strictly TERMINAL session states — `archived`,
`completed`, `done`, `failed` — is the natural design. Over the six rows the
finding named:

| row | owner registry state | strict-terminal grader | activity grader |
|---|---|---|---|
| MI-136 | `archived` | ✅ caught | ✅ caught |
| MI-230 | `idle` | ❌ missed | ✅ caught |
| MI-212 | `idle` | ❌ missed | ✅ caught |
| MI-221 | `idle` | ❌ missed | ✅ caught |
| MI-210 | `idle` | ❌ missed | ✅ caught |
| MI-211 | `idle` | ❌ missed | ✅ caught |
| | | **1 of 6** | **6 of 6** |

`idle` is not terminal — an idle session can be woken, and that is MI-235's
whole mechanism. But the question `in_flight` makes is not *is the owner dead*,
it is **is the owner working on it**, and `idle` answers that as flatly as
`archived` does. The two are kept as SEPARATE states because they share a
verdict and not a remedy: a dormant owner is poked, a terminal one re-routed.

## 4. Why this needs no `mcp__*` tool — the registry is a reliable NEGATIVE

Grading a live session needs `get_session` / `list_sessions`, which CI does not
hold. That wall is escapable here because registry `state` decays in exactly
**one** direction:

- written `working` → becomes false as the session finishes (the MI-178 finding: all 17 `working` rows false)
- written `archived` / `completed` / `idle` → **does not decay**; a session does not un-archive itself

So a recorded non-active state is evidence somebody **positively observed** the
owner not working, while a recorded `working` is evidence of nothing. This
module needs only the first, so it needs no live tool. **The evidence was
already in the repo:** 198 of 211 registry rows carry an observation timestamp,
and all six stale owners carried one, 1–3 days old. Nothing read them.

⚠️ **The cost is stated, not hidden.** A row whose owner died with nobody
recording it grades `unknown_to_registry` — *we could not look* — and this
mechanism does nothing for it. That is the MI-15 registration gap, a different
row's subject. It is reported as `could_not_establish`, never as a pass.

## 5. The 17 unsupported rows, as measured

Every one has a STALE observation (24–75h), so none rests on a fresh read.

| register | row | owner activity | registry | observed |
|---|---|---|---|---|
| checklist | MI-136-ZERO-FOR-THIRTEEN | terminal | `archived` | 75.1h |
| checklist | MI-138-RELAND-1C | terminal | `archived` | 75.1h |
| checklist | MI-143-ICT-SCALP-5M-SHADOW | dormant | `idle` | 46.8h |
| checklist | MI-174-DIGEST-GENERATOR-HEADER | dormant | `idle` | 75.1h |
| checklist | MI-193-LANE-2-RESEARCH-ACTIVE-MANAGEMENT-MECHANICS | dormant | `idle` | 66.1h |
| checklist | MI-195-OPERATOR-TP-DECISIONS-AND-EXAMINE-QLD-TQQQ | dormant | `idle` | 58.7h |
| checklist | MI-210-LANE-1-BACKLOG-DRAIN | dormant | `idle` | 33.1h |
| checklist | MI-211-LANE-2-PARTIAL-CLOSE-NEVER-FIRED | dormant | `idle` | 33.1h |
| checklist | MI-212-LANE-4-STALE-AUTOMATION-LANDING | dormant | `idle` | 36.2h |
| checklist | MI-221-BYBIT2-ETH-PHANTOM-CLOSE-UNPROTECTED | dormant | `idle` | 30.0h |
| checklist | MI-230-M20-EXIT-EVAL-NEAR-MISS-AND-RESTART-GAP | dormant | `idle` | 29.3h |
| checklist | LANE4-RECOVERY-11519-STALE-AUTOMATION-SWEEPER | terminal | `completed` | 24.4h |
| checklist | MI-232-ROOT-CAUSE-EXIT-EVAL-60S-BREACHES | terminal | `completed` | 24.4h |
| **objects** | WO-20260909-EVERY-PR-IN-THE-REPO-IS-RED | dormant | `idle` | 36.2h |
| **objects** | WO-20260909-GATE-API-BOT-DB-THE-MONEY-DB | dormant | `idle` | 33.1h |
| **objects** | WO-20260909-THE-HEALTH-BACKLOG-GREW-BY-49-WHILE | dormant | `idle` | 33.1h |
| **objects** | WO-20260909-THE-PARTIAL-CLOSE-PRODUCER-IS-BUILT-AND | dormant | `idle` | 33.1h |

⚠️ **This table is a list of rows whose `in_flight` CLAIM IS UNSUPPORTED BY THE
REGISTER. It is not a list of abandoned work, and it must not be actioned as
one.** What each row becomes — re-routed, poked awake, or left alone because the
registry is simply stale — is a separate call for a manager who reads it.
**Nothing here may be auto-closed**: the correct landing state for a row whose
owner died mid-task is emphatically not `done`, and guessing it would destroy
the "where do we pick this up" record that is the operator's core mandate.

## 6. What this does NOT establish

- **It does not prove any of these rows is abandoned.** It proves the register
  does not support the claim. Those are different facts and only the second is
  measured.
- **It does not cover a row whose owner is unrecorded.** 8 rows grade
  `could_not_establish`, six of them `owner: manager` and two `owner: null` /
  `owner: claude`.
- **It says nothing about the 27 checklist rows that are NOT `in_flight`.** A
  row sitting at `ready`/`unassigned` forever is MI-246's subject, not this one.
- **The census has no reader yet.** The REFUSAL needs none — it is a required
  check — but the per-row census currently lands only in CI logs. `MI-238`'s
  Workflow page is the natural consumer; `scripts/ops/owner_liveness.py` is
  importable precisely so that page can render it without a second definition.
