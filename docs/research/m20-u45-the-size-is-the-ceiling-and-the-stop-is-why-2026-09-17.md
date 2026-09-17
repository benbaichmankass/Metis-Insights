# M20 U45 — the order size IS the margin ceiling, and the STOP is why: a per-order proof that needs no new stamp

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U45** · RESEARCH lane · Tier-1 · works `PB-20260822-AVAX-SCALP-SIZED-OFF-MARGIN-NOT-RISK` (performance backlog, `severity: high`, `tier: 3`, `status: open`).

Instrument: [`scripts/research/margin_bound_sizing_basis.py`](../../scripts/research/margin_bound_sizing_basis.py)
(50 self-test controls; 41 pytest controls in
[`tests/test_margin_bound_sizing_basis.py`](../../tests/test_margin_bound_sizing_basis.py);
8 planted defects, 8 caught by a named control).

---

## Input provenance

Two live pulls, both read this session. The digests are declared so the next
session can tell a data change from a code change
(`scripts/research/memo_input_provenance.py`, MI-278 U26).

<!-- input-provenance
source: /api/diag/journal?table=trades&limit=1000
pulled_at: 2026-09-17T10:56Z
n_rows: 1000
id_field: id
rowset_digest: sha256:cf218ae83590cfa00ad40e00b097a920e04372f59f4c68e07bbe3385aa808d71
order_digest: sha256:a82f45fd24fd895ec4f3d9d549b6affffc5eb3062b2584d3a4c78bd6b5fc08ea
id_first: 5866
id_last: 4867
n_rows_without_id: 0
n_duplicate_ids: 0
-->

<!-- input-provenance
source: /api/diag/journal?table=order_packages&limit=1000
pulled_at: 2026-09-17T11:03Z
n_rows: 1000
id_field: id
rowset_digest: sha256:e2d5d71311bf3c349ef4c3bd932c51a5a43b5cec3fcc82b3ca15e586b20d38b3
order_digest: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
n_rows_without_id: 1000
n_duplicate_ids: 0
-->

⚠️ **The packages pull's `order_digest` is VACUOUS and the register says so
loudly rather than quietly** — `order_packages` is keyed on
`order_package_id`, not `id`, so the fingerprint tool found no id on any of the
1000 rows (`n_rows_without_id: 1000`) and the order digest is the hash of the
empty string. The **rowset digest is unaffected** and is the one this memo's
tables depend on. Recorded rather than dropped: a reader who skims
`n_rows_without_id` would otherwise compare two empty-string order digests and
conclude two pulls were identically ordered.

**Journal span**: ids 4867–5866, `created_at` 2026-08-21T06:00:57Z →
2026-09-17T10:03:26Z. **Account**: `bybit_1` (`account_class: paper`,
`mode: live`, `market_type: linear`), which declares `risk.risk_pct 0.015` and
`risk.leverage 3` and no `confidence_sizing` key.

---

## 1. What the row asks for, and why this lane cannot give it

`PB-20260822`'s `resolution_criteria` opens:

> notes.margin_basis records the PRE-CLAMP risk-derived qty beside
> max_qty_by_margin, so 'the risk model asked for X and got the ceiling
> instead' is readable per order rather than inferable from two rows.

That is a stamp inside `src/units/accounts/risk.py` — **Tier-2**, and outside
this lane. So the unit asked the prior question: **how much of that sentence is
already readable per order?**

---

## 2. The answer — an equity-free, per-order proof (MEASURED)

The risk sizer asks for a notional of `equity × risk_pct × entry /
risk_per_unit` (`risk.py::_size_unbounded`, `raw_qty = balance × risk_pct /
risk_distance`). The margin pre-flight cap allows `basis_usd × leverage ×
buffer` (`risk.py::position_size`). **Every basis the sizer may use is at most
the account's equity** — `venue_available` is equity minus pledged margin,
`equity_unadjusted` is equity, `free_balance` is below it — so

```
demanded_notional      risk_pct × entry
-----------------  ≥  ---------------------------------
ceiling_notional       risk_per_unit × leverage × buffer
```

and **the equity term cancels**. When that ratio is `≥ 1` the margin cap MUST
bind, whatever the balance. Equivalently, the cap binds whenever the declared
initial stop is closer than

```
10000 × risk_pct / (leverage × buffer)  =  55.56 bp    (0.015, 3×, 0.9)
```

`buffer` is `risk.py::_MARGIN_SAFETY_BUFFER`, **imported by the instrument, not
copied**, so a change to the constant moves this measurement with it.

### The census, `bybit_1`, sizer-output rows only

| | n |
|---|--:|
| journal rows in the pull | 1000 |
| on `bybit_1`, non-backtest | 707 |
| of those, `position_size` written by `RiskManager.position_size` | **387** |
| — `provably_margin_bound` (ratio ≥ 1, at any balance) | **43** |
| — `observed_at_ceiling` (its own stamp says it reached the cap) | 22 |
| — `risk_derived` (stamp present, cap not reached) | **2** |
| — `not_provable` (*we did not look*) | 207 |
| — `no_basis` (no usable order size, or no declared initial risk) | 113 |
| **gradeable** (everything but `no_basis`) | **274** |
| **provableCoverage** | **0.2445** |

⚠️ **`provableCoverage` is the instrument's honesty metric and the bound rate
must never be quoted without it.** The inequality is slack by exactly
`equity / basis_usd`, and on a book holding positions the venue's *available*
margin is a fraction of equity — so `not_provable` is ***we did not look***,
never *risk-derived*, and 43/274 is a **floor**, not a rate.

⚠️ **SIGNAL GEOMETRY IS NOT ORDER SIZE, and this memo's first draft conflated
them.** 113 of the 387 rows carry a usable entry and declared stop but a
`position_size` of **0.0** — a per-trade refusal, or an intent-layer drop
before sizing ran. **19 of those 113 would have been provably margin-bound had
they been sized.** They are reported separately and never pooled: a signal that
was never sized is not an order whose size was the ceiling. The first cut of
the per-strategy table did pool them, and reported **62** provably-bound orders
against a census of **43** — a disagreement nothing in the output surfaced. It
was caught by summing the table, not by reading it, and the instrument now
carries the reconciliation as a printed field (`per-strategy n 274 vs gradeable
274`, `per-strategy bound 43 vs census 43`, `OK`) plus three pytest controls
that fail if the two populations diverge again.

### The control — the prediction against each row's OWN stamped ceiling

Population: the 32 `bybit_1` rows carrying both a `notes.margin_basis` stamp
and a declared `risk_per_unit`.

| | n |
|---|--:|
| predicted bound **and** at ceiling | 8 |
| predicted bound **and NOT** at ceiling — *false positives* | **0** |
| not predicted **but** at ceiling — *expected slack* | 22 |
| not predicted **and** not at ceiling | 2 |

**Zero false positives** on n=32. The inequality is sound on this pull and
loose by a factor the coverage figure reports. A false NEGATIVE is expected;
a false POSITIVE would falsify it, and the instrument names the ids if one
appears.

---

## 3. The finding the row does not contain: this is a SCALP-FAMILY property, not an AVAX one

Per-strategy, `bybit_1`, gradeable sizer-output rows (n=274), sorted by
provably-bound share:

| strategy | n | bound | % | median ratio | max ratio | stop bp p25 / med / min |
|---|--:|--:|--:|--:|--:|---|
| `ict_scalp_5m` (BTC) | 12 | 6 | **50.0** | **1.003** | 2.05 | 46.18 / 55.54 / 27.05 |
| `ict_scalp_xrp_5m` | 11 | 4 | 36.4 | 0.823 | 1.95 | 47.12 / 67.49 / 28.50 |
| `ict_scalp_sol_5m` | 28 | 8 | 28.6 | 0.701 | 1.48 | 54.49 / 79.39 / 37.58 |
| `ict_scalp_avax_5m` | 71 | 19 | 26.8 | 0.756 | **45.60** | 52.55 / 73.46 / **1.22** |
| `ict_scalp_eth_15m` | 16 | 4 | 25.0 | 0.531 | 1.97 | 78.01 / 104.60 / 28.22 |
| `fade_breakout_4h` | 5 | 1 | 20.0 | 0.512 | 1.02 | 58.30 / 108.56 / 54.71 |
| `ict_scalp_xrp_15m` | 10 | 1 | 10.0 | 0.389 | 4.62 | 107.88 / 144.86 / 12.03 |
| `ict_scalp_sol_15m` | 16 | 0 | 0.0 | 0.348 | 0.72 | 114.16 / 165.71 / 77.27 |
| **the other 15 legs** (every `*_pullback_*`, every `trend_donchian*`, `htf_pullback_trend_2h`, `squeeze_breakout_4h`) | **105** | **0** | **0.0** | 0.117–0.509 | ≤ 0.768 | min 72.36 |

**The split is the finding.** Aggregated:

| family | legs | n | provably bound |
|---|--:|--:|--:|
| `ict_scalp_*` | 7 | 164 | **42** |
| everything else on the account | 16 | 110 | **1** |

Two things follow, and they matter more than the AVAX headline:

1. **`ict_scalp_5m` on BTC is proportionally worse than the leg the row was
   filed about** — 50.0% of its gradeable orders are provably margin-bound and
   its **median** ratio is 1.003, i.e. the median order sits fractionally past
   the point where the cap must bind. The row names one leg; the condition is a
   property of the **5m/15m scalp family**.
2. **Forty-two of the 43 bound orders are scalp orders.** Not one pullback or
   donchian leg is bound at any ratio; their stops sit at 72–476 bp, outside
   the 55.56 bp binding distance, and their highest ratio anywhere is 0.768.
   The difference between the two groups is the **stop width** — not the
   account, the leverage, or the margin.

## 4. The headline for the operator: the size is degenerate because the STOP is

The worst row in the pull, `bybit_1` / `ict_scalp_avax_5m` trade **id 5653**,
2026-09-10T17:40:42Z, `status: exchange_rejected`:

* entry **7.621**, declared `risk_per_unit` **0.00092857** — a **1.22 bp** stop
* margin-bound ratio **45.60**, i.e. the risk model demands **45.6×** what the
  margin cap allows, **at any balance**

The ratio is equity-free, which is the point; the dollar figures below are an
ILLUSTRATION at the only equity reading this session could obtain
(`exposure_soak`, `bybit_1`, 2026-09-16/17: **134,164–135,480**, n=90). ⚠️ That
is **NOT** a measurement of what the account held on 2026-09-10 — the soak log
is capped at 1000 lines ≈ 22h and no reading from that day survives. At
$134,500:

* demanded notional `134,500 × 0.015 / 0.00012184` ≈ **$16.6M — 123× equity**
* the ceiling at 3× with the 0.9 buffer ≈ **$363k**

For the margin cap to pass that order the account would need roughly **137×**
leverage (`3 × 45.6`). The ceiling is not mis-sizing this trade; it is the only
thing standing between this leg and a nonsense order — which is precisely why
the row warns that *fixing* the venue clamp converts refusals into
ceiling-sized FILLS.

### Where the degenerate stop comes from (read at the field, `src/units/strategies/ict_scalp.py:508-523`)

```python
sl_buffer = float(params["atr_sl_buffer_mult"]) * atr_now      # 0.20 × ATR
sl   = sweep["extreme"] - sl_buffer                            # long
risk = entry - sl
if risk <= 0:
    raise ValueError(...)                                      # THE ONLY GUARD
```

The stop is the swept extreme plus a 0.2×ATR buffer, and **the only floor on
`risk` is that it be positive**. When the entry (`last_close`) sits essentially
on the swept extreme, `risk` collapses toward `0.2 × ATR` and then toward zero —
and `_size_unbounded` divides by it. No `min_stop_bps` or equivalent parameter
exists on any `ict_scalp` leg in `config/strategies.yaml`.

⚠️ **But the degenerate case is RARE, and that matters for the remedy.** Over
the 274 `bybit_1` `ict_scalp_*` rows carrying a declared risk, `risk / ATR` is
median **3.30** and only **5 rows (1.8%)** fall below 0.25×ATR. So the typical
stop is set by the distance to the swept extreme, not by the buffer, and an
**ATR floor is the wrong instrument for the margin problem**: a 1.0×ATR floor
touches 14 of 274 rows (5.1%) and only 14 of the 65 bound ones. The quantity
the margin cap keys on is **basis points of price**, and a bp floor at the
binding distance touches 65 of those same 274 rows (23.7%) by construction.
⚠️ **State which 274.** This paragraph's population is *`bybit_1` `ict_scalp_*`
rows carrying a declared `risk_per_unit`*, which INCLUDES the signal-geometry
rows whose `position_size` is 0.0. The §2/§3 census counts only rows that were
actually sized, where the scalp family is **42 bound of 164** (25.6%). The two
denominators answer different questions and coincidentally share a size.

⚠️ **AND ANY FLOOR ON `risk` ALSO MOVES THE TARGET**, because
`tp = entry ± tp_at_r × risk` three lines below. Widening the stop is not a
pure risk change — it re-geometries the trade. Refusing the signal instead is
the Prime-Directive-shaped alternative and does not.

---

## 5. ⚠️ DOES IT MATTER? THE POOLED ANSWER REVERSES UNDER A WITHIN-LEG CONTROL — so no Tier-3 change is proposed

The obvious next step is to ask whether the margin-bound orders do worse. Run
naively, the answer looks emphatic. Population: closed, non-backtest,
sizer-output `bybit_1` `ict_scalp_*` rows carrying a `pnl` and a declared
initial risk, n=130.

| pooled arm | n | pnl | MEASURED-only | pnlCoverage | wins | median realised risk_usd |
|---|--:|--:|--:|--:|--:|--:|
| provably bound | 34 | **+1,013.93** | +216.94 (n=23) | 0.6765 | 18 | 387.25 |
| not provable | 96 | **−10,842.22** | −11,875.62 (n=51) | 0.5312 | 45 | 866.80 |

**That comparison does not survive its own control.** One leg,
`ict_scalp_sol_15m`, has **zero** bound rows and contributes **−5,045.80** —
47% of the unbound loss — to the unbound arm alone. Per leg, restricted to the
five where both arms reach n≥3, on the MEASURED-only basis:

| leg | bound n / MEASURED pnl | unbound n / MEASURED pnl | favours |
|---|---|---|---|
| `ict_scalp_avax_5m` | 11 / **+266.93** (n=10) | 29 / −4,655.09 (n=21) | bound |
| `ict_scalp_eth_15m` | 4 / **+101.54** (n=2) | 12 / −5,912.03 (n=9) | bound |
| `ict_scalp_sol_5m` | 8 / −1,492.93 (n=5) | 20 / −2,731.50 (n=9) | bound (both negative) |
| `ict_scalp_5m` | 6 / −337.04 (n=2) | 6 / −331.74 (n=1) | tied |
| `ict_scalp_xrp_5m` | 4 / +1,833.21 (n=3) | 6 / **+4,306.74** (n=5) | unbound |

**VERDICT: not established, in either direction.** Three of five comparable
legs lean toward the bound arm, one is tied and one leans against, at **1–10
MEASURED rows per arm** — far too thin to carry a Tier-3 change, on a **paper**
account over 27 days. What the pooled row mostly measures is which legs happen
to produce tight stops.

⚠️ **THIS WITHDRAWS THE OBVIOUS REMEDY, WHICH THIS MEMO'S OWN §4 DRAFTED.** A
minimum stop distance widens `risk`, which *lowers* the demanded quantity until
the cap stops binding — i.e. it **removes the size clamp from the cohort that,
on the only measurement available, is not the losing one.** Proposing it on
this evidence would be arguing against the data. The canonical rule against
lowering a bar to manufacture a verdict cuts the same way here: the honest next
step is to **sweep** a stop floor offline and decide from that, not to set one
from an inference.

⚠️ **AND NOTE WHAT THE CLAMP IS DOING MEANWHILE.** Median realised `risk_usd`
(`position_size × risk_per_unit`) is **387** on the bound cohort against **867**
on the rest, while the account's declared per-trade budget at the observed
equity is ≈ **$2,000** (0.015 × ~134k). On the bound cohort the per-trade risk
is being set by *available margin*, not by policy — an undeclared,
balance-dependent limiter. Whether that is acceptable is the decision in §8.

⚠️ `risk_usd` here is the **realised** (post-clamp) risk, not the intended one;
reading it as the sizer's choice is the same conflation this memo spends §2 and
§6 refusing.

The block is reproducible: `--outcomes` on the instrument prints the pooled
arms and the per-leg control **together**, and emits no verdict of its own.

---

## 6. Two methodological findings, one of which WITHDRAWS this lane's own earlier claim

### 6a. `order_packages.sl` is amended too, and `risk_per_unit` is the decision-time truth

`r_provenance`'s module docstring already records that `trades.stop_loss` holds
the *current* stop. **The same is true of `order_packages.sl`**, and this memo
measures it. Over the 677 packages in the pull carrying a declared
`risk_per_unit`:

| | n | % |
|---|--:|--:|
| `\|entry − sl\| == risk_per_unit` (unamended) | 543 | 80.2 |
| `\|entry − sl\| <  risk_per_unit` (trailed toward entry) | 123 | 18.2 |
| `\|entry − sl\| >  risk_per_unit` | 11 | 1.6 |
| — of those, stop on the **profit side** of entry | **11** | **100** |
| partition sums to population | 677 = 677 | ✓ |

All 11 of the "wider" cases are stops trailed **past** entry — a long whose
stop is above its entry, a short whose stop is below it. So `|entry − sl|` is
not a risk distance in either direction once trailing has run, and **one order
in five would be mis-measured by using it**. `signal_logic` / `meta`'s
`risk_per_unit` is written once by the strategy at signal time (9 files, all
`"risk_per_unit": float(risk)`; every other reference in `src/` is a read), and
the instrument reads it through the one owner,
`r_provenance.declared_initial_risk`.

*(Aside, recorded because it cost a minute: `meta` and `signal_logic` carry the
same blob on 676 of 1000 package rows and **one** row carries `risk_per_unit` in
`meta` only. The two are not byte-identical everywhere.)*

### 6b. ⚠️ WITHDRAWN — this lane's paused 2026-09-13 note claimed a positive control that passed for the wrong reason

The note left on `WO-20260912-…`'s `progress.U45` proposed reconstructing
decision-time equity by inverting `position_size × |entry − sl| / 0.015`, and
recorded that on 2026-09-12 it gave **133,868** against a measured equity of
133,217–134,506 — *"they agree to ~0.5%, so the method looks verified"*. It also
flagged, correctly, that it had not yet been asserted as a control.

**Re-run this session, that agreement is a coincidence between two errors.**
The 09-12 maximum came from trade id **5710**, `pairs_sol_eth_a`:
`position_size 19.7`, `entry 101.93`, `stop_loss 203.86`, direction `short`.

* The **pairs sleeve is an isolated order path** —
  `src/units/strategies/pairs_executor.py`, called once per tick from
  `src/main.py`, *"never through `multi_account_execute`"* by its own module
  docstring — so `RiskManager.position_size` never produced that quantity.
* Its stop is a **sentinel at 2× entry**, not a risk level.

`19.7 × |101.93 − 203.86| / 0.015 = 133,868`. The number agreed with the
account's equity to 0.5% for no reason at all. The note's 09-11 p90 of 132,211
is the same class: three `adopted_orphan` rows (ids 5715, 5719, 5725) that are
the *same* re-adopted trade, whose size was read off the venue.

**The three populations that must be excluded, and are, by the instrument's
`qty_provenance` axis:** `intent_reduce` (the intent multiplexer's reduction
qty), `adopted_orphan` (a venue-read size), and the pairs sleeve. On this pull
they are 21 + 15 + 284 = 320 of the 707 `bybit_1` rows.

**With them excluded and `risk_per_unit` substituted for `|entry − sl|`, the
inversion is a valid LOWER BOUND and a poor estimator.** Against the only two
days for which measured equity survives in `exposure_soak` (the log is
hard-capped at 1000 lines ≈ 22h):

| day | inversion max | measured equity (n) | recovered |
|---|--:|---|--:|
| 2026-09-16 | 104,753 | 134,348–135,118 (44) | 0.775 |
| 2026-09-17 | 12,286 | 134,164–135,480 (46) | 0.091 |

So the equity-reconstruction route is **abandoned** as an estimator, and the
equity-free ratio in §2 replaces it. ⚠️ **The note's "09-04 outlier of
8,531,596" needs no exclusion rule** — it was an artifact of the same defect
(a profit-side trailed stop inflating `|entry − sl|`) and does not exist once
`risk_per_unit` is used. Do not re-derive an outlier rule for it.

---

## 7. The row's stated blocker is real but mis-diagnosed

`PB-20260822` says the pre-clamp risk qty *"is not currently recorded"* and
proposes a `risk.py` stamp. Both halves of that need correcting:

* **The equity term IS already recorded.** `_size_unbounded`'s `balance_usdt` is
  `live_balances[bybit_1]` (`coordinator._default_balance_fetcher`), which is the
  same value `account_context_snapshots.equity` records
  (`coordinator.py:3408` reads `live_balances.get`) per
  `(order_package_id, account_id)`.
* **What is missing is a READ SURFACE, not a write.** `_JOURNAL_TABLES`
  (`src/web/api/routers/diag.py:62`) admits four tables — `order_packages`,
  `trades`, `insights_history`, `insights_usage` — and
  `account_context_snapshots` is not one of them. **Both closures re-measured
  against the live API on 2026-09-17**, not inherited from the paused note:
  `GET /api/diag/journal?table=account_context_snapshots` with a valid bearer
  returns **HTTP 400** (the table is refused, so the web-api is up and the
  request is the problem), and
  `GET /api/bot/db/table/account_context_snapshots` browser-direct returns
  **HTTP 401**. `exposure_soak` carries a per-account `equity` and is capped at
  1000 lines, which on this fleet is ~22h.

**Adding one table name to `_JOURNAL_TABLES` is a smaller Tier-2 change than
the stamp the row proposes, and would make the exact pre-clamp quantity
computable for every order.** It is **proposed, not applied** — `src/` is
Tier-2 and outside this lane.

---

## 8. What this does NOT establish, and the decision it leaves open

* It does **not** compute the pre-clamp risk qty. It proves the cap binds for a
  subset and reports the coverage of that subset honestly.
* It does **not** propose a disposition for any leg. `ict_scalp_avax_5m`'s
  record is not re-measured here, and *tune before demote* applies regardless.
* It says nothing about **real money**. `bybit_1` is `account_class: paper`.
  The only other account running an `ict_scalp` leg is `ib_paper`
  (`ict_scalp_mgc_15m`), which is `market_type: futures` — where
  `position_size` skips the crypto margin cap entirely, so this instrument
  **refuses** rather than grading it. Whether the same degenerate-stop shape
  reaches a real-money book is a separate question this pull cannot answer.
* The control is **sound on this pull**, which is a statement about 32 rows on
  one account on one day, not a standing property.

### The decision this leaves with the operator

Filed as `DEC-20260917-SCALP-MARGIN-BOUND-SIZING` on
[`WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER`](../claude/work/objects/WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER.yaml).
In short:

**Established.** On **42 of the 164 gradeable sizer-output `ict_scalp_*`
orders (25.6%)** in this pull — `bybit_1`, non-backtest, ids 4867–5866,
2026-08-21 → 2026-09-17 — the per-trade size is set by available margin rather
than by the account's declared `risk.risk_pct` of 0.015. It is undeclared,
balance-dependent (the ceiling moves with the book), and invisible on every
surface — the stamp that records it ships on refusal rows only. ⚠️ And the
figure is a FLOOR: the equity-free test is sufficient, not necessary, so the
true share is higher by an unknown amount (`provableCoverage` 0.2445 over the
whole account).

**Not established.** Whether that is costing or saving money. §5.

**The options, and none of them is "add a stop floor now":**

1. **Leave it, knowingly** — record that per-trade risk on this cohort is set by
   available margin, and that the figure moves with the book. A legitimate
   outcome; it must be a recorded decision rather than a default.
2. **Measure first** — run an M8 sweep of a `min_stop_bps` floor on the
   `ict_scalp` family offline and decide from the sweep. This is the
   recommendation: the live evidence points the opposite way to the intuition,
   and the repo's own rule is that a bar is not set from an inference.
3. **Make it observable first (Tier-2, cheap)** — add
   `account_context_snapshots` to `diag.py::_JOURNAL_TABLES`, which makes the
   exact pre-clamp quantity computable per order and would satisfy the row's
   first `resolution_criteria` conjunct without the `risk.py` stamp it asks for.
   Independent of 1 and 2.

**What must NOT happen**, and the row says so in terms: do not widen the margin
cap. On the worst order in the pull the demand is already 45.6× the ceiling.
