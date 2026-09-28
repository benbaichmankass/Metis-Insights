# gld_pullback_1h stop-geometry confirming walk-forward — 2026-09-28

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Dispatched by:** operator decision, popup 2026-09-28 ~11:55Z, on pipeline item
`BL-20260831-E35-RESWEEP-AT-POWER-SURFACES-TWO-BRACKET-GEOMETRY-LEADS-GLD-1H-AND-TREND-DONCHIAN-1H`.
Verbatim: *"A research lane runs the confirming walk-forward of the exact
proposed config (about $5–10, no live risk). If it passes, the stop change
ships under your data-backed authority, and I notify you."*

**Decision rule, registered before this run** (operator, same popup): both
effective-fold count ≥5/6 **AND** net_R_OOS delta > 0, with stated n.

**Run:** `scripts/research/e35_bracket_geometry_sweep.py --only gld_pullback_1h
--gate-top 2 --split-target-oos 60 --out out/gld_pullback_1h_confirm` — session-local,
candles fetched fresh via Dukascopy (`GLD` 60m, 2021-09-24 → 2026-09-25, 8744
rows), fee/slippage at the harness default (7.5 bps fee + 5.0 bps slippage
round-trip), no funding leg. Full raw output:
`out/gld_pullback_1h_confirm/2026-09-28/{report.json,SUMMARY.md,results.jsonl}`
(gitignored scratch — not committed; this doc is the landed record).

**Live config as of this run** (`config/strategies.yaml`, verified before the
run): `tp_r=4.0`, `atr_stop_mult=2.5`, `trail_mult=4.0`, no explicit timeout
(`1e9` bars = effectively unbounded), `account: alpaca_paper`, `execution: live`.

## Result

| cell | change from live | OOS n | wf folds (effective) | OOS Δnet_R | verdict vs decision rule |
|---|---|---|---|---|---|
| `sm1.5` | `atr_stop_mult` 2.5→1.5 only | 54 | **3/6** | +9.563 | **FAIL** — fold count below the ≥5/6 bar |
| `tp6_sm1.5_to24` | `tp_r` 4→6, `atr_stop_mult` 2.5→1.5, timeout 1e9→24 bars | 54 | **6/6** | +20.0845 | **PASS** — clears both clauses |

Per-fold detail for `sm1.5` (the one that failed): 2021 OK (+3.27), 2022 **FAIL**
(-3.85), 2023 OK (+12.28), 2024 **FAIL** (-1.62), 2025 OK (+11.73), 2026 **FAIL**
(-3.05). Three of the six yearly folds now lose net R relative to the live
2.5x-stop baseline.

## What this does and does not confirm

**`atr_stop_mult 1.5` alone — the change the operator described as "the stop
change" — does NOT confirm.** The 2026-08-31 e35 re-sweep (run 33411906178,
`docs/research/e35-bracket-corpus.jsonl`) reported this same cell at 5/6
effective folds; this fresh run reads 3/6. The IS/OOS split boundary and
underlying candle window both moved forward (this run's data ends
2026-09-25 vs the original's cutoff before 2026-08-31, and OOS n moved 59→54),
so the discrepancy is plausibly ordinary walk-forward instability on a
marginal cell rather than a bug — but that is exactly why this confirming run
existed: the original result did not survive a second, later look. **Per the
operator's own pre-registered rule, `sm1.5` does not ship.**

**`tp6_sm1.5_to24` DOES confirm** (6/6 effective folds, unchanged from the
original sweep's 6/6; OOS Δnet_R +20.08 > 0, n=54). But this is **not** "the
stop change" as scoped by the operator's decision — it is a three-parameter
geometry replacement (take-profit 4R→6R, stop 2.5x→1.5x ATR, and a new 24-bar
timeout the live config does not have at all today). Shipping it is a larger,
different Tier-3 change than what "if it passes, the stop change ships" was
written to pre-authorize.

## Disposition

Landing this result record only. **Not shipping anything** — per the
operator's own instruction, "the manager decides the ship," and here the
literal proposal (`sm1.5` alone) is the one that failed. Whether the passing
joint cell (`tp6_sm1.5_to24`) is close enough to what was pre-authorized, or
needs its own fresh Tier-3 ask, is the manager's call, not this lane's.

`gld_pullback_1h` is on `alpaca_paper` today (paper money); the stated
urgency ("its geometry matters now") is that it is eligible for real-money
promotion under the armed `MD-PROMOTE-S1-S2` mandate, not that it is already
live risk.
