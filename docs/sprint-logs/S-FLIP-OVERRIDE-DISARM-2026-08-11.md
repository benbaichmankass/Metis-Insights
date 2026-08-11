# Sprint Log: S-FLIP-OVERRIDE-DISARM-2026-08-11

## Date Range
- Start: 2026-08-11 ~17:20Z
- End: 2026-08-11 ~23:15Z

## Objective
- Primary goal: measure the **live, un-walk-forwarded** flip-confidence override
  (`FLIP_CONFIDENCE_THRESHOLD=0.15` / `FLIP_MIN_POSITION_AGE_HOURS=4.0`) against
  plain `FLIP_POLICY=hold`, and act on the result — the override had been routing
  real money since ~2026-08-10 with no backtest behind it
  (`BL-20260811-FLIP-OVERRIDE-NEVER-WALKFORWARDED`).
- Secondary goals: settle whether a **TF-class-restricted** re-arm is warranted;
  get a warm-process `tick_cost` read for `BL-20260810-TICK-CHAIN`; recover the
  registry-sourced shadow-stats soak start.

## Tier
- **Tier 3** (plus Tier-1 docs).
- Justification: the deliverable was a change to a live order-routing parameter on
  real money. The measurement and the proposal are mine; the flip itself was
  operator-approved in-conversation before dispatch, per
  [`CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md) § Permission Tiers.

## Starting Context
- Active roadmap items: M26 (TF-class conflict taxonomy) supplied the arm
  vocabulary; `BL-20260810-TICK-CHAIN-260S-PER-TICK` open from the prior session.
- Prior sprint reference: `S-M20-LADDER-VERDICTS-AND-PAIRS-CLOSE-PATH-2026-08-10.md`.
- Known risks at start: the override was **live on real money** with no evidence
  either way, so every hour of measurement was an hour of unmeasured routing. The
  harness had no arm for it, so the measurement could not even be run at session start.

## Repo State Checked
- Branch: `claude/system-review-backtest-shadow-tick-93b00s`; base `main` moved
  `995ff00` → `8dc0f8a` → `32487a4` during the session.
- Deployment state: live VM `ict-bot-arm` read at `git_sha 995ff005` at 22:40Z —
  `ict-git-sync` had not yet pulled `8dc0f8a`. Both merges are docs-only, so no
  functional deploy was pending; recorded here so a later session does not
  misread the lagging sha as a failed deploy.
- Canonical docs reviewed: `CLAUDE.md` (§ Environment Variables — the two
  `FLIP_*` rows), `CLAUDE-RULES-CANONICAL.md` (§ Permission Tiers, § Always state
  the population, § Green is not evidence).

## Files and Systems Inspected
- Code: `src/runtime/intents.py` (`resolve_flip_confidence_threshold`,
  `resolve_flip_min_position_age_hours`, `_evaluate_confidence_override`,
  `get_existing_position_info`), `src/runtime/tick_cost.py`, `src/main.py`
  (`_tick_hook`), `scripts/backtest_system.py` (flip-arm plumbing).
- Config: live `.env` on the trader (via `get-env`), `config/strategies.yaml`
  (read-only, for the BTCUSDT legs the walk-forward covered).
- Deployment files: `.github/workflows/flip-override-walkforward.yml` (added this
  session, on `main` as of #8774).
- Docs: `CLAUDE.md`, `ROADMAP.md` (M26 row), `docs/research/M26-*`,
  `docs/claude/health-review-backlog.json`.
- Services/timers: `ict-trader-live.service` (restart at 20:14:15Z reset the
  per-process tick counters — the reason the first read was unusable).
- GitHub Actions: `flip-override-walkforward.yml` runs `31523739722` (initial) and
  `31529876774` (TF-class, folds A+B — jobs `93907088540` / `93907088507`);
  `guards.yml` run `31545035369`.

## Work Completed
- **Built the missing arm.** `scripts/backtest_system.py` gained
  `hold_confgap` plus the TF-class-restricted `hold_confgap_crossclock` /
  `hold_confgap_sameclock` variants, and a verdict that can report **NOT TESTED**
  distinctly from *tested and equal* — without which a `fired=0` arm silently
  reads as "matches the incumbent." Landed in PR **#8784** (`995ff00`).
- **Ran the walk-forward.** Folds A+B, BTCUSDT, four arms. The live `0.15 / 4.0`
  pair **lost to plain `hold`** — net-negative across both folds.
- **Answered the TF-class question.** Cross-clock-only loses to plain `hold` in
  3 of 4 cells and worsens maxDD in both train halves. Closed
  `BL-20260811-FLIP-OVERRIDE-TFCLASS-REARM` as **no re-arm warranted** — the
  decision rule was fixed *before* reading the result (beating the blind arm is
  insufficient, since that arm loses).
- **Disarmed the override on the live trader** (Tier-3, operator-approved in
  conversation): `FLIP_CONFIDENCE_THRESHOLD=0.0` via `set-env` (issue **#8785**).
  `FLIP_MIN_POSITION_AGE_HOURS` deliberately left at `4.0` — `_evaluate_confidence_override`
  returns at the `threshold <= 0` check before reading age, so one key is the whole
  gate, and clearing both would make a future re-arm ambiguous about which value
  was the tested one.
- **Recorded the state in `CLAUDE.md`** — both `FLIP_*` rows rewritten to carry the
  live value, the disarm date, the evidence, and the re-arm bar. PR **#8789** (`8dc0f8a`).
- **Warm `tick_cost` read** (diag **#8791**) and its backlog row. PR **#8792** (`32487a4`).

## Validation Performed
- **Tests run:** `guards` PASS 13 / FAIL 0 on both PR branches, plus `json-notes-cap`
  run explicitly; `pytest-run`, `pytest-collect`, `repo-inventory` green on every
  merged head.
- **Live verification of the Tier-3 change:** `FLIP_CONFIDENCE_THRESHOLD` read back
  from **`/proc/<MainPID>/environ`** in issue **#8787** — `process: '0.0'`,
  `declared: '0.0'`. This is the authoritative read; the `.env` file alone would
  not have proven the running process picked it up.
- **Walk-forward populations stated:** per-arm `fired` and `conflicts_observed`
  were read *before* the PnL, and both fold job logs were read end-to-end rather
  than from a summary.
- **`tick_cost` population:** 51 ticks, ONE process (started 20:14:15Z), ~2.35h of
  steady state. Mean **107.2s** / max **122.2s**. The max lands on the *second*
  tick and the steady-state spread is narrow (mean 107.2 vs last 107.5), so the
  overrun is what the tick costs, not a cold-start artifact.
- **An independent confirmation fell out of that read:** `attributed_pct` came back
  **98.4** with the two top-level hooks summing 52.4 + 46.0 = 98.4 exactly, which
  confirms on live data that the prior session's nesting fix (it had reported
  **136.8%** — a share of a whole exceeding the whole) is correct.
- **Gaps not yet verified:**
  - The **registry-sourced shadow soak start** (#8774) is **not** verified
    end-to-end against the live fleet. It is wired and merged; nothing has
    confirmed a real soak start reads the registry date correctly in production.
  - The pre-fix `251s / 296s` tick figures used as the "roughly halved" comparison
    were measured over **10 ticks** — a different and much smaller denominator than
    the 51 here. The comparison is **directional only** and is labelled as such.
  - `check_broker_naked_ib_positions` shows mean 3.3s over n=51 despite a documented
    300s cadence gate. That is arithmetically consistent with *correct* gating
    (~18 executions across 5457s at ~9s each; max 6.7s fits), so it is recorded as
    the one hook this table cannot settle — **not** claimed as a broken gate.
  - The live VM had not pulled either docs merge at session end.

## Documentation Updated
- Rules doc updates: none required.
- Architecture doc updates: none — no pipeline stage changed.
- Trade pipeline doc updates: none (routing *behaviour* reverted to the documented
  `FLIP_POLICY=hold` default; no new stage).
- Roadmap updates: M26 row — the TF-class arm question is answered and closed.
- Subsystem doc updates: `docs/research/flip-override-walkforward-2026-08-11.md`
  (the measurement of record).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **`CLAUDE.md`'s two `FLIP_*` rows described a live Tier-3 configuration that no
  evidence supported.** Both now carry the live value, the disarm, and the bar for
  re-arming. This is the drift that motivated the whole sprint.
- **A `guards` run reported `conclusion: failure` having executed zero guards.**
  Run `31545035369` attempt 1: `actions/checkout` could not clone —
  `server certificate verification failed. CAfile: none CRLfile: none` — three
  retries, all TLS-failed, job over in ~30s. **The webhook reports this identically
  to a genuine guard failure**, so it invites a hunt for a defect in the diff that
  does not exist. Tell them apart by duration and first error line; a real guard
  failure gets past checkout and prints `PASS n · FAIL n`. Recorded on the
  coordination board; `rerun_failed_jobs` cleared it on the identical SHA.
- GitHub Actions was degraded through the evening — `guards` normally completes in
  ~30s and both attempts here ran 5+ minutes.

## Risks and Follow-Ups
- **Remaining technical risks:** `BL-20260810-TICK-CHAIN` stays **open**. 107s still
  misses the operator's 60s evaluation bar by ~1.8×, and a live position is
  re-evaluated once per tick. The row's resolution criteria ask for the dominant
  term to be justified or reduced; halving is neither.
- **Remaining product decisions (Tier 3):** re-arming the flip override needs a
  *fresh* walk-forward that clears **plain `hold`**. Beating the blind override arm
  is not sufficient — that arm loses.
- **Blockers:** none.

## Deferred Items
- End-to-end live-fleet verification of the registry-sourced shadow soak start (#8774).
- `BL-20260811-STATUSCHECK-PID-LOOKUP-AND-STALE-REPO-HEARTBEAT` (Tier 1, small).

## Next Recommended Sprint
- **Suggested next sprint:** instrument *inside* the two dominant tick hooks.
- **Why next:** the per-hook split is now trustworthy and says **where** but not
  **why** — `run_one_tick` 56.2s and `order_monitor` 49.3s, two near-equal halves.
  Inside the monitor, `strategy_monitor_loop` is 24.5s and the remaining ~20s is
  spread across **seven** 2–5s sweeps, none individually wrong. That is exactly the
  "each component is cheap, nobody watched the sum" shape this row exists to catch,
  and it is also why no optimisation should be proposed from the current data.
- **Required verification before starting:** a fresh `tick_cost` read to confirm the
  process has not been restarted (the counters are per-process; `process_started_utc`
  says from when), so the next measurement is not compared against a different population.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries — `intents.py`
      was read to confirm the `threshold <= 0` early return is what makes the age
      knob inert.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage changed, so `docs/TRADE-PIPELINE.md` needed no update.
- [x] Roadmap status was checked (M26).
- [x] Contradictions were recorded — including the CI one I did not cause.
- [x] Remaining unknowns were stated clearly, above, rather than rounded to done.
