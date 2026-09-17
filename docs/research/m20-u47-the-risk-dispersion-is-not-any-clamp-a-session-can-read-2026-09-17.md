# M20 U47 — the risk dispersion is not any clamp a session can test, and naming that is the finding

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U47** · RESEARCH lane · Tier-1 · works `BL-20260913-EVERY-PROFITABLE-LEG-IS-SIZED-SMALLER-PER-UNIT-OF-RISK-THAN-EVERY-LOSING-ONE-ON-REAL-MONEY` (performance backlog, `severity: high`, `tier: 3`, `status: open`).

Instrument: [`scripts/research/risk_dispersion_attribution.py`](../../scripts/research/risk_dispersion_attribution.py)
(40 self-test controls; 27 pytest controls in
[`tests/test_risk_dispersion_attribution.py`](../../tests/test_risk_dispersion_attribution.py);
6 planted defects, 6 caught by a named control).

---

## Input provenance

* `GET https://ict-bot.duckdns.org/api/bot/performance?window=30d`, read **2026-09-17T11:4xZ**, `since: 2026-08-18T11:43:52.914895+00:00`, `error: false`.
* `GET /api/diag/journal?table=trades&limit=1000` — `rowset_digest: sha256:cf218ae83590cfa00ad40e00b097a920e04372f59f4c68e07bbe3385aa808d71`, ids 4867–5866.
* `GET /api/diag/journal?table=order_packages&limit=1000` — `rowset_digest: sha256:e2d5d71311bf3c349ef4c3bd932c51a5a43b5cec3fcc82b3ca15e586b20d38b3`.
* `GET /api/diag/log_file?name=exposure_soak&lines=1000` — 91 `bybit_2` rows, 2026-09-16T12:20Z → 2026-09-17T10:53Z.

**Population for every per-row figure:** `bybit_2`, closed, non-backtest, `pnl NOT NULL`, `closed_at ≥ 2026-08-18T11:43:52`, with a declared `risk_per_unit` and a positive `position_size` — **n = 33**. ⚠️ **The endpoint reports 41 for the same window.** The journal pull is a 1000-row tail starting at `created_at 2026-08-21`, so 8 rows opened 08-18→08-21 sit **below** it. **Coverage 33/41 = 80.5%**, and no per-leg PnL conclusion is drawn from the 33 for that reason.

---

## 1. Criterion (b) first: the separation reproduces, but this is NOT a replication

The row's second exit is *"it is measured again at materially larger n and the perfect winner/loser separation does NOT reproduce"*. On today's window:

| leg | n | pnl | R | $/R | arm |
|---|--:|--:|--:|--:|---|
| `eth_pullback_2h` | 4 | −0.330 | +0.284 | 1.161 | **ungradeable** (holds both signs) |
| `trend_donchian_eth_4h` | 7 | +14.106 | +10.375 | 1.360 | winner |
| `trend_donchian` | 5 | +7.814 | +2.627 | 2.975 | winner |
| `ict_scalp_5m` | 18 | −26.426 | −7.924 | 3.335 | loser |
| `xrp_pullback_2h` | 6 | −11.875 | −2.910 | 4.080 | loser |
| `trend_donchian_xrp_4h` | 1 | −4.819 | −1.053 | 4.577 | loser |

Winner max **2.975** < loser min **3.335**, gap +0.360, spread **3.3664×** (the row filed 3.0921×). **The separation reproduces.**

⚠️ **This does not satisfy or refute criterion (b), and must not be reported as either.** n is **41 both times** — not "materially larger" — and the two windows overlap heavily (the row's `since` was 2026-08-14, today's is 2026-08-18; both end now). Most rows are shared. The row's own caveat applies: *"it is one window and it will keep saying the same thing until the window rolls."* It has rolled four days; that is not enough.

---

## 2. The algebra that makes criterion (a) a CLOSED question

A leg's dollars-per-R is `|Σpnl / ΣR|`, and per trade `R = pnl / risk_usd`, so **dollars-per-R is a risk-weighted mean of `risk_usd`**. And `risk.py::_size_unbounded` computes `raw_qty = balance × risk_pct / risk_distance`, so

```
risk_usd = position_size × risk_per_unit = balance × risk_pct
```

**exactly**, on any row nothing clamped. `bybit_2` declares **one** `risk.risk_pct` (0.015) for every leg, so the dispersion is either a **clamp** or the **balance** — and the clamps are a finite list readable inside `position_size`. Attribution is an enumeration, not a hunt.

Realised `risk_usd` on the 33 rows spans **1.053 → 4.600**, a **4.37×** spread, with the budget estimate `B = 4.600` (the largest realised risk seen — an *estimate*, since nothing publishes the decision-time budget).

---

## 3. The enumeration, and the verdict on each

| candidate | verdict | basis |
|---|---|---|
| **per-leg risk config** | **unreachable** | the account declares ONE `risk.risk_pct` (0.015) and the sizer reads no per-strategy risk field — all 6 legs share a budget **by construction** |
| **conviction sizing** | **unreachable** | `risk.confidence_sizing` absent → `_confidence_scalar` returns 1.0; and `CONVICTION_SIZING_ACCOUNTS` is `bybit_1` |
| **whole-unit round-up** | **unreachable** | `bybit` ∉ `WHOLE_UNIT_QTY_EXCHANGES` and `market_type: linear` ≠ futures — the branch cannot run |
| **gross-exposure ceiling** | **unreachable** | `policy_declared: False` on **all 91** soak rows, `max_gross_exposure_pct: None` → `exposure_headroom_usd()` returns None |
| **instrument lot floor** | **refuted** | **33 of 33** sized rows are an EXACT multiple of their instrument lot, so flooring lost them nothing |
| **daily-loss-budget scaling** | **refuted** | Pearson **r = +0.018** against the day's realised PnL before the trade opened; and the three most under-risked rows had **zero** prior-day PnL |
| **margin pre-flight cap** | **refuted** | **r = −0.073** against the equity-free margin-bound ratio (U45); `r(stop_bp) = +0.050`; 0 of 33 carry a `margin_basis` stamp |
| **free balance / open positions** | **refuted** | **r = +0.346 with the WRONG SIGN** (more open positions → *larger* realised risk); open notional r = +0.036 |
| **post-entry rewrite (netting)** | **contributes** | 4 of 33 rows carry a netting stamp, and **the two most under-risked rows (0.229, 0.299) are both stamped** — but 2 of the 4 sit at 0.842 and 0.883 |

**Four of the nine are `unreachable` on a config or code read**, which is a stronger verdict than any correlation: the mechanism cannot run on this account at all.

### ⚠️ The one that looked like an answer and was not

Per-leg **median** realised risk orders perfectly by **lot count**:

| leg | symbol | lot step | median qty | **lots** | median risk_usd |
|---|---|--:|--:|--:|--:|
| `eth_pullback_2h` | ETHUSDT | 0.01 | 0.0200 | **2** | 1.684 |
| `trend_donchian_eth_4h` | ETHUSDT | 0.01 | 0.0300 | **3** | 2.976 |
| `trend_donchian` | BTCUSDT | 0.001 | 0.0040 | **4** | 3.608 |
| `ict_scalp_5m` | BTCUSDT | 0.001 | 0.0080 | **8** | 3.695 |
| `xrp_pullback_2h` | XRPUSDT | 0.1 | 58.5 | **585** | 4.064 |
| `trend_donchian_xrp_4h` | XRPUSDT | 0.1 | 99.6 | **996** | 4.577 |

Six of six, monotone, no exceptions. It reads as a complete attribution — and **the arithmetic refutes it**: `floor(L)/L` is **1.000 on every one of the 33 rows**, because each recorded `position_size` is already an exact multiple of its lot. Flooring lost nothing. The ordering is real and the mechanism is not; a session that stopped at the table would have shipped a wrong cause with a beautiful exhibit behind it.

---

## 4. What survives, and why it cannot be tested from here

`risk_usd = balance × 0.015` is an identity on an unclamped row, so the residual dispersion **is the decision-time `balance_usdt`** — realised/B is `balance_i / balance_max`.

⚠️ **And the observable balance does not span it.** `exposure_soak` reads `bybit_2` equity at **242.69 – 242.79** over 91 samples in 22h — a ratio of **1.000**, flat — while realised/B spans **0.229 – 1.000**. So either the balance moved far more across the 30-day window than the visible 22h shows, or a clamp outside the enumerated list is running. **Neither is established.**

**The one term that would settle it is recorded and unreadable.** `_size_unbounded`'s `balance_usdt` is `live_balances[bybit_2]` (`coordinator._default_balance_fetcher`), which is the same value `account_context_snapshots.equity` records per `(order_package_id, account_id)` (`coordinator.py:3408`). That table is **not** in `diag.py::_JOURNAL_TABLES` (measured 2026-09-17: `GET /api/diag/journal?table=account_context_snapshots` with a valid bearer → **HTTP 400**), and the Data Explorer route returns **HTTP 401** browser-direct.

**So U45's one-line Tier-2 proposal — add `account_context_snapshots` to `_JOURNAL_TABLES` — is now load-bearing for a SECOND high-severity row.** It was filed there as a cheaper alternative to a `risk.py` stamp; here it is the only thing that can finish an attribution the row asks for by name.

---

## 5. The verdict, stated as the instrument states it

`attributed_in_part`. One candidate contributes on 4 of 33 rows including the two most extreme; eight do not; the residue is unexplained and its cause is unreadable.

⚠️ **Narrowing the field is not an attribution**, and the instrument's `verdict` string says so rather than letting four `unreachable` and four `refuted` verdicts read as a result. Criterion (a) asks the dispersion be *traced to a named mechanism*; what this delivers is **the mechanism list closed, eight entries eliminated, one partial cause named, and the missing input identified**.

## 6. What this does NOT establish

* **No resize is proposed.** Per-leg risk sizing is Tier-3; `risk.py` and `config/accounts.yaml` were read only.
* **No claim about any leg's edge.** Dollars-per-R is a sizing statistic.
* **The `contributes` verdict is not a cause.** 4 stamped rows of 33, two of them at the high end — netting attribution rewrites `position_size` post-entry (MI-278 U33) and therefore corrupts `risk_usd` as a measure of what the *sizer* chose, but it cannot carry a 4.37× spread.
* **`B = 4.600` is an estimate**, so every `realised/B` is bounded by 1 **by construction**. That is fine for the correlations (scale-invariant) and must never be read as "no row exceeded its budget".
* **80.5% coverage.** 8 of the endpoint's 41 rows are below the journal tail.
