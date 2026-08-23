# Deriving a real `target_r` — crypto pullback legs (2026-08-23)

**Operator decision this serves:** *"Declare real target_r per leg"*, taken
2026-08-23 after the live soak showed 13 of 14 open positions carrying a
take-profit whose level Bybit's ~9.9% cap chose rather than any strategy
(`BL-20260818-MOST-OPEN-TRADES-HAVE-NO-DECISION-DRIVEN-EXIT`).

**Scope, stated up front:** two legs — `xrp_pullback_2h` (REAL MONEY, live R:R
**0.687**) and `ada_pullback_2h` (**0.843**). These are the two the live soak
measured below 1.0, and the two the tune-before-demote sequence must clear
before a disposition (`BL-20260818-EVERY-CRYPTO-PULLBACK-LEG-IS-OOS-UNPROFITABLE`).
**The other 50 enabled legs are NOT derived here.**

## Method

Config-exact params read from `config/strategies.yaml`, not retyped:
`trend_lookback 40 · pullback_lookback 10 · pullback_frac 0.5 · atr_period 14 ·
atr_stop_mult 2.5 · trail_mult 5.0`, plus `adx_min 25` + `trail_decay_arm_r
4.49 / tight_mult 2.5` (XRP) and `adx_min 28` + `vol_skip_below_pctl 0.1` (ADA).

Corpora fetched with the repo's own `scripts/ops/fetch_backtest_candles.py
--source binance_vision`: **13,212 2h bars each**, 2023-08-14 → 2026-08-18.
(That matches the 13,206-bar corpora the OOS row used.) The committed
`data/backtest_candles.csv` was deliberately NOT used — it is BTCUSDT
**1-minute**, where the 9.9% cap lands ~37R against these legs' 2–3R, and
`m31_harness_mfe_dist.py`'s docstring already names using it as an
order-of-magnitude error.

Each leg was run **twice** on the identical corpus: capped (`--tp-cap-pct
0.099`, what production does) and uncapped (`--tp-cap-pct 0`, which removes the
take-profit path entirely so `mfe_r` is the TRUE excursion rather than one
truncated at the ceiling).

## Finding 1 — `tp_r: 50` is unreachable by an order of magnitude

| leg | max MFE (capped) | max MFE (uncapped) | declared `tp_r` |
|---|---:|---:|---:|
| `xrp_pullback_2h` | 4.31 R | 20.46 R | **50.0** |
| `ada_pullback_2h` | 4.36 R | 8.96 R | **50.0** |

Over three years and 13,212 bars, the single best excursion either leg ever
produced is **8.96 R** — and that is the uncapped, best-case figure. The
declared target is 50 R. It has never once been approached, which is exactly
why the venue cap binds on essentially every trade: the sentinel is not a
target that is rarely hit, it is a target that **cannot** be hit.

## Finding 2 — the cap is the dominant destroyer of edge on these legs

| leg | net_R capped | net_R uncapped |
|---|---:|---:|
| `xrp_pullback_2h` | +5.75 | **+22.86** |
| `ada_pullback_2h` | +3.83 | **+21.84** |

⚠️ **These are NOT the same population and the delta is directional, not a
clean measurement.** Trade counts differ (XRP 181 capped vs 154 uncapped; ADA
146 vs 111) because a capped take-profit exit ends a trade earlier and permits
more subsequent entries. Do not quote "the cap costs 17 R" as a measured
number. What IS supported: removing the cap roughly **quadruples** net R on
both legs, on the same bars under the same parameters.

## Finding 3 — the one that changes the remedy

**Declaring `target_r` is necessary but NOT sufficient.** The placed target is
`min(entry × 1.099, entry + target_r × risk)`, so the cap binds whenever
`cap_r < target_r`, where `cap_r = 0.099 × entry / risk`. Measured per trade:

| leg | cap_r p25 | p50 | p75 | `cap_r < 3` | `cap_r < 2` | **`cap_r < 1`** |
|---|---:|---:|---:|---:|---:|---:|
| `xrp_pullback_2h` (n=154) | 1.80 | 2.45 | 3.34 | 68.2% | 33.1% | **9.1%** |
| `ada_pullback_2h` (n=111) | 1.58 | 2.15 | 2.81 | 84.7% | 42.3% | **11.7%** |

So for roughly **1 trade in 10 on these legs, NO coherent bracket exists at
all** — the venue ceiling sits nearer than the stop, and the trade is
structurally guaranteed a reward-to-risk below 1 whatever anyone declares. The
live XRP position (`cap_r` 0.687) is not an outlier; it is the ~9% tail,
observed.

That is a structural incompatibility between a **volatility-scaled stop**
(2.5 × ATR) and a **fixed-percentage venue ceiling**. No `target_r` value
repairs it, which is why this file does not propose one as if it would.

## Proposed `target_r`, and its limits

True-excursion percentiles (uncapped):

| leg | p50 | p75 | p80 | p90 | p95 |
|---|---:|---:|---:|---:|---:|
| `xrp_pullback_2h` | 1.09 | 2.72 | 3.07 | 4.15 | 4.97 |
| `ada_pullback_2h` | 1.11 | 2.54 | 3.42 | 4.49 | 6.06 |

**Proposal: `target_r: 2.5` for both.** It sits just under p75 of true
excursion (so it is a level the strategy actually reaches), and above the
median `cap_r` on both legs (2.45 / 2.15) so the declaration is the binding
term on roughly half of trades rather than the venue. A 3.0 target would be
unplaceable on 68–85% of trades and would leave the cap in charge; a 1.5 target
would sit below p75 excursion and cut winners the strategy demonstrably runs.

⚠️ **This proposal is NOT validated as an improvement.** It is derived from the
excursion distribution, not from a run that trades it. The honest next step is
a sweep of `target_r ∈ {1.5, 2.0, 2.5, 3.0}` scored on net R against the capped
baseline — which is also the **tune-before-demote attempt** these legs owe
(`CLAUDE-RULES-CANONICAL.md` § "Tune before demote"), so one piece of work
discharges both.

⚠️ **`n` is small at the tail.** 154 and 111 trades over three years; the p90+
figures rest on 15 and 11 trades. State that alongside any value taken from
them.

## What this says about the other 50 legs

Nothing. The 26 remaining crypto legs are reachable the same way
(`binance_vision`, Bybit interval codes — `120` = 2h, `240` = 4h) and can be
derived with this method. The equity / ETF / futures legs have **no reachable
corpus** from a sandbox session and no committed one in-repo; they need
trainer-side sweeps. Deriving a `target_r` for them from the BTCUSDT 1-minute
fixture would be the order-of-magnitude error described in the Method section.

---

# The sweep — run the same day, and it is also the tune-before-demote attempt

The derivation above proposed `2.5` for both legs **from the excursion
distribution alone**, and flagged itself as derived-not-validated. The sweep
that validates it is `scripts/ml/strategy_tune_sweep.py` — the canonical M8
harness, which writes the artifact
`runtime_logs/strategy_tunes/<date>/<strategy>__tp_r.json` that
`strategy_review_packet`'s tune-before-demote override reads. So one run
discharges both obligations.

**A registry gap had to be closed first, and it mattered more than it looks.**
`_REGISTRY` had **no pullback harness at all and no `tp_r` row for any
harness** — so from the moment tune-before-demote became mechanical, every
pullback demotion would have been softened to `tune` *forever*, with no sweep
able to produce the evidence that discharges it. A rule whose evidence is
unobtainable is worse than no rule. Rows added for
`backtest_pullback.py:{tp_r, adx_min, atr_stop_mult, trail_mult,
min_confidence}` and `backtest_trend.py:tp_r` (the trend family carries the
same 50.0 sentinel and is the largest group in the fleet).

⚠️ **A `tp_r` sweep must pin `--tp-cap-pct`.** The harness has no take-profit
exit path at all when the cap is 0, so an unpinned sweep would score every
candidate against a book that never takes a target and the "optimum" would be
noise. Pinned to `0.099` throughout, matching production.

Walk-forward, single split, OOS from 2026-01-01. Metric basis is **OOS**.

## `xrp_pullback_2h` — a clean interior optimum

| `tp_r` | trades | OOS net_total | OOS net_exp |
|---:|---:|---:|---:|
| 1.5 | 41 | 0.894 | 0.022 |
| 2.0 | 39 | 1.687 | 0.043 |
| 2.5 | 38 | 4.124 | 0.109 |
| **3.0** | 36 | **4.379** | **0.122** |
| 4.0 | 36 | 2.149 | 0.060 |
| 50.0 *(current)* | 36 | 2.159 | 0.060 |

**Recommendation: `tp_r: 3.0`** — beats baseline **True**, train/OOS consistent
**True**. Roughly **doubles** OOS net_total against the sentinel (4.38 vs 2.16).

Two internal checks worth noting. `4.0` and `50.0` return near-identical
results on identical trade counts (2.149 vs 2.159, both n=36) — above ~4R the
cap binds anyway, so they are the *same book*, which is direct evidence the
harness models the live clamp correctly. And the curve rises to a peak and
falls, rather than drifting monotonically, which is the shape a real optimum
has.

The derivation said 2.5; the sweep says 3.0. They are adjacent and both roughly
double the baseline, so the two methods **agree on the region** — that is
corroboration, not contradiction. The sweep wins because it actually trades the
value; the derivation only measured where price went.

## `ada_pullback_2h` — the honest opposite, and it must not be applied

| `tp_r` | trades | OOS net_total | OOS net_exp |
|---:|---:|---:|---:|
| 1.5 | 33 | 5.635 | 0.171 |
| **2.0** | 32 | **8.656** | **0.271** |
| 2.5 | 31 | 5.263 | 0.170 |
| 3.0 | 31 | 6.750 | 0.218 |
| 4.0 | 31 | 5.088 | 0.164 |
| 50.0 *(current)* | 31 | 5.088 | 0.164 |

The harness flags it itself: **train/OOS consistent `False`**, with
*"⚠ OOS pick is not net-positive in-sample — treat as weak."*

And the grid shape says the same thing independently: 5.63 → 8.66 → 5.26 →
6.75 → 5.09 zigzags rather than peaking. **That is noise, not a signal.** A
value picked from the top of a zigzag is picked from sampling variance.

**Recommendation: do NOT apply a `tp_r` change to `ada_pullback_2h` on this
evidence.** The tuning attempt is on record — which is what the
tune-before-demote rule required — and it did not produce a value worth
shipping. That is a legitimate outcome of an attempt, not a failure to make one.

## Status of the two obligations

| leg | tuning attempt on record | value to ship |
|---|---|---|
| `xrp_pullback_2h` | ✅ (verified: gate reads `True`) | **`tp_r: 3.0`** — Tier-3, operator-approved |
| `ada_pullback_2h` | ✅ (verified: gate reads `True`) | **none** — evidence too weak |

The other four crypto pullback legs (`eth_pullback_2h`,
`eth_pullback_prop_2h`, `sol_pullback_2h`, `htf_pullback_trend_2h`) have **no**
attempt on record — verified, the gate returns `False` for them — so any
demotion proposed for those would correctly be softened to `tune` today.

⚠️ **None of this repairs Finding 3.** `cap_r < 1` on 9.1% / 11.7% of trades
stands whatever `tp_r` says: for that tail the venue ceiling is nearer than the
stop and no declared target is reachable. A `tp_r` change makes the target a
decision on the ~90% where one is placeable; it does not make one exist where
none can.

## ⚠️ A limitation of the tune-before-demote evidence, found while running this

`runtime_logs/` is **gitignored** (`.gitignore:29`), so the artifacts this sweep
just wrote are **host-local**. They are not committed and do not travel.

Consequences, stated rather than left to be discovered:

- A sweep run in a sandbox session does **not** make the evidence visible to a
  `/performance-review` running on the VM, or to CI. `tuning_attempt_on_record`
  would return `None` there (the directory does not exist) — which is the
  **fail-safe** direction by design: the matrix verdict stands rather than being
  softened on a failed read. So nothing breaks silently; a demotion is simply
  not blocked by an attempt the reviewing host cannot see.
- But it also means "on record" means **on the host running the review**. The
  canonical home is the live VM, where `/api/bot/strategies/{name}/tune` serves
  the same files.
- The two sweeps above are therefore recorded HERE, in a committed document,
  with their full grids — so the evidence survives even though the artifact does
  not. That is why the tables are pasted in full rather than referenced by path.

The durable fix is either committing tune results to a tracked location (as
`comms/` already does for GPU spend and broker truth) or running the sweep on
the VM. Filed as its own row; not fixed here.
