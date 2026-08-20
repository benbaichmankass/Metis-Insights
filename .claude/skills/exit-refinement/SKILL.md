---
name: exit-refinement
description: The binding, repeatable pipeline for building, validating, and shipping EXIT improvements (trailing-stop geometry, stale-stops, giveback-stops, partial-TP ladders, ML exit heads) for any strategy×symbol leg — data → harness lever sweep → E0/E1/E1.5 exit-head → live parity check → Tier-3 flip → first-decision health check — plus the committed coverage matrix that is M20's done-condition. Use when the operator says "improve the exits", "process <strategy> for exit refinement", "run the exit pipeline on X", when wiring a NEW strategy (every new leg gets exit-processed), or when asking "which legs haven't been exit-processed". NOT for entry-side tuning (M7/M8 review/tune) and NOT a replacement for the backtesting skill (it composes with it).
---

# /exit-refinement — the exit-improvement pipeline (M20 system)

Codified from the M20 sprint (operator directive 2026-07-12: "we need a
system for how we build new exit refinement strategies and how we test
them"). Evidence anchors: `docs/research/M20-exit-refinement-2026-07-12.md`
(the memo), `docs/research/M20-exit-head-PROGRAM.md` (the ML exit-head
E0–E3 program), sprint log `S-M20-EXIT-REFINEMENT-2026-07-12`.

## The coverage matrix is the contract

`docs/research/exit-refinement-coverage.json` — one row per strategy leg,
one verdict per lever column (`trail_geometry`, `stale_stop`,
`giveback_stop`, `exit_ladder`, `exit_head_ml`). Statuses:
`shipped / passed_unshipped / honest_negative / pending / blocked:<reason>`,
each with a PR/memo ref.

- **Update it in the SAME PR** as any verdict-producing work. A sweep or
  training run whose verdict isn't in the matrix didn't happen.
- **Honest negatives are recorded, never skipped** — a `honest_negative`
  cell is a completed deliverable.
- **A NEW strategy gets a `pending` row at wiring time** — add this to the
  `new-strategy` checklist output; the leg is not "done" until its exit row
  has verdicts or explicit blocks.
- The milestone/health view of "are we done" = no `pending`/`blocked` rows
  on live legs.

## The pipeline (per strategy×symbol×tf family)

**P0 — data.** The family needs (a) 3–5y of native-TF candles reachable
from the trainer and (b) a config-exact harness that can emit per-trade
paths (`--emit-trades`). If candles are missing, that's a `blocked` row +
an ml-backlog item — fixing coverage IS the task, don't silently skip
(MB-20260712-EXIT-ANALYSIS-COVERAGE).

**P1 — evidence read.** Live trade paths from the journal
(`scripts/research/m20_exit_analysis.py`): MFE vs realized R, giveback,
round-trip fraction, hold times. This quantifies WHICH failure mode the
family has (chop-hold, giveback, early-stop) and which levers are worth
sweeping.

**P2 — hard-lever sweep (IS/OOS, config-exact).**
`scripts/research/m20_exit_sweep.py` + the harness levers (stale-stop,
giveback-stop, trail multiples, ladder variants), net of fees.

**Gate (operator directive 2026-08-10 — capital efficiency is a SHIPPING
criterion, no longer a tiebreak).** A cell qualifies by EITHER path, and
whichever path it took it must then pass the SAME yearly walk-forward:

- **Path A — return.** Beats baseline on **net_R AND maxDD**, in BOTH IS and
  OOS. Unchanged; this is the historical gate.
- **Path B — capital efficiency.** Improves **`net_r_per_capital_day`** in BOTH
  IS and OOS, AND maxDD does not worsen, AND net_R falls by no more than the
  declared floor. Exists because a trade reaching TP after 149 bars and one
  reaching it in 10 are **not the same object**, and Path A scores them
  identically — measured live 2026-08-10, where 24 of 25 open positions had no
  stall exit at all and a real-money `eth_pullback_2h` sat 149 bars at −0.33R
  (`BL-20260810-NO-STALL-EXIT-CAPITAL-SITS-IN-DEAD-TRADES`).

The metric is single-homed in **`scripts/capital_efficiency.py`** — never
re-derived per harness, or a cross-harness comparison means nothing.
`net_r_per_capital_day` is SIZE-WEIGHTED (a partial-TP release is credited its
shorter hold); a harness that does not track the release bar must report
`capital_bars == position_bars` and say so, never fabricate a weighting.

### The GRANT CAP — `dN/N_b ≤ 1.0` (operator-approved 2026-08-11, Tier-3)

The sweep implements Path B's drawdown clause as a **derived tolerance** rather
than a flat "maxDD does not worsen": `allowed = D_b × (dN / N_b)`
(`m20_fleet_exit_sweep.py::drawdown_exchange_rate`). Read `allowed` as a
**fraction of the base book's entire drawdown**, and note the fraction was
unbounded above — measured over the 604-row corpus, **31 rows were entitled to
more than the whole base drawdown**, the largest at **1.70×**. Past 1.0 the
allowance has stopped being a share of the book's risk budget and become an
**expansion** of it, so the entitlement is now capped at `D_b`.

**The cap is structural, not fitted** — it is the exact point where a share
becomes an expansion. That is why it needs no statistical case, unlike the
base-rate floor, which was a *prediction* claim and **failed** it
(`no_separation` on both candidate predictors —
`docs/research/M20-path-b-floor-and-trail-widen-2026-08-10.md`).

⚠️ **HOW TO READ A CAPPED ROW — three misreadings, all easy:**

1. **It caps the ENTITLEMENT, never the ASK.** A cell asking for less than the
   cap is untouched. `grant_capped: true` does **not** mean "too risky" — it
   means "its entitlement was absurd; its ask may well have been fine." Most
   capped rows *improve* drawdown (`tlt_pullback_1h trail4`: ratio 1.70, ask
   **−0.69R**).
2. **It changes ZERO verdicts on the measured population, and that is not a
   defect.** Of the 31 over-entitled rows, **none** asks for more drawdown than
   `D_b` (largest real ask among them: **+0.78R** against a 15.35R base). It is
   **prophylactic** — a bound on a future cell, not a correction of a present
   one. Re-graded against the corpus: **5 rows capped, 0 verdicts changed.**
   *(An earlier version of this recommendation claimed it "binds 1 of 18 rows".
   That was wrong — measured before shipping. A risk control that controls
   nothing, described as one that does, is worse than no control.)*
3. **`grant_ratio > 1.0` is not a failing row — read `passes`.** A genuine cap
   refusal carries `reason: "grant_exceeds_base_drawdown"`, which is
   deliberately distinct from a rate refusal: they call for opposite follow-ups
   (tighten the cell's drawdown vs improve its net_R).

The cap enters `passes`, not only the reported allowance — clamping the printed
number while the decision used the uncapped one would be a diagnostic describing
a policy the code does not apply. `allowed_d_max_dd_uncapped` is still reported
so a reader can see **what** was clamped.

### The MIN-OOS-TRADES FLOOR — `MIN_OOS_TRADES = 25` (operator-set 2026-08-11)

**Both paths now require the cell's base book to hold ≥ 25 OOS trades.** A cell below it
gets its own verdict **`insufficient_base`** and no walk-forward.

**This is a DENOMINATOR REQUIREMENT, not a fitted threshold** — the opposite kind of
object from the base-rate floor above, which was a *prediction* claim and failed its
separation test. A minimum trade count needs no statistical case, the same way
`research_results_gate.min_trades` doesn't.

Why it exists: Path A's `beats()` had **no** minimum trade count, so **33 of the 40
cells the sweep passed (82%) sat on an OOS base under 50 trades, 13 under 10** —
`spy_trend_long_1d vt_hot90_t2` passed on **3 OOS trades** with a 6/6 walk-forward and a
ΔmaxDD of *exactly* 0.0.

The **value** came from the coverage cost curve, not a fit: floor 10 → 34 of 51 legs /
27 passes · **floor 25 → 32 legs / 27 passes** · floor 50 → 20 legs / 7 passes. 10→25 is
free (two legs, zero passes); 25→50 is the cliff. **50+ would structurally exclude every
daily-timeframe leg**, which cannot reach 50 trades in a ~1y OOS window.

⚠️ **Two things to read correctly:**

1. **`insufficient_base` is NOT a failure.** It says the population was too thin to
   judge. Folding it into `is_oos_fail` would make a thin book indistinguishable from a
   refuted lever. `would_have_been` records the counterfactual verdict so the floor's
   effect is auditable.
2. **The floor is a PROXY for what actually matters** — the trades the *lever* fired on,
   and whether the effect exceeds its own noise. It will **not** catch a cell on a
   200-trade base that modified two exits (ΔmaxDD 0.0 is that cell announcing itself).
   Per-cell fire counts are not recorded; that gap is open.

`min_oos_trades_floor` travels in the corpus **measurement-identity key** — the same cell
graded unfloored and graded at 25 can carry different verdicts, and `None` means
*"ungraded by any floor"*, never floor 0.

**This is a THIRD threshold, and it does not set the other two.**

⚠️ **Path B's two original thresholds ("improves materially", "the net_R floor") are
NOT yet set, and MUST NOT be invented.** No sweep has yet reported the
`net_r_per_capital_day` distribution, and a threshold with no distribution
behind it is the exposure-ceiling mistake
(`gross-exposure-governance-DESIGN.md` § 6–7: a ceiling below normal operation
silently throttles correct work). The first pullback-family stale/giveback
sweep REPORTS the distribution; the operator sets the two values from it, and
until then Path B **surfaces candidates for review rather than shipping them**.

**Path B is a second door, never a lower bar.** It does not relax Path A, and
it does not skip the walk-forward — relaxing a gate to admit a lever is exactly
how a cosmetic lever ships (`BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS`). A
cell that improves capital efficiency by shrinking the book into
insignificance is a Path-B *failure*, which is what the net_R floor is for.

One lever per cell; combos only after singles pass (M20 finding: combos were
worse).

**P3 — ML exit head (optional, when hard levers leave money on the
table).** The E0–E3 program (`M20-exit-head-PROGRAM.md`): E0 per-bar
dataset (truncation-honest labels) → E1 LightGBM + purged walk-forward +
τ-policy replay → E1.5 conditional shapes if the unconditional policy
fails the trend-tail. Gate as in the program doc.

**P4 — validation standard.** Purged walk-forward (time folds, embargo,
purge on the trade's LAST bar), truncation-honest replay (exit value =
observed close mark — never a re-simulated barrier), live-set sign
agreement when a live sample exists. **The offline validation IS the
confidence gate** (fast-gate doctrine, operator directive 2026-07-12).

**P5 — live mechanical verification (hours–days, NOT weeks).** Deploy the
observe-only scorer/annotator (Tier-2), then:
1. **Feature/trigger parity** — diff live-logged rows against the offline
   recompute for the same bars. The 2026-07-12 parity diff caught three
   real skews in one hour (partial-bar scoring, entry-anchor off-by-one,
   out-of-family scoring) — run it every time; expect near-exact.
2. **First-decision sanity** — scores/triggers in-distribution, dedup and
   in-family guards holding.

**P6 — Tier-3 flip.** YAML declare per the M20 pattern (params on the
strategy leg; absent = off; rollback = delete the lines). Exact diff to
the operator; merge on approval; deploy + restart; verify live HEAD.

**P7 — online soak + first-decision check.** The lever/head soaks LIVE.
The next `/health-review` MUST verify the mechanics of the first real
lever-driven exit; `/ml-review`/`/performance-review` track the realized
`future_r_delta` record. Demotion = delete the YAML lines.

## Hard rules

These rules are also codified generically (for any research skill, not just
this one) in
[`docs/research/RESEARCH-RIGOR-STANDARD.md`](../../../docs/research/RESEARCH-RIGOR-STANDARD.md) —
this list stays the authoritative source for exit-refinement specifically.

- **Never blend real/paper/prop** in any evidence read.
- **Truncation-honest counterfactuals only** — no barrier re-simulation
  (the T0.4 lesson).
- **Config-exact sweeps** — the harness runs the leg's ACTUAL YAML params,
  not defaults.
- **In-distribution guards on any shared-monitor scorer** — every
  donchian-family leg reaches the same monitor hook; a head scores only
  its trained (tf, symbols) (the IWM incident, #6201).
- **Closed bars only in live scorers** (live == train; #6207).
- Tier boundaries: research/tooling/matrix = Tier-1; observe-only scorer
  deploys + restarts = Tier-2; YAML/monitor behaviour flips = Tier-3.

## Composes with

`backtesting` (harness entry points + account-compat matrix — mandatory
before routing), `model-training` (trainer runs), `diag-data` (live
evidence pulls), `vm-ops` (deploy/restart), `doc-freshness` (memo/roadmap/
sprint-log sync), `new-strategy` (adds the pending matrix row).

---

## Definition of done — a capability is not shipped until something RUNS it

*(Operator directive 2026-08-20, binding on every build skill: "we don't keep
building things out half way and then leaving them to rust while the system
chugs along with bad structure.")*

Merging is not shipping. Before you call any capability from this skill done,
all four must hold — and the ones you cannot satisfy get **said out loud**, not
left implied:

1. **A RUNNER exists.** A workflow, a systemd unit, a call site in `src/`, an
   entry in `run_guards.py`, or a documented cadence. A tool that is genuinely
   manual-only declares it in its own file:
   `# wiring: manual-only — <who runs it, when>`. Verify with
   **`python3 scripts/ci/check_unwired_artifacts.py`** — if your new file
   appears in its output, it is not done.
2. **A CONSUMER exists.** Anything the capability *writes* must be *read* by
   something that acts on it. A signal written and never read is worse than a
   missing one — reviewers see the field and assume something acts on it
   (`provenance-consumer-guard` exists for exactly this).
3. **A DETECTOR exists.** Something fails if this silently stops working. A
   test, a guard, an alert, or an invariant in
   `scripts/ops/system_invariants.py`. "We'll notice" is not a detector.
4. **It has been OBSERVED working on real data** — not only in a test. Cite the
   evidence (a diag pull, a log line, a row) or state plainly that it has not
   yet been observed and what would settle it.
5. **The LIVE environment matches the repo's declaration.** If your change adds
   or depends on an env var, a service, a timer, a path or a routing entry,
   **read it back from the VM** (`get-env`, `/api/diag/services`,
   `/api/bot/config`, the relay) and confirm the running value is the declared
   one. *"The repo says X"* is not evidence that the VM does X — the two drift,
   and this repo has the scars: a `FLIP_CONFIDENCE_THRESHOLD` running live for a
   day with no record behind it, a `DIAG_BASE_URL` still pointing at a VM
   terminated 2026-06-16 while the doc-coherence guard passed (it checks the
   docs, not the environment), and a `BYBIT_TPSL_MODE` "flip" that was a no-op
   re-assertion of a value already live.
6. **The change is CONCENTRATED.** Count the files you had to touch. If a
   *routine* addition of this kind cost more than the source-of-truth files plus
   tests and docs, say so — every hand-maintained registry you had to update in
   lockstep is a place the next person half-applies the change. Measured
   2026-08-20: wiring one strategy leg touched **17 files**, of which three were
   `src/` maps holding facts `strategies.yaml` already contains. **A file you
   edited only to keep a derived map in sync is a design finding, not a chore** —
   record it (audit skill § 3.7 MODULARITY) even when you cannot fix it here.

7. **A parameter shared with production has ONE definition, and you asserted
   it.** If your work reads a value that also lives in a config file —
   `risk_pct`, a fee, a cap, a threshold — do not re-derive its units. Import the
   resolver; if there is no resolver, that is the finding. Then state which
   branch you are on: **SWEEP** the parameter, or **FIX** it at the live value
   and assert that equality in the run's own output. A default that merely
   *looks* live is the failure. Measured 2026-08-20 (audit F-37..F-40):
   `accounts.yaml::risk_pct: 0.015` is a FRACTION while five research/prop files
   compute `rpct / 100.0` as a PERCENT, so `--risk-pct 0.015` means 1.5% in one
   research script and 0.015% in another — **100× apart under one flag name** —
   and every harness default sits **5×** below the live basis.
   ⚠️ **"It's R-normalized so risk doesn't matter" does NOT discharge this.**
   That claim assumes the trade SET is invariant to the parameter, and
   production quantizes: futures floor to whole contracts and **refuse
   sub-1-contract outright**, Alpaca floors to whole shares, `min_qty` and the
   margin cap bite. Below a threshold the trade does not shrink — it does not
   happen. Unless your harness models refusal, it cannot test its own
   independence premise, and it errs flatteringly (small risk reads as safe when
   it means the leg does not trade).

**The measured cost of skipping this:** 161 of 384 tools under `scripts/` have
no runner (2026-08-20). `scripts/ops/trainer_dataset_gc.py` — the retention
tool for a 12 G dataset tree — had no caller, no timer and **0 mentions across
7,442 cycle-log rows** while the disk it was written for reached **93 %**.
`exchange_fills_ib.closed_pnl_from_fills` has **zero production callers**, so
IBKR's own realized PnL is pulled hourly and never read. Every one was found by
accident, months later.

`/system-review` now enumerates everything shipped since the previous review and
grades each `running` / `wired_not_yet_exercised` / **`UNWIRED`** /
`unverifiable` (`review_coverage.since_last_build_verification`, enforced by
`render_system_report.py --strict`). **Your work will be graded against this
list.** Leave it wired, or leave it declared.
