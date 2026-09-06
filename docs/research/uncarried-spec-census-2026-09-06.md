# The un-carried spec census — how many finished specifications does nothing point at?

**Measured 2026-09-06** against `origin/main` at **`817a5a5f`** (probe run from branch
`claude/count-uncarried-specs-20260906`, working tree `02133786`).
**MI-152 · `WO-20260906-COUNT-THE-UN-CARRIED-SPECS`. Tier-1** — a measurement, a report and a
proposed check. Nothing here touches `src/`, `config/`, or an order path.

Instrument: [`scripts/ops/uncarried_specs.py`](../../scripts/ops/uncarried_specs.py) —
stdlib-only, read-only, writes nothing and mutates no register.

---

## 0. The answer

**Between 40 and 98 of 111 specifications are un-carried, depending on where the carrier
boundary is drawn. The 14-day `EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md` gap was not a
one-off — it is the norm.**

The operator's framing was *"if the answer is twelve, it reframes what this cycle's real
constraint is."* Under the strictest defensible reading the answer is **98**. Under the
loosest reading anyone could argue for, it is **8**. Under every reading in between it is
**dozens**.

The sub-population where *"specifies work"* requires **no judgement at all** — 13 artifacts
whose **own text** says the work is unbuilt (*"has never been run"*, *"is not built"*,
*"NOT YET SHIPPED"*) — is **11 of 13 un-carried**, and the only 2 that are carried were both
given carriers **today**, by the program that discovered this problem.

---

## 1. Population — stated, as required

| | count |
|---|---|
| Artifacts scanned under `docs/research/` + `docs/design/` | **331** (research 312, design 19) |
| Excluded from the population by a reasoned rule | 2 (`RESEARCH-CAPABILITY-INDEX.md` — a routing index over `scripts/`, not a spec; `README.md` — orientation) |
| Register / doc surfaces read for the carrier join | **1428** |
| **Classified as SPECIFICATIONS (the headline denominator)** | **111** (research 92, design 19) |
| — tier A (filename declares it) | 98 |
| — tier B (the artifact's own text declares its work unbuilt) | 13 |
| Borderline, tier C — reported separately, **NOT** in the headline | 118 |
| Neither | 102 |

Every number below is over the **111**, not the 331.

## 2. The classifier — the rule I used, published

*"Specifies work"* is a judgement, so here is the rule, and it **prefers over-inclusion**: a
marginal artifact listed separately is honest, a silent exclusion is the same failure class
being measured.

- **Tier A — the filename declares it.** Contains any of `SESSION-PROMPT`, `PROPOSAL`,
  `-DESIGN`/`DESIGN-`, `WORKPLAN`, `-PLAN`/`PLAN-`, `-SCOPE`, `PROGRAM`, `-BRIEF`, `PACKET`,
  `FEASIBILITY`, `-PROCESS`, `METHODOLOGY`. **Known over-inclusion:** a `-DESIGN` whose build
  shipped is still counted. I cannot mechanically establish "did this ship", so it is counted
  and flagged rather than silently dropped.
- **Tier B — the artifact's own text directs a future session.** *"paste this whole file"*,
  *"as the opening message"*, *"next session should"*, *"has never been built/run/started"*,
  *"not yet built/shipped/wired"*, *"proposed but never implemented"*. **This tier needs no
  judgement from me** — the document says it about itself. It is the load-bearing sub-population.
- **Tier C — borderline, excluded from the headline.** Only a weak forward-looking phrase
  (*"recommendation"*, *"next steps"*, *"follow-up"*). n=118. These are mostly evidence
  reports whose job was only ever to report. **Listed as a separate number so the exclusion is
  visible**; if the operator judges them in scope, the un-carried count rises accordingly.

### Borderline cases, explicitly

- **`-DESIGN` files whose thing shipped.** Counted as specs. The probe cannot see "built".
- **Evidence reports carrying a recommendation** (tier C, n=118). Excluded from the headline.
- **`docs/design/` is counted whole** — all 19 files match tier A. That is correct by
  construction (it is the design directory), not a probe artifact.

## 3. The positive control — mandatory, and it caught a real defect

The work object required the probe be shown to **find a carried spec** before its silence
means anything. Two calibration points were named: the operating-layer design (**six**
carrying rows, known positive) and `EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md` (known negative).

| control | expected | result |
|---|---|---|
| `docs/design/operating-layer-build-plan-DESIGN.md` | carried | **`active`, 9 work-object carriers** (`WO-20260901-PHASE-A..H` + `OPEN-ITEMS.json`) — ≥ the 6 the work object states |
| `docs/research/EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md`, measured against the **pre-remediation** tree `daebbada` | NOT carried | **`dormant_only`** — reproduces the known 14-day stranding |

Both are wired into the probe as `--self-test` (and `--strict-negative`), so it fails loudly
rather than silently mis-grading.

### ⚠️ The control caught the probe being wrong, in the dangerous direction

**The first cut of this probe graded the known-stranded prompt as `planned`** — i.e. it would
have reported the pile as far smaller than it is. Two separate errors:

1. **The control was contaminated by this session's own program.** Run against *today's*
   tree, the negative control is legitimately carried — `WO-20260906-COUNT-THE-UN-CARRIED-SPECS`
   and `WO-20260906-THE-EXIT-GEOMETRY-REBUILD-WAS-SPECIFIED-AND-NEVER-DISPATCHED` were both
   created **today**, by the work that diagnosed the stranding. A control measured *after* the
   remediation proves nothing about the condition. The census is therefore run at HEAD but the
   negative control is asserted against the pre-remediation commit.
2. **"Named by a work object" is not "carried".** At `daebbada` the prompt's only work-store
   reference was `BL-20260823-IB-TRAILING-...yaml` — a `lifecycle: dormant` bulk-migrated
   backlog row about IB trailing, which names the artifact only to say a caveat was *"carried
   **into**"* it. It points **outward**. The repo's own words settle it: that row's
   `review_trigger` reads *"A dormant object is NOT a queued one — nothing is scheduled to
   pick this up"*, and CLAUDE.md calls the migrated population *"carried, not started, and NOT
   queued."*

## 4. The carrier ladder — five states, never collapsed

Binary "carried / not carried" is what produced the error above.

| state | meaning |
|---|---|
| `active` | an `in_flight`/`waiting` work object, or `OPEN-ITEMS` / manager checklist / due list. Something is moving or being watched. |
| `queued` | only a `ready` work object. Nothing is moving, but it is claimed. |
| `dormant_only` | only `dormant`/`done` objects. **By the store's own semantics nothing is scheduled to pick it up.** This is the state the motivating incident was in. |
| `mentioned` | only a non-carrying surface (a backlog row's prose, ROADMAP, another artifact, a skill). A reference is not a carrier. |
| `uncarried` | named nowhere outside itself. |
| `unreadable` | **we could not look.** Never folded into `uncarried` — a failed read is not evidence of absence. (n=0 this run.) |

`active` and `queued` are the only two that mean a session meets the artifact **without
someone happening to think of it**.

## 5. The count

```
CARRIED — a session meets it without anyone thinking of it:
  active         13
  queued          0
NOT CARRIED — nothing is scheduled to pick it up:
  dormant_only    1
  mentioned      89
  uncarried       8
  -> UN-CARRIED TOTAL: 98 of 111 specs (88.3%)
```

### Sensitivity to the carrier boundary

89 of the 98 are `mentioned`-only, and **57 of those are named in `ROADMAP.md`** — the level-3
canonical "centralized record". Whether a ROADMAP mention is a *carrier* or a *record* is the
single biggest judgement in this measurement, so it is measured both ways rather than asserted:

| reading | un-carried | share |
|---|---|---|
| **STRICT** — only `active`/`queued` work registers carry | **98** | 88.3% |
| a `ROADMAP.md` mention counts as a carrier | **40** | 36.0% |
| ROADMAP **or** a sprint log counts | **24** | 21.6% |
| **LOOSEST** — any mention anywhere counts | **8** | 7.2% |

**The finding is robust to the boundary**: the operator's "if the answer is twelve" threshold
is exceeded under every reading except the loosest, and the loosest is not defensible — it is
precisely the reading under which the motivating incident (one incidental mention) counts as
carried.

### Tier B — the judgement-free core

13 artifacts say of **themselves** that the work is not built. This sub-population needs no
classifier argument at all:

| reading | un-carried |
|---|---|
| STRICT | **11 of 13** |
| ROADMAP counts | **4 of 13** |
| LOOSEST | 0 of 13 |

The only 2 carried are `bracket-calibration-2026-09-06.md` and
`exit-lever-wiring-audit-2026-09-06.md` — **both written today**, both carried because this
week's program put objects behind them by hand. That is the mechanism the operator is asking
for, executed manually, once, for two files out of 111.

## 6. The un-carried list

### Named nowhere outside themselves (n=8) — the hardest cases

| artifact | what it is |
|---|---|
| `docs/research/bracket-geometry-decision-packet-2026-08-26.md` | *"a PROPOSAL. Nothing here is applied"* — 14 `passed_unshipped` exit-geometry cells. **One of the seven re-derivations named in the motivating backlog row.** |
| `docs/research/A1-backtest-augment-runner-SCOPE-2026-08-02.md` | *"SCOPE only. No runner is built or dispatched"* |
| `docs/research/research-to-results-cost-gate-DESIGN-2026-08-01.md` | design + build plan for the R4 cost-gate |
| `docs/research/M27-P1-15m-promotion-proposal-2026-07-22.md` | a promotion proposal |
| `docs/research/trainer-compute-ceiling-proposal-2026-07-29.md` | a proposal |
| `docs/research/crypto-finetune-proposals-2026-07-30.md` | proposals |
| `docs/research/prop-firm-testing-tool-DESIGN.md` | a tool design |
| `docs/research/options-level3-alpaca-research-BRIEF-2026-06-27.md` | a research brief |

### `dormant_only` (n=1)
`docs/research/exit-reason-frozen-at-close-2026-08-22.md` — its own text: *"is not shipped"*.

### `mentioned`-only (n=89)
Full list: `python3 scripts/ops/uncarried_specs.py --triage`. The tier-B members are the
priority: `T1-gpu-burst-spend-SPEC.md` (*"NOT YET SHIPPED"*),
`e1-exogenous-panel-preconditions-2026-08-20.md` and `e2-feature-information-2026-08-20.md`
(*"has never been run"*), `e35-rr-floor-walkforward-2026-08-20.md` (*"is not built"*),
`technical-quant-research-platform-scoping-2026-07-27.md` (*"is unbuilt"*),
`sol-pullback-stale8-deep-corpus-2026-08-18.md` (*"is still NOT shipped"*),
`exit-attribution-broker-truth-2026-08-22.md` (*"next session should"*),
`promotion-gatecheck-and-mes-labeling-2026-06-26.md`, `roadmap-toolbox-assessment-2026-07-29.md`,
`RESEARCH-INFRA-AUDIT-2026-07-30.md` (*"remains unbuilt"*).

## 7. What this measurement does NOT establish

- **It cannot tell whether an un-carried spec's work was already done** by some path that never
  named the file. A design whose thing shipped still reads un-carried. This is a real
  over-count in the tier-A population and is why tier B is quoted alongside.
- **Basename matching over-credits carriers.** A register mentioning a *similarly named*
  artifact counts as a hit. This biases toward **under**-reporting the pile — the conservative
  direction for a claim that N artifacts are stranded.
- **It is a snapshot at `817a5a5f`.** A register row landing after that changes a grade.
- **A carried spec is not a progressing one.** `active` means something names it, not that
  anything is happening.

## 8. The mechanism — proposed, with which half it covers

The backlog row offers three candidates. **Choosing with reasons:**

- **(c) a scheduled sweep — rejected.** It produces a report nobody is obliged to read. The
  OI row warns explicitly that *"an eighth document restating the diagnosis makes this row
  MORE true"*, and a ninth memo about un-read memos is exactly that.
- **(a) a CI check on new artifacts — necessary but insufficient alone.** It covers only the
  **new** half and would leave all 98 invisible. And the 14-day gap **was not caused by bad
  filing** — the artifact was well-written and correctly filed.
- **(b) a rendered `CLAUDE.md` SESSION-BRIEF section — the load-bearing half.** Per CLAUDE.md
  it is *"the only surface that reaches a session before it acts"* (project hooks do not run on
  Claude Code on the web, and CI fires at merge — after the wrong work is built). It covers
  **both** halves: it renders the existing pile, and a new artifact enters it automatically
  with nobody filing anything.

**Proposal: (b) then (a). (b) covers the existing 98; (a) covers the new half.**

Two constraints the design must respect, or it reproduces a failure this repo has already paid for:

1. **A rendered list of 98 rows is alarm fatigue** — the P1 this repo names by name. The brief
   section must be **bounded and ranked**: tier B first (judgement-free), then `uncarried`
   tier A, newest first, hard-capped at ~10 rows with the residual count stated. That is ~21
   candidates today, capped to a readable section.
2. **Disposition must have no third state**, copying `scripts/ops/check_research_index.py`'s
   proven shape (its `EXEMPT` requires a mandatory reason and reports dead entries as stale). A
   spec is either **named by a work object** or **dispositioned with a written reason**
   (`shipped` / `superseded` / `not-work`). *"Nobody got around to it"* must not be representable
   — that is the state all 98 are in now.

⚠️ **Stated honestly:** the SESSION-BRIEF surface is itself **UNPROVEN**
(`OI-20260826-SESSION-BRIEF-NEVER-READ-BY-A-FRESH-SESSION`). Building on it is the best
available option, not a guaranteed one, and its own verification stays owed.

---

**Clears clause (1)** of `OI-20260906-RESEARCH-THAT-SPECIFIES-WORK-IS-CARRIED-BY-NOTHING`
(the count is measured, with a stated classifier and a positive control). **Clause (2) is not
cleared** — the mechanism is proposed here, not built, and has not been run over the existing
tree as a live surface.
