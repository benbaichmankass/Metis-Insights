# A declared exit arm is a volatility threshold in disguise — M20, 2026-08-16

**Status:** measurement + mechanism. **Nothing was flipped.** Every Tier-3 item
stays exactly where the overnight session left it; this memo changes what the
operator knows before deciding, not what the system does.

**Answers** the last open question from the overnight M20 session's 13:11Z
release: *"why the live book enters wider is untested — ATR regime at those
eight entry times versus a 2010–2026 average, or a sizing-path difference. That
is the next question and it is open."*

Both candidates were named there. **One is refuted by code, the other is
measured here** — and the mechanism that survives is not specific to
`gld_pullback_1d`.

---

## 1. `risk/entry` is exactly `atr_stop_mult × ATR/close` — so the sizing-path candidate is dead

Live and backtest compute the stop identically:

| | file | code |
|---|---|---|
| live | `src/units/strategies/htf_pullback_trend_2h.py:319-321` | `sl = entry − atr_stop_mult*atr` · `risk = entry − sl` |
| live | `src/units/strategies/trend_donchian.py:384-385` | same |
| harness | `scripts/backtest_pullback.py:432-433` | same |

and the two `_atr` bodies are **byte-identical**
(`tr.rolling(period, min_periods=1).mean()`). So on every side:

```
risk/entry  ≡  atr_stop_mult × (ATR₁₄ / close)
```

**This kills the sizing-path hypothesis at the definition level.** `sl` is fixed
at signal time, before any sizing runs; `RiskManager` sets *quantity*, never the
stop distance. It also rules out an ATR-definition skew — which was the
candidate that would have made this a **parity bug** rather than a regime fact,
and neither could be told apart from the numbers alone.

`atr_stop_mult` is a per-leg constant. **So the entire live-vs-backtest gap in
`risk/entry` is a difference in normalized volatility at entry, and nothing
else.**

## 2. Therefore `cap_R` is inversely proportional to normalized volatility

`position_telemetry.cap_r` (shipped, M31 P2) computes `cap_pct·entry/risk`.
Substituting § 1:

```
cap_R = 0.099 / (atr_stop_mult × ATR/close)
```

Invert it and a declared arm stops looking like a property of the leg:

> **An arm `A` on a leg with stop-mult `M` can only ever fire while
> `ATR/close ≤ 0.099 / (M · A)`.**

Every declared arm, computed from config alone:

| leg | symbol | tf | exec | stop-mult | arm | **ATR/close ceiling** |
|---|---|---|---|--:|--:|--:|
| `trend_donchian` | BTCUSDT | 1h | live | 2.5 | 6.49 | 0.610% |
| `trend_donchian_sol_4h` | SOLUSDT | 4h | live | 2.5 | 5.57 | 0.711% |
| `avax_pullback_2h` | AVAXUSDT | 2h | shadow | 2.5 | 4.86 | 0.815% |
| `xrp_pullback_2h` | XRPUSDT | 2h | live | 2.5 | 4.49 | 0.882% |
| `gld_pullback_1d` | GLD | 1d | live | 2.0 | 5.06 | 0.978% |
| `qqq_trend_long_1d` | QQQ | 1d | live | 2.5 | 3.56 | 1.112% |
| `scha_trend_long_1d` | SCHA | 1d | live | 2.5 | 2.00 | 1.980% |
| `trend_donchian_xrp_4h` | XRPUSDT | 4h | live | 2.5 | 2.00 | 1.980% |

This explains the one entry the registry grades `reachable` at 100% without
needing a per-leg story: BTC 1h ATR is ~0.333% of price, comfortably under its
0.610% ceiling. It is not that BTC's arm was chosen better — it is that BTC 1h
is the quietest instrument on the list in normalized terms.

## 3. Where the arms came from — the chain is mechanical, not per-leg bad luck

1. **2026-05-27** — the live units clamp TP to 9.9% of entry to satisfy Bybit
   `ErrCode 10001` (PR #2141; comment at `trend_donchian.py:125-132`).
2. **2026-07-12/13** — P4.4 computes each leg's **p80 winner-MFE** and ships six
   arms: BTC 6.49 · xrp 4.49 · avax 4.86 · sol_4h 5.57 · qqq 3.56 · gld 5.06
   (`S-M20-EXIT-REFINEMENT-2026-07-12.md:534`).
3. **2026-08-10** — the harness first gains `--tp-cap-pct`
   (`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`;
   `m20_corpus_relabel_tp_cap.py` dates the two bracketing runs to 22:23Z and
   22:31Z that day).

**So the six arms were derived roughly four weeks before the harness could model
the cap at all.** They are p80s of an **uncapped** MFE distribution, where MFE
runs as far as the trend goes, applied to a **capped** live book where MFE
cannot exceed `cap_R`. Nothing compared the two until the reachability audit.

That makes `BL-20260816-TRAIL-DECAY-ARM-R-SITS-ABOVE-THE-VENUE-TP-CAP` a
**downstream symptom** of `BL-20260810-...`, not an independent finding — and it
predicts exactly which legs are affected: those whose uncapped p80 exceeds their
own ceiling. § 4 tests that prediction.

## 4. Measurement — entry-conditioned `risk/entry` by era (relay #9710)

Config-exact, `--tp-cap-pct 0.099`, per-trade emit, repo venv. **Backtest
population** — a third basis, distinct from the registry's authoritative
`order_packages/risk_per_unit`, and deliberately not the unconditional candle
screen (the registry's own `basis_note` records that screen overstating xrp
90.5% vs 33.3%, because entries are filter-selected).

| leg | n | span | med `risk/entry` | `cap_R` @med | arm | **arm reachable** |
|---|--:|---|--:|--:|--:|--:|
| `gld_pullback_1d` | 112 | 2010-03→2026-04 | 2.300% | 4.30 | 5.06 | **42/112 (37.5%)** |
| `qqq_trend_long_1d` | 81 | 2007-04→2026-05 | 3.660% | 2.70 | 3.56 | **16/81 (19.8%)** |
| `scha_trend_long_1d` | 65 | 2010-03→2026-06 | 3.729% | 2.65 | 2.00 | **54/65 (83.1%)** |

**The § 3 prediction holds on all three.** gld (5.06 > 4.30) and qqq
(3.56 > 2.70) declare arms above their median ceiling and are largely
unreachable; scha (2.00 < 2.65) declares below it and is largely reachable —
and scha's 2.00 is *not* one of the six uncapped p80 arms.

### The era cut — which resolves the splice

`gld_pullback_1d`, reachability of arm 5.06 by entry year:

| era | median `risk/entry` | `cap_R` @med | arm reachable |
|---|--:|--:|--:|
| 2017 | 1.709% | 5.79 | 6/7 |
| 2018 | 1.805% | 5.48 | **7/7** |
| 2019 | 1.538% | 6.44 | 4/6 |
| … | | | |
| 2025 | 3.452% | 2.87 | 1/5 |
| 2026 | 7.396% | 1.34 | 0/2 |

**2025–2026 combined: 1 of 7.** The live measurement over the leg's complete
package history is **0 of 8**.

So the backtest and the live book **do not disagree** — pooled over 17 years the
backtest says 37.5%, but restricted to the era the live book actually traded it
says 14.3% against live's 0%, on two independent populations of 7 and 8. The
splice the overnight session flagged (*"a p80 over one population and a ceiling
over another are not comparable just because both are in R"*) dissolves once era
is held fixed. **It was never a population conflict; it was an unstated era.**

⚠️ **The effect is regime-clustered, not a monotone trend.** 2011 (3.327%) and
2013 (3.169%) are also inside the live band, and 2020 (2.661%) is elevated;
2010–2013, 2020 and 2025–2026 are high-vol clusters separated by the quiet
2014–2019 and 2022–2024 stretches. "Gold got more volatile recently" is the
wrong summary — **normalized vol mean-reverts across multi-year regimes, and a
p80 pooled across all of them describes no regime in particular.**

### The two remaining queued legs — and the sharpest form of the mechanism (relay #9715)

| leg | n | span | med `risk/entry` | `cap_R` @med | arm | **reachable** |
|---|--:|---|--:|--:|--:|--:|
| `trend_donchian_sol_4h` | 127 | 2023-01→2026-06 | 6.038% | 1.64 | 5.57 | **0/127 (0.0%)** |
| `xrp_pullback_2h` | 204 | 2023-01→2026-07 | 4.306% | 2.30 | 4.49 | **12/204 (5.9%)** |

**`trend_donchian_sol_4h` is zero in every single year** (2023 0/36 · 2024 0/36 ·
2025 0/38 · 2026 0/17). **So era is not the universal answer** — for GLD the leg
straddles its threshold and the era decides; here it never clears in any regime.
The registry's candle screen read 2.8% against this 0.0%, overstating in the same
direction it overstated xrp, which is exactly why `unmeasured_reason` declined to
record it as a verdict.

**`xrp_pullback_2h` resolves a warning the registry raised about itself.** Its
live basis is truncated to the 6 newest of up to 25 and reads 33.3%, with the
note *"do NOT read 33.3% as a lifetime rate — the sample is truncated and
recency-biased."* At n=204 it reads **5.9%**. The truncated sample overstated by
**~5.6×**. The warning was right, and this is the leg behind real-money trade
4163 (`bybit_2`), whose M31 telemetry recorded `peak_r 3.4179` against `cap_r
3.9233` and `arm_r 4.49`.

#### The clearest statement of the defect

`trend_donchian` and `trend_donchian_sol_4h` are the **same family, same
`atr_stop_mult` 2.5**, and were given near-identical arms — **6.49 and 5.57**, a
1.16× spread. Their ceilings differ by **7.3×**:

| leg | ATR/close | `cap_R` | arm | reachable |
|---|--:|--:|--:|--:|
| `trend_donchian` (BTC 1h) | ~0.333% | **11.91** | 6.49 | 100% |
| `trend_donchian_sol_4h` (SOL 4h) | ~2.415% | **1.64** | 5.57 | **0%** |

That is what a p80 over an **uncapped** book does: uncapped winner-MFE
distributions are similar *in R* across a family, so the sweep produced similar
arms — while the capped ceiling, which nobody was computing, differs by an order
of magnitude with the instrument and timeframe. **The arms were never wrong
relative to each other; they were measured against a ceiling that was not in the
measurement.**

## 5. The cap is a Bybit constraint, and three of these legs never touch Bybit

`_TP_SENTINEL_CAP_PCT = 0.099` is documented as a Bybit `ErrCode 10001`
workaround and is a **module-level constant with no venue branch**, applied in
four strategy modules. Routing, from `config/accounts.yaml`:

| leg | symbol | accounts carrying it | venues |
|---|---|---|---|
| `gld_pullback_1d` | GLD | `alpaca_paper` · `alpaca_portfolio` · `alpaca_live` | **Alpaca only** |
| `qqq_trend_long_1d` | QQQ | `ib_paper` · the three Alpaca accounts | **IBKR + Alpaca only** |
| `scha_trend_long_1d` | SCHA | `alpaca_paper` | **Alpaca only** |

**No Bybit account carries any of these symbols.** And the value is not
re-derived downstream: `pkg.tp` reaches Alpaca verbatim as
`take_profit.limit_price` (`alpaca_client.py:189`), with no distance check
anywhere in that client.

### ⚠️ CORRECTION — this claim was too broad, and it does NOT cover QQQ

An earlier draft of this section said the cap *"is imported from a venue they do
not trade on"* for **all three** legs. **Checking the venues' own rules narrows
that to two, and the code comment turns out to have been more accurate than my
reading of it** — it says *"Bybit **(and most exchanges)** reject TP further than
~10%"*, and I had been treating the parenthetical as throat-clearing.

| venue | documented rule on take-profit distance | source quality |
|---|---|---|
| **Alpaca** | **No maximum distance.** Only a `$0.01` minimum stop offset and `take_profit.limit_price` must be better than `stop_loss.stop_price`. | read this session (`docs.alpaca.markets`) |
| **IBKR** | **Reported to reject stock orders priced more than ~10% from NBBO** (20% options) — which would make the 9.9% cap *approximately correct* on this route. | ⚠️ **search summary only — both IBKR primary pages returned HTTP 403 to the fetcher, so I did not read it on a source page.** Treat as unconfirmed. |

So the claim survives for the **Alpaca-only** legs — `gld_pullback_1d` (GLD) and
`scha_trend_long_1d` (SCHA) — and **`qqq_trend_long_1d` is NOT a clean instance**,
because it also routes to `ib_paper`. If IBKR really does enforce ~10%, then a
single order package fanning out to both Alpaca and IBKR has to satisfy the
tighter of the two, and a venue-aware cap would have to resolve per *route*, not
per leg — which makes the design change meaningfully harder than "look up the
leg's venue".

⚠️ **What is STILL NOT established:** that Alpaca would *accept* a farther
take-profit in practice. Documented rules are not the live API, and absence of a
distance check in our client is not proof the venue lacks one. An undocumented
validation is exactly the kind of thing only an order attempt reveals — which is
why step 1 of the backlog row is a paper-account test, not more reading.

⚠️ **And the unconditional application has a real structural reason**, not just
oversight: `tp` is computed in the signal builder, which runs **before**
per-account routing, so no account/venue is in scope at that point. Making the
cap venue-aware is a design change to where the geometry is resolved — Tier-3,
not a one-line fix.

## 6. What this does and does not change

**Does not change:** any arm value, any disposition, any config. The seven
queued Tier-3 items stay seven.

**Does change what "inert" means.** `gld_pullback_1d` is not inert as a property
of the leg — it was reachable **7/7 in 2018** and is unreachable now. The
registry's `vol_conditional` vocabulary is the right one; the missing piece was
that *every* arm is vol-conditional, and the condition is computable in closed
form (§ 2).

**Four of the five queued entries now have a large-n basis they lacked** — every
one except `gld_pullback_1d`, which already had a complete live history:

- `qqq_trend_long_1d` — was `inert` on **n=1** live package plus a candle screen.
  n=81 entry-conditioned agrees (19.8% reachable). Note the two bases differ in
  the *opposite* direction from xrp: the screen read `cap_R` 2.13, entries read
  2.70, i.e. QQQ entries select **quieter** bars than the unconditional
  distribution.
- `scha_trend_long_1d` — was `unmeasured`, and flagged as *"the leg most
  sensitive to which basis is used"*. Both bases now agree it is largely
  reachable (screen 73.6%, entry-conditioned 83.1%), with the failures
  concentrated in high-vol years (2020: 2/6, 2022: 1/3, 2026: 1/3). **On this
  evidence its queued item looks closable as `ok` — but that is the operator's
  call and I have left the disposition untouched.**
- `trend_donchian_sol_4h` — was one of **three** entries recorded `unmeasured`
  (with `scha_trend_long_1d` above and the non-queued `uso_trend_1h`); an
  earlier revision of this line called it *"the last entry recorded
  `unmeasured`"*, which was wrong on its own page — `scha` is described as
  `unmeasured` two bullets up. Corrected 2026-08-16. n=127
  reads **0.0%, in every year**. Its `unmeasured_reason` said *"No entry-conditioned
  pull yet"*, which is no longer true, so **that text was corrected** — but the
  `verdict` was not. On a 3.4× gap at n=127 the evidence supports `inert`; I left
  every verdict in the file untouched this session for consistency, and because
  the authoritative live basis is still absent.
- `xrp_pullback_2h` — its truncated live basis (33.3%, 6 of up to 25) is now
  bounded by a large-n one at **5.9%**, an overstatement of ~5.6×.

**All five queued reachability entries are now decidable on measurement rather
than on a screen or a truncated sample.** None of them was decided here.

**The methodology consequence is the durable half.** A p80 arm swept over a
pooled multi-era population encodes a volatility mix the live book does not
sample. Two cheap remedies, either sufficient, both Tier-1 tooling rather than
Tier-3 values — filed as `PB-20260816-ARM-SWEEP-POOLS-VOL-ERAS`:

1. **Report the arm's implied `ATR/close` ceiling beside every proposed arm** so
   a reader can check it against the current regime without re-deriving § 2.
2. **Sweep the arm on the recent era**, or report the p80 per era so the pooled
   figure is never the only one quoted.

---

## Provenance

- **Code claims** (§ 1, § 2, § 5) — read this session from the files cited, not
  inherited from a doc.
- **Measurements** (§ 4) — relay #9710, one run, repo venv (`pandas 3.0.3`),
  three legs. The gld overall median reproduces the overnight session's
  independent figure to 0.001pp (2.300% vs 2.301%) and its live-band overlap
  exactly (16/112), which is the cross-check that the harness invocation matched.
- **Chain dates** (§ 3) — `S-M20-EXIT-REFINEMENT-2026-07-12.md:534` and
  `m20_corpus_relabel_tp_cap.py`'s run-SHA evidence. The arm's introducing commit
  could **not** be blamed: `git blame` hits the shallow-clone boundary at
  `72ee5c7`, so the sprint log is the provenance, not git.
- **Not measured:** whether Alpaca/IBKR accept a farther TP (§ 5); whether
  removing the cap would improve results on those legs (a backtest question, and
  the harness default that `--tp-cap-pct` controls is itself a queued Tier-3
  decision, memo § 4).
