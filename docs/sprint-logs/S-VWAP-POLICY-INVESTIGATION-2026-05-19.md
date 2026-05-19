# Sprint Log: S-VWAP-POLICY-INVESTIGATION-2026-05-19

## Date Range
- Start: 2026-05-18 (continuing prior session 01LsgzrBzfwPkZG5iBrXk8Ss)
- End:   2026-05-19

## Objective
- **Primary goal:** Diagnose why the VWAP strategy is losing on live (bybit_2,
  ~18% win-rate, longs at 10.9% / shorts at 40.9%) and find a regime-aware
  policy variant that lifts overall edge above the current break-even.
- **Secondary goals:**
  - Get the adaptive-policy backtest mode (`run_backtest_vwap --adaptive`)
    actually producing JSON output instead of crashing in aggregation.
  - Establish a statistical bar for what counts as a real per-regime edge
    vs. small-sample noise.
  - Leave the policy state on `main` consistent with the most-recent
    backtest evidence so the next session does not regress.

## Tier
- **Tier 1** (backtest-only investigation, no live-trader code changed).
- Justification: every code change in this sprint touched
  `src/units/strategies/vwap_policy.py` (policy lookup table, consumed
  by the adaptive backtest only — not yet wired into `build_vwap_signal`)
  and `src/backtest/run_backtest_vwap.py` (offline analysis tool). The
  live module `src/units/strategies/vwap.py` and its
  `ENTRY_STD_THRESHOLD = 1.0σ` were not touched. The live trader
  continues to run with the unchanged threshold + the existing
  HTF-gate-disabled config from PR #1372.

## Starting Context
- **Active roadmap items at start:**
  - Bring VWAP-long win rate up (live bybit_2 at 10.9% long / 40.9% short,
    Telegram audit by the operator on 2026-05-18).
  - Validate the regime-aware adaptive policy proposed in #1474 + #1482.
- **Prior sprint reference:**
  - `S-AUDIT-PIPELINE-2026-05-17.md` (DB-rebuild + matcher fixes) — got
    the trade-attribution accurate enough to trust the 18% WR observation.
  - PR #1466 — added regime-aware sweep + recency-weighted sampling to
    the backtest runner.
  - PR #1482 — added `--adaptive` backtest mode that consults
    `vwap_policy.lookup_policy` per window.
- **Known risks at start:**
  - `ENTRY_STD_THRESHOLD` had been reverted from 1.5σ → 1.0σ on 2026-05-17
    (PR #1372) after the prior tuning regressed; the operator was wary of
    another tuning round that did not generalise.
  - Strategy parameter changes are Tier-3 (CLAUDE.md hard limit) — any
    change to `vwap.py` constants needs operator approval, not auto-merge.

## Repo State Checked
- **Branch:** `claude/investigate-vwap-performance-Dosz4` (reset onto
  current `main` after each merge cycle).
- **Deployment state:** Live VM at `158.178.210.252` running
  `ict-trader-live.service`. As of 2026-05-19, git SHA progressed across
  three merges to `main`:
  `bdc724e` (#1537 merge of skip-list + strong-up/low override).
  Confirmed via `vm-diag-snapshot` issue #1495 + #1510 + the deploys in
  this sprint.
- **Canonical docs reviewed:**
  - `docs/CLAUDE.md` (project-root) — production-environment rules,
    autonomous vs approval-gated contract, doc-hygiene mandate.
  - `docs/CLAUDE-RULES-CANONICAL.md` (referenced; not edited).
  - `docs/ARCHITECTURE-CANONICAL.md` (referenced; not edited).
  - `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md` (template for this file).

## Files and Systems Inspected
- **Code files inspected:**
  - `src/units/strategies/vwap_policy.py` — read + edited (4 cycles).
  - `src/units/strategies/regime.py` — read; not edited (classifier
    contract stayed stable).
  - `src/units/strategies/vwap.py` — read; not edited (module-level
    `ENTRY_STD_THRESHOLD = 1.0σ`, `SL_STD_MULT_DEFAULT = 0.5σ`).
  - `src/backtest/run_backtest_vwap.py` — read + edited (None-handling
    in aggregation; `threshold=None` no-override branch in adaptive).
  - `scripts/ops/vwap_backtest_sweep_action.sh` — read (operator-action
    wrapper that fetches candles + invokes `run_backtest_vwap`).
- **Config files inspected:**
  - `.github/workflows/operator-actions.yml` — re-read the body parser
    to confirm `bt_mode:` is the correct key (line 231); plain `mode:`
    is reserved for `set-account-mode`.
- **Workflows inspected:**
  - `operator-actions.yml` (`vwap-backtest-sweep` action, 15-min cap).
  - `vm-diag-snapshot.yml` (general snapshot endpoint).
- **Docs inspected:**
  - `comms/follow_ups.json` — appended `FU-20260518-003` for the
    missing-completion-comments pattern.

## Work Completed

### PRs merged (4 in this sprint)

| PR | Title | Effect on main |
|----|-------|----------------|
| **#1487** | `fix(backtest): skip None win-rates in adaptive aggregation` | Adaptive aggregator filters `None` win-rates (skipped windows) before `statistics.mean`; previously crashed in `run_windows` line 650. Unblocks every `--adaptive` dispatch. |
| **#1502** | `docs(follow_ups): track missing operator-action completion comments` | Adds `FU-20260518-003` to `comms/follow_ups.json`. |
| **#1531** | `feat(vwap-policy): skip sideways/low regime` | Moves `sideways/low` from active (1.2σ override) to SKIP after #1511 backtest. |
| **#1537** | `refactor(vwap-policy): skip-list + strong-up/low override (n=6 evidence)` | Final design: `POLICY_TABLE` has exactly **three** entries — `weak-up/low` SKIP, `sideways/low` SKIP, `strong-up/low @ 2.0σ` override. All other entries dropped; fall through to `DEFAULT_POLICY` (`threshold=None` → use module `ENTRY_STD_THRESHOLD`). Backtest adaptive logic gains a third case for `allow=True, threshold=None`. |

### Backtest dispatches (operator-action `vwap-backtest-sweep`)

All compare- or adaptive-mode runs against 5m BTCUSDT, 365-day data, random
14- or 30-day sub-windows, seed 42 (deterministic). Receipts kept as
operator-action issues:

| Issue | mode | windows × wd | Outcome | Headline result |
|-------|------|--------------|---------|-----------------|
| #1486 (pre-sprint) | adaptive | 8 × 14d | crash | `TypeError: statistics.mean(None)` → motivated PR #1487 |
| #1490 | (`mode:` typo, ran compare) | 8 × 30d | hang past 15-min cap | (closed as stale, see note below) |
| #1494 | (`mode:` typo) | 8 × 30d | hang past 15-min cap | (closed as stale) |
| #1509 | (`mode:` typo, ran compare) | 8 × 14d | ✅ | no-HTF baseline +1.13 R, 5/8 positive |
| #1511 | adaptive (full policy) | 8 × 14d | ✅ | **+8.38 R** mean — looked great until #1533 disproved it |
| #1533 | adaptive (post-#1531) | 8 × 14d | ✅ | **+1.32 R** — same seed, +1 day of data, mean collapsed (revealed n=1 noise) |
| #1536 | adaptive (full policy) | 24 × 14d | ✅ | **+0.36 R** — properly-powered baseline; per-regime n now 1–6 |
| #1550 | adaptive (skip + strong-up/low override only) | 24 × 14d | ✅ | **+0.13 R** — net neutral vs #1536; some regimes gained, others lost |

**The `mode:` vs `bt_mode:` operator-error**: the `vwap-backtest-sweep`
action's body parser at `.github/workflows/operator-actions.yml:231`
expects the key `bt_mode:` for backtest mode (plain `mode:` is reserved
for `set-account-mode` account-mode flips). Issues #1490 / #1494 / #1509
used `mode:` and silently fell back to `compare` mode. Tracked in
`FU-20260518-003`.

### Documentation
- `src/units/strategies/vwap_policy.py` docstring rewritten in #1537 to
  cite #1536's per-regime evidence table and to spell out the
  "n≥3 same-direction samples + positive mean_R" bar for any future
  per-regime override.
- `comms/follow_ups.json` — added `FU-20260518-003`.

## Validation Performed

### What was measured

The same 24 random 14-day windows were evaluated under two policy variants:

| Regime | n in #1536 / #1550 | Old policy | New policy (#1537) | Δ mean_R |
|--------|----:|------------|--------------------|---------:|
| **strong-up/low** | 6 | 2.0σ override | 2.0σ override (kept) | 0 |
| sideways/low | 3 | SKIP | SKIP | 0 |
| weak-up/low | 3 | SKIP | SKIP | 0 |
| strong-up/medium | 3 | 0.8σ override | module-default 1.0σ | -2.57 R |
| weak-down/low | 3 | 1.5σ override | module-default 1.0σ | **+5.88 R** |
| strong-down/low | 2 | 0.8σ override | module-default 1.0σ | **-13.65 R** |
| weak-down/medium | 1 | 1.5σ extrap | module-default 1.0σ | +2.72 R |
| strong-down/medium | 1 | 2.0σ override | module-default 1.0σ | -6.35 R |
| strong-down/high | 1 | DEFAULT 1.2σ (pre-PR #1537) | module-default 1.0σ | **+19.69 R** |
| weak-up/medium | 1 | 1.2σ override | module-default 1.0σ | -4.02 R |

**Net: -5 R cumulative across 24 windows ≈ -0.23 R per window mean
(headline `+0.36 → +0.13 R`).**

### Empirical findings (locked in by the data above)

1. **n=1 per-regime threshold picks are noise.** #1511 (+8.38 R) → #1533
   (+1.32 R) collapsed at the same policy with only +1 day of data
   appended, because each window's regime classification shifted and
   resampled the n=1 cells (e.g. `weak-up/medium` flipped from +19 R
   to -24 R at the same 1.2σ threshold). Variance dwarfs any signal.
2. **The skip-list is robust.** `weak-up/low` and `sideways/low` skip
   rules held up at n=3 each across both #1536 and #1550 — every
   skipped window stayed at zero, no recovery at any threshold.
3. **Exactly one per-regime threshold cleared n≥3 + positive mean_R:**
   `strong-up/low @ 2.0σ` at n=6, +7.98 R mean, 5/6 positive in #1536
   and again in #1550 (same value because same windows hit the same
   override).
4. **Dropping `strong-down/low @ 0.8σ` (n=2, +10.73 R in #1536) cost
   ~13 R.** Falling back to module-default 1.0σ converted it from
   winner to loser (-2.92 R / 2 windows in #1550). The "n≥3 bar"
   was too strict for this one — the swing is well outside noise.
5. **The strategy is roughly flat at every tested policy.** Both
   #1536 (+0.36 R) and #1550 (+0.13 R) land near zero across 24
   windows × 14d each ≈ ~11.5 months of coverage. Policy tuning
   moved the needle ±5 R/yr, not the magnitude needed to explain
   or fix the live 89%-losing-longs symptom.

### Manual code verification
- `lookup_policy('strong-up/low')` returns `{allow: True, threshold: 2.0, fallback: False}` on main HEAD.
- `lookup_policy('weak-up/low')` returns `{allow: False, threshold: None, fallback: False}`.
- `lookup_policy('sideways/low')` returns `{allow: False, threshold: None, fallback: False}`.
- `lookup_policy('strong-down/low')` returns `{allow: True, threshold: None, fallback: True}` (fallthrough).
- `python3 -c "import ast; ast.parse(open('src/backtest/run_backtest_vwap.py').read())"` — syntax OK.

### Gaps not yet verified
- No live-trader bytecode change shipped, so the policy is not yet
  consulted by `build_vwap_signal`. The live signal builder still
  uses module constants directly. Wiring it in remains a future
  Tier-3 PR.
- The +0.36 R / +0.13 R results are 24 × 14d random windows; we have
  not yet split into train/test or run a different seed.

## Documentation Updated
- **Rules doc updates:** none.
- **Architecture doc updates:** none (no system-shape changes).
- **Trade pipeline doc updates:** none (live pipeline untouched).
- **Roadmap updates:** none.
- **GitHub Actions doc updates:** none.
- **Subsystem doc updates:**
  - `src/units/strategies/vwap_policy.py` module docstring (in PR #1537).
- **Historical docs marked superseded:** none.

## Contradictions or Drift Found
- **`scripts/ops/vwap_backtest_sweep_action.sh:18-21` description.** Comment
  block still references "1h EMA-200 Phase-3 design" and the
  `--compare` configurations. After PR #1482 added `--adaptive`, the
  shell wrapper added the `adaptive` MODE case but the header doc
  block was not refreshed. Low-impact (operator-facing only) —
  flagged here so a future hygiene pass can rewrite it; not changed
  in this sprint to keep the diff small.
- **`operator-actions.yml:228-230` parser comment.** Says `bt_mode`
  picks between `--compare` and `--threshold-sweep`. It now also
  picks `--adaptive` (added in #1482). Same low-impact comment
  drift; tracked but not fixed.

## Risks and Follow-Ups
- **Remaining technical risks:**
  - Live VWAP win-rate is still ~18% on bybit_2. Policy refinement
    in this sprint did not move that needle. The trader continues
    to bleed on longs in trending markets — see Next Recommended
    Sprint.
  - The `strong-up/low @ 2.0σ` override is the only "real" edge in
    the policy table. If next year's data shifts the regime balance
    (currently 6/24 = 25% strong-up/low), the +7.98 R contribution
    shrinks proportionally.
- **Remaining product decisions (Tier 3):**
  - Wiring `vwap_policy.policy_for_candles` into `build_vwap_signal`
    is a Tier-3 PR pending operator approval. Skipping that wire
    means the policy table is currently consulted ONLY by the
    backtest — live trading is unaffected. The next sprint may want
    to take this step once strategy parameters are tuned.
- **Blockers:** none.

## Deferred Items
- **Restoring `strong-down/low @ 0.8σ`** (n=2, +10.73 R in #1536, lost
  -13.65 R when removed). The "n≥3 bar" rejected it; the post-removal
  delta says it was real. Either relax the bar to n≥2-with-large-swing,
  or wait for a future sweep where this regime accumulates n=3+.
- **`strong-up/medium` as a possible skip.** n=3 across #1536/#1550,
  consistently losing (-4.87 R then -7.44 R). Borderline skip
  candidate; needs at least one more confirmation window before
  flipping.
- **A wider time-range sweep** (e.g. days=730 to reach n≥3 in more
  regimes). Out of 15 trend×volatility cells, 5 are still n=1 in
  the 365d sample.
- **Confirming the operator-actions completion-comment race**
  (`FU-20260518-003`). The earliest hang issues (#1490, #1494) were
  retroactively explained by the `mode:` typo running compare-mode
  with 30d windows, possibly tripping the 15-min cap. The completion
  comment may not be the actual problem — keep the FU open until the
  next session confirms.

## Next Recommended Sprint

### **`S-VWAP-STRATEGY-PARAMS-2026-05-XX`** — investigate the strategy itself, not the policy gate

**Why next:** Two properly-powered backtests (#1536, #1550) at varied
policies landed at +0.13 R and +0.36 R / 24 windows ≈ flat. The live
89%-losing-long symptom cannot be explained by regime gating; it
requires a problem in one of the strategy's structural parameters:

- `ENTRY_STD_THRESHOLD` (currently 1.0σ on `vwap.py`). The
  2026-05-17 revert (PR #1372) bounced this back from 1.5σ on
  evidence that 1.5σ had a long-bias regression. Worth revisiting
  with the 24-window framework now in place.
- `SL_STD_MULT_DEFAULT` (currently 0.5σ). The R:R contract is
  `ENTRY_STD_THRESHOLD / SL_STD_MULT_DEFAULT`. Asymmetry between
  long and short performance suggests the SL rule may be biased.
- VWAP anchor + window (`vwap_anchor`, `vwap_window_bars`). Currently
  rolling 100-bar; an alternative is session-anchored at UTC midnight.
- Time-decay rule and `vwap_cross` exit conditions — when these
  fire on losing longs in trending markets they may be cutting
  winners too early or holding losers too long.

**Suggested approach for the next sprint:**

1. Reuse the 24-window adaptive framework as the validation harness,
   but vary the *strategy* knobs, not the *policy*. Concretely: run
   the `--compare` backtest over the same windows with each candidate
   ENTRY threshold (0.8 / 1.0 / 1.2 / 1.5σ) under the locked-in
   policy table from #1537.
2. Add a **long-vs-short split** to the per-window aggregate. The
   live observation is 10.9% long-WR vs 40.9% short-WR. The backtest
   does not currently surface this — add `total_r_long`,
   `total_r_short`, `wins_long`, `wins_short` to the aggregate so
   the next sprint can verify the long-side asymmetry.
3. Once a winning combination of `(ENTRY, SL_MULT)` shows up at
   n≥3 windows positive on both legs, open a Tier-3 draft PR
   changing `vwap.py` constants and ping the operator for the
   `pull-and-deploy` ack.

**Required verification before starting:**
- Re-read `docs/strategies/vwap_mean_reversion.md` and reconcile any
  drift with the live constants.
- Confirm the live VM SHA matches the current `main` HEAD via
  `vm-diag-snapshot` issue (the first action of the next sprint).
- Pull a fresh 7-day live PnL slice via `/api/diag/journal?table=trades`
  to confirm the long-side bleed hasn't already self-corrected since
  this sprint's work landed.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint
      (PR #1537 docstring; PR #1502 follow-up entry; this sprint log).
- [x] No pipeline-stage changes, so `docs/TRADE-PIPELINE.md` did not
      need updating; Trade Process tab not affected.
- [x] Roadmap status was checked (no change required — VWAP edge
      hunt continues into next sprint).
- [x] Contradictions were recorded (two low-impact comment drifts
      noted above; not fixed in this sprint).
- [x] Remaining unknowns were stated clearly (see Deferred Items).
