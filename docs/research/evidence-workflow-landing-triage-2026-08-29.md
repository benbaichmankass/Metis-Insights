# Evidence-workflow landing triage — the classification `BL-20260827` asked for

**Date:** 2026-08-29 · **Closes the analysis half of** `BL-20260827-EIGHTEEN-EVIDENCE-WORKFLOWS-UPLOAD-AND-LAND-NOTHING`.

---

## 0. What this is NOT, because the row is explicit about it

> ⚠️ *"'DOES NOT LAND' IS A SHAPE, NOT A DEFECT, AND MUST NOT BE READ AS 17 BUGS."*
> *"**NOT sufficient:** adding `assert_rows_landed` to all of them, which would answer the wrong
> question loudly."*

There is also a recorded **operator decision (2026-08-27)** against wiring R2 assertions into them
now, because for most of these nobody has decided what store they would assert against — that is
**R1 (the results contract)**, not R2.

**So this document adds no assertions and changes no workflow.** It is the classification, with a
reason per workflow, which is what the row's `resolution_criteria` actually names.

---

## 1. First: the row's inventory is STALE, and stale in the one place that matters

Re-measured today with the row's own predicate
(`git push|git commit|git-auto-commit|add-and-commit|create-pull-request|peter-evans|gh pr create`)
and, critically, **its own positive control** — the four known landers must come back as landing,
or the probe is broken and its silence means nothing:

| control (known lander) | hits |
|---|---|
| `e35-bracket-sweep` | 2 ✅ |
| `gpu-burst-train` | 1 ✅ |
| `m20-exit-lever-sweep` | 4 ✅ |
| `training-rerun-5m` | 2 ✅ |

Control passes. Against the row's 18 "does not land":

> **`trainer-offload-train` NOW LANDS** (4 landing hits, and 2 `assert_rows_landed` calls).

That is not a small correction. `trainer-offload-train` was the row's **only confirmed
wanted-result-lost case** — *"Only trainer-offload-train is a confirmed case of a result someone
WANTED that never arrived."* R3 fixed it on 2026-08-28 (#10368/#10390), one day after the row was
filed.

**Current population: 22 evidence workflows, 5 land, 17 do not.**

⚠️ **The lesson is about the artifact, not the number.** A hand-maintained inventory in a backlog
row went stale in **one day**, in the row's own headline case. The row filed the list *"as a
DENOMINATOR so the next session does not have to re-derive it"* — and the next session had to
re-derive it anyway. **A denominator that cannot re-measure itself is a snapshot, not a
denominator.** If this list is to be relied on again it should be produced by a script, not typed.

---

## 2. The classification

Basis: each workflow's own header block (this repo writes explicit purpose headers) plus its
trigger set. **The distinguishing question I applied**, stated so it can be argued with:

> Is the value in the **accumulation** — many rows across time/legs/symbols, compared *to each
> other*, such that a later session wants to query the corpus? → **(a)**
> Or is the value **one answer to one question at one time**, which belongs in a doc or a backlog
> row? → **(b)**

### (a) MEANT TO ACCUMULATE — 10

These want a declared store under R1, and only then a landing assertion under R2.

| workflow | why accumulation is the value |
|---|---|
| **`research-backtest-augment`** | The strongest case, and the only **scheduled** one. Header names its store *and* its consumer: *"records the trades as `is_backtest=1` rows into ONE `backtest_trades.db` artifact — the W1.2 → W2.1 handoff."* A declared handoff to a downstream stage that never lands is the defect shape exactly. |
| **`m20-capture-census`** | Per-leg MFE-capture **baseline distributions**, explicitly *"to size the prize and order the legs BEFORE a lever is designed."* A baseline's whole purpose is to be compared against later. |
| **`pullback-frac-cross-leg-sweep`** | *"Does ONE `pullback_frac` generalise across the fleet"* — 19 legs in two strata. **Cross-leg comparison IS the question**, so per-leg rows are the answer, not a byproduct. |
| **`regime-debt-matrix`** | A per-(trend-regime, direction) net-R matrix over a roster — a matrix is a corpus by construction. |
| **`regime-cell-walkforward`** | Per-cell OOS verdicts, and the `regime-selectivity` skill makes a walk-forward a **precondition** for a Tier-3 OFF-cell. Unstored verdicts mean every revisit re-runs the gate. |
| **`regime-adx-cutpoint-sweep`** | Per-strategy robustness verdicts on cut-points *"every `config/regime_policy.yaml` cell keys on"* — fleet-wide and comparable. |
| **`research-panel-build`** | Self-described *"STABLE backtest-substrate discovery **infrastructure**"* — a reusable substrate. ⚠️ Its header also says the entry panel is *"now exhausted — Studies 7/8/9/10"*, so confirm it is still wanted before spending R1 effort on it. |
| **`research-exit-head-build`** | Builds the dense per-bar in-trade exit panel and trains the head. Same substrate argument as its sibling above. |
| **`research-symbol-p0-build`** | Per-symbol P0 validation across a roster — dispatched repeatedly, one symbol at a time. |
| **`gld-compat-matrix`** | Per-account ROUTE verdicts. **Lane B needs a before/after verdict diff across two runs of this exact workflow** (`OI-20260827-LANE-P-COMPAT-VERDICTS-UNMEASURED`) — which is precisely the comparison an unstored run cannot support. |

### (b) ONE-SHOT PROBE — 7

Answer belongs in a doc or a row; **committing a corpus would be make-work**, which the row warns
about by name.

| workflow | why one-shot |
|---|---|
| **`flip-override-walkforward`** | Answered, and the answer was **acted on** — the live override was disarmed 2026-08-11 on its result. Retained because re-arming requires a *fresh* walk-forward, but each run is a decision artifact, not a row in a series. |
| **`c1-conviction-ab`** | The C1 deployable-evidence A/B. Its `PARTIAL` verdict is recorded in `ROADMAP.md` and `docs/research/C1-conviction-sizing-evidence-2026-08-04.md`. |
| **`dukascopy-span-probe`** | Measures a **data-source property** (how far back the feed serves) once; the answer is adjudicated in a doc. |
| **`research-e2-horizon-arm`** | One arm varying one condition (label horizon) against a fixed reference — a study arm, not a series. |
| **`prop-tp-r-gate`** | An EV/survival gate for one specific Tier-3 `tp_r` decision — *"measure first, then decide."* |
| **`ict-scalp-backtest`** | An explicit **human-read pre-live gate**: *"the operator must see a passing backtest summary here before flipping `enabled: true`."* |
| **`vwap-backtest`** | *"comments results back on the issue"* — the output is addressed to a person. See § 3. |

### (c) DEAD — 0 proposed, and I am declining to guess

**I have no run-history evidence.** The intended probe (per-workflow last-run via `actions_list`)
returned a response too large to read and was abandoned rather than half-read.

⚠️ **And low recency would be weak evidence anyway**: 16 of these 17 are `workflow_dispatch`/`issues`
triggered, so *"has not run lately"* is the **normal** state of an on-demand research tool, not a
death certificate. Reading it as one would retire working infrastructure.

**What would actually settle (c)**, for whoever picks it up: a workflow is dead if its *subject* is
gone — the strategy it gates is retired, the milestone it serves is closed, or a named successor
supersedes it — not if it merely sits idle. That is a per-workflow read, and none of the 17 failed
it on the headers I read.

---

## 3. One finding that is not about landing at all

**`vwap-backtest` SSHes into the trainer VM, and appears not to need it.** Its header says it runs
there *"to avoid compute contention"*, but it *"always pulls fresh candles from Bybit"* — a public
feed — and runs a backtest over them. That is **CPU-only work over public data**, which
`docs/claude/vm-resource-management.md` and the R5 routing rule both send to a free runner.

Every sibling in this population already made that move, and several say so in their own headers
(`research-symbol-p0-build`: *"the resource-optimized home for work that was WRONGLY being run as a
trainer-vm-diag SSH job"*). This one looks like it was simply never revisited.

**Not acted on here** — it is a live workflow and re-homing it is its own change with its own
verification. Recorded because it is directly relevant to
[`R6-VM-RESIDENCY-VERDICT-2026-08-28.md`](R6-VM-RESIDENCY-VERDICT-2026-08-28.md): it is the only
member of this population still pinned to the trainer, and R6's question is what still needs to be.

---

## 4. What this does and does not close

**Closes:** the classification `BL-20260827`'s `resolution_criteria` asks for — every workflow in
exactly one bucket, with a reason.

**Does not close, deliberately:**
- **R1 for the 10 (a) workflows** — each needs a *declared store and schema* before any assertion
  is meaningful. That is the operator-decided ordering, unchanged.
- **(c)** — no run-history evidence gathered; § 2 says what would settle it.
- ~~**The stale-inventory problem itself** (§ 1). The fix is a script, not a better-typed list.~~
  **CLOSED later the same session — see § 5.** Left struck through rather than deleted, so this
  section still reads as the state at the time the classification was made.

---

## 5. § 4's last item, done: the inventory now re-measures itself

§ 4 left *"the stale-inventory problem itself. The fix is a script, not a better-typed list."*
That script is [`scripts/ops/evidence_workflow_inventory.py`](../../scripts/ops/evidence_workflow_inventory.py).

It **asserts nothing and gates nothing** — the operator decision of 2026-08-27 against wiring R2
assertions into these workflows is untouched, and remains R1's question. It reports.

- **Landing predicate taken verbatim from the backlog row**, so the row and the script cannot drift
  into two disagreeing denominators.
- **Positive control runs first and short-circuits everything.** If the four known landers stop
  classifying as landing, it prints *no counts at all* and exits non-zero — because a predicate that
  silently stopped matching would otherwise report "nothing lands" and look like a dramatic finding.
- **Three states, never collapsed:** `lands` · `does_not_land` · `unreadable` (*we could not look* —
  not "it does not land").
- **`--self-test` with planted controls**, including one that pins a **known over-report**: the
  predicate is a substring match, so a comment merely *mentioning* `git push` reads as landing. That
  case asserts the wrong-but-actual behaviour deliberately, so anyone tightening the predicate sees
  it fail and learns why it was there. It can only over-report landing, never under-report — it
  cannot manufacture a "nothing lands" finding, which is the direction that would matter.

### 5.1 First run — and it does not reproduce the 22, which is itself the finding

Positive control **OK**. Population: **40 workflows upload an artifact**, of **120** workflow files;
**7 land, 33 do not, 0 unreadable**.

**40 ≠ the 22 counted in § 1, and narrowing the regex until it returned 22 would have been the wrong
fix** — that is a differently-typed hand list wearing a script's clothes. Uploading an artifact is
**necessary but not sufficient** for producing research evidence: `get-diag-token`, `prop-report`,
`llm-delegate`, `continue-work` and `health-snapshot` all match and are plainly operational.

> **The repo has no mechanical definition of "evidence workflow."** That is precisely why the 22 was
> hand-curated, and why it went stale in a day. The script reports the superset and says so; the
> (a)/(b) judgement stays here, where it can be argued with.

Supplying that definition — a marker in the workflow header, say — is the natural R1 companion, and
is **not done here**: it is a convention that needs deciding, not inferring.

### 5.2 It independently reproduces § 1's correction

`trainer-offload-train` comes back **`lands`** from the mechanical scan, with no hand input —
the same correction § 1 had to make by hand against the backlog row's headline case. That is the
script doing the job the list could not.
