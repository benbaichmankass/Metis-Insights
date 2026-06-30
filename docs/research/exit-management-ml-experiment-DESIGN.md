# Exit-management ML — experiment DESIGN (2026-06-30)

> **Tier-1 research/experiment design.** Offline feasibility + observe-only soak;
> no live order path, config, or money-DB change until a backtest-gated,
> operator-approved graduation. For operator review before build.
>
> Origin: the redirect from
> [`where-edge-lives-entry-wall-2026-06-30.md`](where-edge-lives-entry-wall-2026-06-30.md)
> — entry-prediction edge is at the M18 wall (rules + flexible ML agree), and the
> one ML head that works is the **regime** head. So point the same rigor at the
> **untested** frontier: exit management.

## 1. Why exit, not entry

The entry-wall finding leaves a sharp hypothesis: **the strategies' entries are
~coin-flip, so most of the realized-PnL variance — and most of the recoverable
edge — is in how trades are *managed and exited*, not in when they're opened.**
Two supporting facts:

- The project's ML that demonstrably works is **regime/context** (the live,
  A/B-validated vol-gate), not point prediction — and exit management is a
  context problem (given an open position + its trajectory, is holding still
  favorable?).
- Exit is **causally downstream** of an already-taken position, so a useful exit
  signal needs less of the impossible "predict the market" and more of the
  tractable "is this move exhausting / is my stop mispriced" — a different,
  less-efficient information set than entry direction.

Critically, **the wall here is untested.** M18 measured *entry* outcomes; no one
has measured whether a model can beat the fixed SL/TP exit out-of-sample. This
experiment tests that — cheaply, because the infra already exists.

## 2. What "exit-management ML" means (precisely)

Condition on an **OPEN position** (entry taken by a rule strategy — unchanged)
and learn an exit/management decision per bar:

- **Framing A — optimal-exit classifier.** For each in-trade bar, label: does
  *holding* (to the eventual SL/TP) realize a better R than *exiting now* at the
  current price? Target = `should_hold ∈ {0,1}`. The model's P(hold) vs a
  threshold drives an early-exit / hold decision.
- **Framing B — remaining-favorable-excursion regression.** Predict the forward
  max-favorable-excursion (in R) from the current in-trade bar, vs the forward
  max-adverse. A learned dynamic take-profit / trail.
- **Framing C — stop-mispricing.** Predict P(stop is hit before a +Rβ target
  within H bars) from the current state — a learned, regime-aware stop placement.

Start with **Framing A** (cleanest label, binary, directly backtestable). B/C are
follow-ons if A clears.

## 3. The existing exit-research infra to reuse (this is why it's cheap)

| Need | Already exists |
|---|---|
| **A starting corpus + the soak pattern** | The **ExitPlan exit-ladder shadow-soak** (`src/runtime/exit_ladder_soak.py`, `/api/bot/exit-ladder/soak`) — already logs, per executed order, the laddered exit that *would* be used vs the single SL/TP actually placed (dynamic-take-profit consistency P3; graduation is the backtest-gated P4). The learned exit head is the **learned version of exactly this**; the soak is precedent + data. |
| **Labeling** | `ml/datasets/labeling/triple_barrier.py` (de Prado, stdlib, unit-tested) — apply it from the *in-trade* bar instead of the entry bar to get forward excursion / hold-value labels. |
| **Features** | `ml/datasets/families/market_features.py` (offline==online bar features) + the position state (unrealized R, bars-held, distance-to-SL/TP, regime stamp). |
| **Trade trajectories** | `trade_journal.db::{trades, order_packages}` (entry/exit/SL/TP/PnL) + the candle CSVs the backtest harnesses read → reconstruct per-bar in-trade trajectories. |
| **Train / eval / gate / ladder** | The whole manifest → trainer (LGBM) → evaluator (purged-WF CV + live_holdout) → registry → `gate-check` → shadow→advisory ladder. 100% reuse. |
| **Backtest harness** | `scripts/backtest_*.py` already simulate exits; an exit-policy arm compares learned-exit vs the fixed SL/TP on net-of-fee R (the same harness P0's `--backtest-log` used). |

**New code is small:** an `exit_candidates` dataset family (in-trade-bar sampling
+ Framing-A labeling via `triple_barrier`), a manifest, and — only at graduation —
an exit-influence shim. The analysis/eval/promotion stack composes.

## 4. The cheap feasibility test (pre-registered, autonomous, zero live risk)

1. **Build the `exit_candidates` dataset.** For each closed trade, walk its
   in-trade bars (from the candle data); at each bar emit features (market_features
   + position state) and the Framing-A label (`should_hold`: did holding to the
   actual exit beat exiting at this bar's price, in R). Backtest-generated trades
   (the harnesses, for volume) + real closed trades (`is_live_trade` flag, for the
   live-holdout — same domain-shift contract the meta-label head uses).
2. **Train an exit head** (LGBM classifier) on bar+position features.
3. **Evaluate OOS** under **purged walk-forward CV** + **live_holdout**. The
   pre-registered **kill criterion**: the exit head's `should_hold` must
   discriminate OOS — **AUC > 0.55** (and beat a "always hold to SL/TP" baseline) —
   AND, when its decisions are simulated in the backtest harness, deliver a
   **net-of-fee R improvement vs the fixed SL/TP**. Miss either → the exit wall is
   as hard as the entry wall; stop and record it (itself a valuable result).
4. **If it clears**, wrap it as an observe-only exit annotator at `shadow` (logs
   would-be exits next to actual, exactly like the exit-ladder soak), accrue a
   live track record, then graduate via the normal gate.

**This first step is ~the same cost as the entry free-read** — a dataset family +
a manifest + a backtest arm — and answers "is exit timing learnable OOS?" before
any live surface.

## 5. Phasing + tiers (mirrors every other model lifecycle)

- **P0 — offline feasibility (Tier-1).** Dataset + manifest + OOS eval + backtest
  arm → the AUC/net-R verdict. No live surface. *First deliverable.*
- **P1 — observe-only shadow soak (Tier-2 — runs on the live trader).** The exit
  head logs would-be exits per open position (the exit-ladder-soak pattern). Ships
  with a kill-switch + per-tick wall-clock budget — "observe-only ≠ zero
  compute-risk on the money box" (the lesson from
  [`signal-research-framework-DESIGN.md`](signal-research-framework-DESIGN.md) §10).
- **P2 — backtest-gated apply proposal (Tier-3).** Net-of-fee + survival vs the
  fixed exit on the account-compat matrix; operator-approved.
- **P3 — advisory exit influence (Tier-3).** The exit head conditions the actual
  exit (e.g. early-close / dynamic-TP), graduated like the regime vol-gate.

## 6. Honesty / risks

- **This is ALSO M18-gated.** Exit might hit a wall too — the test is built to
  report a null OOS result plainly, not to keep slicing. A consistent null here
  would itself be decisive: edge is then in *sizing/selection*, not timing at all.
- **Label leakage** is the live risk in exit labeling (the future exit defines the
  label). Mitigate with strict past-only features + the purged-WF CV the gate
  already enforces; unit-test the in-trade sampler against fixtures.
- **Survivorship / regime mix** — train across regimes; the backtest arm measures
  net-of-fee, not gross (the vwap fee-wall lesson).

## 7. Sibling redirects (noted, not scoped here)

The same "edge isn't in entry" logic points at two adjacent frontiers already
soaking observe-only — worth their own scoping if exit clears or stalls:

- **Selection** — the M18 portfolio capital-allocator soak (`allocator_soak.py`,
  `/api/bot/allocator/soak`): which of N concurrent candidates to fund (regret on
  ≥2-candidate ticks). A learned allocator is the selection analogue of this exit head.
- **Sizing** — the conviction-sizing soak (`conviction_sizing.py`) +
  `CONVICTION_SIZING_MODE`: how much to risk given context. Already an apply-path,
  currently `off`/annotate pending backtest evidence.

Exit is proposed first because it has the most existing infra (the exit-ladder
soak) and the cleanest label.

## 8. Results — the verdict (2026-06-30): exit-timing is at the wall too

P0 ran end-to-end on the trainer VM. The `exit_candidates` family built **691,957
in-trade rows** (ETHUSDT 5m, synthetic CUSUM-event arm + the real-trade live arm;
`sl_mult=1.0`, `target_beta=2.0`, `hold_horizon=10`), and `exit-policy-v1` trained
clean (`rc=0`). Two pre-registered OOS reads:

| Read | split | n_eval | AUC | verdict |
|---|---|---|---|---|
| **Headline** (synthetic→real transfer) | `live_holdout` (train synthetic, eval REAL in-trade bars) | 62 | 0.555 | **inconclusive** — 95% CI ≈ ±0.10 on n=62, indistinguishable from 0.5 |
| **Powered** (within-distribution) | `time_aware_holdout` 0.2 over the 692k synthetic rows | **138,391** | **0.5209** | **NULL** — CI ≈ ±0.003; decisively **below** the 0.55 kill-criterion |

The powered read is the decisive one. At n=138k the AUC standard error is ~0.0015,
so **0.521 is real but tiny — far under the pre-registered AUC > 0.55 gate.** The
model collapses to the majority class (recall ≈ 0.0008, f1 ≈ 0.0016, Brier 0.225 ≈
the no-skill baseline): past-only position-state + market features carry essentially
**no OOS signal** about whether *holding beats exiting-now*. The headline 0.555 came
back above 0.5 with the synthetic→real transfer intact — mildly encouraging — but on
62 rows it's noise; the powered number overrides it.

**Verdict: exit-timing (Framing A) is at the M18 wall, just like entry.** Per the
pre-registered §4 kill-criterion (miss AUC>0.55 → "the exit wall is as hard as the
entry wall; stop and record it"), this null is the decision-grade result. We do **not**
advance Framing A to the net-of-fee backtest arm (P1+).

**Caveats (honesty):** ETHUSDT 5m only; Framing A only; one barrier config
(`sl=1.0×ATR / target=2.0β / H=10`). Framings B (remaining-favorable-excursion
regression) and C (stop-mispricing P(stop-before-target)) are **untested** — a
different label could carry signal Framing A's binary `should_hold` washes out. But
the result is consistent with the entry-wall + M18 pattern: **point-in-time *timing*
prediction (entry OR exit) is at the wall; ML's demonstrated value is in
*regime/context*** (the live, A/B-validated vol-gate).

**Redirect:** fold into the §7 sibling frontiers — **sizing** (`conviction_sizing`
soak + `CONVICTION_SIZING_MODE`) and **selection** (the M18 `allocator_soak` —
which of N concurrent candidates to fund). Both already accrue observe-only soak
data, and the regime head's success suggests there is context-structure to find
there that the timing problem lacks. A learned allocator (selection) is the
cleanest next probe: it's a *ranking* problem over a genuine choice set (≥2-candidate
ticks), not a point prediction, and the soak corpus is already building.

**Provenance:** trainer-vm-diag relays #5226→#5233 (build #5229, headline collect
#5231, powered synth-OOS #5232 + #5233). The infra (this experiment's dataset
family + manifest + AUC evaluator) is retained — it composes for Framings B/C and
for the allocator/sizing probes — and the manifest stands as the pre-registered
record of exactly what was tested.
