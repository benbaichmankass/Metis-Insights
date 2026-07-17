# Sprint Log: S-TRAINER-RESOURCE-OOM-2026-07-17

## Date Range

2026-07-16 → 2026-07-17 (follow-through from the 2026-07-16 `/system-review`).

## Objective

Two linked deliverables, both spawned by the 2026-07-16 daily review:

1. **Phase 2 — direction-aware regime filter.** The operator's directive after
   the 07-16 losses: ADX measures trend *strength*, not *direction*, so
   long-only pullback strategies can fire into strong downtrends. Design +
   backtest a direction filter (DI+/DI− or midline slope) to pay down
   `BL-20260717-REGIME-COVERAGE-DEBT` with real Tier-3 cells.
2. **Trainer-resource hardening.** Running Phase-2 backtests on the trainer
   surfaced (again) the resource-contention thrash the operator flagged as
   unacceptable ("this is still happening… we're raising resources on this").
   Genuinely fix it: enforce the heavy-job queue so nothing can bypass it, and
   close the single-manifest-OOM gap so an oversized manifest can't
   crash-loop/thrash the box.

## Tier

Tier-1 throughout (research tooling + trainer-VM tooling + docs). The Phase-2
*outcome* would have been Tier-3 (a live `regime_policy.yaml` cell) had it
cleared the go bar — it did not, so no Tier-3 change was made or proposed.

## Starting Context

- 2026-07-16 was an "unacceptably terrible" trading day; the operator asked why
  the system still bets into losing trades after 4 months and directed a
  structural fix (regime coverage for new strategies + ML promotions surfacing
  routinely), plus "launch Phase 2" for the direction filter.
- Earlier in the session `S-STRATEGY-COVERAGE-GUARD-2026-07-17` shipped the
  merge-time regime-coverage preventer + seeded the 35 uncovered live strategies
  as tracked `coverage_debt`. Phase 2 is the paydown attempt.
- Trainer VM is 1 OCPU / 6 GB (Ampere Always-Free, pool full — can't grow it);
  `MemoryMax=5G` cgroup cap. Repeated OOM-kills + manual `systemctl stop`s to
  "make room" were the observed Jul 14–17 failure mode.

## Repo State Checked

- `config/regime_coverage_exemptions.yaml` (`coverage_debt` roster + ceiling),
  `config/strategies.yaml` pullback params, `scripts/backtest_{pullback,trend}.py`.
- `scripts/ops/run_training_cycle.sh` (checkpoint/resume, per-manifest timeout,
  exit-code handling), `scripts/ops/_trainer_heavy_lock.sh`, `ml/cli.py`.
- Live trainer state via the `trainer-vm-diag` relay (issues #6795–#6798).

## Files and Systems Inspected

- Trainer cycle loop + exit-code branches (rc 0 / 78 / 124 / 137 / other).
- The heavy-lock queue (voluntary vs enforced) and its re-entrancy signal.
- Trainer journal / `training_cycle.jsonl` / `cycle_progress_*.json` / `free -m`
  / kernel OOM-kills (diag #6798).

## Work Completed

**Phase 2 — direction-aware regime filter (REFUTED, merged #6779 + #6788):**
- Added `_directional_indicators()` + a `--direction-filter {off,di,slope}` lever
  to `scripts/backtest_pullback.py` and `scripts/backtest_trend.py`; `off` is
  byte-identical to prior behaviour (verified).
- Ran the 5 alt `*_pullback_2h` symbols on the trainer's ~5-year 2h feed at the
  exact live params (`adx_min: 25`), net of 7.5 bps. **Go bar (improve net
  expectancy AND cut maxDD vs `off`) not cleared**, and it *failed on the pilot
  ETH outright*; only SOL/`di` + XRP/`slope` marginally passed, inconsistently
  (a different lever each) amid 4-of-5 fails — noise, not signal.
- Root cause of the failure: a pullback strategy *buys the dip*, so the entry
  bar's instantaneous DI/slope reads "down" — that's the setup, not an
  anti-signal; and the unit already has a direction gate (`close > Donchian
  midline`), so a second faster one subtracts. The 07-16 losses reframe as
  normal variance of net-positive legs; the real risk is **correlated
  simultaneous alt entries** (→ correlation-aware sizing, a separate design).
- Disposition: **no cells authored, no Tier-3 PR.** The `*_pullback_2h` alts
  stay in `coverage_debt` — now *measured* (tested + rejected, not merely
  unaddressed). Harness `--direction-filter` kept as reusable research tooling.
  Full evidence: `docs/research/M-regime-direction-filter-DESIGN.md`.

**Trainer-resource hardening:**
- **Enforced heavy-job queue (merged #6791, live-verified #6792).** The queue
  was voluntary — a bare `python -m ml train`/`build-dataset` bypassed it. Added
  `src/utils/trainer_heavy_lock.py` (acquired at the `ml` CLI entrypoint, gated
  on the `/etc/ict-trainer-vm.role` marker → pure no-op off-box), re-entrant via
  the wrappers' `TRAINER_HEAVY_LOCK_HELD=1`, fail-open, clean queue-timeout →
  exit 75. Added a `heavy_lock_holder.json` coordination flag. Live-verified on
  the trainer: marker present, contended bare invocation exits 75, re-entrant
  skip works.
- **Single-manifest OOM quarantine (merged #6800).** The queue can't shrink a
  manifest that OOMs *alone*; the cycle bounded it (30-min cap → continue) but
  the per-day progress file retried it every cycle forever, burning the window
  silently. Added `src/utils/trainer_manifest_health.py` — a cross-cycle
  OOM-streak tracker (state under `runtime_logs/trainer/`, survives the cycle's
  `git reset --hard`). After 3 consecutive OOM/timeouts (rc 124/137) it
  quarantines the manifest (skip) + emits a loud `manifest_quarantine_tripped`
  cycle event (the durable escalation → `/api/bot/ml/cycle`, since the trainer
  can't commit a backlog item itself); self-heals via a 7-day recheck + clears
  on a successful train. `ml-review` + `system-review` skills now surface
  quarantines as a mandatory Rule-3 flag.
- Documented both in `docs/claude/trainer-resource-protocol.md` (Rules 1–3 +
  tuning knobs).

## Validation Performed

- Phase-2: `off` output byte-identical; 5-symbol × 3-arm backtest on real
  trainer data (table in the research doc); `di` skips zero entries on the
  confirmed-breakout trend follower (expected — a confirmed breakout already has
  +DI>−DI).
- Queue + quarantine: 22 tests pass (9 `test_trainer_manifest_health` + 6
  `test_trainer_heavy_lock` + 7 `test_run_training_cycle_sh`); `bash -n
  run_training_cycle.sh` clean; `env-gate-guard` clean (all new vars are
  threshold-style `*_AFTER`/`*_DAYS`/`*_CLEAR`/`*_STATE_FILE`, not `*_ENABLED`).
- Verified `timeout` returns **124/137, never 143**, so a genuine single-manifest
  wedge is always caught going forward (the logged 143s were manual
  `systemctl stop` interruptions — the behaviour this work eliminates).
- Live evidence (trainer-vm-diag #6798): box at ceiling (5571/5909 MB used, 158
  avail); `ict-trainer.service` OOM-killed Jul 03/04/07/08;
  `btc-regime-5m-lgbm-flow-v1` exit 137 on 07-03 + 07-15 (the 18.7h wedge),
  cascading to the 5m siblings — the confirmed single-manifest-OOM case.
- All 18 CI checks green on each PR (#6779, #6791, #6800).

## Documentation Updated

- `docs/research/M-regime-direction-filter-DESIGN.md` (created — design + REFUTED
  results + disposition).
- `docs/claude/trainer-resource-protocol.md` (enforced-queue + Rule-3
  auto-quarantine sections + tuning-knob table).
- `.claude/skills/ml-review/SKILL.md` + `.claude/skills/system-review/SKILL.md`
  (quarantine = mandatory flag with a Rule-3 disposition).
- `docs/claude/ml-review-backlog.json` (BL-20260717-TRAINER-SINGLE-MANIFEST-OOM —
  the named oversized manifest awaiting a shrink/GPU/drop decision).

## Contradictions or Drift Found

None across the canonical set (`canonical-doc-coherence` checker passes all 4).
This log + the ROADMAP row update below close the decision-landing gap the
doc-freshness sweep found (Phase-2 outcome + trainer-resource work were in the
research/protocol docs + backlog but not yet in the roadmap/sprint record).

## Risks and Follow-Ups

- **`btc-regime-5m-lgbm-flow-v1` (+ 5m siblings) needs a Rule-3 disposition** —
  shrink (LightGBM is CPU-bound, so chunk the dataset / shorten the 5m window /
  cut features — preferred), GPU-burst, or drop. Tracked:
  BL-20260717-TRAINER-SINGLE-MANIFEST-OOM (ml-review-backlog).
- **Correlation-aware sizing / concurrency limits across concurrently-open
  correlated alt legs** — the genuine fix for 07-16-type clustering (a separate
  design, not a per-leg direction gate). Worth a dedicated sprint.
- The 35 `coverage_debt` legs stay flagged; direction-filter cells are ruled out
  for the pullback family, so paydown for those legs is now "measured debt", not
  a pending backtest.

## Deferred Items

- Authoring correlation-aware sizing (out of scope here).
- Re-running the direction filter for non-pullback families if a future roster
  change adds trend-continuation legs that lack an existing direction gate.

## Next Recommended Sprint

Correlation-aware sizing / concurrency caps for simultaneously-open correlated
alt legs (the real 07-16 risk), OR the `btc-regime-5m-lgbm-flow-v1` shrink so it
re-enters the training rotation.

## Wrap-Up Check

- [x] All three PRs merged to `main` (#6779, #6791, #6800), CI green.
- [x] Trainer picks the changes up on its next `git reset --hard origin/main`
      cycle (deploy is pull-based; no manual VM step).
- [x] doc-freshness sweep run; coherence checker passes; this log + the ROADMAP
      row record the outcomes.
