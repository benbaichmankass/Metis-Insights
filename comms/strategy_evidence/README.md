# `comms/strategy_evidence/` — per-leg OFFLINE edge records

> **Doc status:** `live` · category `lookup` · last verified `2026-09-09` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md)

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

## ⚠️ `fidelity` decides what the number is evidence ABOUT

`faithful` means the harness modelled **every** lever the leg's config declares.
`approximate` means it did not, and `omitted_levers` names which. An
`approximate` number is evidence about a leg that is *not quite this one*.

**The producer records the grade and refuses to decide what it means** — a
consumer branches on it. Collapsing `approximate` into `measured` would launder a
weaker number into evidence; discarding it would throw away a usable one. Where
that line falls is an operator decision, and leaving it to the consumer means it
can move later without regenerating anything.

Measured 2026-09-09 over all 52 enabled legs: **29 faithful · 13 approximate ·
10 unclassifiable**. So `faithful` is **55.8%** of the fleet, not the 98.1% a
harness-family name-match suggests.

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

## Regenerating

```bash
python3 scripts/ops/build_strategy_evidence.py --dry-run          # classify + fidelity, no run
python3 scripts/ops/build_strategy_evidence.py --strategy <leg>   # one leg
python3 scripts/ops/build_strategy_evidence.py                    # every enabled leg
```

Needs `pandas` (the harnesses do); a fresh sandbox has none. Fetches candles from
`data.binance.vision` for `*USDT` and Yahoo otherwise.
