# `alpaca_live` roster candidates — what a $200 whole-share CASH account can actually run

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-201 · `WO-20260908-WIRE-MORE-STRATEGIES-TO-ALPACA-LIVE-WHICH` · Tier-1 PROPOSAL.**
Nothing here is enacted. `config/accounts.yaml` is Tier-3; the exact diff is
written out below and is the operator's to approve.

Operator, 2026-09-10: *"what's going on with the alpaca live roster? why am I
still not seeing any live trades weeks after we reactivated this account?"*

---

## 0. The answer, in three sentences

**The account is not broken and it is not gated.** Its one routed leg
(`tlt_pullback_1h`) has produced **3 order packages since the roster was armed
on 2026-08-31 — 2026-09-01, 09-03 and 09-08 — and all three were SHORT**, which
this account cannot take because **Alpaca reports `shorting_enabled: false` on
it**, before our own `side_filter: long` gate is even consulted.

**It is idle because almost nothing is assigned to it, and because the one thing
assigned to it stopped producing longs the day before it was armed** — that leg's
long side fired 18× in June, 2× in July, 13× in August and **0× in September**.

**The roster CAN be widened, but not by much, and not on positive evidence.**
After the three hard constraints below, exactly **five** legs in the whole Alpaca
universe are affordable, long-capable and not already excluded — and **not one of
them has a positive measured track record at a gradeable sample size**.

---

## 1. What I MEASURED

Everything in this section is a reading taken in this session, with its
population and its locator. Section 2 is what I INFER from it; section 5 is what
I PROPOSE. §7 lists what I inherited and did **not** re-verify.

### 1.1 The account, on the venue

**MEASURED** `/api/diag/broker_account_status?account_id=alpaca_live`, read
**2026-09-10T08:16:55Z** (direct HTTPS, `served by https://ict-bot.duckdns.org`;
live `git_sha 55c52392f` = `git_sha_on_disk`, `restart_pending: false`):

| field | value |
|---|---|
| `status` | `ACTIVE` · `trading_blocked: false` · `account_blocked: false` |
| `capacity.multiplier` | **`"1"`** — a CASH account, verified rather than inherited |
| `buying_power` / `regt_buying_power` / `cash` / `equity` | **all `200.22`** |
| **`shorting_enabled`** | **`false`** |
| open positions (`/api/diag/exchange_positions`) | **0** |

### 1.2 The two hard sizing filters, read out of the sizer rather than assumed

From `src/units/accounts/risk.py` at `55c52392f`, with `alpaca_live.risk` carrying
`risk_pct: 0.02` and **no `confidence_sizing` key** (so `_confidence_scalar`
returns the flat `1.0` — I read the field, not the prose about it):

| term | source | value at `equity = 200.22` |
|---|---|---|
| per-trade risk budget | `balance × risk_pct × conf_scalar` | **$4.0044** |
| risk-per-share ceiling | `_ROUND_UP_BUDGET_MULT = 1.5` × budget | **$6.0066** |
| **share-price ceiling** | `_MARGIN_SAFETY_BUFFER = 0.9 × available_usd` | **$180.20** |

A share priced above $180.20 refuses at **any** risk setting — the margin cap
floors `max_qty_by_margin` to 0 whole shares and `position_size` returns `0.0`.
A leg whose stop distance exceeds $6.0066 refuses the trade outright (the
round-up-to-one-share relaxation is what the 1.5 multiple is, and beyond it the
sizer declines rather than over-risking).

⚠️ **Hand arithmetic gets this wrong in the permissive direction** — that is
recorded in `docs/research/alpaca-200-affordability-sweep-2026-08-25.md`, so
every verdict in §1.4 below is produced by **running `RiskManager.position_size`
itself**, never by comparing a budget to a stop.

### 1.3 Directional flow, per leg, over a complete population

**MEASURED** `trade_journal.db::signals`, `event=pipeline_result`,
`since=2026-06-01T00:00:00Z`, read via `/api/diag/audit_query` on
2026-09-10. Every count is far below the route's 1000-row limit, so these are
**complete, not truncated**; every row carries `status: multi_account_dispatched`.

⚠️ **Instrument note, because two obvious probes are wrong here.** The
`<strategy>_eval` rows use the side vocabulary `none`/`buy`/`sell`, while
`regime_ml_vol_shadow` rows use `long`/`short`. A query filtered on
`side=long|short` therefore returns *only* the regime-shadow rows and silently
misses every package — I ran exactly that query first and got a plausible,
wrong answer. And `_eval` rows repeat **every tick** while a setup persists
(209 in six days on one leg), so counting them over-states flow by ~2 orders of
magnitude. `pipeline_result` is the one row per dispatched package.
**Positive control:** this probe returns **33 buys** for `tlt_pullback_1h` over
2026-06-22→, and `config/accounts.yaml`'s own 2026-08-31 measurement — taken
against `order_packages` through a different route — records **33 long** over
2026-06-22→2026-08-28. The instrument agrees with a known good.

| leg | pkgs | buy | sell | %buy | price range | long packages by month |
|---|--:|--:|--:|--:|---|---|
| `tlt_pullback_1h` | 77 | **33** | 44 | 42.9% | 81.64–87.38 | Jun 18 · Jul 2 · Aug 13 · **Sep 0** |
| `uso_trend_1h` | 35 | 35 | 0 | 100% | 106.50–145.15 | Jul 29 · Aug 3 · Sep 3 |
| `spy_pullback_1h` | 73 | 33 | 40 | 45.2% | 731.62–771.96 | — |
| `qqq_pullback_1h` | 66 | 25 | 41 | 37.9% | 674.94–738.49 | — |
| `slv_trend_1h` | 40 | 22 | 18 | 55.0% | 49.66–64.06 | Jun 1 · Jul 9 · Aug 7 · Sep 5 |
| `gld_pullback_1h` | 54 | 16 | 38 | 29.6% | 367.21–423.61 | — |
| `tlt_pullback_1d` | 6 | 3 | 3 | 50.0% | 82.88–86.15 | Jun 1 · Jul 2 |
| `iwm_trend_long_1d` | 3 | 3 | 0 | 100% | 298.50–303.03 | — |
| `qqq_trend_long_1d` | 2 | 2 | 0 | 100% | 732.11–732.36 | — |
| `ief_pullback_1d` | 2 | 1 | 1 | 50.0% | 93.50–94.10 | Jul 1 |
| `gdx_pullback_1d` | 8 | 1 | 7 | 12.5% | 73.55–97.95 | Sep 1 |
| `gld_pullback_1d` | 9 | 1 | 8 | 11.1% | 370.75–400.43 | — |
| `iaum_pullback_1d` | 7 | 1 | 6 | 14.3% | 40.32–43.51 | Sep 1 |
| `slv_pullback_1d` | 6 | 1 | 5 | 16.7% | 51.87–60.12 | Aug 1 |
| `scha_trend_long_1d` | 1 | 1 | 0 | 100% | 35.68 | Aug 1 |
| `qld_trend_long_1d` | 1 | 1 | 0 | 100% | 95.02 | Aug 1 |
| `spy_trend_long_1d` | 1 | 1 | 0 | 100% | 755.62 | — |
| **`splg_trend_long_1d`** | **0** | 0 | 0 | — | — | **never** |
| **`tqqq_trend_long_1d`** | **0** | 0 | 0 | — | — | **never** |

**And in the armed window specifically** (`since=2026-08-31`, complete):
`tlt_pullback_1h` produced **3 packages, 0 buy / 3 sell** — 2026-09-01T16:05:36Z
@ 82.12, 2026-09-03T13:30:33Z @ 82.34, 2026-09-08T13:30:28Z @ 82.47.

### 1.4 The sizer's own verdict on every long setup this fleet has produced

**MEASURED** by running `RiskManager.position_size` (`whole_units=True`,
`market_type="spot"`, `available_usd = total_account_usd = balance = 200.22`,
`risk_pct 0.02`, `confidence 0.5`) over **every distinct `(entry, stop_loss)`
pair** on the buy side of each leg's `<strategy>_eval` rows since 2026-06-01
(`/api/diag/audit_query`, read 2026-09-10; none of the pulls hit the 1000-row
cap, so each is a complete population of that leg's long setups in the window).

| leg | long setups | **sized > 0** | median risk/share | qty | risk $ | notional (% of $200.22) |
|---|--:|--:|--:|--:|---|---|
| `tlt_pullback_1h` | 240 | **240 (100%)** | 0.368 | 2 | 0.55–1.76 | 164–175 (**82–87%**) |
| `slv_pullback_1d` | 217 | **217 (100%)** | 3.766 | 1 | 3.54–3.90 | 59–62 (29–31%) |
| `iaum_pullback_1d` | 79 | **79 (100%)** | 1.690 | 2 | 3.30–3.58 | 87–90 (43–45%) |
| `ief_pullback_1d` | 67 | **67 (100%)** | 0.938 | 1 | 0.89–0.94 | 94 (47%) |
| `tlt_pullback_1d` | 44 | **44 (100%)** | 1.594 | 2 | 3.03–3.22 | 171–172 (**85–86%**) |
| `scha_trend_long_1d` | 29 | **29 (100%)** | 1.301 | 3 | 3.81–3.90 | 107 (53%) |
| `slv_trend_1h` | 116 | 116 (100%) | 1.193 | 2–3 | 1.19–3.97 | 107–180 (53–90%) |
| `uso_trend_1h` | 348 | 348 (100%) | 2.722 | 1 | 1.40–4.80 | 106–150 (53–75%) |
| **`gdx_pullback_1d`** | 59 | **0 (0.0%)** | **7.665** | — | — | — |
| **`qld_trend_long_1d`** | 1 | **0** | **7.168** | — | — | — |

Current daily closes, read the same day from `/api/bot/candles?timeframe=1d`,
confirm the observed setup prices are still representative and that nothing
crosses the $180.20 wall: TLT 81.66 · IEF 91.89 · SLV 60.71 · IAUM 43.83 ·
SCHA 34.09 · GDX **99.43** · QLD 89.92. **SPLG returned ZERO candles.**

### 1.5 Measured track record — every candidate, both paper books

**MEASURED** `/api/bot/performance?window=all`, read 2026-09-10.
⚠️ **STATE THE POPULATION BEFORE READING A SINGLE NUMBER HERE.** These are
**paper MARGIN books** (`alpaca_paper` `multiplier 4`, `risk_pct 1.5%`, balance
~$82k; `alpaca_portfolio` `multiplier 4`, `risk_pct 2.0%`, balance ~$95k) sizing
in **hundreds of shares**. `alpaca_live` is a **CASH** book at **$200** sizing in
**1–3 shares**. The dollar magnitudes below do **not** transfer. Only the
win-rate / R shape does, and even that is at n ≤ 15 with `pnlCoverage` ≤ 0.5.

| leg | `alpaca_paper` n / wins / totalPnl / measured / cov / expR | `alpaca_portfolio` n / totalPnl / cov |
|---|---|---|
| `tlt_pullback_1h` | 11 / 2 (18.2%) / **−2998.00** / −1654.95 / 0.364 / **−0.503** | 6 / −2134.18 / 0.333 |
| `tlt_pullback_1d` | 4 / 2 (50%) / +1105.35 / +1111.70 / **0.000** / **−0.119** | 1 / +593.94 / 0.000 |
| `ief_pullback_1d` | 1 / 0 / −1.68 / 0.00 / 0.000 / −0.230 | — none |
| `slv_pullback_1d` | 2 / 0 / **−1807.33** / −510.88 / 0.000 / **−1.101** | — none |
| `iaum_pullback_1d` | **no closed rows** | **no closed rows** |
| `scha_trend_long_1d` | **no closed rows** | **no closed rows** |
| `gdx_pullback_1d` | **no closed rows** | **no closed rows** |
| `splg_trend_long_1d` | **no closed rows** | **no closed rows** |
| `uso_trend_1h` | 15 / 9 (60%) / **+10498.03** / +10451.08 / 0.467 / **+0.920** | 8 / +10239.39 / 0.500 |
| `slv_trend_1h` | 23 / 5 (21.7%) / −6107.00 / −4214.86 / 0.261 / −0.397 | 8 / −3528.78 / 0.500 |
| `gld_pullback_1h` | 13 / 0 (0%) / −5030.58 / −3133.93 / 0.385 / −0.962 | 4 / −2955.89 / 0.750 |
| `spy_pullback_1h` | 12 / 4 / −1331.91 / −1878.61 / 0.250 / −0.614 | 6 / −1840.14 / 0.500 |
| `qqq_pullback_1h` | 13 / 2 / −2085.03 / −1231.19 / 0.231 / −1.306 | 4 / −257.66 / 0.500 |

⚠️ `tlt_pullback_1d`'s **`pnlCoverage: 0.000`** means **zero MEASURED rows** —
its `+$1,105` rests on 3 ESTIMATED rows and nothing the broker confirmed. Its
`expectancyR` is **negative** while its PnL is positive; per
`docs/reference/bot-api-reference.md` those need not share a sign and forcing
agreement would be fitting the instrument to a desired answer. **At n=4 with no
broker truth, that leg is `insufficient_n`, not "profitable".**

⚠️ `uso_trend_1h` is the ONLY positive leg in the whole affordable set, at the
top of every ranking, and it is **excluded on a standing non-financial operator
constraint** (2026-08-29: *"I don't wanna make money from fossil fuels"*). The
`config/accounts.yaml` comment predicted precisely this — that the next session
to rank by PnL would find USO first and must not promote it. Confirmed; not
promoted.

### 1.6 The CI-enforced coupling any roster edit must satisfy

`tests/test_paper_portfolio_accounts.py::test_alpaca_portfolio_mirrors_alpaca_live_minus_proxies`
asserts, on **every PR**:

1. `set(alpaca_live.strategies) − {splg_trend_long_1d, iaum_pullback_1d} ⊆ set(alpaca_portfolio.strategies)`
2. `alpaca_portfolio.symbols == alpaca_live.symbols − {SPLG, IAUM}` — **strict, ordered list equality**

**MEASURED** by running the invariant's own logic over simulated configs, with a
positive **and** a negative control (a green run over one config proves nothing):

| config | verdict |
|---|---|
| current `main` | **PASS** (positive control — the checker is not vacuous) |
| Option A (§5.1: 4 legs added, no mirror edit) | **PASS** |
| Option A **+ `scha_trend_long_1d`, no mirror edit** | **FAIL** — *"ROSTER-SYNC missing from the mirror: ['scha_trend_long_1d']"* (negative control) |
| Option A + SCHA **with** the mirror + `symbols` edit | **PASS** |

### 1.7 `SILENT_REFUSAL_SKIP` — the read MI-140 could not make

**MEASURED** `get-env` on `ict-trader-live.service`, GitHub Actions run
[34455224799](https://github.com/benbaichmankass/Metis-Insights/actions/runs/34455224799),
issue #11656, 2026-09-10T08:27:26Z:

```
SILENT_REFUSAL_SKIP
    process : 'alpaca_live'      ← /proc/<MainPID>/environ, authoritative
    declared: 'alpaca_live'      ← the unit's EnvironmentFile
```

No `pending_restart`. See §6 for what this settles.

---

## 2. What I INFER from the above

**INFERRED from §1.1, §1.3 and §1.4.** *The account cannot be woken by removing a
gate, because there is no gate to remove; and it cannot be woken much by adding
legs, because the constraint is the instrument set, not the roster.* Named
inputs: the `shorting_enabled: false` reading, the 3-packages-0-buy armed-window
count, and the sized>0 column.

**INFERRED from §1.4's notional column.** *A $200 whole-share cash book holds
roughly ONE position at a time, so extra legs buy CHANCES at one slot, not
diversification.* One `tlt_pullback_1h` fill consumes **82–87%** of the account;
`tlt_pullback_1d` 85–86%; `scha` 53%. The cash-settlement gate is armed on this
account (`ALPACA_CASH_SETTLEMENT_MODE=apply`, `ACCOUNTS=alpaca_live`) and its
basis is `max(min(venue_buying_power, venue_cash − our_unsettled), 0.0)`
(`src/runtime/cash_settlement.py`), so an open position removes the cash from
`available_usd` and a **sale** removes the proceeds for one trading day. This is
the honest reason a roster expansion here is modest by construction — it is not
pessimism about the legs.

**INFERRED from §1.3's monthly column.** *The account's idleness is partly bad
timing, not only structure.* `tlt_pullback_1h` bought 1-per-3.06-days across the
101-day window; observing 0 in the 10 days since arming is unlikely under a flat
rate (~4%). The leg is a trend-following pullback and TLT has been in a
downtrend, so its long side is *systematically* unavailable for the duration of
that trend. **That, not throughput, is the real argument for a wider roster:
what you want is legs whose LONG side is available in different regimes and
different assets** — a longer-only book is silent for the whole length of a
downtrend in its one instrument.

---

## 3. The screen, and what survives it

Population: **all 19 legs routed to any Alpaca account** (`alpaca_paper` 19,
`alpaca_portfolio` 14, `alpaca_live` 1). Filters applied in order:

| # | filter | legs removed |
|---|---|---|
| 1 | **share price > $180.20** (cash wall) | `spy_trend_long_1d`, `spy_pullback_1h`, `qqq_trend_long_1d`, `qqq_pullback_1h`, `gld_pullback_1d`, `gld_pullback_1h`, `iwm_trend_long_1d` — **7** |
| 2 | **sizer refuses every observed long setup** | `gdx_pullback_1d` (risk/share 7.665 > 6.0066), `qld_trend_long_1d` (7.168) — **2** |
| 3 | **produces no long packages at all** | `splg_trend_long_1d` (candle-starved — §4), `tqqq_trend_long_1d` (0 packages in 101 d) — **2** |
| 4 | **excluded by a standing decision** | `uso_trend_1h` (operator, non-financial, 2026-08-29); `slv_trend_1h` (Tier-3 demote to `execution: shadow`, operator-approved 2026-08-24 — 0 wins in 13 closed, −$5,375 at 0.77 coverage) — **2** |
| | **survivors** | **`tlt_pullback_1h` (incumbent) · `tlt_pullback_1d` · `ief_pullback_1d` · `slv_pullback_1d` · `iaum_pullback_1d` · `scha_trend_long_1d`** |

⚠️ **`slv_trend_1h` is the trap in this set and I nearly fell into it.** It has
the best long-flow profile of any affordable candidate — 22 buys, and the only
one with long packages in **every** month including September, 116/116 setups
sizing. Its blocker is a config flag, which reads like an accident. It is not:
`config/strategies.yaml` carries `execution: shadow  # shadow-guard: allow —
operator-approved Tier-3 demote 2026-08-24`, taken because it was the fleet's
worst leg *and specifically to remove it from the account being funded*. That is
a **DECIDED** statement and is not mine to re-litigate. Ranking candidates by
activity puts it near the top; do not promote it.

---

## 4. `splg_trend_long_1d` is structurally dead, and it is the S&P sleeve

Not a new finding — **already filed twice**
(`BL-20260807-SPLG-NO-CANDLES-STRATEGY-CANNOT-EVALUATE`, kept_open;
`PB-20260902-SPLG-TREND-LONG-1D-IS-CANDLE-STARVED-AND-CANNOT-SIGNAL`, open) —
but it decides a roster question, so it is restated here rather than left in a
1,300-row register.

**MEASURED** 2026-09-10: every `splg_trend_long_1d_eval` row in the readable
window carries *"need at least 46 candles for the donchian(30) / atr(14)
windows; **got 12**"*, and `/api/bot/candles?symbol=SPLG&timeframe=1d` returns
**zero** candles. Zero order packages since 2026-06-01.

⚠️ **Worth adding to the existing row as a fresh observation:** the 2026-08-07
row records *"35 to the strategy"*. It is now **12**. The starvation has
**deepened**, not held steady.

**Consequence for this proposal:** `SPLG` is `alpaca_live`'s **only** S&P proxy
(`config/accounts.yaml` says so in terms), and SPY itself is cash-refused at
$755. So **`alpaca_live` has no live route to S&P exposure at all** — by data
starvation, not by policy — and no roster edit can change that.

---

## 5. THE PROPOSAL — three options, one recommendation

### 5.1 Option A (RECOMMENDED) — add the four affordable legs that need no mirror edit

```diff
   alpaca_live:
     ...
     strategies:
       - tlt_pullback_1h
+      - tlt_pullback_1d      # TLT 1d pullback. Sizes 44/44 observed long setups
+                             # (2 sh, $3.03-3.22 risk, 85-86% of the book).
+                             # Already in alpaca_portfolio -> ROSTER-SYNC clean.
+      - ief_pullback_1d      # IEF 1d pullback. 67/67 (1 sh, $0.89-0.94, 47%).
+                             # Already in alpaca_portfolio.
+      - slv_pullback_1d      # SLV 1d pullback. 217/217 (1 sh, $3.54-3.90, 29-31%).
+                             # Already in alpaca_portfolio.
+      - iaum_pullback_1d     # IAUM 1d pullback (the gold affordability PROXY).
+                             # 79/79 (2 sh, $3.30-3.58, 43-45%). A declared proxy,
+                             # so DELIBERATELY exempt from the alpaca_portfolio
+                             # mirror -- do not "fix" that by adding it there.
```

**No `symbols:` change is required** — TLT, IEF, SLV and IAUM are all already in
`alpaca_live.symbols`, **and** each is independently carried by
`alpaca_paper.symbols`, so the tick loop's symbol union is unchanged either way.

> ⚠️ **CORRECTION, 2026-09-10, to this document's own first version — I got this
> wrong and the wrong version is on `main` in commit `145101e54`.** It said the
> account-level `symbols:` list is *"**inert for Alpaca**. Its only consumers are
> `clients.py::_configured_symbols` … and `exchange_accounts.py`"*. **That is
> false.** I swept two consumers and generalised from them, which is the exact
> shape RULE ONE's ledger calls *a search returning nothing read as proof of
> absence* — and I did it while writing a document whose whole subject is
> measurements that decay.
>
> **`src/main.py::_resolve_tick_symbols` reads `acct.symbols` and it decides
> which symbols the tick loop runs.** A symbol carried by NO configured,
> non-opted-out account is never fetched, so no strategy on it can ever
> generate a signal. `CLAUDE.md` says this correctly (*"the symbol set is
> derived from `config/accounts.yaml`"*) — **field beats comment, and here the
> field agreed with the comment and disagreed with me.**
>
> **The complete consumer set, enumerated rather than sampled:**
>
> | consumer | scope |
> |---|---|
> | `src/main.py::_resolve_tick_symbols` | **ALL accounts — the tick loop's symbol set, as a UNION.** An explicit `strategies: []` opts an account out of it. |
> | `clients.py::_bybit_configured_symbols` (→ `account_open_positions`, `_bybit_position_protection`) | Bybit only |
> | `exchange_accounts.py::bybit_fill_accounts` | Bybit only (`exchange == "bybit"` filter) |
> | `clients.py::account_ib_venue_session` (first-symbol fallback) | IB only |
>
> ⚠️ **`src/main.py::_symbols_for_account` is a DIFFERENT function and is not
> this one** — it reads each STRATEGY's `symbols` and is used only for the Bybit
> `set_leverage` pre-flight. Confusing the two is what produced the error.
>
> **The conclusion survives, on the correct reason.** Measured against
> `config/accounts.yaml`: TLT is carried by `ib_paper`, `alpaca_paper`,
> `alpaca_portfolio` and `alpaca_live`; IEF and SLV by `alpaca_paper`,
> `alpaca_portfolio` and `alpaca_live`; IAUM by `alpaca_paper` and
> `alpaca_live`; **SCHA by `alpaca_paper`** — all with non-empty rosters. So
> every Option A and Option A+ symbol is already in the union, and the tick loop
> fetches it today. That is why `scha_trend_long_1d` produces eval rows at all.
>
> ⚠️ **THE DANGEROUS READING THIS CORRECTS:** a session acting on *"the list is
> inert"* could delete or neglect an account's `symbols:` and silently stop the
> tick loop fetching a symbol no other account carries — a whole sleeve going
> dark with no error, which is `splg_trend_long_1d`'s failure mode arrived at
> from the other direction.

**What IS true, and was verified directly:** the account `symbols:` list is **not
a routing gate**. Routing is decided solely by
`accounts.yaml::<account>.strategies` in
`coordinator._dispatch_exclusion_reason`, whose Alpaca path tests only
`pkg.strategy not in assigned` (the `symbol_exchange_routing` test above it is
gated on `interactive_brokers`). So adding a leg to `alpaca_live.strategies`
routes it, and nothing about `symbols:` can block that.

**No `alpaca_portfolio` change is required** — verified against the invariant in
§1.6 with a positive and a negative control.

**What Option A actually buys, stated honestly:** the four added legs produced
**6 long packages between them in 101 days**, against the incumbent's 33 — so
this is roughly a **+18% increase in long flow**, spread across three assets
(bonds / silver / gold) whose long side is available in different regimes from
TLT's. **Diversification of WHEN a long is available, not of position count.**

**Risk added,** over the same population as §1.4 (every distinct observed long
setup per leg; n = 44 / 67 / 217 / 79 for `tlt_pullback_1d` / `ief_pullback_1d` /
`slv_pullback_1d` / `iaum_pullback_1d`): each added leg risks **$0.89–$3.90** at
its declared stop — **0.4–1.9% of the $200.22 equity read on 2026-09-10**, and
that range is the min and max across all 407 of those setups, not a central
estimate. The structural ceiling is unchanged and is the round-up bound:
`1.5 × $4.0044 = $6.01`, i.e. **3.0% of that same $200.22**, in one trade. It is
an arithmetic bound on the sizer, not a measurement — no leg in Option A
approaches it (widest observed is `slv_pullback_1d` at $3.90, over its 217
setups). The daily-loss cap is `daily_loss_pct 0.10 × 200.22 = $20.02`, so it
takes ~5 full stop-outs at that widest observed risk to trip it. Contention: at most one position at a time (§2), and the added legs are
all 1d, so the incumbent's slot is rarely taken.

### 5.2 Option A+ — also add `scha_trend_long_1d` (small-cap)

Adds a fifth asset class (Russell-2000-ish small caps), sizes 29/29 at 3 shares
/ $3.81–3.90 / 53% of the book. **Costs a mirror edit**, because SCHA is not a
declared proxy: `alpaca_portfolio.strategies` must gain `scha_trend_long_1d`
**and** `SCHA` must be appended to **both** `symbols` lists (strict ordered
equality). Verified to pass in §1.6.

**Weaker than Option A on evidence:** 1 long package ever (2026-08-17), **no
closed rows on either paper book**, so it is `insufficient_n` in the strongest
sense — there is no record at all.

### 5.3 Option B — add nothing yet

The cycle priority is `CY-20260906-TRADING-TRUTH`: *repair the MEASUREMENT before
acting on what it says.* No candidate clears an evidence bar (§1.5); adding legs
adds real-money trades this system cannot yet grade. MI-201's own checklist row
made this sequencing argument on 2026-09-08 (*"wiring more legs to a real-money
cash account now would add trades this system cannot yet grade"*).

**Why I do not recommend it as the answer:** the operator has now asked twice for
more strategies on this account, and Option A adds trades at **$0.89–$3.90 of
risk each** on a book explicitly framed as a *demonstration account*. The
measurement objection is real but it is not proportionate to a $4 ticket. **The
honest framing is the incumbent's own: a PLUMBING expansion, not an edge bet.**

### 5.4 What none of the options fixes — the constraint is the instrument set

The account declares 11 symbols. **Four of them (SPY, QQQ, GLD, IWM) are
cash-refused at any risk setting**, and a fifth (SPLG) has no data. The
2026-08-25 affordability sweep found **42 reachable ETFs** at this balance —
`VTWO`, `ONEQ`, `GLDM`, `SGOL`, `IAU`, `GOVT`, `SCHX`… — and **not one of them
has a strategy cell wired**. So the real unlock is one of:

- **fund the account above ~$840** (0.9 × 840 > 755) so its own declared roster
  sizes; or
- **build strategy cells on the cheap proxies** — research work, not a config
  edit, and the correct home for it is `ROADMAP.md`, not this proposal.

---

## 6. Also owed — `silent_refusal_alert`'s latch on this account is FROZEN, and now diagnosed

**MEASURED** `/api/diag/log_file?name=silent_refusal_alert_state`, read
2026-09-10:

```
__last_check__      : 2026-09-10T07:58:20Z        <- the detector RAN 37 min ago
bybit_1 / ib_paper / alpaca_paper / alpaca_portfolio
  / bybit_2 / bybit_portfolio / alpaca_options_paper
                    : updated_at 2026-09-10T07:58:20Z   (7 of 8, all refreshed)
alpaca_live         : updated_at 2026-08-21T12:38:38Z   <- 20 DAYS STALE
                      alerting: true · cause: risk_refused
                      verdict: signalled_never_placed · refused 5 · placed 0
                      (no `priority_causes`, no `alerting_basis`, no
                       `alert_disposition` — it predates those keys entirely)
```

**The mechanism, now settled by the §1.7 read.** `alpaca_live` is in
`SILENT_REFUSAL_SKIP`. `run_silent_refusal_check` has **two** loops and **both**
`continue` on `aid in skip`:

- the assessment loop skips it, so `state[aid]` is never rewritten;
- the quiet-account **release** loop — added specifically for this symptom, and
  whose own comment cites *this exact account frozen at this exact timestamp* on
  2026-08-25 — also skips it, so the stale latch is never pruned.

So a **skipped account's latch is immortal**. `silent_accounts()` filters on
`st.get("alerting")` and therefore keeps handing every review skill a 20-day-old
`alerting: true / signalled_never_placed / risk_refused` verdict for this
account. **That is a collapsed state in the canonical sense**: the reader cannot
distinguish *"this account is alerting"* from *"this account's alarm is switched
off and this is what it last said three weeks ago"*.

It is also **wrong on its face now**: the latched cause is `risk_refused` from a
zero-balance era; the account holds $200.22 and its three refusals since arming
are `side_filter` suppressions, a different cause entirely.

⚠️ **The skip itself is CORRECT and must not be removed.** `docs/claude/system-actions.md`
records it as a deliberate operator silencing (*"Added when `alpaca_live` (real
money, 127 of 127 orders refused for zero balance) was deliberately silenced by
operator decision"*). The defect is the immortal latch, not the skip.

**Proposed fix (Tier-2, `src/runtime/`, alert-only, no order path — NOT applied
here; it ships as its own PR):** retire a skipped account's latch on each run —
`alerting: false`, `alert_disposition: "suppressed_env_skip"`, a fresh
`updated_at`, and the last graded verdict/cause/date preserved under their own
keys — and send **no** ping, because silence is the entire point of the skip. It
must not send the ordinary 🟢 `[OK]`, which would assert a recovery nobody
measured.

⚠️ **This is the likely source of the "cannot place" framing this item
inherited.** A stale `alerting: true` that survives its own release mechanism
reads exactly like a live alarm.

---

## 7. Corrections to claims I inherited, and what I did NOT verify

**Corrected — `gdx_pullback_1d` has FALLEN OUT of the reachable set.**
`config/accounts.yaml` and `docs/research/alpaca-200-affordability-sweep-2026-08-25.md`
both record the reachable set as `TLT IEF SLV USO IAUM GDX`, with GDX reachable
at 2% *"via the round-up-to-one-share relaxation"* at $74.59 / $5.28 stop.
**MEASURED 2026-09-10: GDX is $99.43 with a median stop of $7.665, and the sizer
refuses 0 of 59 → 59 of 59 of its observed long setups.** The change is price and
volatility, not code. **The documented reachable set is stale in the permissive
direction** and a session reasoning from it would propose a leg that cannot place.

**Corrected — the "FOUR legs go fully inert under long-only" claim.**
`config/accounts.yaml` records (2026-08-23, over `order_packages` 2026-05-02→
2026-08-23) that `gld_pullback_1d`, `gdx_pullback_1d`, `iaum_pullback_1d` and
`slv_pullback_1d` are *"100% short"*. **Over 2026-06-01→2026-09-10 each of those
four produced at least one LONG package** (§1.3: 1, 1, 1, 1). The original
measurement is not disputed on its own window; it is **stale**, and a session
treating those legs as permanently inert would wrongly exclude three of this
proposal's four candidates. Both readings are correct over their own populations
— which is exactly why the population has to be stated.

**Not verified by me, and flagged as inherited:** the 2026-08-25 sweep's
42-reachable-ETF list (§5.4) — I did not re-run it and its prices are 16 days
old, so treat it as a direction, not a roster; the Tier-3 rationale behind
`slv_trend_1h`'s demotion (I read the record, I did not re-derive the −$5,375);
and the operator's USO constraint, which is a standing decision and not a
measurement at all.

## 8. What this document does NOT establish

- **No candidate is shown to be profitable.** Every survivor is
  `insufficient_n` (§1.5), and three of the six have **no closed rows anywhere**.
  Option A is justified as a *plumbing and utilisation* change at $0.89–$3.90 of
  risk per trade, on the same basis the incumbent was routed. **It is not an edge
  bet and must not be reported as one.**
- **The order path is still UNTESTED on this account.** #11271 established that
  zero long signals have ever reached it. Nothing in this session changed that —
  the first fill will be the first evidence that whole-share sizing, the T+1
  gate and bracket placement behave on a live cash book.
- **Compat-matrix verdicts — one in hand, three in flight.**
  **`tlt_pullback_1d` → `alpaca_live`: ROUTE.** MEASURED, run
  [34455458131](https://github.com/benbaichmankass/Metis-Insights/actions/runs/34455458131)
  (issue #11657), 2026-09-10T08:33:20Z — pullback harness, feed `yahoo`,
  fidelity **`faithful`**, fee **0.0 bps**, **66 emitted trades** over 3650 days,
  `end_return_mean 31.8%`, `P(breach) 0.0`, `survival 1.0` against the standard
  gate (survival ≥ 0.9, P(breach) ≤ 0.1). `alpaca_portfolio` ROUTEs on the same
  ledger.
  **`slv_pullback_1d` → `alpaca_live`: ROUTE** (run `34456190565`, landed
  2026-09-10T08:38:07Z — **95 emitted trades**, `end_return_mean 15.16%`,
  `P(breach) 0.0`, `survival 1.0`).
  Read both off the committed corpus `docs/research/gld-compat-matrix-verdicts.jsonl`
  on `main`, not off a run comment.
  ⚠️ **n=66 and n=95 over ten years are ~6.6 and ~9.5 trades/year** — consistent
  with `BL-20260814-1D-EQUITY-LEGS-TRADE-4-PER-YEAR-SO-PER-LEG-OOS-25-CONSUMES-6-YEARS`,
  and each is a Monte-Carlo over a **bootstrapped** R-ledger, not that many
  independent live outcomes. So **`ROUTE` here means "clears the account's
  survival/breach gate on this ledger", NOT "has a track record"** — §1.5's
  `insufficient_n` verdict stands unchanged beside it.
  **Still outstanding:** `ief_pullback_1d` (issue #11658, run `34455459855`) and
  `iaum_pullback_1d` (run `34456197195`). Both **computed their verdicts
  successfully** (the scoring step succeeded on each) and are queued in
  `Land the corpus on main` — so they are a landing-queue wait, not a failed
  measurement. Whoever picks this up should read the corpus rather than re-run.
  ⚠️ **A compat-matrix `ROUTE` does
  NOT establish affordability** — it scores an R-ledger and never runs the
  whole-share cash wall; `gld_pullback_1h` carries `ROUTE` for `alpaca_live` in
  `docs/research/gld-compat-matrix-verdicts.jsonl` while GLD at ~$400 cannot buy
  one share. Read it beside §1.4, never instead of it.
- **`insufficient_n` may be the correct outcome for all four.** These are 1d
  equity legs; `BL-20260814-1D-EQUITY-LEGS-TRADE-4-PER-YEAR-SO-PER-LEG-OOS-25-CONSUMES-6-YEARS` records ~4
  entries/year, so even a 10-year fetch is a small n. If the matrix returns
  `insufficient_n`, that is a real answer and not a failed run.

---

## 9. Operator decisions this proposal surfaces

1. **Which option** — A (recommended), A+, or B.
2. **`side_filter: long` — keep or drop?** ⚠️ **Dropping it buys ZERO trades.**
   Alpaca reports `shorting_enabled: false` on this account, so a short is
   refused at the venue whether or not our gate suppresses it first. Keeping the
   key is strictly better: the suppression is journalled as a would-be trade and
   stays measurable, where a broker rejection is not. **Recommend: KEEP.** The
   real question behind it is decision 4.
3. **Funding.** At $200 the account cannot touch 4 of its own 11 declared
   symbols at any risk setting (§5.4). Is raising the balance on the table? It
   unlocks more than any roster edit.
4. **The short side.** 44 of 77 `tlt_pullback_1h` packages, and 57–88% of the
   other candidates' flow, is short and structurally unexecutable here. The only
   routes are inverse ETFs — already filed as
   `BL-20260823-NO-INVERSE-ETF-INSTRUMENTS-DECLARED`, and the 2026-08-25 sweep
   found 9 affordable ones — or enabling shorting/margin at the broker, which
   would end the cash-account regime and the T+1 gate with it.
5. **`SPLG` (§4).** Its candle starvation has deepened 35 → 12 bars. Retire the
   leg, repair the feed, or record it as knowingly dead.

---

## 10. Two instrument notes for the next session

**The `gld-compat-matrix` workflow's `issues.opened` trigger fired on 2 of 4
identically-formed requests.** #11657 and #11658 produced runs 1557/1558 within
a second of creation; #11659 and #11660, created in the same minute with the same
label (`get_labels` on #11659 confirms `gld-compat-matrix-request` IS attached)
and the same body shape, produced **no run at all** — no failure, no skipped run,
nothing in `actions_list`. Re-dispatched successfully via `run_workflow`
(`workflow_dispatch`, ref `main`), which returned 204 for both. **So a labelled
issue that produces no run is a real and silent state**, and the remedy is the
dispatch path rather than re-filing the issue. If you are waiting on one of these
and see nothing, check `actions_list` before assuming it is queued.

**⚠️ THE `GITHUB_TOKEN` ZERO-CHECK TRAP HAS A THIRD ENTRANCE, AND I WALKED INTO
IT WHILE DOCUMENTING IT.** `CLAUDE.md`'s prevention is *"open the PR yourself
first, then push the arming file"*, and it works — #11665 and #11666 both carry
five real check runs because of it. But it is **an ordering across two pushes**,
and the correction PR for this very document collapsed them: I amended the
arming file into the branch's FIRST commit, so `claude-pr-automerge` saw an
armed branch with no open PR, called `pulls.create` under `GITHUB_TOKEN`, and
#11667 was born with **no `pull_request` event and no CI** — the exact state
the prevention exists to avoid.

So the rule is sharper than "open it first": **the arming file must not be in
the branch's first push.** If you are amending or squashing while setting a
branch up, that is precisely when the two pushes silently become one. The
remedy is unchanged (push one ordinary commit — never an empty one), but the
cheaper move is to keep `.github/pr-automerge-requests/` out of the initial
commit entirely.

**Every compat-matrix run opens a landing PR to `main`** carrying
`docs/research/gld-compat-matrix-verdicts.jsonl` (the workflow's own header
records this, and warns that its older "does NOT push" sentence is false). Four
runs is four register-touching PRs, which is the churn the coordination board
measured on 2026-09-08. Recorded so the cost is visible rather than surprising.
