# Roll-adjusted continuous native-futures data — DESIGN + plan

**Status:** increments 1 + 2 BUILT & MERGED (#5870). Increment 3 (re-backtest)
RAN 2026-07-07 — result below and in `ib-metals-native-backtest-2026-07-07.md`:
the roll-artifact hypothesis for `mgc_trend_1h` is **refuted** (spliced +221.6R
→ continuous +196.2R on native MGC 1h, only −11%); the edge survives
roll-adjustment. It stays shadow for a different reason (cross-series conflict
with the GC=F/spot demote + 2023-concentrated returns).
**Author:** Claude, 2026-07-07.
**Motivating finding:** `docs/research/ib-metals-native-backtest-2026-07-07.md`
— `mgc_trend_1h` scored a surprising **+57.8R** on native MGC 1h that did NOT
reverse the shadow demote, because the native shard is **spliced dated contracts
without roll back-adjustment** and a Donchian breakout reads the roll gaps as
breakouts. This is the tooling to test breakout/trend cells on native futures
*honestly* so we can continue intraday futures research on clean data.

## 1. The problem

The IBKR historical pull (`ml/datasets/adapters/ibkr_offvm.py::_historical_bars`)
pages over **dated contract months** (e.g. `MGCQ26`, `MGCU26`, `MGCZ26`) and
stitches them into one `market_raw` stream, **deduped by timestamp**. At each
contract roll the absolute price level gaps (contango/backwardation): a
September contract and a December contract trade at genuinely different prices
for the same underlying at the same instant.

- **Pullback / mean-reversion cells** (`mgc_pullback_1d`, `mhg_pullback_1d`):
  largely immune — they enter on pullbacks into a local range, not on the gap.
  Their native-data positives ARE credible (kept live).
- **Breakout / trend cells** (`mgc_trend_1h`, any Donchian/channel intraday
  candidate): **corrupted** — the breakout logic reads a roll gap as a breakout
  and "rides" it, manufacturing a fake edge. Their native-data results are NOT
  trustworthy until the gaps are removed.

The existing merged shard **cannot** be fixed after the fact: the dedup discards
which contract each bar came from, so you can't tell a roll gap from a real move.
Roll-adjustment needs **per-contract** bars with the cross-contract **overlaps
preserved** — the overlap is where the roll offset is measured.

## 2. The approach — back-adjusted continuous series

Order the dated contracts oldest → front. Anchor the **front** (newest) contract
at its real tape prices. For each consecutive pair, measure the price offset at
the **roll** (the last timestamp both contracts share a bar), then shift every
older segment by the **cumulative forward offset** so the series moves only when
the market moves. Two conventions:

- **`panama` (additive, default):** shift the whole older bar (O/H/L/C) by the
  cumulative price *difference*. Keeps absolute price levels + **ATR** consistent
  near the front (the un-adjusted end) — the right choice for this repo's
  price-and-ATR backtest harnesses (`backtest_trend.py`, `backtest_pullback.py`).
  Older absolute levels can drift from the historical tape (and, deep enough,
  even go negative) — acceptable, because the front is what a live strategy
  trades.
- **`ratio` (multiplicative):** scale older segments by the cumulative price
  *ratio*. Preserves percentage returns exactly, never negative — better for
  return-based studies, worse for absolute-price/ATR stops.
- **`none`:** plain splice, no gap removal — reproduces today's adapter output;
  kept as the A/B baseline arm so a backtest can show the gap's effect directly.

The roll point is the **last common bar** between the pair (the natural handover
just before the near contract's data ends). Refinement noted for later: roll a
few days early on a **volume crossover** (front liquidity moves to the next
contract before expiry) — v1 uses last-common-bar, which is robust with the
overlapping data the pull provides. A pair that never overlaps (a data gap)
degrades honestly to a **zero** offset (no fabricated adjustment) + a logged
warning.

The output is the **canonical `market_raw` 9-key shape** (see
`ml/datasets/adapters/base.py::CANONICAL_COLUMNS`) with an adjusted `symbol`
token (default `<SYM>.c`, e.g. `MGC.c`) and `source="ibkr_continuous"`, so the
existing backtest harnesses read the continuous series with **zero change**.

## 3. Increment 1 — the back-adjustment core (BUILT, Tier-1)

Offline, stdlib-only, no socket, no live path:

- **`ml/datasets/continuous.py`**
  - `build_continuous(contracts, *, symbol, timeframe, method, out_symbol, source)`
    — per-contract series → one back-adjusted continuous `market_raw` list.
  - `group_bars_by_contract(tagged_bars, contract_key="contract")` — reshapes a
    flat contract-tagged stream (the increment-2 pull output) into the
    `[{"month","bars":[...]}]` structure `build_continuous` expects.
- **`scripts/research/build_continuous_contract.py`** — CLI: per-contract jsonl
  (a `--tagged` flat stream with a `contract` field, or `--contract MONTH=FILE`
  / `--contract-glob`) → continuous `market_raw` jsonl the harnesses consume.
- **`tests/ml/datasets/test_continuous.py`** — proves the roll gap is removed
  (the +9 splice jump becomes a continuous +1 step), the front stays
  un-adjusted, cumulative offsets across 3 contracts, ratio/none methods,
  no-overlap degradation, canonical-shape + no-dup-timestamp invariants.

## 4. Increment 2 — per-contract pull (BUILT, Tier-2, live-VM)

The core needs per-contract input with overlaps. Today's `iter_bars` dedups
across contracts and discards contract identity — so the per-contract path is
**additive** and does NOT change `iter_bars` (the working metals/MES pull is
untouched):

- **`IBKRHistoricalMarketRawAdapter.iter_contract_bars(...)`** — collects bars
  **per dated contract without the cross-contract dedup** (overlaps are
  load-bearing) and yields rows tagged with their `contract` month (the
  `lastTradeDateOrContractMonth[:8]`). Same paging, pacing, exchange-per-symbol
  (`_SYMBOL_EXCHANGE`, the 2026-07-07 COMEX fix) as `iter_bars`; the only
  difference is a `per_contract=True` flag on `_historical_bars` that swaps the
  global dedup for a per-contract one and stamps the `contract` tag.
- **`ml/datasets/percontract_pull.py`** (`python -m ml.datasets.percontract_pull`)
  — writes the tagged per-contract stream to
  `market_raw_percontract/<SYM>/<tf>/<ver>/data.jsonl` (a NEW artifact family,
  distinct from `market_raw/` — the per-contract stream is NOT canonical
  `market_raw`; it carries the extra `contract` key, so it deliberately does not
  go through the `market_raw` builder/schema check).
- **Pull vehicle:** `scripts/ops/pull_mes_ibkr_history.sh` gains a `PER_CONTRACT=1`
  branch that swaps `python -m ml build-dataset market_raw` for the writer above
  — reusing ALL the existing safety rails (distinct clientId, `nice`/`ionice`,
  heartbeat live-first guard, single-instance lock, detach).
- **`pull-ibkr-history` system-action** gains a `per_contract: 1|true|yes`
  issue-body knob (validated; injects `PER_CONTRACT=1`) so it's Claude-dispatchable.
- **Tier-2**: it runs on the live VM and shares the IB gateway. Additive (no
  change to the existing pull path), but the FIRST real pull gets an operator OK
  before dispatch, per the tier rules.

**To run it (after the PR merges + deploys):** dispatch a `system-action` issue
with body:

```
action: pull-ibkr-history
symbol: MGC
timeframes: 1h
hist_start: 2019-05-06
dataset_version: v001
max_contracts: 28
per_contract: 1
reason: per-contract MGC 1h for the roll-adjusted continuous backtest of mgc_trend_1h
```

then (trainer VM) rsync `market_raw_percontract/` over and run
`build_continuous_contract.py` (§5).

## 5. Increment 3 — re-backtest breakout cells on the clean series

Once per-contract data exists (weekend / CME break window, live-first guard):

1. Pull per-contract MGC 1h + MGC/MHG 1d (and MES 5m for the intraday trend
   candidates).
2. `build_continuous_contract.py --tagged … --symbol MGC --timeframe 1h
   --method panama --out /tmp/MGC.c_1h.jsonl`.
3. Re-run the breakout/trend backtests on `MGC.c` and compare to the spliced
   `none` arm:
   - **`mgc_trend_1h`** — does the +57.8R survive on the *continuous* series? If
     it collapses toward the continuous-GC=F/spot demote (−15.5R / −50.7R), the
     roll-artifact diagnosis is confirmed and the shadow demote stands
     definitively. If it survives clean, that's a real (re-examinable) signal.
   - The intraday shortlist trend/breakout candidates
     (`docs/research/ib-intraday-strategy-survey-2026-07-07.md` #7 MGC trend 4h,
     etc.) get their first **honest** native test.
4. Grade net-of-fee at ≥2× the real micro cost, as usual. Record verdicts in a
   dated `docs/research/` note; propose any Tier-3 promotion/demotion to the
   operator (never self-enacted).

## 6. Why this unblocks intraday futures research

Every futures **breakout/trend** idea we want to test intraday — the whole
higher-frequency IB direction — is untrustworthy on spliced native data. With a
clean continuous series the backtests stop lying about roll gaps, so we can
actually distinguish a real intraday edge from a data artifact. The pullback
cells never needed it; the trend/breakout cells can't be trusted without it.

## 7. Guardrails

- **No live-path touch in increment 1** — pure offline tooling; the adapter and
  order path are untouched.
- **Increment 2 is additive** — `iter_bars` (the working metals/MES pull) keeps
  its exact behaviour; the per-contract path is a separate method + a separate
  artifact family.
- **The continuous series is research-only** — it feeds backtests, never the
  live trader (which trades the real front contract, not a synthetic
  back-adjusted price). Any strategy change that a clean backtest motivates is
  Tier-3, operator-gated.
- **Honest degradation** — a no-overlap pair applies a zero offset + logs it,
  never a fabricated adjustment; the `none` arm makes the gap's effect auditable.
