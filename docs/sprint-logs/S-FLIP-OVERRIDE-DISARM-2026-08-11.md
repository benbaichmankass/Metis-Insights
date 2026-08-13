# Sprint Log: S-FLIP-OVERRIDE-DISARM-2026-08-11

## Date Range
- Start: 2026-08-11 ~17:20Z
- End: 2026-08-12 (session continued overnight at operator direction — see
  "Overnight continuation" below; everything above this line describes the work
  through ~23:15Z and is left as written)

## Objective
- Primary goal: measure the **live, un-walk-forwarded** flip-confidence override
  (`FLIP_CONFIDENCE_THRESHOLD=0.15` / `FLIP_MIN_POSITION_AGE_HOURS=4.0`) against
  plain `FLIP_POLICY=hold`, and act on the result — the override had been routing
  real money since ~2026-08-10 with no backtest behind it
  (`BL-20260811-FLIP-OVERRIDE-NEVER-WALKFORWARDED`).
- Secondary goals: settle whether a **TF-class-restricted** re-arm is warranted;
  get a warm-process `tick_cost` read for `BL-20260810-TICK-CHAIN-260S-PER-TICK`; recover the
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
- **Remaining technical risks:** `BL-20260810-TICK-CHAIN-260S-PER-TICK` stays **open**. 107s still
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

## Overnight continuation (2026-08-12, operator-directed)

The operator directed the session to continue autonomously overnight, working the
remaining list and reserving every Tier-3 decision for the morning. No Tier-3
action was taken in this window — nothing here changes strategy logic, risk caps,
account mode, or a live parameter.

- **Split the OTHER half of the tick** (`BL-20260810-TICK-CHAIN-260S-PER-TICK`,
  still open). `src/runtime/pipeline.py` gained a `_phase()` helper mirroring
  `order_monitor`'s, wrapping four children: `regime_bar_scoring` · `signal_build`
  · `news_score` · `dispatch`. Chosen because their **fixes differ** — a
  cadence-gated fetch, a per-strategy candle fan-out (batchable), a network call
  (cacheable), and broker round-trips (**not** reducible without touching the
  order path) — so guessing between them was not good enough. Names are dotted,
  so `snapshot()` counts them as children and `attributed_pct` cannot regress
  into the >100% double-count. Budget 20 of 32 names.
- **Incidental finding worth keeping:** only `run_one_tick` and `order_monitor`
  are instrumented at top level, so the measured `attributed_pct: 98.4` means all
  **ten** other tick hooks COMBINED are **1.6%**. They are genuinely cheap; the
  entire tick cost is in those two halves. That retires "maybe it's the prop
  prompts / soaks" as a hypothesis without further measurement.
- **Fixed `status-check`'s two diagnostic defects**
  (`BL-20260811-STATUSCHECK-PID-LOOKUP-AND-STALE-REPO-HEARTBEAT` — **fix shipped,
  row deliberately left OPEN**). Its resolution criteria demand a status-check run
  whose diagnostic actually resolves the PID and dumps `DATA_DIR`, and that cannot
  be satisfied from a branch: `ict-git-sync` deploys from `main`, so a run today
  would exercise the OLD script and a green result would prove nothing. The row
  closes on a post-merge run, not on this commit.
  The PID lookup used a `pgrep` pattern matching `python3` where the venv
  ExecStart is `python`, so the tool printed "trader pid not found" while its own
  `ps` output listed the process; it now resolves via
  `systemctl show -p MainPID` — **the idiom the repo already uses** in `get-env`
  and the mounted-storage runbook, so this aligns with convention rather than
  inventing a second answer. Failure is now three-state (no MainPID / could-not-
  read `/proc` / read-ok). The legacy repo-path `runtime_logs` header now says
  outright that it is pre-cutover and NOT the live heartbeat, which is what let a
  two-month-stale mtime sit unlabelled beside a 42s-old live one.
- **Filed `BL-20260812-ACTIONS-RUNNER-TLS-VERIFY-FAILS-AS-RED`.** Three runner TLS
  failures in ~70 minutes across two workflows and two different steps, all
  clearing on rerun against the identical sha. Filed for how it PRESENTS — the
  webhook reports `conclusion: failure` with the check name, byte-identical to a
  real guard failure, and I chased a phantom defect in my own diff once before
  reading the log. Deliberately proposes no retry-wrapper: masking a flake so it
  reads as a clean pass is worse than a loud one.
- **Doc-vs-reality drift I introduced, then fixed:** `CLAUDE.md`'s
  `/api/diag/tick_cost` row said `order_monitor` "is a PARENT of 14 children",
  naming one parent. Adding the `pipeline.*` children made that incomplete the
  moment it shipped, so the row now states both parents, the cross-symbol `n`
  caveat, and the name budget.

### Overnight validation
- `guards` PASS, `pytest-collect` PASS, `repo-inventory` PASS on the head
  carrying the pipeline split; `tests/test_tick_cost.py` 22 passed locally.
- The 4 failures in `tests/test_smoke_test_pipeline.py` were confirmed
  **pre-existing** by stashing and re-running on clean `main` — identical set.
  Not caused by this work.
- The three-state PID resolver was exercised across all branches **including a
  positive control** (a real readable pid reads `ok`), so the probe is shown able
  to find a positive rather than only to stay quiet.
- `canonical-doc-coherence` passes locally (all 5 checks).
- **Still not verified:** the registry-sourced shadow soak start (#8774) against
  the live fleet — diag request #8799 is open for it, and the read is written to
  be falsifiable (if the log floor and the registry date coincide by luck, that
  is explicitly NOT a confirmation).

## Day-2 continuation (2026-08-12, post-merge)

After #8796 merged and `git-sync` pulled `1a5126a`, the session continued at
operator direction. All Tier-1.

### The tick question got its answer
The post-deploy warm read (diag #8804 — 96 ticks, one process from 09:56Z) gave
mean **107.9s** / max **127.7s**, consistent with the pre-split 107.2s, and the
new `pipeline.*` children localised the dominant cost:

| | share of tick | per tick |
|---|---|---|
| `pipeline.signal_build` | **43.3%** | ~46.7s |
| `monitor.strategy_monitor_loop` | 21.9% | ~23.6s |
| `pipeline.regime_bar_scoring` | 5.4% | ~5.8s |
| `pipeline.news_score` / `dispatch` | 0.1% / 0.2% | negligible (n=13 / n=7) |

**Read the `n`:** `pipeline.*` children carry n=2208 against the parents' n=96 —
exactly 23 symbols per tick — so their `mean_ms` is per SYMBOL, not per tick.
`pct_of_total` is the comparable field. Arithmetic checks: monitor children sum
to 44.3% vs the parent's 44.5%; the two parents sum to 98.4 vs `attributed_pct`
98.3. What this RULES OUT is as useful as what it finds — news and dispatch fire
only on actionable signals, and the other ten hooks are 1.6% combined.

### A cache that structurally cannot hit
Derived from code, no deploy needed: `_candle_cache_ttl` is
`min(bar_seconds × frac, 60.0)` while consecutive ticks are ≥108s apart, so **the
candle cache cannot hit across ticks for any timeframe** — its only value today is
within-tick sharing. The 60s cap binds for every bar ≥10m (a 1h frame wants 360s,
a 4h frame 1440s), which are exactly the frames where a ~108s-old copy is safest.
Shipped as a MEASUREMENT (`fetch.<timeframe>` + `fetch.cache_hit`, PR #8805), not
as the fix: if the misses are ≥15m-dominated, raising the cap is a one-line win;
if they are 5m-dominated it buys nothing. The counts decide, not the argument.

### `main` was red, and not because of us
`test_exchange_fills_list_rows.py::test_newest_first` failed on CLEAN main
(verified by reverting). A **time bomb that detonated 2026-08-12**: absolute
fixture dates against a 7-day window measured from the real clock, so the "old"
fill (2026-08-05) crossed the boundary and is now correctly dropped. The
production code is right; the test aged out. Fixed the CLASS — `_fill` defaults to
2026-08-06 and eight other calls query the same window, all one day from the same
failure — by injecting `now` throughout, the pattern the sibling test already used.

### Backlog hygiene, and the guard it produced
Two HIGH rows were **already fixed and still open**: the trend-harness fork
(closed by convergence — 624→1022 lines / 30→45 flags in the canonical harness,
the other reduced to a 106-line shim) and the shadow-stats soak start
(live-verified: `soak_days` 82.18d where the rotation floor said ~6.7d, a 12×
understatement on the promotion gate's own denominator). A third,
`TICK-TAKES-253S`, was reconciled — its number is superseded by 107.9s, its
concern is not.

Both had the same cause: **no `resolution_criteria`**. So a diff-scoped guard now
requires new rows to state what done looks like. Scope chosen from measurement —
114 of 262 open rows (43%) lack usable criteria, so a whole-tree gate would fail
on day one and be switched off; the census ships advisory alongside so the debt
stays visible. Placeholders rejected and a length floor applied, because a guard
cheaper to lie to than to satisfy is worse than none.

### Duplicate-netted-pnl: done, and the residue is false positives
Census (#8809, read-only): **already marked 29, to mark 4**. The whole `bybit_1`
population — 29 rows, $24,270.53, the actual research-integrity problem — was
already applied. The 4 residual `bybit_2` rows are **rounding collisions, not
duplicates**: a stamped netted record is identical to the cent (the confirmed
cluster was −2970.99 across 60× sizes), while these differ at the 3rd decimal
(1.38330527 vs 1.38136254; −1.13104541 vs −1.12674735). Recommend NOT marking
them — $5.02 total, and marking a correct row destroys real information.

### Day-2 gaps
- The probe's counts need hours of ticks; nothing is decidable until then, and the
  three branches are pre-specified so the decision cannot be fitted to the data.
- The two downstream re-validations on the duplicate-pnl row (re-run the P1.x
  fidelity trust map post-exclusion; check ML labels spanning those rows) are
  **not** done — deliberately not started late in a long session, since they are
  trainer-VM jobs that would land with nobody to read them.
- `status-check`'s row still needs one post-deploy dispatch to close.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries — `intents.py`
      was read to confirm the `threshold <= 0` early return is what makes the age
      knob inert.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage changed, so `docs/TRADE-PIPELINE.md` needed no update.
- [x] Roadmap status was checked (M26).
- [x] Contradictions were recorded — including the CI one I did not cause.
- [x] Remaining unknowns were stated clearly, above, rather than rounded to done.

## Day-3 overnight continuation (2026-08-12 21:50Z →, operator asleep)

Operator handed the session off for autonomous overnight work with two
conditional Tier-2 pre-approvals and an hourly Telegram cadence. All Tier-1
except where noted; no Tier-3 was enacted.

### The tick question is answered: branch (a), and the fix is not the one-liner

The probe returned its first WARM read — **`ticks_measured` 50, one process from
19:56:48Z, payload 21:55:42Z** (diag #8811). Population stated because the prior
reading was 3 cold ticks.

| on-loop | n | mean_ms | per tick | % of tick |
|---|---|---|---|---|
| `fetch.1d` | 160 | 3633.8 | 11.63s | 13.9 |
| `fetch.15m` | 341 | 1386.7 | 9.46s | 11.3 |
| `fetch.1h` | 446 | 570.1 | 5.09s | 6.1 |
| `fetch.5m` | 473 | 516.9 | 4.89s | 5.8 |
| `fetch.4h` | 265 | 220.4 | 1.17s | 1.4 |
| `fetch.2h` | 219 | 214.5 | 0.94s | 1.1 |
| `fetch.1m` | 50 | 226.3 | 0.23s | 0.3 |
| `fetch.cache_hit` | 548 | — | — | — |

Misses at ≥15m are **1431 of 1954 = 73.2% by count, 84.7% by time** → branch (a).
Arithmetic reconciles: fetch is 33.4s/tick, the `pct_of_total` column sums to
39.9%, and 33.4/83.868 = 39.8%.

**Branch (c) was ruled out independently**, which matters because it did not rely
on the same instrument: status-check #8813 in the same window shows loadavg
**0.24 on 2 cores** and the trader at 21.2% CPU while a tick takes 83.9s. The
tick is I/O-wait, not compute.

**But the pre-specified fix was wrong, and that is the finding.** Branch (a) was
written as "raising the cap is the fix… Tier-2". Strategies read
`candles_df["close"].iloc[-1]` as the CURRENT PRICE for entry geometry
(`_base.py:145`, `trend_donchian.py:831`, `turtle_soup.py:476`, `vwap.py:1039`,
`ict_scalp.py:773`) and the monitor reads the same field for exits — so the TTL
bounds how stale the price behind a live order may be. The VALUE is an
order-path decision (Tier-3); making it SETTABLE is Tier-1. PR #8815 ships that
split: `CANDLE_CACHE_TTL_MAX_S` at the incumbent `60.0`, byte-for-byte unchanged,
one env flip to move. I did not take the pre-approval as licence to ship a
change whose tier I had just discovered was different from the one approved.

Also shipped `fetchby.<consumer>`, the cut that decides whether a SAFE fix
exists: if the ≥15m misses are `regime_bar_scoring` (observe-only, dedups to
closed bars) there is a large win that never touches an order's price; if they
are `signal_build`, there is not.

**A reading that would have been misread:** the tick mean fell 107.9s → 83.9s,
and that is NOT an improvement — the M20 decouple moved `strategy_monitor_loop`
into `offloop_hooks`, so the work went concurrent, not away.

### The duplicate-pnl question is answered, and the answer is the worse one

Marking applied 2026-08-06 23:25–23:29Z (#8543, verified #8545); all 29 present
in the trainer's real journal (#8818).

All 29 carried `pre_remediation_exit_price_source = 'bybit_closed_pnl'` →
**MEASURED**, and `pnl_is_trustworthy` **ADMITTED 29 of 29** pre-marking (#8823,
full population). Exposure was real, not nil. Scope differs by family and the
difference is load-bearing: `conviction_meta` and `setup_candidates` exclude
paper and every marked row is `bybit_1` (`account_class: paper`), so those two
were never exposed; `trade_outcomes` / `setup_labels` / `setup_labels_audit`
filter only `status` + `is_backtest`, so for those three the rows were fully
admissible labels until 2026-08-06. `trade_outcomes` labels `won = pnl > 0`, so
a pnl duplicated across 5 rows manufactured 5 labels from 1 outcome. The current
fleet is clean — the newest runs (2026-08-12 01:41–01:47) postdate the marking.

**The class it exposes** (`BL-20260812-MEASURED-PROVENANCE-CANNOT-SEE-MISATTRIBUTION`):
those rows wore MEASURED and were CORRECT to. `bybit_closed_pnl` accurately
names the source; one netted close's record was written to N siblings, so each
row has genuine broker provenance and the wrong OWNER. Provenance grades where a
number came from, not whether it belongs to this row — an orthogonal axis with
no field. That is why every provenance sweep passed them and an ARITHMETIC
impossibility (R = −99.5 on 0.012 BTC ⇒ a ~$247k move) is what surfaced them. A
per-row grade cannot see a cross-row duplication.

### Also fixed, found on the way
- **`backtest_fidelity_calibrate`** silently widened its population when
  `provenance` failed to import — `rows_trusted` equalled `rows_scanned`, byte-
  identical to a spotless leg. Now declares `trust_filter: applied|unavailable`.
  The decisive test asserts the two at EQUAL counts so the field cannot be keyed
  off the counts.
- **`status_check.sh`'s could-not-look branch** now has a test that extracts the
  block from the shipped script text; verified against the ACTUAL pre-fix string.

### Two corrections of my own work, both caught by checking rather than by luck
- My `json.dump` used `indent=1` and re-serialised the whole backlog (12,948-line
  diff), which made `impossibility-claim-guard` attribute 13 long-standing claims
  to this PR. Fixed to `indent=2` + prepend → 58 lines. **The trap:** my first
  "baseline" comparison was against my own bad commit, and a pristine
  `origin/main` worktree returned `0` — which was VACUOUS (diffing main against
  itself scans nothing). I nearly took that as a clean bill of health.
- I reported the trainer DB sync as 10 days stale. Wrong: I had queried a stray
  repo-root `trade_journal.db` (8.5 MB, 0 trades, 15,644 signals — itself the
  CWD-fallback artifact `canonical-db-resolver` exists to kill). The real copy is
  804 MB, fresh 05:00.

### CI: "checks not attaching" was a merge conflict, not a GitHub flake
`get_check_runs` returned `total_count: 0` across three pushes. An empty commit
did not help. The cause was `mergeable_state: dirty` — `main` had advanced to
`1a2922c` (#8814, another session documenting the same decouple) and GitHub does
not run PR checks on an unmergeable PR. Resolving the `CLAUDE.md` conflict
restored them immediately. **First thing to check on a not-attaching PR is
`mergeable_state`, before reaching for workarounds.** The conflict was resolved
by MERGING both versions, not picking one — theirs is richer on `offloop_hooks`
(thread identity, empty-means-broken), mine had `fetchby.*` and the measured
27-of-32 name budget.

### Open / handed forward
- The cap VALUE (Tier-3) — operator's, with the numbers above.
- `fetchby.*` needs a post-deploy read before the consumer-scoped TTL is decidable.
- The fidelity trust-map re-run: excluded by construction, but no committed
  artifact exists so the recorded verdict could not be dated.

### Day-3 addendum: the fetchby cut answered it, and removed the safe option

`#8815` merged and deployed (trader restarted **06:21:38Z**). The consumer
split, at `ticks_measured` **19** on that new process:

| on-loop consumer | % of tick | % of fetch |
|---|---|---|
| `fetchby.signal_build` | 27.3 | **78.9** |
| `fetchby.regime_bar_scoring` | 6.4 | 18.5 |
| `fetchby.unattributed` | 0.9 | 2.6 |

**The instrument validated itself:** `fetch.*` and `fetchby.*` are recorded
independently and both sum to **34.6%** of tick — delta **0.0pp**.

The cut existed to test one hypothesis: is there a consumer-scoped TTL that
never touches an order's price? `regime_bar_scoring` is observe-only and dedups
to closed bars, so a long TTL there is free. **The answer is no, not at a useful
size.** The families cannot be multiplied (two cuts, not a joint distribution)
but they bound: at least **63.3%** of fetch time is both `signal_build` and
≥15m, and regime explains at most **21.9%** of the ≥15m cost. So the frames
worth caching are largely the ones feeding `close.iloc[-1]` into entry geometry,
and the cap trades cache hits against the price behind live orders with no side
door. Prize if that trade is accepted: **19.8% of the tick** (1d+1h+4h+2h).

Two things left explicit rather than glossed: `fetchby.strategy_monitor_loop`
(off-loop, n=1120) is the largest fetch consumer by COUNT anywhere, so a TTL
change reaches EXITS too; and nothing measures WHICH timeframes `signal_build`
asks for — that needs a consumer×timeframe cross costing names against
`_MAX_HOOK_NAMES`. Docs + backlog in **#8930**.

**A tooling note for the next overnight session:** `send_later` clamped a
requested 62-minute delay to **07:00Z** (~8h). It cannot carry an hourly
cadence; background `sleep` timers can, and did.
