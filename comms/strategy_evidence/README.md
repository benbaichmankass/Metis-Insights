# `comms/strategy_evidence/` — per-leg OFFLINE edge records

> **Doc status:** `live` · category `lookup` · last verified `2026-09-24` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md)

One `<leg>.json` per strategy leg, written by
[`scripts/ops/build_strategy_evidence.py`](../../scripts/ops/build_strategy_evidence.py)
(MI-216, operator-approved 2026-09-09). This is the artifact the M7 review gate is
meant to take its **edge** verdict from, so that live rows are left to prove
**mechanics** — `docs/CLAUDE-RULES-CANONICAL.md` § "Promotion evidence — offline
edge, live mechanics".

## ⚠️ AN ABSENT RECORD IS NOT A LEG WITHOUT EDGE

**This directory is PARTIAL and will be partial for a while.** A missing file
means *nobody has run the producer for that leg*, which is a statement about us,
never about the strategy. Do not read a thin directory as a thin fleet — that
inversion is the whole failure this work stream exists to correct, one level up.

Where a record exists, **`coverage_state` is the first field to read.** Four
values, and they answer different questions:

| state | means |
|---|---|
| `measured` | the harness ran and the numbers are real |
| `no_harness` | nothing routes this leg — **we know**, and the record says why |
| `harness_failed` | it ran and broke — **we looked** |
| `not_attempted` | we did not run it |

## ⚠️ A FAILED RUN CANNOT CLOBBER A `measured` RECORD — and that is new

Until 2026-09-22 it could, and twice it did. With `yfinance` absent from a
sandbox the producer graded every leg `harness_failed` and **rewrote** the
committed records for `gld_pullback_1d` and `qqq_trend_long_1d`, nulling
`net_r_oos`, `n_trades_oos` and `fold_detail`. They survived only because the
lane read `git diff` before committing (`PI-20260922-E41-0007`).

Why that is worse than losing a number: a `harness_failed` stub **does not read
as a loss — it reads as a leg nobody ever measured**, which is exactly the
collapsed state the table above exists to prevent, sitting on the corpus the
real-money promotion bar reads.

`build_strategy_evidence.py` now **refuses**, loudly on three surfaces (a stderr
line, a `runs/<date>/<leg>__refused_record.json` sidecar holding the record it
would have written, and exit code **3**), and leaves the committed record
**byte-identical**. An unreadable prior record is refused too — *we could not
look* is not permission.

⚠️ **A SUCCESSFUL re-measure still overwrites normally.** `measured → measured`
is a different question and the refusal does not touch it.

## ⚠️ `fidelity` decides what the number is evidence ABOUT

`faithful` means the harness modelled **every** lever the leg's config declares.
`approximate` means it did not, and `omitted_levers` names which. An
`approximate` number is evidence about a leg that is *not quite this one*.

**The producer records the grade and refuses to decide what it means** — a
consumer branches on it. Collapsing `approximate` into `measured` would launder a
weaker number into evidence; discarding it would throw away a usable one. Where
that line falls is an operator decision, and leaving it to the consumer means it
can move later without regenerating anything.

Measured **2026-09-24**, over all 52 enabled legs: **37 faithful · 14
approximate · 1 unclassifiable** (`turtle_soup`). So `faithful` is **71.2%** of
the fleet (37 of 52 enabled; 72.5% of the 51 that route), not the 98.1% a
harness-family name-match suggests. Re-derive it by importing
`regime_debt_matrix` and calling `classify()` + `build_harness_cmd()` over every
enabled leg — no fetch, no harness run. (Was 29 · 13 · 10 = 55.8% on 2026-09-09;
then 37 · 14 · 1 = 71.2% after E25 routed `fvg_range` and E28 routed the
eight-leg `ict_scalp_*` family; then 25 · 26 · 1 = 48.1% on 2026-09-22, E46's
correction below; **37 · 14 · 1 = 71.2% again from 2026-09-24, E55's fix**,
the same figure as the pre-E46 reading but for the opposite, now-correct
reason — read the two steps below in order, they are not the same event.)

⚠️ **STEP 1, 37 → 25 (2026-09-22): A CORRECTION, not a regression** (E46 /
`PI-20260922-E41-0005`). `tp_r` sat in the trend and pullback `PLAIN` sets —
asserting the harness modelled it — while `build_harness_cmd` passed neither
`--tp-r` nor the `--tp-cap-pct` that makes it take effect, so those runs modelled
**no take-profit at all**. 12 legs were claiming `faithful` with
`omitted_levers: []` against a declared, binding `tp_r`. E46 made the grade
computed from the argv that actually runs — which DISCLOSED the omission
honestly, but the harness still did not model `tp_r` for those 12 legs.

⚠️ **STEP 2, 25 → 37 (2026-09-24): THE ACTUAL FIX, not another disclosure
correction** (E55 / `PI-20260924-JN54P2HH-0005`, graded continuation of
`PI-20260922-E41-0006`). `regime_debt_matrix._tp_r_flags` now forwards
`--tp-cap-pct`/`--tp-r` — live-parity with the venue TP clamp
(`src/runtime/tp_venue_cap.py` owns the `0.099` constant) — for every
trend/pullback leg declaring a readable `tp_r`, so `tp_r` is genuinely
modelled rather than merely disclosed as omitted. All 41 enabled trend/pullback
legs declare a numeric `tp_r`; `tp_r` no longer appears in any of their
`omitted_levers`. 12 of the 13 previously-omitted legs are now `faithful`
(`xrp_pullback_2h` stays `approximate` on an unrelated lever). **A 50R
"parked" sentinel is now ALSO modelled** — the old threshold that excused it
answered a narrower question (worth disclosing the omission) than the one
that matters now the harness CAN model the cap: the venue PRICE term binds
regardless of how large `tp_r` is, so a sentinel is not harmlessly
unreachable once the cap can be modelled at all.

Regenerating every affected record through the harness (never hand-edited)
flipped **8 of the 41** enabled trend/pullback legs' Stage-0 verdicts.
VERIFIED against `config/accounts.yaml` (all four real-money rosters read
explicitly): **none of the 8 are on `bybit_2`, `bybit_portfolio`,
`alpaca_live` or `alpaca_portfolio` today** — 6 are `execution: shadow` or
routed only to `bybit_1`, 2 route only to `alpaca_paper` (both Stage-1 soak).
Filed as `PI-20260924-EDNBNMSG-0001` for whoever next reviews Stage-1
promotion candidates.

⚠️ **This closes `BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP` for
every leg that declares a `tp_r`** — previously read here as deliberately left
to its own row because folding it in "would re-base the whole fleet's
history". E55 measured that re-basing rather than continuing to defer it (the
8-flip result above). `faithful` now DOES mean the live-capped TP was
modelled, for every trend/pullback leg with a `tp_r`.

## ⚠️ `basis` is `harness_timefolds`, NOT purged walk-forward

The harnesses do not do purged WF-CV. `--start/--end` is a single window; these
folds are **emitted trades split into contiguous time blocks after the fact**.
For a fixed-param rule that is defensible — nothing is fit, so there is no
leakage to purge — but it is a **different object** from
`ml/promotion/oos_edge.py`, and the field says what was actually done.

`param_selection_provenance: not_established` is there for the same reason: the
folds are out-of-sample *with respect to each other*, which is not the claim that
a leg's parameters were chosen out of sample. If a leg was tuned on this same
history the pooled number is optimistic, and **no field here can detect that.**

## ⚠️ Read `folds_positive` beside `net_r_oos`, never `net_r_oos` alone

A pooled positive carried by one good block is not the same fact as one that
holds across the window, and the pooled number cannot tell them apart.

The **first record ever produced** demonstrates it: `eth_pullback_2h` pools
`net_r_oos: +3.3242` over 56 trades — and `folds_positive` is **2 of 4**
(+6.32, −2.92, +2.77, −2.85). Most of that edge is a single quarter.

## `schema_version: 2` — `cost_stack` and `decision_rule` (R1, 2026-09-22)

Added by `scripts/ops/build_strategy_evidence.py` so a `measured` record can
clear `scripts/ci/check_roster_promotion_evidence.py`'s C3/C4 clauses:

- **`cost_stack`** — `{fees, slippage, funding}` in bps (funding per funding
  window), read from the harness's own `--json` summary. **Not new pricing** —
  `trend`/`squeeze`/`pullback`'s CLI path already resolves unset
  slippage/funding to venue-aware defaults before it writes that summary
  (`scripts/backtest_trend.py:74-76`), so `net_r_oos` was already net of the
  full cost stack before this field existed. This block only makes the
  resolved bps legible in the record. `null` when the harness's summary does
  not carry all three components (e.g. a family this producer classifies but
  whose harness predates the cost model).
- **`net_r_oos_fee_only`** — the same pooled sum using each trade's
  `net_r_fee_only`, so the size of the slippage+funding correction is on the
  record next to the number it corrects, the way E3 did for the harnesses it
  wired directly.
- **`decision_rule`** — `{id, rule, registered_at, verdict}`. `registered_at`
  is fixed in the producer's source **before** any run; `verdict` is `pass`
  when `net_r_oos > 0`. C4 checks `registered_at < generated_at` so a rule
  cannot be written after seeing the result.

A `schema_version: 1` record (no `cost_stack`/`decision_rule`) means the
producer has not been re-run against that leg since 2026-09-22 — regenerate it
rather than reading the old fields as current.

## `source_run` points into `runs/`, and that is a FIX, not decoration (R5/R7, 2026-09-22)

Every record written before 2026-09-22 names a `source_run` under `/tmp` — a
directory that exists on no machine today. MEASURED by reading all 52 committed
records on `0e0a8f3`: **42 name a dead `/tmp` path, 10 name nothing at all.**
Filed as `PI-20260922-EVIDENCE-SOURCE-RUN-IS-A-TMP-PATH`.

That is not cosmetic. `docs/CLAUDE-RULES-CANONICAL.md` § "A MEASURED must say
WHERE THE MEASUREMENT LIVES" is explicit that a number whose source cannot be
reached **is not MEASURED** — it degrades to INFERRED from an unstated
measurement. `net_r_oos` is a sum over per-trade rows; if those rows are gone,
nobody can check the pooling, the fold split, or whether the cost stack was
actually applied per trade.

So the producer now defaults its workdir to `comms/strategy_evidence/runs/<UTC-date>/`
and both locators are repo-relative and committed:

| field | points at | what it lets you re-check |
|---|---|---|
| `source_run` | `runs/<date>/<leg>__trades.jsonl` | every trade the pooled number sums, with `net_r` **and** `net_r_fee_only` per row |
| `cost_stack.source` | `runs/<date>/<leg>__bt.json` | the fee/slippage/funding bps the harness actually resolved |

`runtime_logs/` was **not** an option — it is gitignored (`.gitignore:33`), so a
locator under it is exactly as unreachable as `/tmp` to anyone but the machine
that ran it. The fetched candle feed (`<leg>__data.csv`) is deliberately **not**
committed: it is a reproducible input, not a measurement, and no record cites it.

⚠️ **A record can still carry a dead locator, and you should check.** Passing
`--workdir` a path outside the repo reintroduces the defect. The 12 legs R1 ran
on 2026-09-22 (`f3746ab`) carry `cost_stack` and a `decision_rule` but still name
`/tmp/tmp.RsNZr5lfkw` — schema-complete, locator-dead. Re-running the producer
for a leg is what fixes it.

## Regenerating

```bash
python3 scripts/ops/build_strategy_evidence.py --dry-run          # classify + fidelity, no run
python3 scripts/ops/build_strategy_evidence.py --strategy <leg>   # one leg
python3 scripts/ops/build_strategy_evidence.py                    # every enabled leg
```

Needs `pandas` (the harnesses do); a fresh sandbox has none. Fetches candles from
`data.binance.vision` for `*USDT` and Yahoo otherwise.
