# New-strategy candidates — ranked research proposal (2026-05-31)

> Research-only (Tier-1). No live wiring. Produced as the "expand the roster"
> half of the profitability-gaps work, alongside the cross-zero primitive,
> harness consolidation, and the strategy selection gate. The two top picks are
> scaffolded in code (see "Status" below); ranks 3–4 are documented only.

**Scope:** complementary members to the one live winner `trend_donchian` (2h
Donchian breakout, BTCUSDT). The bar is the audits' bar: *standalone profit is
necessary but not sufficient — a member must be low-correlated to the
trend-follower AND survive the shared-account / flip dynamics, with fee drag
kept low.*

## Ground truth verified first

Roster + priorities (`config/strategies.yaml` + `intents.py::DEFAULT_PRIORITIES`):

| Strategy | TF | Family | execution | priority |
|---|---|---|---|---|
| trend_donchian | 2h | channel breakout | **live** | 20 |
| fade_breakout_4h | 4h | failed-breakout fade | live | 10 |
| squeeze_breakout_4h | 4h | vol compression→expansion | live | 5 |
| fvg_range_15m | 15m | static-range MR | shadow | 3 |
| turtle_soup | 15m/1m | sweep+reversal | live (poison in-system) | 50 |
| ict_scalp_5m | 5m | sweep→displacement→FVG | live (poison in-system) | 30 |
| vwap | 5m | MR to drifting anchor | shadow | 40 |

Decisive findings designed against:
- **Flip-churn is first-order.** `reverse` is worst; `hold` zeroes flips and turns the book net-positive. `FLIP_POLICY=hold` is the live default (PR #2451).
- **High-priority losers hog the single shared position** (turtle+ict_scalp −$7,490 in-system). A new member must ship at **low priority** and not crowd the book.
- **Fee drag kills tight-target/high-churn strategies** (vwap fees = 418% of gross). Every winner shares one lever: **wide fee-efficient stops + Chandelier runner exit**. Tight targets died every time.
- **Correlation > standalone R** (fade@0.035, squeeze@0.30, SPX-trend@0.009).
- A naive **funding-fade was already falsified**; **ES-derived crypto signal "doesn't transfer."**

**Data-availability correction (shapes feasibility):** the multi-year BTC 5m
archive lives on the **trainer VM**, not this checkout (local has only ~3.5-day
+ ~7-day fixtures). **No funding feed exists anywhere in the repo.** SPX/QQQ
files are tiny 2026 fixtures. ⇒ candidates needing a *new* feed carry a real
acquisition cost, priced into the ranking.

## Ranked shortlist

| Rank | Candidate | Mechanism axis | New data? | Corr-to-trend prior | Fee class |
|---|---|---|---|---|---|
| **1** | `session_breakout_trend` | time-of-session | **No** | moderate-low (est) | low |
| **2** | `htf_pullback_trend_2h` | trend pullback (flip-safe by construction) | **No** | low-moderate (est) | low |
| **3** | `funding_carry_2h` | derivatives carry/positioning | **Yes (absent)** | very low (est) | low |
| **4** | `equity_leadlag_filter` | cross-asset lead-lag | **Yes (absent)** | ~0.01 (measured) | low |

### Rank 1 — `session_breakout_trend`
Time-of-session momentum. The marginal price-setting flow in BTC perps
concentrates at the US equity cash open (13:30 UTC) and the CME session; an
opening-range breakout *inside a session window* has higher follow-through than
the same breakout at a random hour. **Complementary** because it reuses the
proven breakout+Chandelier mechanism but gates on an axis orthogonal to price
structure — **clock time** — so its trade timestamps barely overlap
trend_donchian's. Fills the roster's only empty dimension (no member is
time-gated; ict_scalp even ships a disabled `session_filter`). **Zero new data**
(BTC OHLCV + bar timestamps). First test: net-positive after 7.5bps on full
sample + nested walk-forward, corr-vs-trend < ~0.4, positive in-system under
`hold`. Honesty: grounded but untested on this archive.

### Rank 2 — `htf_pullback_trend_2h`
Trend-continuation via a mean-reversion *entry*: in an established trend,
pullbacks to a dynamic level overshoot and revert in-trend. **Complementary
by construction with a structural flip-safety property:** it enters on weakness
*in the same trend direction* trend_donchian is riding, so conflicts are
**same-side (max-qty, no flip)**, never opposite-side churn — directly honoring
the #1 system finding. **Zero new data.** First test: standard gates, plus
verify in `backtest_system.py` that same-side conflicts net to max-qty without
adding flips. Risk: may correlate too highly with trend (the corr gate checks).

### Rank 3 — `funding_carry_2h` (only if a funding feed is sourced)
Perp funding as a contrarian *positioning* signal — but used as a
conditioning gate/tilt on a directional entry (or a slow carry-harvest), NOT
the naive funding-fade that was already falsified. Lowest-correlation story
(orthogonal data axis), but needs a funding feed sourced onto the trainer VM
first, and carries a documented prior-failure cousin.

### Rank 4 — `equity_leadlag_filter`
SPX/QQQ risk-state as a *gate* on a BTC directional entry (not a transplanted
signal — that variant failed). SPX-trend corr ~0.009 is the prize, but the
24/7-vs-equity-hours mismatch is a real headwind and this naturally belongs on
the separate MES/IBKR book, not the crypto fund.

## Deliberately NOT proposed
Liquidation-cascade fade (needs an absent liq feed; near-cousin of the
in-system-poison fade/turtle), basis/term-structure (absent quarterly feed;
dominated by funding), new tight-target MR/scalp variants (fee-death, ruled out
a priori), and a slower/faster Donchian (corr 0.40–0.44 — a momentum cousin,
not a diversifier).

## Cross-cutting activation contract (whichever advances)
Ship `execution: shadow`, priority ≤ 3, route to **bybit_1 (demo)** only, reuse
the **verbatim shared Chandelier `monitor()`**. Selection gate (the audits'
bar, enforced by `scripts/strategy_gate.py`): net-positive after 7.5bps (full
sample + nested walk-forward), low monthly corr to trend_donchian, and
**positive in-system contribution under `FLIP_POLICY=hold` in
`scripts/backtest_system.py`** (register in `ROSTER`) — the last test is the
one that killed fade/turtle in-system and is non-negotiable.

## Status (this session)
- **Ranks 1 & 2 scaffolded** (zero-new-data, so unblocked once prem cores free up):
  - `src/units/strategies/session_breakout_trend.py`, `src/units/strategies/htf_pullback_trend_2h.py` — pure signal modules, **not wired** into the builder/intents/YAML (inert until the Tier-3 activation PR).
  - `scripts/backtest_session.py`, `scripts/backtest_pullback.py` — net-of-fee harnesses (clones of `backtest_trend.py`) for validation on the trainer-VM archive.
  - `tests/test_new_strategy_scaffolds.py` — package shape, monitor trail, non-actionable paths, and an inertness guard.
- **Ranks 3 & 4** documented only (blocked on data acquisition: funding feed / multi-year SPX).

**Next** (gated on prem-tier cores): run `backtest_session.py` + `backtest_pullback.py` on the trainer-VM BTC archive → if a candidate clears standalone net-of-fee + walk-forward + corr, register it in `backtest_system.py::ROSTER` and run the in-system `hold` gate → if positive, propose the Tier-3 activation PR (shadow, bybit_1).
