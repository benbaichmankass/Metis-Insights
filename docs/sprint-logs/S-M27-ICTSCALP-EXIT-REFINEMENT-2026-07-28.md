# Sprint Log: S-M27-ICTSCALP-EXIT-REFINEMENT-2026-07-28

## Date Range
- Start: 2026-07-28
- End: 2026-07-28

## Objective
- Primary goal: Exit-refine the `ict_scalp_mgc_15m` leg (the PR #7848 follow-up) —
  and, once the harness gap was found to be family-wide, exit-process the whole
  `ict_scalp` family (8 legs) under the M20 gate, recording honest verdicts.
- Secondary goals: build the missing exit-lever support (`ict_scalp` had none at
  either layer); update the exit-refinement coverage matrix to honest states.

## Tier
- Tier 1 (research tooling + docs). No `config/` or live-path change; the live
  `ict_scalp.monitor()` is untouched. The one Tier-3 outcome (an ETH-15m
  stale-stop that cleared the gate) was **proposed, not shipped** — operator HELD.
- Justification: harness scripts, unit tests, a CI matrix workflow, a coverage
  JSON, an assessment memo, and a backlog entry — all Tier-1 surfaces.

## Starting Context
- Active roadmap items: M20 (Exit Refinement — coverage matrix is its
  done-condition), M27 (Scalp Expansion — `ict_scalp_mgc_15m` newly wired to
  `ib_paper` in PR #7848).
- Prior sprint reference: `S-M20-EXIT-REFINEMENT-2026-07-12`,
  `S-M27-P0-BATCH1-2026-07-20`.
- Known risks at start: the new MGC leg had a `pending` coverage row filed
  optimistically; sandbox can't reach Bybit; the 1-core trainer is too slow for a
  parallel family sweep.

## Repo State Checked
- Branch or commit reviewed: `claude/wire-ict-scalp-mgc-15m-j8i7i4`.
- Deployment state reviewed: no live-VM change (Tier-1 session).
- Canonical docs reviewed: `exit-refinement` skill, `backtesting` skill,
  `docs/research/exit-refinement-coverage.json`, `CLAUDE-RULES-CANONICAL.md`.

## Files and Systems Inspected
- Code files inspected: `scripts/backtest_ict_scalp.py`,
  `scripts/research/m20_fleet_exit_sweep.py`,
  `src/units/strategies/ict_scalp.py`, `scripts/backtest_pullback.py` (lever ref).
- Config files inspected: `config/strategies.yaml` (`ict_scalp_*` blocks, read-only).
- Docs inspected: `docs/research/exit-refinement-coverage.json`,
  `docs/research/M27-P0-MGC-15m-findings-2026-07-28.md`.
- GitHub Actions workflows inspected: `.github/workflows/ict-scalp-exit-sweep.yml`
  (authored this session).

## Work Completed
- **Built the `ict_scalp` exit-lever harness support** — added
  `--stale-exit-bars` / `--stale-exit-below-r` / `--giveback-min-mfe-r` /
  `--giveback-r` to `scripts/backtest_ict_scalp.py` (stop-first, fire at bar
  close, mirroring `backtest_pullback.py`; default-off = baseline byte-for-byte
  unchanged), and registered a `scalp` family in `m20_fleet_exit_sweep.py`.
- **Unit tests** — `tests/test_ict_scalp_exit_levers.py` (8 tests: long/short
  giveback, stale fires/holds-a-winner, default-off baseline invariance,
  stop-first ordering).
- **Config-exact IS/OOS + walk-forward driver** —
  `scripts/research/m27/ict_scalp_exit_sweep.py`, plus the parallel per-leg CI
  matrix `.github/workflows/ict-scalp-exit-sweep.yml` (free 4-core runners,
  `scp`-ing each leg's config-exact `m27_data` CSV from the trainer — keeps the
  heavy sweep OFF the 1-core trainer per vm-resource-management).
- **Swept the whole family (8 legs).** MGC-15m (XAU proxy) + 7 live crypto legs:
  **7 of 8 honest_negative** (incl. the real-money BTC-5m leg). **ETH-15m** was
  the sole IS/OOS survivor and it **cleared the yearly walk-forward** (stale8 +
  stale12 each 3/4 usable folds) — marginal (one fold fails; mostly a ~3R
  maxDD benefit; stale12 the stronger, OOS +2.92R).
- **Coverage matrix + memo** — `docs/research/exit-refinement-coverage.json`
  updated for all 8 legs (structural levers `n/a`, `stale/giveback_stop`
  honest_negative except ETH-15m stale_stop `passed_unshipped`, `exit_ladder`
  blocked:no_harness_levers, `exit_head_ml` pending/blocked). Assessment memo:
  `docs/research/M27-MGC-15m-exit-refinement-assessment-2026-07-28.md`.

## Validation Performed
- Tests run: `tests/test_ict_scalp_exit_levers.py` (8/8), existing
  `tests/test_ict_scalp_variants.py` (no regression from the signature change);
  full CI green on merged PR #7849 (ruff, pytest, all guards).
- Manual code verification: default-off path leaves `_simulate_exit` byte-for-byte
  unchanged; stop-first ordering confirmed by the dedicated unit test.
- Gaps not yet verified: live-VM parity of a stale-stop in `ict_scalp.monitor()`
  — not built (no lever passed for any SHIPPED leg; the ETH-15m survivor is held).

## Documentation Updated
- Roadmap updates: added the changelog-banner entry for this session.
- Subsystem doc updates: `docs/research/exit-refinement-coverage.json` (8 leg
  rows to honest verdicts); assessment memo written.
- Backlog: `MB-20260728-ICTSCALP-EXIT-LEVERS` (ml-review backlog) updated with
  the family sweep result + the ETH-15m held candidate + remaining follow-ups.

## Contradictions or Drift Found
- None across the canonical set (`check_canonical_doc_coherence.py` PASS).
- Fixed the memo's ETH-15m section, which left the ship/hold call as "operator
  decides" — now records the operator's actual **HOLD** decision.

## Risks and Follow-Ups
- Remaining technical risks: none live (nothing shipped to the order path).
- Remaining product decisions (Tier 3): the ETH-15m stale12 stale-stop is a
  standing, operator-gated proposal — held pending an `ib_paper`/bybit_1 soak;
  re-open when soak evidence accrues.
- Blockers: `exit_ladder` (partial-TP banking) un-built + fleet-parked;
  `exit_head_ml` blocked on native MGC-15m history depth.

## Deferred Items
- Real-money `ib_live` route for MGC-15m — separate later Tier-3 gate, blocked on
  the `ib_paper` soak (zero soak data yet).
- ETH-15m stale-stop live-monitor wiring — held (see Tier-3 above).

## Next Recommended Sprint
- Revisit the ETH-15m stale-stop once the paper soak has a real closed-trade
  sample (run `m20_exit_analysis.py` first), and drain the remaining
  `MB-20260728-ICTSCALP-EXIT-LEVERS` follow-ups.
