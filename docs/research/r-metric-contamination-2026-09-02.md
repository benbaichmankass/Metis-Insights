# R-metric contamination — the measurement record (2026-09-02)

> ## ⚠️ CORRECTION to this PR's own `FOR THE MANAGER` section
>
> The PR body's **Tests** bullet reads *"I could not run the full suite … **I
> verified this is not my change: `origin/main` produces the identical 99
> errors.** CI is the authority."* **Read that bullet with this correction.**
>
> **What was actually verified** was narrower than the sentence implies: the 99
> **collection** errors, which are identical on `main`. A full-suite comparison
> was then run and came back **983 failed / 11,592 passed / 163 errors** on
> `main` against **999 / 11,863 / 172** on the branch — a delta I could not
> attribute, because **that baseline ran in a different working directory**
> (a `--shared` clone in `/tmp`, so no untracked `runtime_logs/`, no `.env`,
> different path resolution). The `+271 passed` against 67 new tests is the tell
> that the two runs were never comparable. My own bad instrument, reported here
> rather than left standing.
>
> **Re-run apples-to-apples, same working directory, same untracked state:**
> `main` **994 / 11,801 / 172** vs branch **999 / 11,863 / 172** — **error
> counts identical**. And the *same ref* swung 983 → 994 failures between two
> runs, so this sandbox is not a stable instrument at any point.
>
> **What actually answers the question:** CI `pytest-run` is **GREEN on head
> `824254e9`** — the full suite in the correct dependency environment. So is
> `guards` (local `PASS 51 · FAIL 0 · SKIP 19`, which also supersedes the body's
> stale `PASS 50 … b8bb899`), `pytest-collect`, and `repo-inventory`.
>
> **Still open, and deliberately not claimed either way:** the same-directory
> delta is `+5 failed / +62 passed`, and `5 + 62 = 67` is exactly the new-test
> count. That arithmetic coincidence is **unexplained**. It does not reproduce
> when the two new test files run alongside their nearest neighbours (97
> passed), and CI's full-suite run is green — but if any of the new tests are
> order-dependent, that is a real defect worth fixing even where CI's ordering
> happens to pass. Not asserted as resolved.
>
> The corrected bullet text is committed at
> `automation/pr-requests/r-metric-contamination-20260902.json`. The **live PR
> body was left unedited on purpose**: `update_pull_request` replaces the whole
> body, and hand-re-sending 23 KB of a document this dense with precise figures
> risks introducing an error into the deliverable — a worse outcome than a stale
> caveat sitting next to a visibly green check.

Durable home for the numbers behind **PR #10748**. The PR argues; this file is
where a later session goes to re-check or refresh, per
`docs/CLAUDE-RULES-CANONICAL.md` § "A MEASURED must say WHERE THE MEASUREMENT
LIVES".

## Locator

| | |
|---|---|
| source | `/home/ubuntu/ict-trading-bot/data/trade_journal.db` on the trainer VM (a synced copy of live) |
| copy mtime | `2026-09-02T04:28:35Z` |
| `max(created_at)` | `2026-09-02T04:11:21Z` (~17 min behind live) |
| trader serving sha | **`2c7ae605`** (`/api/diag/version`, read 2026-09-02T04:33Z, `restart_pending: false`) |
| probes (re-runnable) | `automation/trainer-diag-requests/r-contamination-probe-20260902{b,c,d}.sh`, `r-detector-live-validation-20260902.sh` |
| outputs | `automation/trainer-diag-results/` (same basenames, `.txt`) |
| relay | `.github/workflows/trainer-diag-relay.yml` — push the request file, read the result file back |

⚠️ **The trainer holds TWO journals** and the obvious path is the stale one:
`/home/ubuntu/ict-trading-bot/trade_journal.db` had an mtime of **2026-08-02**,
a month behind. The live-synced copy is under `data/`. The probes pick the
newest by mtime and print the path; do not hard-code either.

⚠️ **`sqlite3` (the CLI) is NOT installed on the trainer.** Probe v1 returned
`command not found` on every query while exiting 0 at the relay level — a
green run that measured nothing. Use `python3` + the `sqlite3` module.

## Population

`status='closed' AND pnl IS NOT NULL AND COALESCE(is_backtest,0)=0` — **n = 1346**
(1325 with both `entry_price` and `stop_loss` non-null).

⚠️ **WIDER than `/api/bot/performance`'s**, which additionally drops reconciler
/ superseded / reset-flat rows. Do not quote these counts as that route's.

## The partition, as graded by `src/runtime/r_provenance.py`

Module sha256 `fd40d801c4a3ffdacf2ab71ba656765ed9440528bcc11eb77ab91a43e5755210`
(fetched from the branch's raw URL by the validation probe, so the code that
ran IS the code under review).

| state | n | share | reason breakdown |
|---|--:|--:|---|
| `contaminated` | 118 | 8.8% | `wrong_side_of_entry` 118 |
| `confirmed_initial` | 156 | 11.6% | `matches_declared_initial_risk` 156 |
| `unverified` | 1051 | 78.1% | `no_declared_initial_risk_record` 643 · `disagrees_with_declared_initial_risk` 300 · `bracket_mirrored_vs_direction` 108 |
| `no_basis` | 21 | 1.6% | `risk_inputs_missing` 21 |

Sums to 1346 — asserted in the probe, not trusted.

**The naive wrong-side count is 226 and it OVERSTATES contamination by ~1.9×.**
108 of those rows are direction-mirrored `intent_reduce` legs (see below), not
trailed stops.

## The mirrored-bracket resolution

A cross-check built into probe v3 **failed**: 34 rows graded
`confirmed_initial` on distance while sitting on the wrong side of entry.
Probe v4 resolved it rather than smoothing it over:

- 34 of 34 are `setup_type='intent_reduce'`.
- `trades.direction` is the **opposite** of `order_packages.direction` on all
  34 (108 rows disagree that way across the population).
- 34 of 34 have an incoherent `entry`/`sl`/`tp` ordering — the **whole bracket**
  is inverted, not just the stop.
- **Correct by design:** a reduce leg's `direction` is the closing side while
  its SL/TP are inherited from the original position.

Discriminator, threshold-free: **a trail moves the STOP; a mirror moves BOTH.**
Live validation: **108 of 108** mirrored rows are `intent_reduce` and no other
`setup_type` appears — the test isolates exactly the known-cause population.

## The disagreement ratio — STATE THE POPULATION

`declared_initial_risk / stored_stop_distance`, over the 682 rows carrying a
declared `risk_per_unit`. The two side-populations differ completely and
**pooling them describes neither** (an earlier pass of this measurement did
pool them and produced a median of 1.022 that is true of no real population):

| population | n | median | p75 | p90 | p95 | max | ≥2.0 | <0.99 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| correct-side | 456 | **1.0000** | 1.0262 | 1.1411 | 1.3370 | 211.7 | 12 (2.6%) | **111 (24.3%)** |
| wrong-side | 226 | 1.0353 | 5.3117 | 17.95 | 62.60 | **3014.2** | 91 (40.3%) | 37 |

The dense mass at ~1.0 on correct-side rows is **not a trail** — a quarter of it
reads *below* 0.99 (stored stop **wider** than declared), which trailing cannot
produce. Its cause is **not established**. That is why a distance disagreement
is never graded `contaminated`.

## Concentration

Zero contamination, structurally: `vwap` (318 real-money rows), the four
`pairs_*` legs (283 rows), every `*_1d` leg. **INFERRED:** these are exactly
the families with no trailing monitor. Worst: `mgc_trend_1h` paper **18 of 19**,
`ict_scalp_mgc_15m` paper 4 of 6, `ict_scalp_sol_5m` paper 13 of 29,
`ict_scalp_5m` **real money** 9 of 33.

## Recoverability

`order_packages.meta.risk_per_unit` is written at signal time and
`order_monitor._apply_update` writes only `sl`/`tp`, so a trailing amend cannot
reach it. **682 of 1346 rows (50.7%) carry it** ⇒ the fix is **not
forward-only**. The other ~49% — `vwap` and the pairs sleeve included — has no
independent record and is permanently ungradeable.

`order_packages.sl` is **not** an alternative: it equals `trades.stop_loss` on
929 of 1325 rows, differs on 36, and no package joins on 360.

## Worked example

Trade **4773**, `ict_scalp_mgc_15m`, long: entry `4364.6`, stored stop
`4371.1469` (**above** entry), declared `risk_per_unit` `18.947142857143263`.
Stored distance `6.5469` ⇒ the declared risk is **2.89×** the stored one.

Worst single row: trade **5027**, `ict_scalp_sol_15m` paper, short, entry
`100.44`, stop `100.439115` — a stop distance of **0.000885** on a ~$100
instrument, giving **R = +3672.32**.
