---
name: backtesting
description: Run and interpret strategy backtests for the ICT bot — the standalone research harnesses (scripts/backtest_squeeze.py, backtest_fade.py, backtest_trend.py, backtest_ict_scalp.py, src/backtest/run_backtest_vwap.py), the on-demand M5 `/test <strategy>` consumer that writes to trade_journal.db::backtest_results, and the trainer-VM sweep mirror surfaced at /api/bot/backtests/sweeps. Use when the operator says "backtest <strategy>", "run a sweep", "validate this config on history", or asks where backtest code/data/outputs live. NOT for live tuning of config/strategies.yaml params (Tier-3) — this is the evidence-gathering step that precedes that.
---

# /backtesting — run and read ICT strategy backtests

Backtesting is the evidence step before any Tier-3 strategy change. This
skill covers the **per-strategy P&L harnesses** — the data each needs and where
results land.

> **This skill is NOT the full research toolbox, and must not be read as it.**
> The authoritative map of what this repo can measure is
> [`docs/research/RESEARCH-CAPABILITY-INDEX.md`](../../../docs/research/RESEARCH-CAPABILITY-INDEX.md)
> — regime conditioning, exit panels + **offline ML exit-head replay**, entry heads,
> research panels, allocator, pairs, and the robustness gates all live there and are
> **not** listed below.
>
> This paragraph replaces an earlier claim that the skill mapped *"every real backtest
> entry point in the repo (verified against the scripts on `main`)"*. That was false —
> 47 of 51 `scripts/research/` tools were in no skill at all — and the false claim of
> completeness did real damage on 2026-07-30: a session looking for a way to replay an
> ML exit head found none here, trusted a code comment saying it was impossible, and
> reported six live regime gates as permanently un-auditable. `analyze_exit_head.py`
> had done exactly that job the whole time. **A skill that overstates its coverage
> stops a session from looking further, which is worse than one that says "partial".**
> Audit: [`docs/research/RESEARCH-INFRA-AUDIT-2026-07-30.md`](../../../docs/research/RESEARCH-INFRA-AUDIT-2026-07-30.md).

**Per-strategy research harnesses are net-of-fee — with one exception.**
Gross-R sweeps mislead — S-STRAT-IMPROVE-S2/S4-A showed vwap was
gross-positive / net-negative once round-trip fees were charged. Most
harnesses below take `--fee-bps-roundtrip`; quote net metrics, not gross.
**Exception (BL-20260610-M15-1):** `scripts/backtest_ict_scalp.py` has
**no** fee model — no `--fee-bps-roundtrip`, so its R/PnL are **gross
only**. Don't read ict_scalp harness output as net; apply a fee haircut
manually, and prefer the account-compat matrix / trainer sweeps (which
stamp `net_of_fee_bps`) for the live-routing gate. Adding a fee model to
the ict_scalp harness is the open follow-up.

## MANDATORY: the per-account compatibility matrix (every strategy)

Before a strategy is proposed for live routing, run it against **every account's
ruleset** — the top-down "which strategy on which account" gate (design:
`docs/integrations/prop-accounts-architecture-DESIGN.md`):

```bash
python scripts/prop/account_compat_matrix.py --strategy <name> --data <feed>
```

For each account (`src.prop.account_rulesets.all_account_units`): **prop**
accounts are scored on the cost-aware EV + survival gate under their prop ruleset
(breach rules + economics); **standard** accounts on net-of-fee performance at
their own risk. Output: `runtime_logs/prop_eval/<date>/compat_<strategy>.{md,json}`
with a **ROUTE / skip verdict per account**. A strategy is never routed to an
account it wasn't evaluated against under that account's rules. (Prop verdicts are
research on the configured feed — revalidate on the account's real venue data
before any Tier-3 live wire.)

**Daily/ETF cells + the Alpaca real-money gate.** For an Alpaca ETF cell (the
daily/intraday SPY/QQQ/IWM/GLD/SLV/USO/TLT/IEF legs, outside the BTC engine
`ROSTER`), score it via the harness-emit path: pass the cell's
`scripts/backtest_{trend,pullback}.py --emit-trades` JSONL to
`account_compat_matrix.py --ledger <jsonl> --symbol <SYM> --base-risk-pct <pct>`.
The orchestrator `scripts/ops/etf_account_compat.sh` runs this for every Alpaca
cell at its exact `config/strategies.yaml` params (trainer-VM-resident
`data/<SYM>_<tf>.csv`). For these `standard` accounts the ROUTE gate is **tighter
than positive end-return** — it also requires `survival ≥ --min-survival` (0.90)
AND `P(breach) ≤ --max-p-breach` (0.10) under the account's own soft limits, and
stamps `symbol`/`asset_class`/`net_of_fee_bps` onto each row. **This daily/ETF
compat run is the MANDATORY gate before promoting any cell onto the real-money
`alpaca_live` account** (Tier-3); the ROUTE verdict must still be revalidated on
Alpaca's own real venue fills + fees before the live wire.

## Before a backtest number justifies a live change (added 2026-07-30)

Three checks. Each one caught a real error on 2026-07-30, and in every case the harness
reported success — the number was wrong, not missing.

### 1. Does the MEASURED population match the LIVE-TRADED population?

A verdict is only decision-grade if the trades measured are the trades that actually happen.
Two live examples, hours apart:

- **`side_filter` wasn't forwarded** to the harnesses, so two live *short-only* strategies
  (`sol_pullback_2h`, `trend_donchian_xrp_4h`) were measured on **both** legs. It didn't
  fail closed — the row degraded to `approximate`, which is common enough to be background
  noise.
- **The regime harness has no vol axis at all** (grep: zero hits for
  `trend_vol|vol_regime|calm|volatile`). So a 1-D cell re-audit **pools every vol state**,
  including states a 2-D `trend_vol` cell already gates live. A `squeeze_breakout_4h`
  trending-short proposal passed every stability gate and was **withheld** for exactly this:
  an unknown fraction of its 26 short trades are calm-regime trades live already refuses.
  Contaminated today: `squeeze_breakout_4h`, `trend_donchian`. Clean: `htf_pullback_trend_2h`,
  `gld_pullback_1h` — **checked, not assumed** (`BL-20260730-2D-VOL-CELLS-UNAUDITABLE`).

So: enumerate every live gate on the strategy (`config/strategies.yaml` levers **and** every
`config/regime_policy.yaml` cell, 1-D *and* 2-D) and confirm the harness models each, or name
what it omits. **`fidelity: faithful` is load-bearing** — never author or retire a live gate
off an `approximate` row.

### 2. Is n enough, and is the edge concentrated in one fold?

Pooled net-R alone is not evidence.

- **n matters more than it looks.** `squeeze_breakout_4h` over 730d gave n=28; at 2900d,
  n=135 — and **both** flagged cells reversed, *in opposite directions*: trending long went
  −2.24 (n=3) → **+11.60** (n=29), trending short went +1.15 (n=6) → **−3.40** (n=26).
  Acting on the thin read would have been wrong either way. Treat any cell under ~20 trades
  as unmeasured, and a cell at **n=0** as **cosmetic** — zero trades is not evidence a gate
  works.
- **Before accepting a small n, establish what BOUNDS it.** "Not enough trades yet" is a
  claim about the *source*, and it needs verifying like any other — see
  `CLAUDE-RULES-CANONICAL.md` § "Green is not evidence", obligation 5. A candle-replay
  harness is bounded by the history you request, not by the calendar: the same
  `squeeze_breakout_4h` cell went n=28 at 730d and n=135 at 2900d purely because of the
  `--days` argument. **Never lower a pre-registered bar or an honesty floor to manufacture
  a verdict** — raise the sample, or report insufficient and say what would raise it. A
  thin sample is also frequently OPTIMISTIC, not merely noisy: the macro M3 join read
  0.7364/0.909 at n=11 and 0.5885/0.720 at n=1263.
- **Always check the ex-max-fold sign.** `htf_pullback_trend_2h`'s short leg pooled **+7.89R**
  and was **−3.70R ex-fold-3** — ~147% of the edge in one fold, so the sign flipped.
  `squeeze_breakout_4h`'s short pooled −3.40R and held at **−0.77R ex-fold-3** — the sign
  survived. Report both numbers; a pooled figure that inverts without its biggest fold is
  a single-period artifact.

### 3. Restrictive vs permissive — they need different evidence

Turning a leg **off** is restrictive and can be justified *affirmatively* (a stable drag).
Turning one **on** is permissive, and "we can't justify keeping it off" is **not** "we have
evidence it makes money." On 2026-07-30 only the restrictive half of a two-sided finding was
proposed, because only that half was affirmatively supported. Don't flatten a two-sided
result into one change — it smuggles the weak half in on the strong half's evidence.

## Where backtest code lives (on `main`)

| Entry point | Strategy | Invocation |
|---|---|---|
| `scripts/backtest_squeeze.py` | squeeze_breakout (BB/KC squeeze) | `python scripts/backtest_squeeze.py --data <csv> [...]` |
| `scripts/backtest_fade.py` | fade_breakout (failed-breakout fade) | `python scripts/backtest_fade.py --data <csv> [...]` |
| `scripts/backtest_trend.py` | trend_donchian (confirmed-breakout follower) | `python scripts/backtest_trend.py --data <csv> [...]` |
| `scripts/backtest_ict_scalp.py` | ict_scalp_5m | `python scripts/backtest_ict_scalp.py --data <csv> [...]` |
| `src/backtest/run_backtest_vwap.py` | vwap (HTF-filter sweep) | `python -m src.backtest.run_backtest_vwap [...]` |
| `src/backtest/run_backtest_m5.py` | on-demand `/test` consumer | `python -m src.backtest.run_backtest_m5 <strategy>` |
| `src/backtest/run_backtest.py` | core `ICTBacktester` harness (`load_data`, `summarize`) | imported by the M5 runner; not a CLI you call directly |

> **Research-only harnesses live on the program branch, not `main`.**
> `scripts/research_decider.py` and `scripts/ops/fetch_dukascopy_index.py`
> are referenced by the strategy-improvement program but ship on the
> persistent branch `claude/strategy-improvement-program-EZi1X` (see
> CLAUDE-RULES-CANONICAL.md § "Strategy-improvement program — branching
> convention"). Don't document them as if they're on `main` — if you
> need them, continue on the program branch.

## Backtest data

All standalone harnesses read a candle CSV resolved as:

```
--data <path>   →   $BACKTEST_DATA_PATH   →   data/backtest_candles.csv
```

Never a bare filename relative to CWD — pass `--data` explicitly or set
`BACKTEST_DATA_PATH`. The trainer VM keeps a longer-history parquet cache
(qashdev/btc) provisioned by `scripts/ops/trainer_bootstrap.sh`; sweeps
that need multi-year history run there, not in the sandbox.

## Running a research backtest (squeeze / fade / trend / ict_scalp)

Common flags: `--data`, `--timeframe`, `--symbol` (default BTCUSDT),
`--resample`, `--start`/`--end` (ISO walk-forward window),
`--fee-bps-roundtrip` (default ~7.5 bps), `--json <out>` (write the
summary), `--emit-trades <jsonl>` (per-trade rows).

**Confidence-threshold sweeps** (squeeze / fade / trend / ict_scalp): each
emits a live-parity per-trade `confidence` (the same formula the strategy's
`order_package()` uses) and accepts `--min-confidence <f>` (skip entries
below the floor) and `--confidence-sweep '<lo>:<hi>:<step>'` (tabulate net
metrics per threshold to read off the PnL-optimal floor). This is the
evidence path for a `config/strategies.yaml::<strategy>.min_confidence`
proposal (Tier-3). Match the live params exactly (timeframe, trail, and any
regime gate like fade's `--adx-max` / squeeze's `--kc-mult`) or the optimum
shifts. Multi-year 5m sweeps (ict_scalp) are slow — run detached on the
trainer and collect from a file.

```bash
# Squeeze breakout (Bollinger/Keltner squeeze → breakout)
python scripts/backtest_squeeze.py --data data/backtest_candles.csv \
  --timeframe 4h --bb-period 20 --bb-std 2.0 --kc-mult 1.5 \
  --atr-stop-mult 2.5 --trail-mult 3.5 --timeout-bars 48 \
  --fee-bps-roundtrip 7.5 --json /tmp/squeeze.json

# Failed-breakout fade (Donchian pierce → reversion)
python scripts/backtest_fade.py --data data/backtest_candles.csv \
  --timeframe 4h --donchian 20 --atr-stop-buffer 0.5 \
  --exit-style far --adx-max 25 --json /tmp/fade.json

# ICT scalp 5m (sweep + displacement + FVG, HTF-gated)
python scripts/backtest_ict_scalp.py --data data/backtest_candles.csv \
  --timeframe 5m --htf-rule 1h --htf-ema-period 20 \
  --warmup-bars 50 --timeout-bars 24 --json /tmp/ict_scalp.json
```

**ict_scalp exit-model caveat (Phase-0, 2026-07-20):** the harness default
(static SL/TP + 24-bar timeout) does NOT match live exits — live runs a
break-even trail at +1R (`monitor_breakeven_sl` + `be_offset_bps`) and has
no timeout. Pass `--sim-breakeven` (and a wide `--timeout-bars`, e.g. 288)
for a live-faithful run. `--stamp-regime` + `--vol-spec-json <frozen spec>`
stamp decision-time regime/vol onto `--emit-trades` rows (same pure
functions as the live builder) for per-(trend,vol) cell attribution; emit
rows also carry `mfe_r`/`mae_r`/`bars_held`/exit fields. See
`docs/research/ict_scalp_5m-phase0-findings-2026-07-20.md`.

`backtest_ict_scalp.py` reads `config/strategies.yaml` by default; pass
`--ignore-yaml` to backtest pure CLI params instead of the live config.

The vwap harness (`python -m src.backtest.run_backtest_vwap --help`) has
its own rich flag surface (HTF filter, band-pct, regime split, net-of-fee
aggregates via `--fee-bps-roundtrip`, `--label`). It is the sweep workhorse
behind the `vwap-backtest-sweep` system-action.

## On-demand `/test <strategy>` (the M5 consumer → trade_journal.db)

The operator's `/test <strategy>` Telegram command (or a comms request)
runs inside the trader's poll loop, NOT in this session:

1. `cmd_test_strategy` validates the name against `config/strategies.yaml`.
2. `CommsPoller.poll_once` runs `BacktestConsumer.scan_and_run` (gated by
   `M5_CONSUMER_ENABLED`).
3. It spawns `python -m src.backtest.run_backtest_m5 <strategy>` under an
   `M5_BACKTEST_TIMEOUT_S` wall clock (default 120s).
4. The subprocess writes **one row** to `trade_journal.db::backtest_results`
   and prints `{"db_row_id": N, "summary": {...}}` as its last stdout line.
5. The consumer appends one NDJSON row to `runtime_logs/validation.jsonl`
   and answers the comms request.

Read those results from the sandbox via `GET /api/bot/backtests?limit=N&strategy=X`
(diag-reachable, Tier-1). Runbook: `docs/runbooks/strategy-testing.md`.

## Operator sweeps (trainer VM → mirror → /api/bot/backtests/sweeps)

The operator's real multi-config sweeps run on the trainer VM (kicked via
the `vwap-backtest-sweep` system-action or a `trainer-vm-diag` relay) and
publish `SUMMARY.md` + `all_metrics.json` into
`runtime_logs/trainer_mirror/backtests/<UTC-date>/` via
`scripts/ops/publish_trainer_mirror.sh`. Surfaced at
`GET /api/bot/backtests/sweeps?limit=N`. **This is the route that holds
the operator's real sweeps** — `backtest_results` (the table above) only
ever holds on-demand `/test` runs.

## Heavy backtests on a free GH runner — scope them, and know when they're done (2026-08-05)

From a web/PM session, route CPU-heavy backtests to a **free GitHub runner**, not
the 1-OCPU trainer (resource contract: `docs/claude/vm-resource-management.md`).
The pattern is an issue-labelled workflow that fetches candles → runs the harness →
posts a result comment: e.g. `c1-conviction-ab` (conviction A/B) and
`research-backtest-augment` (label-augmentation). Dispatch = open the labelled issue
(`git-actions` skill).

Two lessons the C1 A/B paid for in wasted runs — follow them:

**1. Scope the run to the runner's wall-clock cap. Measure the pass-time; don't
guess.** A `backtest_system` pass is roughly linear in bars: over ~210k 5m bars
(**730d**) it is **~25 min**; over ~105k (**365d**) **~13–18 min**. A job dies at its
`timeout-minutes`. So `N_symbols × N_arms × pass_time` must fit the cap **with
margin** — a 3-symbol × 2-arm year-of-5m A/B needs ~2.5 h and will never fit a
60–90-min cap. When it doesn't fit, **split one symbol per dispatch** (2 passes
≈ 30 min, huge margin) — those run in parallel on separate runners and each posts its
own result — or shorten the window. Estimating from a measured single-pass time and
dividing the work up front beats two guessed re-runs that each burn the full cap
timing out (exactly what happened 2026-08-05: 730d and 365d-×3 both timed out before
the per-symbol split landed the numbers).

**2. Know when it finished — and reliably. The wake mechanism matters more than the
run.** In this remote environment:

| signal | wakes this session? | use it for |
|---|---|---|
| **PR activity** (`subscribe_pr_activity`) | **Yes — reliable.** CI-fail + merge webhooks are delivered as `<github-webhook-activity>` turns. | Anything you must not miss — route the deliverable through a PR and watch it. |
| **Issue-triggered workflow result** (the SUMMARY.md issue comment) | **No.** The comment posts, but nothing pings Claude. | Fine for a result you'll actively go read; NOT for fire-and-forget. |
| **`ScheduleWakeup` / `send_later` self check-in** | **Unreliable.** A long idle stretch reclaims the ephemeral container and the self-wake never lands. | Short waits only; never the sole mechanism for a long research run. |

So: don't lean on a self-timer to collect a long (≥ tens-of-minutes) research run — it
routinely won't fire. Prefer the **PR/webhook** path when you need a dependable "it's
done", keep self-check-ins short and expect to re-arm them, and don't conclude a run
succeeded until you've actually read its result comment.

**Make a timed-out / errored run LOUD, not a soft null.** A workflow whose result step
falls back to a bland "(summary not produced)" on a *cancelled* run reads like a clean
negative — the unasserted-denominator trap (`silent-empty` / diagnostic-provenance
class). A research workflow must post a clearly-marked **❌ RUN DID NOT COMPLETE
(timed out / errored) — re-dispatch smaller** so the next reader knows a **re-run** is
needed, not that the answer is null. Assert the result artifact exists before believing
"no result = negative result".

## Output locations — at a glance

| Output | Where |
|---|---|
| Standalone harness summary | `--json` path you pass (e.g. `/tmp/*.json`) |
| Standalone per-trade rows | `--emit-trades` JSONL path you pass |
| `/test` headline metrics | `trade_journal.db::backtest_results` (one row/run) |
| `/test` audit trail | `runtime_logs/validation.jsonl` (one NDJSON row/run) |
| Operator sweep summaries | `runtime_logs/trainer_mirror/backtests/<date>/` |

## Tuning a parameter from a review-gate `tune` packet (M8)

When the M7 strategy review gate emits `proposed_action: "tune"`, its
`tune_recipe` block is executed by the **canonical M8 sweep harness**,
`scripts/ml/strategy_tune_sweep.py` — don't hand-roll a sweep over the
harnesses above. It ingests the recipe (a review packet or a bare recipe
JSON), expands the `search_space`, dispatches to the right backtester here
(research-harness `min_confidence` per-value; vwap `threshold` via
`--threshold-sweep`), normalizes net-of-fee metrics, and writes a
`strategy_tune_result/v1` packet to `runtime_logs/strategy_tunes/<date>/`
with an **advisory** Tier-3 value proposal. It never writes
`config/strategies.yaml`. Full reference: `docs/strategy-tuning.md`.

```bash
python scripts/ml/strategy_tune_sweep.py --recipe <packet.json> --data <csv>
python scripts/ml/strategy_tune_sweep.py --recipe <packet.json> --dry-run   # plan only
```

## What to report

A backtest result that justifies a Tier-3 change must state: net (not
gross) total R, net win rate, trade count (sample size), expectancy,
max drawdown, the fee bps assumed, and the date window / regime mix. A
gross-positive / net-negative or low-N result does **not** clear the
go-live bar — say so plainly. Promoting a strategy on the strength of a
backtest is still Tier-3 (operator-approved); this skill produces the
evidence, the `new-strategy` skill wires it, the operator approves it.

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
