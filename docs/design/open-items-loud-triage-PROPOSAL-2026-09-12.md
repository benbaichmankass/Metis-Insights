# PROPOSAL (Tier-1): `loud` is set on 84.5% of `OPEN-ITEMS.json` and has stopped selecting anything

> **Doc status:** `live` · category `plan` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**MI-279 (OPS lane, `/backlog-drain`), `session_013iSqp4LsU1eq8K326eUtgj`.**
**This is a PROPOSAL to the manager. Nothing was re-flagged.** No row's `loud`
value was changed, no renderer was edited, and no threshold was set. The
decision is the manager's and, where it changes what the operator is paged
about, the operator's.

---

## 1. The measurement

**POPULATION:** all **84** rows of [`docs/claude/OPEN-ITEMS.json`](../claude/OPEN-ITEMS.json)
at `origin/main`, read **2026-09-12**. Every figure below is from that one read.

| | |
|---|--:|
| rows | **84** |
| **`loud: true`** | **71 (84.5%)** |
| age since `opened` — median / p90 / max | **10d / 13d / 17d** |

By kind:

| kind | rows | loud | loud share |
|---|--:|--:|--:|
| `monitoring` | 64 | **57** | **89%** |
| `awaiting_verification` | 12 | 8 | 67% |
| `background_awareness` | 6 | 4 | 67% |
| `pending_decision` | 2 | 2 | 100% |

Observation state, computed from `check_every_days` against `verified_at`:

| state | rows |
|---|--:|
| `accruing` (cadence not yet elapsed) | **28** |
| `due` (cadence elapsed) | **32** |
| `never_observed` (`verified_at` null or `"never"`) | **8** |
| `no_cadence` declared | 16 |

**32 rows are overdue against their own cadence, but only 2 by ≥7 days**
(worst: `OI-20260901-OPERATING-LAYER-BUILD-IS-IN-FLIGHT-AND-CARRIED-ONLY-BY-THIS-ROW`,
+10d). ⚠️ **That matters for the diagnosis: the cadence is broadly being
honoured.** This is not a neglected register and the problem is not that
sessions ignore it. And the median age is **10 days** — this is not accumulated
cruft either. The flag was over-applied from the start.

## 2. Why this is a defect and not untidiness

`CLAUDE.md` states the rule the flag drives:

> *"A row with `loud: true` must be REPORTED ON in your closing summary — checked and stated, never silently carried."*

**A compliant session must therefore report 71 rows.** Nobody reads a 71-item
page, so the rule is either violated — which is the norm — or obeyed uselessly.
Either way the flag has stopped selecting, and a selector that selects 84.5% of
its population is not a selector.

**This is the desensitised-alarm P1 this repo names as its own worst failure
mode, reproduced inside the register built to prevent it.** It is the same
shape as `BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART`, which put
one condition on 53.7% of the operator's entire ERROR+ feed and trained the
channel away.

**The root cause is that `loud` marks IMPORTANCE, and everything here is
important by construction — that is why it is in the register at all.** So the
flag cannot discriminate, and any hand-setting drifts to `true`.

## 3. The repo has already solved this exact problem once

Operator directive, **2026-09-02**, canonical in
[`docs/CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md) § "A soak must
carry its own alarm". A soak is graded into **four states that are never
collapsed**, and the surfacing is DERIVED from the state:

| state | surfaces? |
|---|---|
| `ready` | **loud row** |
| `not_writing` (dead soak) | **loud row** |
| `unknown` (could not read) | quiet row |
| `accruing` | **no row** — context only |

with the reasoning stated outright: *"`accruing` DOES NOT ESCALATE, and that is
a deliberate refusal … a daily 'soak not ready' ping is the desensitised alarm
this document already calls a P1."* **That rule REMOVED a page rather than only
adding one.**

⚠️ **Only 4 of the 84 rows declare a `soak` block**, so that doctrine currently
governs ~5% of the register while the other 95% uses a hand-set boolean with no
state model at all.

## 4. The proposal

**Stop declaring `loud`. Derive it**, using the soak doctrine's shape, from
fields the row already carries (`kind`, `check_every_days`, `verified_at`).
A session may set the **inputs**; it may not set the selector.

**Selector C**, of four tested against today's register:

> **LOUD if** `kind == "pending_decision"`, **or** the row has **never been
> observed**, **or** it is overdue by **≥ 2× its own `check_every_days`**.
> Everything else that is DUE renders as a **quiet one-line row**; everything
> `accruing` renders **no row at all**.

Measured effect on the 84 rows read today:

| selector | loud | share |
|---|--:|--:|
| today (hand-set) | 71 | 85% |
| A — `pending_decision` only | 2 | 2% |
| B — A + never-observed | 10 | 12% |
| **C — A + never-observed + overdue ≥2× cadence** | **22** | **26%** |
| D — every DUE row | 40 | 48% |

**The test of a selector is whether its selection reads like a to-do list, and
C's does** — it picks out precisely the *shipped-but-never-observed* and
*badly-overdue* rows, e.g.
`OI-20260909-THE-ROLLBACK-REGISTRY-IS-COLLAPSED-AND-THE-ROLLBACK-HAS-NEVER-BEEN-EXERCISED`,
`OI-20260910-ORPHAN-ADOPT-SIZE-GATE-DEPLOYED-AND-HAS-REFUSED-NOTHING`,
`OI-20260911-THE-TWO-DETECTORS-ARE-BUILT-AND-NEITHER-HAS-EVER-FIRED-ON-THE-FLEET`.
Those are exactly what this register exists to carry.

### What earns `loud`
1. **An operator decision genuinely waiting** (`pending_decision`) — always, and
   it is a small stable set (2 today). This is the one thing that should always
   page.
2. **Never observed even once** — a row whose mechanism has never been seen
   working is the class most likely to rot unnoticed, and it is the class this
   register was created for.
3. **Overdue by ≥2× its own cadence** — the row's author chose the cadence; two
   misses is the row's own standard being broken, not an invented threshold.

### What does NOT earn it
- **A row accruing on schedule.** It is the expected state of a healthy
  monitoring row for its entire life. Paging on it is the P1 above. (28 rows today.)
- **Importance, real-money reach, or severity on their own.** They are already
  the admission criteria for the register; re-using them as the selector is what
  produced the 71-of-84 (84.5%) reading at the top of this document.

### Who decides
- **Nobody declares it.** The renderer computes it; `loud` stops being a writable field.
- **The operator owns the two thresholds** (the `2×` multiple, and whether
  `pending_decision` is unconditional).
- **The manager owns adoption** and whether an explicit override survives at all.

⚠️ **If an override IS kept, it must be verified, not presence-only** — the
`new-table-wiring-guard` lesson this repo has already paid for: an override that
is cheaper to set than to satisfy re-creates the drift within weeks. The honest
alternative is no override.

## 5. What this proposal does NOT claim

- ⚠️ **It does not claim any row is unimportant.** It claims the *flag* does not
  discriminate. Every row stays in the register; what changes is which ones
  interrupt a closing summary.
- ⚠️ **It is not evidence that sessions are ignoring the register.** Only 2 of
  84 rows are ≥7d overdue — the opposite.
- ⚠️ **Selector C's threshold is CHOSEN, not tuned.** There is no labelled set
  behind it: it is one reading of one register on one day, and the four variants
  are offered so the choice is the operator's rather than mine. The `2×`
  multiple is the row's own declared cadence doubled, which is the least
  invented option available.
- ⚠️ **`no_cadence` (16 rows) is left deliberately unaddressed.** A row with no
  `check_every_days` cannot be graded by any of these selectors, and inventing a
  default cadence for it would be a second, separate decision. Under C those
  rows surface only if they are `pending_decision`. **That is a real gap in this
  proposal and it is stated rather than hidden.**
