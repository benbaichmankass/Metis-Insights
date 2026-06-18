# Research Framework — high-throughput variation testing + signal isolation (DESIGN, 2026-06-18)

> **Tier-1 research/backtest tooling.** Offline orchestration over the existing
> harnesses + gates. Touches nothing live (`src/`, `config/strategies.yaml`,
> `config/accounts.yaml`, units the live VM consumes). Status: **DESIGN — for
> operator review before build.**
>
> Origin: operator direction 2026-06-18 — *"before we investigate a specific pair,
> create a research framework … run tests where we're checking a bunch of things
> at once and tweaking it … isolating signals so we understand what works better,
> what works worse."*

## 1. Why this exists

Today we can *fan a strategy across variations* (`recombination_sweep.py`: symbol ×
family × ADX × trail × selectivity → tier each). What we **cannot** do is the thing
that actually builds understanding:

> **Isolate which component of a strategy creates the edge** — is it the ADX gate,
> the trail distance, the confidence floor, or the entry geometry? And do it across
> many cells at once, cheaply, with a clean comparison at the end.

The recombination sweep swaps *whole primitives*; it never measures the **marginal
contribution** of one component (with-vs-without). That "ablation" capability is the
missing primitive for research throughput — it turns "this cell is paper_ready" into
"this cell is paper_ready *because of* the ADX gate (+18R) and *despite* the tight
trail (−4R)." Every future direction (cross-asset features, exit-manager swaps,
conviction blends) needs it.

## 2. What already exists (the seams we build on — do NOT rebuild)

The agents' inventory (verified against the repo):

| Capability | Where | Reuse as |
|---|---|---|
| **Per-trade emit contract** `{entry_time, net_r}` JSONL | every `scripts/backtest_*.py --emit-trades` | the universal interface between *any* variant and *any* gate |
| **K-fold gate + tier ladder** | `scripts/ops/m15_ws_b_fold_report.py` + `classify_strategy_tier.py` | gates ANY emit JSONL → reject/paper_ready/live_ready (no new logic) |
| **Portfolio robustness** | `scripts/ops/portfolio_robustness.py` | per-year / multi-cutoff holdout / leave-one-cell-out / bootstrap on a book |
| **Variation enumerator + coherence mask** | `scripts/ops/recombination_sweep.py` + `config/research/recombination_pool.yaml` | the matrix-enumeration engine to generalize |
| **Portfolio replay (real netting)** | `scripts/backtest_system.py` (`aggregate_intents`, shared-account risk) | confluence / multi-signal cells against live arbitration |
| **Execution substrate** | `.github/workflows/vm-driver.yml` + trainer VM + `automation/{jobs,results}/` | git-push → run-on-trainer → commit-back; MCP-independent |
| **ML experiment + purged-WF-CV** | `ml/experiments/{runner,splitters}.py`, `ml/manifest.py`, `gate-check` | feature-ablation A/B for *model* variants |

**The four real gaps** (this is what the framework adds):
1. **No per-component ablation** — can't run a cell with a component disabled and diff the P&L.
2. **No signal-level emit** — harnesses emit order-level trades, not decision points (when did the gate fire? which exit branch?).
3. **No parallelism** — sweeps are serial (a few-hundred-tuple run is hours-to-days on one core).
4. **No unified results store / leaderboard** — every sweep dumps an ad-hoc `.txt`; no queryable comparison across runs.

## 3. The design — four composable pieces

### 3.1 Ablation as a first-class axis (the core new primitive)

Generalize the recombination pool from "swap primitives" to "**toggle components**."
A *base cell* is defined by its full config; an *ablation* runs the same cell with
exactly one component neutralized, so `Δ = base − ablated` is that component's
marginal contribution.

Mechanism (reuses the harness CLI — most levers already exist):
- `--adx-min`/`--adx-max` already exist → ablate the regime gate by dropping them.
- `--trail-mult`, `--min-confidence` already exist → ablate by setting to the
  behaviour-preserving default.
- For components without a CLI lever (e.g. an entry sub-filter), add a single
  `--disable <component>` flag to that harness (small, additive).

Output: per base cell, a row per `{component → Δnet_r, Δtier, Δsharpe}` — the
**attribution table**. This is the "what works better/worse" the operator asked for.

### 3.2 Signal-level emit (`--emit-signals`)

Add an optional `--emit-signals` mode to the harnesses that writes one row per
*decision point* (not just filled trades): `{ts, candidate_side, gated_by,
confidence, regime, adx, exit_branch}`. This lets attribution answer "the ADX gate
rejected 240 would-be entries; the 60 it admitted netted +X" — component impact
*without* a full re-run. (Order-level emit stays the gate input; signal-level emit
is the analysis layer.)

### 3.3 Parallel sweep orchestrator (`scripts/ops/research_sweep.py`)

A generalization of `recombination_sweep.py` that:
- reads a **study spec** YAML (base cells + variation matrix + ablation axes),
- enumerates the coherent product **+ the ablation variants**,
- **partitions** the work and runs N shards concurrently on the trainer (the box
  has spare cores between training cycles; the vm-driver can fan out shards as
  parallel job bodies), and
- pipes every variant through the existing fold-report → tier → (optional)
  portfolio_robustness, writing one unified `summary.parquet`.

Throughput: serial today ≈ hours/hundreds-of-tuples; sharded ≈ cores× faster.

### 3.4 Results store + leaderboard (`automation/results/studies/<study>/`)

One canonical schema per variant row (label, axes, ablation, tier, net_r,
2x_net_r, sharpe, n_trades, per-fold, attribution Δ's), written as parquet +
a rendered markdown leaderboard. Queryable across studies so "what helped ETH
pullback" and "what helped the futures book" live in one comparable place. Mirrors
to the dashboard via the existing trainer-mirror path if useful.

## 4. The study-spec (one file drives a whole experiment)

```yaml
# config/research/studies/<name>.yaml  (Tier-1, research-only)
base:                       # the cell(s) under study
  - {entry: htf_pullback, symbol: ETHUSDT, tf: 2h, params: {...}}
matrix:                     # variations to sweep (Cartesian, coherence-masked)
  adx_min: [none, 20, 25, 30]
  trail_mult: [3.0, 5.0]
ablations:                  # components to toggle off, one at a time
  - regime_gate            # drop --adx-min/--adx-max
  - trail                  # set trail to baseline
  - confidence_floor       # set --min-confidence 0
gate: {kfold: {folds: 5, train_frac: 0.4}, fees_bps: [7.5, 15]}
holdout: out_of_pool        # optional OOP symbols/period
```

One `vm-driver` push runs the whole study and commits back the leaderboard +
attribution table.

## 5. Build order (each piece independently useful)

1. **`research_sweep.py` v1** = recombination_sweep generalized to read the
   study-spec + run ablation variants serially, unified parquet output. (Reuses
   everything; the only new logic is ablation enumeration + the results schema.)
2. **Parallel sharding** over the vm-driver (the throughput win).
3. **`--emit-signals`** on the two flagship harnesses (trend, pullback) +
   signal-level attribution.
4. **ML feature-ablation** hook (`gate-check` with-vs-without a feature block) —
   the bridge to the cross-asset work (see the cross-asset scope doc).

## 6. Guardrails (carried from every prior initiative)

- **Multiple comparisons.** A study with M variants × K ablations is a big search.
  Every survivor still clears the every-fold k-fold gate + 2×-fee + an **out-of-pool**
  holdout before any proposal — the discipline that has caught every overfit so far.
- **Attribution ≠ causation.** A component's Δ is in-sample marginal contribution;
  it must replicate out-of-pool before it's "the reason."
- **Tier-1 only.** The framework proposes cells; live wiring stays the Tier-3 PR.

## 7. Open decisions for the operator

1. **Ablation granularity** — component-level (gate/trail/confidence) is the cheap,
   high-value start. Sub-component (e.g. which ICT leg of ict_scalp) needs the
   `--emit-signals` work — do we want that in v1 or v2?
2. **Parallelism investment** — serial v1 ships fastest; sharding is the bigger
   build. Start serial and add sharding once a study is too slow?
3. **Scope of the first study** — propose the ETH/pullback ADX study (we have the
   data + a known answer to validate the framework against) as the v1 shakedown.
