# S-M31-HARNESS-MFE-DIST — the harness half of P5 precondition 2

- **Date:** 2026-08-17
- **Milestone:** M31 (position telemetry) — P4 Check B / P5 precondition 2
- **Tier:** 1 (research tooling + CI wiring + docs). No `src/`, no `config/`, no order path.
- **Backlog:** `PB-20260817-NO-COMMITTED-PER-TRADE-HARNESS-MFE` — **still OPEN**, updated.

## Objective

M31 P5's binding blocker is precondition 2: P4 Check B abstains because the live
final-MFE population is **n=1 fleet-wide**. That is a soak-depth problem nobody
can hurry.

But Check B needs **two** inputs — a live final-MFE population **and** a harness
`mfe_r` distribution — and the second was missing for an entirely different
reason: it was simply never committed. A session waiting only on live depth
would have reached the floor and *then* discovered the other half absent.

This ships the mechanism for the harness half. It does **not** ship the numbers,
and the distinction is the whole finding.

## Verified, not inherited

The backlog row claimed the corpus carries no per-trade `mfe_r`. Re-measured
here: a key census over **all 1,376 rows** of `docs/research/m20-sweep-corpus.jsonl`
finds **zero** keys containing `mfe`. Reproduces exactly.

Two incidental observations while there, both worth the next session's time:
the corpus keys on **`leg`**, while `--harness-emit` rows key on **`strategy`**;
and every corpus row does carry `tp_cap_pct`.

## What shipped

**`scripts/research/m31_harness_mfe_dist.py`** — aggregates a
`backtest_trend.py --emit-trades` JSONL into a small committed per-leg record
(percentiles + `n`, never per-trade rows, per the backlog row's own resolution
criteria: *"it is small, it versions with the corpus, and it does not need a
sweep to be reproducible"*).

**`m31_mfe_parity.py --harness-dist`** — consumes that artifact. Mutually
exclusive with `--harness-emit`, because two harness sources at once would make
the report's provenance a function of argument order.

Three things follow from having a *committed* artifact rather than raw rows:

- the **`tp_cap_pct` gate becomes PER-LEG** rather than one global flag — an
  artifact can hold legs swept under different settings, and one uncapped leg
  must neither condemn nor be excused by its neighbours;
- `harness_source` (`emit_rows` / `committed_dist`) travels into every record,
  because a fresh sweep and a committed artifact are different provenance for
  the same claim;
- `harness_symbol` / `harness_timeframe` reach the report, which is what makes a
  wrong-instrument comparison *visible* instead of merely wrong.

Percentiles are **imported** from `m31_mfe_parity._pct`, never re-derived — the
same rule that made `backtest_trend.py` import `r_distances` from the live
telemetry module. Pinned by a test that fails even against a byte-identical
local copy.

## The trap this is built around

`data/*.csv` is **gitignored**, so per-leg candles are not in the repo —
measured, not assumed: `m20_fleet_exit_sweep.resolve_data` returns
`(None, False, None)` for all five of `SOLUSDT/4h`, `XRPUSDT/2h`, `BTCUSDT/1h`,
`ADAUSDT/2h`, `QQQ/1d`.

The only committed candles are `data/backtest_candles.csv` — **BTCUSDT
1-MINUTE**, 5,000 bars, median `(high−low)/close` **0.101%** — where the 9.9%
venue cap lands at **~37R**, against live legs measured at cap_R **2.13–5.83**.

So the tempting shortcut (sweep the fixture, commit *that* distribution) would
write a wrong-regime artifact under the exact name Check B reads, and Check B
would then grade live 4h legs against a 1-minute BTC distribution. That is
M31's own defect class — *the harness measured a book production does not run* —
except authored by us and versioned, which is strictly worse than the honest
absence it would replace.

Hence: the aggregator **refuses** an uncapped sweep and **requires**
`--symbol`/`--timeframe`, and the consumer refuses a mismatch. The tooling does
not make the shortcut easy.

## Validation

- Both self-tests green: parity **14/14** (10 pre-existing + 4 new),
  aggregator **8/8**.
- **End-to-end on a real capped sweep**, not a fixture of a fixture: 144 emit
  rows, **144/144** carrying `mfe_r` → aggregator → artifact → parity returned
  `parity_state: compared`, `harness_side: committed_dist`.
- **Agreement test**: the committed distribution and the raw emit rows it was
  built from produce identical parity verdicts. If they disagreed, the artifact
  would be a quiet re-measurement rather than a record.
- 14 pytest cases, every refusal paired with a positive control.
- 43/43 guards on the committed diff; `ruff check .` clean; research index
  77/77 routed.

### Mutation-tested, and one mutation found a defect in my own test

Planted regressions to prove each control can fail:

| mutation | result |
|---|---|
| collapse the per-leg cap to the global flag | parity test 13 FAILS ✅ |
| treat a zero-`n` leg as comparable | parity test 14 FAILS ✅ |
| re-derive `_pct` locally (byte-identical) | one-definition test FAILS ✅ |
| drop the `--symbol`/`--timeframe` requirement | aggregator test 8 FAILS ✅ |
| **delete the uncapped-sweep refusal** | **PASSED — the test was wrong** ❌ |

That last one is the useful part. The test pointed `--emit` at a nonexistent
`x.jsonl` and asserted `rc == 2` — and a missing file returns `2` on its own, so
the assertion held with the guard deleted. **It was passing for a reason it was
not testing**: an exit code that a different failure also produces. Rewritten
against a real emit file, with a positive control (*a capped sweep with identity
IS written*) ahead of the refusals so they cannot pass by refusing everything.
Re-run under the same mutation, it now fails.

A second, smaller one: `test_percentiles_are_the_parity_modules_own` failed on
first run because the test loaded `m31_mfe_parity` twice under different module
identities — the assertion was measuring the test's own loading strategy, not
the production wiring. Fixed by importing the module the aggregator's own
`from m31_mfe_parity import _pct` resolves to.

## CI wiring

`mfe-parity-instrument-guard` now runs **both** self-tests and lists both files
in its globs, so editing either runs both. The aggregator's refusals are exactly
the kind of control that rots silently — nothing else fails when they stop
firing — and *"registered but never invoked"* is the defect this repo hit twice
on 2026-08-17 (`BL-20260817-COLLAPSED-STATE-SELFTEST-REGISTERED-BUT-NEVER-INVOKED`).

The `artifact-validity-guard` caught the new script's absence from
`RESEARCH-CAPABILITY-INDEX.md` before it could ship — the same catch the
concurrent M20 session recorded hours earlier. Indexed; 77/77 routed.

## What is NOT done

The artifact itself. Producing it needs a **capped** (`--tp-cap-pct 0.099`)
trainer-side sweep per telemetry-writing leg — the `trend_donchian` and
`htf_pullback_trend_2h` families only, since no other leg writes
`position_telemetry` and no other leg is therefore comparable — with
`--emit-trades`, fed through the aggregator with each leg's own
`--symbol`/`--timeframe`.

Precondition 2 then still waits on live soak depth, which nothing here touches.
**P5 remains Tier-3 and withheld.**
