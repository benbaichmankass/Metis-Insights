# Scoping note — WS-B candle-shard label-volume plumbing (workplan item 1.1)

> **Status:** scoping (Tier-1 / offline). Opened 2026-07-26 under `research-driver`,
> as **WS-3** of the [de-soak + milestone close-out workplan](./WORKPLAN-desoak-and-milestone-closeout-2026-07-26.md).
> **Anchor:** the label-volume ladder in
> [`AI-TRADER-RESEARCH-PLAN-2026-07-19.md`](./AI-TRADER-RESEARCH-PLAN-2026-07-19.md) (WS-B = L2);
> findings baseline in
> [`M23-phase2-labelvol-findings-2026-07-19.md`](./M23-phase2-labelvol-findings-2026-07-19.md).
> **This note scopes the remaining work; it does not do the build.** No code/config
> is changed by this note beyond adding the note itself.

## Why this is the one genuine infra build

The de-soak investigation (workplan § "Why this workplan exists") found the "weeks of
soak" is ~80% stale governance and ~20% **one real constraint: label volume.** The
meta-label eval book is **BTC-only, ~376 real-money rows** — every meta-label lever
caps at ~11 net-positive trades on it, which is too thin to prove edge no matter how
long it soaks. This is un-fakeable without more labels, so it is the single heavy
investment three milestones (M23 / M24-P4 / G1-G2) queue behind.

## The wall — where closed trades are dropped

The eval book ("live_holdout") is assembled in `ml/datasets/families/setup_candidates.py`
by iterating **one `market_raw` candle shard per symbol** and, inside that per-shard
loop, attaching each of that symbol's real closed trades to the bar at/just-before its
entry (`_iter_one_symbol` → `_load_live_trades(live_trades_db, symbol)`,
~`setup_candidates.py:929-937`). Two consequences:

1. **A trade whose symbol has no shard in `market_raw_paths` is never iterated at all**
   — `_load_live_trades` is filtered to the shard's own symbol, so an un-covered symbol
   is silently absent from the eval book. **This is the structural wall.**
2. Secondary per-shard drops: entry outside the shard's candle window, or a zero/none
   volume bar.

**Measured impact** (`M23-phase2-labelvol-findings-2026-07-19.md`): **216 of 491**
closed trades in the 90-day window had no candle shard — the 4h-donchian alt symbols
plus the entire equities/metals fleet — so they could not enter `setup_candidates`.
The surviving eval book is BTC 376 / ETH 7 / SOL 0 ≈ **383 rows**, and only BTC is
usable in practice.

## Current state — half of 1.1 has already landed, and why the wall persists anyway

- ✅ **The shard roster was expanded** in `scripts/ops/build_trainer_datasets.sh`
  (WS-B block, PR #6934): alt-USDT **ADAUSDT / AVAXUSDT / XRPUSDT @ 15m** plus an
  **equities/metals daily** fleet (`SPY QQQ GLD TLT IWM SLV IEF TQQQ QLD SPLG IAUM`
  via `yfinance_offvm`, `MGC`→`GC=F`, `MHG`→`HG=F`). So the nightly trainer cycle is
  *configured* to build those shards.
- ❌ **The eval-book harness still only consumes BTC/ETH/SOL @ 1h.**
  `scripts/ml/m23_phase2_labelvol.sh` hardcodes `SYMBOLS=(BTCUSDT ETHUSDT SOLUSDT)`
  (line 39) and passes only three `1h` shard paths as `market_raw_paths` into the
  `setup_candidates` build (line 145). **The newly-built alt/equity/metal shards are
  never handed to `setup_candidates`, so those symbols' closed trades still don't
  path-resolve** — the wall is unchanged even though the shards now exist.

**So the genuine remaining plumbing is the harness/manifest side, not the roster.**
Building the shards without widening the consumer is a no-op for the eval book.

## Scoped work (the actual WS-B plumbing)

| Step | What | Where | Kind |
|---|---|---|---|
| **3a** | **Verify the nightly build actually emits the WS-B shards** — confirm `datasets-out/market_raw/{ADAUSDT,AVAXUSDT,XRPUSDT}/15m/…` and the equities/metals daily shards exist and are fresh after a cycle. | trainer VM (`datasets-out/` is trainer-VM-only, uncommitted) — read via the `trainer-vm-diag` relay | VM **read/verify** (autonomous; not a merge) |
| **3b** | **Widen the eval-book harness to the full covered roster.** Replace the hardcoded `SYMBOLS` + `MR_PATHS` in `m23_phase2_labelvol.sh` with the covered set, mapping **each symbol to the timeframe its shard was built at** (BTC/ETH/SOL 1h · alts 15m · equities/metals 1d) so `market_raw_paths` + `live_trades_db` cover every traded symbol. Handle the timeframe heterogeneity explicitly (the current script assumes 1h for all three). | `scripts/ml/m23_phase2_labelvol.sh` (+ any manifest that pins the shard list) | **Repo commit** (Tier-1, offline harness) |
| **3c** | **Rerun P2b and re-measure the eval-book row count** by symbol (the script already prints `LIVE by_symbol`). Record the new usable-volume number against the ~376 baseline. | trainer VM cycle / relay | VM **run** + findings doc |

Steps 3a/3c are trainer-VM actions (autonomous per CLAUDE.md § VM authority split);
3b is the only repo merge. None touches the live order path, `config/strategies.yaml`,
or `config/accounts.yaml` — `market_raw` carries no labels and `setup_candidates` reads
the journal `mode=ro`. **Tier-1 / offline throughout.**

## Sequenced follow-ons (after coverage — do NOT bundle into 3b)

- **L3 — paper-book labels:** feed the soak **paper accounts'** real fills across the
  full roster as *train-side* labels (account_class as a covariate; real-money rows stay
  the only eval book). Gate = a fair A/B, train `+paper` vs `−paper` on the same
  real-money holdout.
- **L5 — net-R label swap:** re-target the meta-label heads from gross `won`/`won_r` to
  the **M24 net-of-cost R label** (`net_R = (gross − fees − funding)/risk_usd_at_entry`,
  `src/runtime/net_r_label.py`) as broker-truth fee coverage widens; re-run the C1 EV
  sweep on net labels.

## Honest caveat (state it in any result)

Even at **full** coverage the eval book is only ~500 rows against the ≥40-*selected*
usable-volume floor a meta-label lever needs — the findings doc calls P2b explicitly
*"a measurement, not a promised unlock"*. **Coverage removes the structural drop; it
does not by itself guarantee a net-positive meta-label.** WS-B is the prerequisite that
makes the measurement honest, not a promise that the measurement passes. L1 pooling +
L3 paper-book labels are the volume levers that follow if ~500 real rows still under-fill.

## Definition of done for WS-B

1. 3a verified (shards exist + fresh).
2. 3b merged (harness consumes the full roster at correct per-symbol timeframes).
3. 3c rerun + a short findings addendum recording the new eval-book row count by symbol
   vs the ~376 baseline, with the caveat above.
