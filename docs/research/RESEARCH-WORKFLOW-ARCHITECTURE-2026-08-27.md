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

## 1. The finding: compute is solved. Landing is not. Three times.

The system has repeatedly moved work to the right tool and repeatedly failed to make
the **result** arrive. Three independent instances, all found this session:

| # | compute | landing |
|---|---|---|
| **e35 bracket sweep** | ✅ runs as a 19-leg parallel matrix on free runners | ❌ corpus job shipped 2026-08-23, sweep ran 08-26, and `m20-sweep-corpus.jsonl` is **still 1,379 rows with ZERO stop cells** — byte-identical population to the 08-23 measurement |
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

### R2 — Landing is part of the run *(fixes all three instances in § 1)*

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

After R3, re-measure what still requires residency. **INFERRED, not measured:** the
likely answer is the dataset cache (solvable by a fetchable content-addressed store) and
24/7 cron (solvable by scheduled workflows) — leaving nothing, and freeing 1 OCPU / 6 GB
for trading ops. **That is a hypothesis to test after R3, not a decision to take now.**

---

## 5. What this is NOT

- **Not "move everything to runners today."** R3 is the gate; until a runner-trained
  model can register, the trainer is still load-bearing for ML.
- **Not a throughput problem.** Compute is free and abundant. Every measured leak is a
  landing failure.
- **Not more guards.** R2 is an assertion inside a run, not a CI checker over a diff.
