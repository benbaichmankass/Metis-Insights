# Are any roadmap rows redundant? A census of all 33, and the answer is none

> **Doc status:** `live` · category `evidence` · last verified `2026-09-07` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Basis for that status:** every figure below was computed by this session (MI-160) on
> 2026-09-07 against the working tree at `origin/main`. It is `live` rather than `unknown`
> because *"nobody has looked"* is false here — the measurements are this session's own.
> ⚠️ **What `live` does NOT claim:** that the roadmap rows this document reasons over are
> themselves accurate. **31 of the 33 are marked `UNVERIFIED` by MI-159** and this session
> did not verify them (that is **T-11**). Every redundancy claim below is therefore a claim
> **about the text of a row**, not about the work it describes, and is labelled as such.

**PROPOSE-ONLY.** No milestone row in `ROADMAP.md` was edited by this session.

---

## 1 · The ask, and the scope boundary that governs it

Operator, verbatim:

> *"we can prune the road map if there are **redundancies**, just in general that it's not a
> problem to have open milestones as long as we have the tasks organized so that we're not
> building up technical debt, and we don't have blockers."*

**The permitted scope is REDUNDANCIES — two or more rows saying the same thing.** A row
describing work nobody is doing is **not** a redundancy; it is a track. That distinction is
not this session's invention:

> *"I don't really mind that there's lots of open items there. Right? Like, the UI isn't
> fully built out, but that's not something we're gonna put in time for right now. It's okay
> to understand that we have different tracks of things that we wanna take care of."*
> — operator, 2026-09-07

So this document does **not** recommend consolidating milestones for tidiness, does not
treat dormancy as debt, and does not re-raise the "too many open milestones" framing.
[`TASK-PRIORITY-2026-09-07.md`](../claude/TASK-PRIORITY-2026-09-07.md) binds itself to that
rule and it binds this document too.

The second clause — *"we're not building up technical debt, and we don't have blockers"* —
is the **condition** under which many open tracks are fine. Establishing whether that
condition holds is §§ 5–6, and it is the larger half of this document.

---

## 2 · The population

**33 rows.** Every line in § "Milestone Roadmap" matching `^| \*\*M`, i.e. `ROADMAP.md`
lines 108–141. `M0–M5` is one row spanning six milestones, so **the row count is not the
milestone count**. This is the same population MI-159 censused, re-derived independently
here and reproducing its count exactly.

| what was examined | how |
|---|---|
| all 33 rows, all 4 columns | read in full (the table is 330 KB across 34 lines; individual rows run to 20 KB) |
| duplicate milestone IDs | mechanical — `sort \| uniq -d` over the ID column |
| pairwise text similarity | mechanical — Jaccard over de-linked, de-code-fenced content words of the **Focus** column, all 528 pairs |
| the `M32`–`M35` gap | traced to its source |
| stated blockers | regex over every row for blocking language, then read in context |
| half-landed states | regex for the signature vocabulary, then read in context |

**What this document did NOT do:** re-grade any row's status against the field. That is
**T-11** and doing it here as a side effect is the exact defect MI-159 was dispatched to fix.

---

## 3 · Result: **0 redundancies proposed, over a population of 33**

No two milestone rows say the same thing. There are **no duplicate milestone IDs** and no
row is a restatement of another.

This is a negative result over a stated denominator, and it is offered as the finding
rather than as a failure to find one.

### The mechanical check, so this is not just an eyeball

Jaccard similarity over the Focus column, all 528 pairs. The top pairs by *substantive*
overlap (pairs sharing only 1 term are artefacts of the four rows whose Focus cell is three
words long — `Strategy tuning`, `Strategy review gate` — and are discarded):

| pair | Jaccard | shared terms | why they overlap |
|---|---:|---:|---|
| `M38` ~ `M41` | 0.198 | **72** | declared **inheritance** — M41 says so in terms |
| `M30` ~ `M36` | 0.160 | 28 | declared **composition** — M36 Track D *is* M30's integration |
| `M26` ~ `M36` | 0.127 | 20 | declared composition — M36 Track A *is* M26 |
| `M14` ~ `M19` | 0.105 | 10 | declared **succession** — M19 calls itself "the successor to M14" |
| `M29` ~ `M36` | 0.101 | 18 | declared composition — M36 Track C merges M28⊕M29 |

**Every one of the five highest-overlap pairs overlaps because a relationship between them
is written down and load-bearing** — inheritance, composition, succession. That is the
opposite of redundancy: it is one row correctly citing another. A prune of any of them
would break a citation that a reader depends on.

---

## 4 · The near-misses, and why each is NOT a redundancy

Recorded individually so the judgement is arguable rather than asserted, and so a future
session does not re-derive them.

### 4.1 `M38` (local vision backend) ~ `M41` (production-ready local LLM) — the closest call

**Highest text overlap in the table (72 shared terms), both about a local LLM, and it is
still not a redundancy.** M38 is `⛔ CLOSED` — the operator withdrew the *screenshot driver*
on 2026-08-23. M41 was opened 2026-09-04 to carry the surviving half, and says so:

> *"**INHERITS M38's SURVIVING FINDINGS RATHER THAN REDISCOVERING THEM** … M38 was closed
> with 'we should still build it, just not for the screenshots' — this is that build, with a
> different driver."*

So the shape is **closed record + open successor with an explicit citation**, which is
correct and is what the overlap consists of. Pruning M38 would delete the measured
constraint the operator paid for — that the public-repo burst worker **cannot carry any
sensitive payload** — which M38 itself flags as the expensive part and instructs future
sessions to re-read before proposing to route sensitive data through `llm-delegate` again.
**Keep both.** ⚠️ *Claim about the text of two rows; neither is verified against the field.*

### 4.2 `M9` (AI / model roadmap) ~ `M10` (HF / data pipeline)

They share a sub-section heading (§ "M9 / M10 — AI traders workstreams") and both cite
`WS5-B-PART-1`, `PART-2` and `WS5-C`. But their Focus columns differ and they report
**different deliverables from the same workstreams**: M9 reports which workstreams closed,
M10 reports which data families those workstreams added (`market_raw`, `market_features`,
`setup_labels`). Two lenses on one shared numbering, not one thing recorded twice.

⚠️ **What IS worth noting, as debt rather than redundancy:** both rows' status cells are
partial restatements of the `WS1–WS10` table 90 lines below them, and both are **behind
it**. The WS table grades `WS5` ✅ DONE (A through F); M9's row names only A, B-1, B-2 and
C. That is a restatement drifting from its own authoritative sub-table — the failure mode
`DOCUMENT-INDEX.md` exists to stop. It is a **T-11 verification item**, not a prune.

### 4.3 `M30` (quant-research platform) ~ `M40` (research & testing workflow)

Both read as "research infrastructure" and they are genuinely different layers. M30 builds
the **discovery** apparatus (C1 panel builder, C2 analyzer, the study ledger). M40 builds
the **workflow** around any research run (a results contract, landing assertion, the offload
loop, a power gate, the scheduler, trainer residency). Their ledgers are different
artifacts — `technical-quant-research-ledger.md` (Studies 0–8) versus the research
**disposition** ledger. Distinct.

### 4.4 `M17` (full-system audit) ~ `M39` (measurement integrity)

M39's own row states the relationship: it is *"the first milestone opened by the new
`review_coverage.structural_health` pass"*. M39 is a **child finding** of M17's audit
programme with two specific measured tracks, not a second copy of it.

### 4.5 `M14` (ML optimization) ~ `M19` (new model types)

M19 is "the successor to the M14 optimization program", and both are open. **Succession
with both ends open is not redundancy** — M14 improves the existing narrow tabular
classifiers; M19 opens new model *kinds*. Different scope, explicit relationship.

### 4.6 `M8` (strategy tuning) ~ `M21` (entry refinement) — the one genuine adjacency

The weakest "not a redundancy" on this list, recorded because it is the closest thing to a
duplicated *capability*. M8 sweeps strategy parameters via `scripts/ml/strategy_tune_sweep.py`
(including `min_confidence`, which **is** an entry filter). M21 points the M20 harness at
entry quality (confirmation filters, breakout-depth, time-of-day cells). Different
harnesses, different deliverables, different parent pipelines — but **overlapping parameter
families**. That is an adjacency worth watching for the
`RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED` hazard, **not** a row to prune. Neither row is
verified; both statuses are stale (M8's newest date is 89 d old, M21's 41 d).

### 4.7 The `M32`–`M35` gap is not four missing rows

The milestone table jumps `M31 → M36`. `M32`–`M35` **are** used in the document — inside
**M28's** row, as labels for M28 macro-research sweeps (`M32` credit/rates, `M33` calendar
seasonality, `M34` cross-family conjunction, `M35` crypto OI/basis). They are not milestones
and no rows are missing.

The token `M31` genuinely does double duty — *position telemetry* (the milestone) and
*implied-vol / options-skew* (an M28 signal family, cited as "M31 Track B" in both M28 and
M36). **This is already caught and disposed of inside the M31 row itself**, which carries an
explicit `⚠️ Id note` saying the collision is *"in the label namespace only"*. Recorded here
only so the next census does not re-open it. **No action.**

---

## 5 · Blockers — the condition the operator's clause depends on

### 5.1 The headline coverage figure has moved, and the dispatch's number is stale

`docs/claude/READOUT.md` is a **dated snapshot (2026-09-02)**, not a live read. Re-measured
today against `docs/claude/work/objects/*.yaml`, using the READOUT's own definition
(`blocked_on_basis` beginning `ASSESSED` / `RE-ASSESSED` / `CLEARED`):

| | READOUT snapshot 2026-09-02 | measured 2026-09-07 (MI-160) |
|---|---:|---:|
| objects in store | 587 | **654** |
| ASSESSED basis | 9 (**1.5%**) | **44 (6.73%)** |
| NOT_ASSESSED | 578 | 608 |
| basis field absent | — | 2 |
| objects carrying ≥1 edge | 9 | **16** (25 edges total) |

**Assessed coverage has improved 4.4× and is still an order of magnitude below the 50%
floor**, so the readout's verdict (`insufficient_basis`, no stage named) stands. Note that
44 assessed but only 16 carrying an edge means **28 rows are genuine `declared_none`** —
someone looked and claimed nothing blocks them. That is real progress and the 1.5% figure
should not be re-quoted.

Lifecycle today: `dormant` 600 · `done` 25 · `waiting` 13 · `ready` 13 · **`in_flight` 3**
(against the ceiling of 8 — headroom 5).

### 5.2 The larger gap: **61.8% of known work is not in the store at all**

This is the finding that most changes how the readout should be read. The store's
denominator is not the population of known work:

| register | rows | with **no** work-store object |
|---|---:|---:|
| `health-review-backlog.json` | 1,274 | **775 (60.8%)** |
| `performance-review-backlog.json` | 114 | 69 (60.5%) |
| `ml-review-backlog.json` | 107 | 85 (79.4%) |
| `research-review-backlog.json` | 16 | 5 (31.2%) |
| **total** | **1,511** | **934 (61.8%)** |

The Phase C migration of 2026-09-01 carried 584 objects; the backlogs have kept growing
since (`CLAUDE.md` records `health-review-backlog.json` at 951 rows — it is **1,274** today).

**So there are two layers of "nobody looked", and only the inner one is currently
reported.** The readout says 608 of 654 objects carry an unassessed `blocked_on`. Against
the *known* work population the assessed figure is **44 / 1,511 = 2.9%**. An empty
`blocked_on` means nobody looked; **a missing object means there is nothing to look at.**

### 5.3 No milestone has a work-store object, so milestone-level blockers are structurally invisible

Store composition by ID prefix: `BL` 510 · `WO` 77 · `PB` 40 · `MB` 22 · `SRQ` 2 · `PRB` 2 ·
`RS` 1. **Not one of the 33 milestone rows has an object.** The `WO-` objects are
session/phase work objects, not milestones.

The constraint computation reads the work store. The roadmap is a *different register*. So
the question *"is any milestone blocked on something nobody has named?"* **cannot be
answered from the work store today** — not because the answer is no, but because milestones
are not in the population it computes over. This is stated as a structural gap, not as a
proposal to migrate them (that would be a design decision for the operator).

### 5.4 Blockers stated in prose, carried by no typed edge

These are the true blocking edges I found **named by the rows themselves**. They are offered
as **candidates for an assessment pass**, with evidence and with what is not established.

| # | blocked | blocked on | evidence | edge exists? |
|---|---|---|---|---|
| **B1** | `M20` — two exit-lever cells | MGC/MHG IBKR history that **no scheduled job produces** | M20 row names `BL-20260814-MGC-MHG-IBKR-HISTORY-NEVER-PULLED` | **No — and it cannot.** That id has **no work-store object**; it exists only as text inside `health-review-backlog.json` (5 hits) and `ml-review-backlog.json` (1). |
| **B2** | `M24` P3/P4 | widening broker-truth cost coverage | M24 row: *"blocked on widening broker-truth cost coverage first"*; this is **T-15** | Object exists (`BL-20260830-BROKER-TRUTH-LEDGER-STALE-59-REAL-MONEY-CLOSES-UNRECONCILED`) but is `dormant` with `blocked_on: []` / `NOT_ASSESSED` |
| **B3** | `M22` P4 (P(win) entry filter) | *"a genuinely new input — order-flow features **or** a net-R label"* | M22 row states the gate explicitly | No edge. **Both upstreams are other milestones' deliverables** — the net-R label is M24's, the order-flow features come from the trainer capture in M40's R6 |
| **B4** | `M31` P5 | P4 Check B returning `compared` — *"a DATA-ACCRUAL problem the writer does not solve"* | M31 row | No edge; see § 6.4 — this is a **declared exclusion**, not debt |

**B3 is the one that compounds and is worth the operator's attention.** M22's P4 gate can
be satisfied by order-flow features, which come from `ict-orderflow-capture.service` — the
stream M40's own row describes as **forward-only and unbackfillable**, running on a trainer
disk measured at **92% full**, with **nothing monitoring it** and with
`active (running)` explicitly *not* evidence it is capturing
(`BL-20260829-ORDERFLOW-CAPTURE-IS-IRREPLACEABLE-AND-UNMONITORED`,
`BL-20260829-TRAINER-DISK-92-PCT-THREATENS-THE-UNBACKFILLABLE-CAPTURE`). A silent stall
would not merely delay M22 P4 — it would destroy the input permanently.
⚠️ **NOT VERIFIED BY THIS SESSION.** Those figures are M40's, dated 2026-08-29. The trainer
is reachable only through the issue relay, which this session did not use. **Whether the
capture is alive right now is unknown and this document does not claim it is.** The
`BL-20260829-ORDERFLOW-CAPTURE-IS-IRREPLACEABLE-AND-UNMONITORED` object is on disk, `dormant`, `blocked_on: []`,
`NOT_ASSESSED`.

### 5.5 Why this session wrote **no** edges into the work store

The dispatch invited writing TRUE edges. I wrote none, deliberately.

Each of B1–B4 is derived from **row prose that is itself unverified** (31 of 33 rows carry
MI-159's `UNVERIFIED` verdict). Writing a typed edge would put an unverified claim into the
one structure the constraint computation treats as measured. The store's own rule is that
*an invented edge is read as a real blocker, and a false blocker is worse than a missing
one* — and, as one 2026-09-06 re-assessed basis in the store records, **a false NON-blocker is the
more dangerous direction still**, because a session picks the work up expecting to finish it.

Asserting `ASSESSED — nothing blocks this` on the two objects above would have been the
cheap move and would have been a claim about an observation nobody made. **Four candidates
with their evidence, and an honest `NOT_ASSESSED`, is the correct output of a session that
did not do the per-object evidence pass.** Each of B1–B4 needs one, and each is a small,
well-scoped task.

---

## 6 · Half-landed debt — where a row describes the state that is worse than either end

`CLAUDE.md` names this class directly: **`done` means merged and observed; `landed_unproven`
means the code is in and its effect has not been seen on the fleet — collapsing those two is
what this repo keeps paying for.** Named specifically:

### 6.1 `M40` R5 — a scheduler that is built, scheduled, and deliberately inert

> *"The R5 dispatcher is deliberately **NEVER FIRED**. Its daily 06:20Z cron passes no
> `--fire` … **none of the three spend gates** … has ever been observed holding against this
> dispatcher, and a test with a stubbed `_fire` proves the counter advances while proving
> nothing about the ledger preflight."*

A cron that runs daily and does nothing, whose three safety gates are unexercised.
`OI-20260827-RESEARCH-QUEUE-DISPATCHER-NEVER-FIRED`. **The clearest half-landed row in the
table.**

### 6.2 `M40` R2 — landing is solved for 2 workflows and unsolved for 18

`assert_rows_landed.py` is built (four states, 13 planted controls) and wired into
`e35-bracket-sweep.yml` and `trainer-offload-train.yml`. **18 evidence workflows still
upload-and-land-nothing.** This is already ranked as **T-16**; recorded here as debt because
a landing guarantee that covers 2 of 20 producers reads, from the outside, like a landing
guarantee.

### 6.3 `M40` R3 — the row **contradicts itself and the stale half comes first**

Measured by character offset within the single 19,020-character M40 row:

- offset **2,005**: *"⚠️ **The drain is UNARMED on both axes** (timer enabled by nothing;
  `OFFLOAD_DRAIN_APPLY` must be exactly `1`)"*
- offset **3,881–3,972**: *"The `ict-offload-drain.timer` is installed, `enabled`+`active`
  (every 20 min) with `Environment=OFFLOAD_DRAIN_APPLY=1` **resolved by systemd**"*

Both are dated 2026-08-28. **The first is refuted by the second ~1,900 characters later and
carries no supersession marker**, so a reader scanning R3 — which is where you look for the
drain's state — takes away the wrong answer. This is documentation debt inside a row, and it
is exactly the *"do not re-quote the previous basis"* discipline the rest of the repo applies
and this row did not.

### 6.4 `M39` (A) — shipped, live, and its new branch has never executed

> *"**(A) SHIPPED 2026-08-24 … and is live on the trader** … ⚠️ **DO NOT MARK (A) DONE —
> criterion 2 is still UNEXERCISED.**"* — 4 post-deploy closes observed, all four had a real
> exit price, so **none reached the `unmeasured` branch the fix added**. *"No regression
> observed; **no exercise either**."*

Track (B) is not started, and its measurement (93 of 93 pairs closes provenance-silent)
is stronger than the argument it was filed on. **Correctly refusing to mark itself done** —
this is the row modelling the discipline, and it is still an open half-landed state.

### 6.5 Others, named without elaboration

- **`M20`** — *"DEPLOYED, NOT PROVEN: no `/api/bot/config` read has yet shown the values on
  the running trader."*
- **`M37`** — *"UNEXERCISED (dispatch-only so far) and the label may not exist"*;
  *"never fired — inferred, not verified."*
- **`M15`** — the T+1 cash-settlement gate is armed on `alpaca_live` and **has never refused
  anything**, and cannot until the account makes a sale.

### 6.6 The distinction that must not be lost: a **declared exclusion** is not half-landed

Applying the operator's own rule one level down. These read like debt and are not:

- **`M31` P5 "deliberately withheld"** — precondition 2 is a data-accrual problem the writer
  does not solve, and the row says so and stops. That is a **stated scoping decision with its
  reason**, not an unfinished thing.
- **`M40`'s research queue "idle BY DESIGN"** — all 5 jobs `not_due` **with stated reasons**.
- **`M21` `📋 DORMANT`** and **`M38` `⛔ CLOSED`** — a paused track and a withdrawn driver,
  both recorded as decisions. Neither is debt.

**Half-landed is: the code is in and its effect has not been seen. A declared exclusion is:
we decided not to, and wrote down why.** §§ 6.1–6.5 are the first; § 6.6 is the second.

---

## 7 · What this session deliberately did NOT propose

Recorded explicitly, because the instruction was that the scope is redundancies and that a
track is not a redundancy.

| not proposed | why |
|---|---|
| Pruning any of the **13 `📋`/`⛔`/dormant-flavoured rows** (`M18`, `M19`, `M21`, `M23`, `M38`, `M39`, `M41`, …) | Every one is a **track**, deliberately unfunded. `M21` is dormant with an honest negative; `M38` is a closed record still cited by `M41`; `M41` was opened 3 days ago on operator directive. Dormancy is an input to ranking, never a problem to solve. |
| Consolidating `M23`/`M24`/`M25` (three ML rows opened within 2 days) | Three distinct deliverables — a take/skip head, a net-R label, a promotion harvest. Adjacent timing is not redundancy. |
| Merging `M36` into the milestones it composes | M36 **is** the composition. Merging it away deletes the integration work itself. |
| Merging `M38` into `M41` | § 4.1 — it would delete the measured public-repo constraint and break M41's own citation. |
| Filling the `M32`–`M35` "gap" | § 4.7 — they are M28 sweep labels. There is nothing missing. |
| Re-grading any row's status | That is **T-11**. Doing it as a side effect is the defect MI-159 was dispatched to fix. |
| Writing typed `blocked_on` edges | § 5.5 — each needs a per-object evidence pass this session did not do. |

---

## 8 · Recommended next actions (proposals, for the operator to rule on)

Ordered by value, none of them a prune:

1. **Do the assessment pass on B1–B4** (§ 5.4). Four objects, evidence already gathered
   here. The readout's own guidance is that a true edge is *"the single highest-value thing
   you can do to this store"*.
2. **Rule on the migration gap** (§ 5.2). 934 of 1,511 known rows have no object. Either
   migrate them, or state that the store is deliberately a subset — but the readout should
   report the outer denominator either way, so `2.9%` is not read as `6.7%`.
3. **Fix the `M40` R3 self-contradiction** (§ 6.3) — a one-line supersession marker on the
   stale `UNARMED` claim. Cheap, and it is currently misinforming any reader of R3.
4. **File B3's compound risk** (§ 5.4) — verify the order-flow capture is alive and get a
   monitor on it. Unlike everything else here, the failure mode is permanent data loss.
5. **T-11 remains T-11.** 31 rows unverified; this document did not change that number.

---

## 9 · Summary

| question | answer |
|---|---|
| rows examined | **33** (the full § "Milestone Roadmap" population) |
| redundancies proposed | **0** |
| duplicate milestone IDs | 0 |
| highest text overlap | `M38`~`M41`, 72 shared terms — a **declared inheritance**, not a duplicate |
| blockers found stated in prose but carried by no typed edge | **4** (B1–B4) |
| typed edges written | **0**, deliberately (§ 5.5) |
| `blocked_on` assessed coverage | **44/654 = 6.73%** in-store; **44/1,511 = 2.9%** against known work |
| half-landed rows named | **5** (§§ 6.1–6.5) |
| declared exclusions explicitly NOT counted as debt | **4** (§ 6.6) |

**A proposal of "0 redundancies found" over a stated denominator was named in the dispatch
as a perfectly good result. It is the result.** The value of this session is in §§ 5–6: the
condition the operator's clause depends on — *no technical debt and no blockers* — **does not
currently hold**, and the reasons are specific, measured, and individually small enough to
fix.
