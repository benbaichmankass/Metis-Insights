# M21 — Entry Refinement (DESIGN)

**Status:** PLANNED (operator-acked 2026-07-13: "yes lets do that").
**Owner surface:** this doc (design of record) + the ROADMAP M21 row (status)
+ a per-leg entry coverage matrix (`docs/research/entry-refinement-coverage.json`,
created when execution starts) + the sprint logs.

## 1. Why entries, why now

M20 industrialized exit refinement and essentially completed it: 15 exit-lever
cells + a live ML exit head shipped in three operator-approved batches, with
honest negatives recorded everywhere else. The remaining PnL problem the exit
program *surfaced but could not fix* is entry quality:

- `trend_donchian` (BTC 1h, live real-money) is **OOS-negative with the best
  exit found** (p80 decay arm improved both axes yet OOS net_R stays < 0) —
  no exit fixes a bad entry.
- The M20 guardrail was explicit: *no entry-side changes*. Entries have never
  had a systematic, gated, fleet-wide program — exits now have had two.
- The M18 allocator's selection phase is parked on exactly one missing input:
  a P(win)-at-entry estimate that beats coin-flip (old ranker OOS AUC ≈ 0.51).
  M20's decisive lesson — **label engineering, not model capacity, was the
  blocker** (holding_pays 0.55 → peak_is_in 0.71 with the same trees) —
  says the P_win head deserves a properly-engineered second attempt.

## 2. Method (inherited from M20 — the binding pipeline)

Same fast-gate doctrine, same machinery, entry-side levers instead:

1. **Config-exact harness cells** per leg from `config/strategies.yaml`
   (incl. all shipped exit declares in the base — `base_args` threads them).
2. **Gate:** cell beats base on net_R AND maxDD, IS and OOS
   (split 2025-07-01), then yearly walk-forward ≥ 2/3 usable folds.
3. **Per-leg coverage matrix** with `shipped / passed_unshipped /
   honest_negative / pending / blocked / n/a` — the milestone's
   done-condition.
4. **Batched Tier-3 declares** (draft PR + evidence table → operator
   approval → activate → first-fire checks). Entry-side params are Tier-3
   like everything else in strategies.yaml.
5. Live monitor/signal-builder changes follow the lever contract:
   YAML-declared ⇒ real effect; undeclared ⇒ annotate-only soak row;
   fail-safe; never raises.

## 3. Work items, in priority order

### E-1. Entry-quality baseline + dataset (FIRST — the evidence pass)

Before sweeping levers, quantify WHERE entries lose: per leg, distribution of
MAE before MFE (how deep do winners draw down first?), immediate-reversal
rate (entries whose first N bars go straight against), time-of-day /
day-of-week PnL split, entry-bar vol/ADX conditions of winners vs losers.
All computable from the existing `--emit-trades` harness output + the E0
per-bar builder. Output: an evidence memo ranking legs by entry-quality
deficit and the most promising filter axis per leg. Tier-1, trainer-side.

### E-2. Hard entry-filter cells (the M20 P4.1 analogue)

Per-leg sweep of the classic entry filters, config-exact, additive flags in
the existing harnesses:

- **Confirmation-bar filter** — require the breakout/pullback signal to hold
  for 1 (or 2) closed bars before entering (kills false breakouts at the
  cost of worse entry price — the harness measures which wins).
- **Breakout-depth / setup-strength threshold** — donchian already has
  `min_confidence` (depth-in-ATR); sweep tighter values per leg. Pullback:
  sweep `pullback_frac` band + `adx_min` tighter cells.
- **Vol-at-entry condition** — skip entries when entry-bar ATR percentile
  is extreme (the trending+volatile false-breakout finding from the vol-gate
  work, generalized per leg).
- **Time-of-day / session cells** — skip entries in the leg's historically
  net-negative hours (killzone-style, but data-derived per leg and gated
  like every other cell; guard against overfit via the yearly walk-forward).

One lever per leg unless a combo A/B passes (the M20 rule, now enforced
structurally by base-threading).

### E-3. P_win entry head (the ML track — unlocks M18)

- **Label (truncation-observable, no barrier re-simulation):**
  `first_touch = +1R before −1R` within the trade's actual bar path (and a
  sibling `reaches_2R`). Computed per TRADE at entry time (one row per
  trade, entry-time features only — strictly bars ≤ entry).
- **Features:** the entry-time slice of the existing exhaustion/context
  block (momentum, ATR phase, band extension, vol ratios, hour/dow) + the
  setup's own geometry (depth, pullback frac, channel width in ATR).
- **Gate (E1):** OOS AUC materially > 0.55 AND a τ-policy replay
  (skip entries with P_win < τ) that beats actual on net_R AND maxDD in
  walk-forward — the exact E-program shape.
- **Graduations:** E2 shadow via the existing multi-artifact channel
  (score at signal time, log-only), E3 = entry gating or allocator input —
  BOTH operator-gated Tier-3. **The allocator (M18 P2) is the primary
  consumer**: selection needs ranking quality, not a hard gate, so even a
  modest-AUC head may unlock it where it failed as a filter.
- **GPU budget:** the standing $10 M19 burst budget applies if trees
  plateau (same serving constraint: artifact must run on the live VM).

### E-4. Regime-at-entry refinements (extend the proven gate)

The regime hard gate already filters entries; two data-backed extensions:
promote ETH/SOL 15m regime heads shadow→advisory to extend the ML vol-gate
beyond BTC (proven playbook, promotion-gated), and author any new
`trend_vol` OFF cells the accrued would-gate evidence now supports.

## 4. Non-goals / guardrails

- **No exit-side changes** — M20 owns exits; anything exit-shaped goes to
  its matrix.
- **No new strategies** — this refines existing legs' entries (new legs go
  through the `new-strategy` skill separately).
- **Overfit discipline for time-cells**: time-of-day cells must pass the
  SAME yearly walk-forward as every other cell; no cherry-picked windows.
- **No proxy-data heads** (levers OK on proxies; heads need native history).
- Live-parity twins for any head features land in the same PR as offline.
- One lever per leg unless a combo A/B passes.

## 5. Zero-regret precursor (running now, M20 tail)

Peak-is-in retarget rounds for the 4h-donchian + 2h-pullback exit-head
families (their E1 fails predate the label discovery) — pure M20 machinery,
informs nothing in M21 but may yield two more shadow exit heads while the
M21 evidence pass runs.

## 6. Tooling touchpoints

`scripts/research/backtest_{trend,pullback}.py` + squeeze/fvg harnesses
(entry-filter flags to add) · `m20_fleet_exit_sweep.py` (fork/extend as the
entry sweep driver) · `build_exit_head_dataset.py` (entry-time row mode for
E-3) · `train_exit_head.py` (P_win target) · multi-artifact shadow channel
(`exit_head_shadow.py` pattern; entry-side twin at signal-builder time) ·
`.claude/skills/exit-refinement/SKILL.md` (generalize or sibling
`entry-refinement` skill when execution starts).
