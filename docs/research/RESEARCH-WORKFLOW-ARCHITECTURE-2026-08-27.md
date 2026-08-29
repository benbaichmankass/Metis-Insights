# Research & testing workflow — the architecture, and what actually leaks value

> **Operator directive, 2026-08-27:**
> *"I don't care about utilizing the VM specifically, I care about value maximization
> from all our tools together … we can transfer the entire trainer infra to the git
> runners if that's better, and leave the VM available for trading ops. We also have
> the GPU bursts — the idea is to optimize the general research and testing workflows
> in general, not on a specific tool. The idea is that we can have more consistent,
> higher frequency training and backtesting, both for MLs and for strategies, with
> actual usable results that are recorded correctly. That should be the guiding
> principle here."*

**MEASURED** unless marked otherwise. Every figure below was read from the repo, the
live API, or the trainer box this session.

---

## 0. ⚠️ CORRECTION, 2026-08-27 — instance 1 below is WRONG and is retracted

**I claimed the e35 sweep's rows never landed. They landed, on main, on the day
the sweep ran.** Measured after the claim was already in this doc, a commit
message, a PR body and a backlog row:

`docs/research/e35-bracket-corpus.jsonl` **on `origin/main`** holds **8,211 rows ·
6,581 stop cells (`sm*`) · 41 legs**, of which **4,378 carry
`sweep_generated_at` 2026-08-26** — the very run I said had produced nothing
readable.

**How I got it wrong, because the mechanism is the lesson.** I probed
`m20-sweep-corpus.jsonl` for `sm*` cells, found zero, and called it absence. That
file is the M20 **lever** corpus; e35 bracket cells go to a **separate** store.
Its lack of stop cells is *correct*. I anchored on that filename because
`BL-20260823-E35-SWEEP-EVIDENCE-HAS-NO-DURABLE-PATH`'s resolution criteria
*suggested* it (*"Acceptable: an e35-shaped extractor writing into
m20-sweep-corpus.jsonl"*) — and never checked what the implementer actually
chose. They chose a separate file, which is **better**: separate stores cannot
silently merge, which was that row's own stated worry.

⚠️ **AND MY "NEGATIVE CONTROL" DID NOT COVER THIS.** I verified the probe could
find positives (`--contains trail` → 212 rows) *in that file*. That controls for
the **probe** and not for the **population**. `CLAUDE.md` § RULE ONE says a
search returning nothing is not proof of absence and *"a negative needs a
denominator"* — the denominator I owed was **"is this the store e35 writes to?"**,
and I never asked it.

**Consequences, stated rather than quietly fixed:**
- `BL-20260823-E35-SWEEP-EVIDENCE-HAS-NO-DURABLE-PATH` is **RESOLVED**, not open.
- The finding below is **TWO** instances, not three.
- **R2 (`assert_rows_landed.py`) loses its strongest case.** Instance 2 still
  justifies it and the tool is sound, but its headline example was mine and it
  was wrong. Whether it keeps its priority is the operator's call, not a
  conclusion this doc should carry as if unaffected.

---

## 1. The finding: compute is solved. Landing is not. ~~Three~~ **Two** times.

The system has repeatedly moved work to the right tool and repeatedly failed to make
the **result** arrive. Three instances were filed this session and **one was
retracted the same day (§ 0)** — the two that survive are below, and the
retracted row is kept in the table rather than deleted so the correction is
visible where the claim was made:

| # | compute | landing |
|---|---|---|
| ~~e35 bracket sweep~~ | ✅ 19-leg parallel matrix on free runners | ✅ **RETRACTED — see § 0.** The rows landed on main the day the sweep ran: 8,211 rows / 6,581 stop cells / 41 legs, 4,378 of them from the 08-26 run. This instance was my error, not the system's. |
| **`trainer-offload-train.yml`** | ✅ trains an OOM-prone manifest on a 4 vCPU / 16 GB runner | ❌ runs `--no-register`; its own header calls wiring the model back *"the documented **v2 slice**"* — so the model is trained and never joins the fleet |
| **M20 coverage matrix** | ✅ 468 cells graded | ❌ the three defects that condition every verdict live in the backlog, not in the matrix |

⚠️ **This is not a story about carelessness.** Each of the three is individually
well-built and honestly documented — `trainer-offload-train`'s header even states the
correct thesis (*"The gap was never compute; it was that the pipeline runs ON the
trainer"*). The failure is that **"the job exited 0" is treated as done**, and nothing
asserts the result arrived.

> **The guiding principle, stated from the evidence: a run's deliverable is a LANDED,
> ADDRESSABLE, SELF-DESCRIBING result. The compute is not the deliverable.**

---

## 2. Why runners win, and by how much

| resource | cores | RAM | concurrency | cost | state |
|---|--:|--:|---|--:|---|
| **GitHub runner** | 4 | 16 GB | effectively unlimited, parallel | **$0** (public repo) | none — ephemeral |
| **Trainer VM** | **1** | 6 GB | **serialized** (one core) | $0 but scarce | persistent |
| **GPU burst** | GPU | — | serialized by the workflow | metered, **$10/mo cap** | ephemeral |

A runner is **4× the cores, 2.7× the RAM, and parallel**. `trainer-offload-train`'s
header records the concrete consequence: a manifest that **OOM-quarantined the trainer
for 18.7 h in D-state** trains fine on a runner.

For *"more consistent, higher-frequency training and backtesting"* the arithmetic is not
close: one serialized core against an unlimited parallel fleet. **Frequency is not
bought by optimising the trainer; it is bought by leaving it.**

## 3. What is actually pinned to the VM — and the answer is: almost nothing

> ⚠️ **CORRECTED 2026-08-29 — this section's heading is WRONG, and the table below is
> right about what it measured.** R6 was run
> ([`R6-VM-RESIDENCY-VERDICT-2026-08-28.md`](R6-VM-RESIDENCY-VERDICT-2026-08-28.md))
> and found a process that genuinely requires 24/7 residency:
> **`ict-orderflow-capture.service`**, a continuous 2-second L2 order-book capture,
> up 44 days, whose data is **forward-only and unbackfillable by construction**.
>
> **It is invisible below, and the reason is the transferable part.** This table was
> built with `du`. The capture's entire output is **5.7 MB — 0.02 % of the 28 G tree** —
> so it sits below the resolution of the instrument, and no amount of careful reading
> of these five rows would have surfaced it. The heading promises *"what is pinned to
> the VM"*; the table answers *"what DATA is pinned"*. Those two questions come apart
> exactly on a process with a large residency requirement and a tiny footprint — the
> repo's own **UNPROVENANCED DIAGNOSTIC OUTPUT class A** (semantic substitution), one
> level up from code.
>
> **Residency is a property of PROCESSES and their input streams, not of stored bytes.**
> Enumerate units first, disk second. The disk figures themselves re-measure as still
> accurate (§ 5 of the verdict), so nothing below is retracted — only its scope.

Measured on the box (41 G / 45 G used, 4.0 G free):

| tree | size | genuinely VM-resident? |
|---|--:|---|
| `datasets-out/market_features` | **11 G** | **No** — derivable, and a manifest needs only *its own pinned version* (e.g. `BTCUSDT/15m/v002` = 465 MB), which a runner can fetch |
| `.venv` | 5.4 G | **No** — and 1.1 G of it is `libcublasLt` + `libtriton`, **CUDA libraries on a CPU-only box** |
| `ml/experiments-runs` | 4.7 G | **No** — these are outputs that should be published, not retained |
| `runtime_logs/m20_exit_head` | 2.5 G | **No** — research byproduct |
| `ml/registry-store` | **9.8 M** | **This is the only real one** — and it is already mirrored to the live VM every 2 min and served at `/api/bot/ml/registry` |

**18 G of the 28 G repo tree is research byproduct that was never landed anywhere a
session can read.** The trainer's disk is, literally, the graveyard of the landing
problem.

⚠️ **And the dataset cache cannot be fixed by garbage collection.** `BTCUSDT/15m`
alone holds **v002, v003, v004, v513, v514, v515, v520** — seven versions at ~400–500 MB
each, back to 2026-07-01. The GC reclaims 0.09 G because **41 manifests pin 111 of 115
version dirs**. It is a retention-policy question (when may a pin be released?), not a
cleanup chore.

### The prize, quantified

The Always-Free Ampere pool is **full**: trainer 1 + gateway 1 + live 2 = **4 of 4 OCPU,
24 of 24 GB**. So the trainer's **1 OCPU / 6 GB is the only headroom that exists** for
growing trading ops. Retiring it is not a tidy-up — it is the sole path to more live
capacity, at $0.

---

## 4. The architecture

Six pieces, in dependency order. **Only R1–R3 are prerequisites; R5 is the payoff.**

### R1 — One results contract *(the foundation)*

Every run — ML train, strategy backtest, lever sweep, macro study — emits rows in **one
schema**, and a row is not admissible unless it carries:

| field | why |
|---|---|
| **what ran** | tool + version/sha |
| **on what data** | dataset id **+ version + fingerprint** — `BL-20260810` (no dataset recorded) and `BL-20260812` (no frame fingerprint) are both open because this is missing |
| **params** | including the **risk basis**, resolved through `src/research/risk_basis.py`, never re-derived |
| **what it measured** | the result |
| **its power** | `n`, and the minimum detectable effect — see R4 |
| **where the artifact is** | a durable, addressable path |

This is the `MEASURED` mark made executable: a number that cannot say what it measured,
on what, with what power, is not a result.

### R2 — Landing is part of the run *(motivated by § 1; see § 0 — its strongest example was retracted)*

A run's final step **re-reads the committed store from `main` and asserts its own rows
are present**, failing loudly if not. Cheap, mechanical, and it is precisely what would
have caught e35 on 08-26 instead of four days later.

> A job that exits 0 having landed nothing is a **failed** job.

### R3 — Close the offload loop

`trainer-offload-train` v2: register the trained model into the registry + mirror. This
is the single change that makes a runner a **full substitute** for the trainer on ML, and
it is already scoped in the workflow's own header.

### R4 — Pre-registration with a **BLOCKING** power gate *(operator-decided, 2026-08-27)*

A queue entry declares the question, the expected `n`, and the minimum detectable
effect, **before** it runs. If the harness cannot meet the bar, **the experiment does not
run** — it converts into a data-acquisition task.

The evidence for blocking rather than advisory: advisory is what exists now, and it
produced **329 `honest_negative` verdicts at a median OOS base of 33 trades**, of which
**only 96 state a denominator at all**. An underpowered run currently costs compute *and*
manufactures a false finding; blocked, it costs nothing and yields a known gap.

### R5 — The scheduler *(the payoff: consistency + frequency)*

Once R1–R3 hold, higher frequency is a cron and a matrix. Routing is by **state
requirement**, not habit:

```
Does this need state that ONLY the VM holds?
├─ NO (almost everything) ──▶ free runner, parallel matrix, $0
├─ GPU-bound ─────────────▶ GPU burst, inside the $10/mo cap
└─ YES ───────────────────▶ the VM — and R6 asks whether anything still qualifies
```

### R6 — Then decide the VM's fate, on evidence

✅ **MEASURED 2026-08-29 — and the hypothesis below was REFUTED.** Full working:
[`R6-VM-RESIDENCY-VERDICT-2026-08-28.md`](R6-VM-RESIDENCY-VERDICT-2026-08-28.md).

**The original text, kept as record:** *"INFERRED, not measured: the likely answer is
the dataset cache (solvable by a fetchable content-addressed store) and 24/7 cron
(solvable by scheduled workflows) — leaving nothing, and freeing 1 OCPU / 6 GB for
trading ops. That is a hypothesis to test after R3, not a decision to take now."*
Labelling it a hypothesis was right; testing it changed the answer.

| inferred residual | measured |
|---|---|
| the dataset cache | ❌ **not** residency — 32 version dirs, a retention question, fetchable |
| 24/7 cron | ⚠️ **partly** — batch timers are runner-portable; **two live-serving timers** (`ict-trainer-forecast` → `fc_*` → the live advisory head, via `ict-trainer-publish`) are re-hostable only with work |
| *(unlisted)* | 🔴 **`ict-orderflow-capture`** — continuous, forward-only, **irreplaceable** |

⚠️ **R3 proved COMPUTE is portable. It did not prove ACQUISITION is.** The very model R3
registered as its proof — `btc-regime-5m-lgbm-flow-v1` — is the **order-flow** model,
trained on columns only that 24/7 capture produces; its manifest says that without the
microstructure side-stream *"the columns are 0.0 and this collapses to the v2 head."*
Reading "a runner trained a model" as "the trainer is disposable" inverts what was shown.

**The question R6 replaced itself with — ✅ ANSWERED 2026-08-29, it was an operator call
and the operator made it.** The question was *where does the order-flow capture live if the
trainer does not?* (87 MB of RAM, 0.37 % of a core — not *this* VM, but *an* always-on host).
**Decision: keep the trainer, for the capture, `"in the meantime"`** — recorded as
option (c) of the retired `OI-20260829-ORDERFLOW-CAPTURE-HOME-UNDECIDED`: kept *as a stated
decision rather than by default*, and **interim, not permanent**. Full record:
[`R6-VM-RESIDENCY-VERDICT-2026-08-28.md` § 8](R6-VM-RESIDENCY-VERDICT-2026-08-28.md).
So the standing recommendation *"do not retire the trainer"* is now a **decision**, not a
pending caveat — but note what that changes: the box is load-bearing **by choice**, which
raises the stakes on the two findings below rather than closing them. ⚠️ The trainer disk is at **92 % (42 G used of 45 G,
3.9 G free — one `df -h` reading, not a trend)** and a full disk stalls that capture
*silently*, since nothing monitors it —
both filed (`BL-20260829-TRAINER-DISK-92-PCT-THREATENS-THE-UNBACKFILLABLE-CAPTURE`, `BL-20260829-ORDERFLOW-CAPTURE-IS-IRREPLACEABLE-AND-UNMONITORED`)
and both now ride `OI-20260829-TRAINER-IS-NOW-A-DECIDED-DEPENDENCY-AND-IS-UNMONITORED` (`loud`, re-observed every 3 days).
⚠️ **Re-measured 2026-08-29 (relays #10422 + #10423): the capture is ALIVE and WRITING** — `datasets-out/market_microstructure/BTCUSDT/5m/v001/data.jsonl`
at `17:30:00Z` against a same-command `date -u` of `17:30:17Z`; disk still 3.8 G free / 92 %. ⚠️ **That path is the canonical one and is worth copying rather than guessing:**
the first probe assumed `runtime_logs/orderflow/`, which does not exist, and its empty result was **byte-identical to a dead capture** — separable only because the
second probe carried a positive control (88 files in the same window). A monitor keyed on the wrong path pages forever or never.

---

## 5. What this is NOT

- **Not "move everything to runners today."** R3 is the gate; until a runner-trained
  model can register, the trainer is still load-bearing for ML. *(R3 holds as of
  2026-08-28, so that gate is open — but see R6: the trainer remains load-bearing for
  **data acquisition**, which is a different claim and was never what R3 tested.)*
- **Not a throughput problem.** Compute is free and abundant. Every measured leak is a
  landing failure.
- **Not more guards.** R2 is an assertion inside a run, not a CI checker over a diff.
