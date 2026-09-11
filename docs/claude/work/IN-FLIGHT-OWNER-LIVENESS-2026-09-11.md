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

⚠️ **RE-MEASURED at `303b1a5ae` the same day, after `main` moved under this
branch** (three merges, incl. the 06:26Z manager tick): checklist **26** in_flight
= 7 / **13** / 6, objects **8** = 2 / **4** / 2, total **34** = 9 / **17** / 8. The
unsupported counts are UNCHANGED — 13, 4 and 17 — and only the `supported` column
moved, by one, as a lane completed. The table above is left as measured at
`4d8b30109` rather than overwritten, because it is what the guard's own ratchet
baseline was taken against.

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

## 7. ⚠️ CORRECTION, later the same day — the UNRELIABLE POSITIVE was being banked as evidence

**Added 2026-09-11 by `session_014tHgKvnnLdpgAgGXbaHRUt` (MI-236, second
lane).** §1's `supported` column is **not a reading of anything**, and this
section is why. The `unsupported` counts above are unaffected and were all
independently confirmed TRUE; only the positive half was unsound.

### What was checked, and with what

This module's licence to run in CI is the argument in its own docstring: *"the
registry is a RELIABLE NEGATIVE and an UNRELIABLE POSITIVE, and only the
negative is needed … a recorded `working` is evidence of nothing."* **For its
first day the code did not implement that.** `_support_for` mapped a recorded
`active` straight to `supported` regardless of how old the observation behind
it was — the module *computed* `observation_state`, passed it through as
decoration, and branched on none of it. Its own inline `collapsed-state`
override said so in terms.

That gap was invisible from inside the module by construction: catching it
needs `list_sessions`, which the docstring correctly says CI cannot hold. This
lane held one.

### MEASURED 2026-09-11T20:1xZ

**Population:** all **27** rows the module grades `in_flight` at `886c93e` (25
`MANAGER-CHECKLIST.json` + 2 `objects/*.yaml`), each graded twice — once by
`grade_owner_activity`, once against `list_sessions(mine=true, limit=100)`.
⚠️ **`has_more: true`**, so that roster is **TRUNCATED**: absence from it is
*we could not look*, **never** terminality, and 3 rows are reported that way
rather than counted as confirmed.

| module verdict | live platform truth | n | reading |
|---|---|--:|---|
| `unsupported` | terminal | **10** | ✅ **true positives** — the negative half held exactly as argued |
| `unsupported` | absent from truncated roster | 3 | unfalsified; not claimed either way |
| `could_not_establish` | names no session | 8 | both agree it is ungradeable |
| **`supported`** | **terminal (IDLE/COMPLETED)** | **4** | ⚠️ **false negatives** |
| `supported` | idle, non-terminal (BLOCKED / REVIEW_READY) | 2 | also not working it |

⚠️ **ZERO of the 27 rows had a live-RUNNING owner, so `supported` had no true
positives at all in this population** — all six were wrong or unconfirmable,
while reading as a pass.

Every one of the six was graded `stale` **by the module itself**, on
observations **2166–4328 minutes** old — **24× to 48× its own declared
`stale_minutes = 90.0`**:

| row | recorded state | observation | age | platform said |
|---|---|---|--:|---|
| `MI-215` · `MI-217` | `working` | `state_observation` | 43.5h | IDLE / **COMPLETED** |
| `MI-241` · `MI-254` | `review_ready` | `spawn_confirmation` — *never observed since spawn* | ~36h | IDLE / **COMPLETED** |
| `MI-183b` · `MI-196` | `blocked` / `review_ready` | `state_observation` | 72.1h | IDLE / BLOCKED, IDLE / REVIEW_READY |

### The fix, and the asymmetry that is the whole of it

`_support_for` now branches on observation freshness, **asymmetrically** —
which is this repo's own rule applied (*"a state nothing branches on is already
collapsed"*), and what `collapsed-state-guard`'s `manager_status.observation_state`
contract already says in terms: *"`recent` and `stale` must therefore stay
apart — that distinction IS the mechanism."*

- A recorded **negative** (`dormant` / `terminal`) **does not decay** — somebody
  positively observed the owner not working, and a session does not
  spontaneously un-archive. Staleness is irrelevant to it: still `unsupported`.
- A recorded **positive** (`active`) decays by the minute — that IS the MI-178
  finding this module rests on. Fresh ⇒ `supported`; **stale or never-observed
  ⇒ `could_not_establish`** (*we could not look*), never `supported`.

⚠️ **Applying the freshness test symmetrically would destroy the mechanism**, so
it is mutation-pinned in both the suite and the guard's self-test: it would
convert the 10 confirmed true positives above into `could_not_establish`.

### Effect, and what it deliberately cannot do

Re-graded over the same 27 rows: **0 supported / 13 unsupported / 14
could_not_establish**, from 6 / 13 / 8. All six moved rows are
`supported → could_not_establish`.

⚠️ **RE-MEASURED ~30 MINUTES LATER AT THE MERGED HEAD, AND IT IS NO LONGER
ZERO — do not quote the zero as a standing property.** After `main` moved
under this branch (three lanes spawned tonight), the same guard reads
**3 supported / 13 unsupported / 14 could_not_establish over 30 rows**. The
three are `MI-275`, `MI-276` and this lane itself, each confirmed **31.7
minutes** earlier and each genuinely `RUNNING` — so **`supported` has a live
positive control and, on this read, zero false positives**, and the earlier
zero was an artefact of *when the population was cut* rather than a property
of the code. The fix therefore discriminates in both directions on real
register data: 3 live-and-supported against 10 confirmed-terminal.

- ⚠️ **`unsupported` is unreachable from this change in either direction.** The
  only transition it can produce is *pass → we could not look*. That is what
  keeps `check_stale_in_flight.py`'s ratchet (which counts `unsupported`) at
  `base=13 head=13 delta=+0`, so it cannot red a PR over pre-existing debt and
  cannot manufacture a staleness claim about a row nobody observed.
- ⚠️ **`supported` is reachable and was OBSERVED at 3 of 30** (see the
  re-measurement above); the zero was population-dependent. Pinned by
  `test_supported_is_still_reachable_from_the_real_registers` so a genuinely
  live lane cannot be reported as ungradeable.
- **What survives of the cadence finding is narrower, and is still real.** The
  basis on all three `supported` rows is `spawn_confirmation`, i.e. they
  qualify because they were spawned 31.7 minutes ago — **not** because anyone
  looked at them since. So a lane grades `supported` only inside 90 minutes of
  its spawn or of a manager observation, and these same three will fall to
  `could_not_establish` **while still working** unless somebody observes them
  again. That is filed as its own row rather than papered over:
  `BL-20260911-THE-REGISTRY-OBSERVATION-CADENCE-IS-SLOWER-THAN-THE-STALENESS-WINDOW-SO-A-SUPPORTED-IN-FLIGHT-CLAIM-IS-UNREACHABLE-IN-PRACTICE`.
  ⚠️ **That id's own tail (`...-UNREACHABLE-IN-PRACTICE`) OVERSTATES the
  finding** — `supported` was measured at 3 of 30 half an hour later. The id is
  deliberately NOT renamed, because this doc references it by name and a
  rename would break the reference the way the truncation below did; read
  the row's own text, not its id.
  ⚠️ **And it was written here TRUNCATED (`...-STALENESS-WINDOW-…`) first**,
  which `check_backlog_refs.py` correctly failed in CI: a truncated id
  resolves to NOTHING, so the doc read as *tracked by a row that was never
  filed*. Write tracking ids in full, however long.
- **It does not make the module able to see liveness.** It makes it stop
  *claiming* to. The residue is unchanged and is the honest one: a lane that is
  genuinely working but unobserved for 90 minutes reads
  `could_not_establish` — *we could not look*, which is true.
- **The self-test's clock is now pinned.** Its fixtures carry fixed
  `state_observed_at` values against a wall clock, so their observation ages
  grew daily; `session_aaaaaaaa` was already ~14h "old" the day it was written.
  Harmless while freshness was ignored, decisive once it is read — an unpinned
  clock would make the self-test's result a function of the calendar.
