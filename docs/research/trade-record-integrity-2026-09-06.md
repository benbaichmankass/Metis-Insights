# Does our record of a trade say what actually happened?

**MI-144** · object `WO-20260906-THE-RECORD-OF-A-TRADE-DOES-NOT` · intent
`IN-20260903-TRADING-SYSTEM-HEALTH` · PR #11131 · 2026-09-06

> Operator, 2026-09-06: *"we're still having a lot of problems with wiring and
> the mechanics and making sure that we're actually measuring the correct
> things, and that's obviously top priority because we can't make good decisions
> if we have bad tools for making those decisions."*

---

## The measurement, and its positive control

**MEASURED.** The whole journal was pulled to this container on **2026-09-06**
via `GET /api/bot/db/table/trades` and `…/order_packages` (paged on `offset`) —
**5518 trade rows and 4435 order-package rows**, saved and analysed off-VM.

Every figure below is over the population `/api/bot/performance` itself uses:
`status='closed'` · non-backtest · `pnl IS NOT NULL` · minus `orphan_adopt`,
`reconcile_status='superseded'` and `exit_reason='exchange_reset_flat'`.

**The control ran first, and it is the reason to believe anything that
follows.** Recomputing the endpoint's own headline fields from the dump
reproduced them to the last digit:

| block | n | wins | totalPnl | totalR | source |
|---|--:|--:|--:|--:|---|
| real money, `window=all` | 424 | 117 | −69.5315 | −72.7916 | live endpoint |
| real money, `window=all` | 424 | 117 | −69.5315 | −72.7916 | this dump |
| real money, `window=30d` | 39 | 15 | −3.6266 | +38.2891 | live endpoint |
| real money, `window=30d` | 39 | 15 | −3.6266 | +38.2891 | this dump |
| paper, `window=all` | 863 | — | +124653.4793 | +4454.6991 | both |

Without that step the decomposition would describe a lookalike population
rather than the instrument.

---

## (a) R was computed against the wrong risk — **LANDED, Tier-1**

### The mechanism

`_clean_trades.r_multiple` computes `pnl / (|entry − stop| · |qty| · contract_value)`.
Two things are wrong with that denominator:

1. **`trades.stop_loss` is the FINAL stop, not the initial one.**
   `order_monitor._apply_update` mirrors every confirmed trailing amend onto the
   row — correct for `/api/bot/positions`, which must show where the stop *is* —
   but R is *defined* against the risk the trade was **entered** with. A stop
   trailed to breakeven collapses `|entry − stop|` toward zero and `pnl / risk`
   explodes.
2. **`abs()` turned an impossible risk into a plausible one.** A long whose stop
   sits *above* entry is not a risk level, it is locked-in profit. `abs()` gave
   it a small positive denominator and a finite, enormous R instead of refusing
   it.

### What it cost — three windows, all live on 2026-09-06

| window | n | published `expectancyR` | `profitFactor` | `totalPnl` | verdict |
|---|--:|--:|--:|--:|---|
| real money `30d` | 39 | **+0.9818** | 0.9507 | **−$3.63** | a losing window publishing positive R |
| `paperPortfolio` | 87 | **+0.6253** | 0.7770 | **−$11,244.87** | same, on a 100%-declared-risk population |
| whole journal | 1287 | totalR **+4381.91** | — | — | **104 rows (8.1%) carry 96.6% of it** |

Single-row maximum: **R = +3672.3** (`ict_scalp_sol_15m`, `bybit_1`).

A concrete row, so the mechanism is not abstract — trade **5295**,
`uso_trend_1h` USO long, entry 136.53, stop **136.45** (8 cents — trailed to
near-breakeven), pnl +$1,793.32:

* published R **+27.24**
* against the risk the signal actually declared: **+0.954**

A 1R winner published as a 27R winner. **R feeds the promotion gates.**

### The fix

`src/runtime/r_provenance.py` gains `initial_risk_usd()` / `r_multiple_provenanced()`
with four bases that never collapse:

| basis | what it means |
|---|---|
| `declared_initial` | the signal's own `risk_per_unit` from `order_packages.meta` — **preferred**, because no trailing amend can reach it |
| `stored_stop` | `\|entry − stop\|`, only when no declared record exists AND the stop is not PROVEN wrong-side. Byte-for-byte the legacy behaviour |
| `refused_wrong_side` | **refused.** Counts in neither the R numerator nor its denominator |
| `no_basis` | no R to compute (missing price/size, non-positive risk) |

`/api/bot/performance` publishes `rBasis` (aggregate **and** per-strategy), so no
published R is over an unstated population.

⚠️ **The refusal uses `classify_r`, not a bare side test**, so a
direction-mirrored `intent_reduce` row — whose whole bracket is inverted
relative to its own `direction` — keeps its stored basis instead of being
refused for a trail it never had.

### What changes, re-measured with the shipped function

| block | n | before | after | `profitFactor` | rBasis |
|---|--:|--:|--:|--:|---|
| real money `all` | 424 | −0.1717 | **−0.3152** | 0.7294 | declared 103 · stored 321 |
| real money `30d` | 39 | **+0.9818** | **+0.1772** | 0.9507 | declared 39 |
| `paperPortfolio` | 87 | **+0.6253** | **−0.2736** | 0.7770 | declared 87 |
| paper `all` | 863 | +5.1619 | **−0.1323** | 1.4066 | declared 501 · stored 362 |

**Cross-check:** on the `stored_stop` basis the new function agreed with the
legacy helper on **683 of 683** live rows, and a test pins that equality so the
two arithmetics cannot drift.

### ⚠️ "the sign now agrees with `profitFactor`" is NOT a general acceptance test

The object asks for it and it must be answered honestly rather than fitted.

* On **`paperPortfolio`** — the exhibit where the inversion was *mechanically*
  caused by contamination, and where declared-risk coverage is 87 of 87 rows —
  the sign **does** now agree: +0.6253 → −0.2736 against `profitFactor` 0.777 and a book
  that lost $11,244.87.
* On **real-money `30d`** the magnitude fell 5.5× (+0.9818 → +0.1772) but the
  sign did **not** flip. All 39 rows use the declared basis, so the residual is
  not stop contamination. `profitFactor` is 0.9507 — essentially break-even in
  USD — and R weights each trade by its **own risk** while USD does not. Two
  differently-weighted averages of a near-zero quantity need not share a sign.
* On **paper `all`** the corrected R (−0.1323) now *disagrees* with
  `profitFactor` 1.4066. That is the instrument working, not a regression:
  **that block's entire +$124,653 net PnL is dominated by ONE row** — id 4773,
  `ict_scalp_mgc_15m` MGC, **pnl +$249,185.00**, i.e. 200% of the block's net,
  and its `pnlProvenance` is `estimated`, not a broker fill. A `profitFactor`
  resting on a single estimated mark is not a trustworthy sign reference.

**So the acceptance test that actually holds is the one stated per-window
above, with its population — not a blanket "R and PF must agree".** Forcing
agreement everywhere would be fitting the instrument to a desired answer.

---

## (b) `exit_reason` is frozen at the one moment the answer cannot be known — **read-path derivation LANDED (Tier-1); journal re-labelling PROPOSED (Tier-2)**

`order_monitor._close_trade_from_order_status`'s no-record fallback hard-codes
`exit_reason='reconciler_filled'` with `exit_price` still NULL — correctly, since
no price exists yet. `_sweep_pending_pnl_from_bybit` re-runs the classifier when
the price later arrives (#10262, `BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE`),
but that is **one path, forward only**. Every row closed before it shipped, and
every row closed by a path it does not cover, still carries its birth label.

`src/runtime/bracket_outcome.py` re-derives the verdict from the **recorded exit
price** against the package's own `sl`/`tp`, using the same conservative
inequality (`<=` / `>=`, because fills slip *through* a level) that
`order_monitor._classify_broker_exit` uses — pinned by a test that runs both
classifiers over the same case table. `/api/bot/performance` publishes it as
`bracketOutcome`.

**Nothing is written back.** A producer-authored label (`vwap_cross`,
`pairs_stop`, `exit_head`) records a real decision, and a re-derivation must not
overwrite a better record than the one being written.

### THE ANSWER: are trades ending at their brackets?

**MEASURED** on the dump above, with the shipped classifier.

| population | n | gradeable | **reached a bracket** | sl | tp | mid-bracket | stored label says `sl\|tp` |
|---|--:|--:|--:|--:|--:|--:|--:|
| **real money, all** | 424 | 420 | **228 (54.3%)** | 187 | 41 | 192 (45.7%) | 127 (30.0%) |
| all accounts, all | 1287 | 1125 | 430 (38.2%) | 345 | 85 | 695 (61.8%) | 251 (19.5%) |
| all accounts, last 200 | 200 | 200 | 46 (23.0%) | 39 | 7 | 154 (77.0%) | 27 (13.5%) |
| last 200, ex-pairs | 112 | 112 | 46 (41.1%) | 39 | 7 | 66 (58.9%) | 27 (24.1%) |

**On real money, a little over half of closes reach a declared bracket, and the
stop side outnumbers the target side 4.6 : 1** (187 sl vs 41 tp). The stored
label reports 30.0% — it understates bracket exits by roughly half, in every
window measured.

`mid_bracket` is an **outcome, not a defect**: `vwap` (318 gradeable rows),
`exit_head` and `time_decay` exit deliberately before a bracket. Reading that
bucket as failure would be as wrong as reading the stored label as truth.

⚠️ **One caveat that must travel with the all-accounts numbers.** All **301**
gradeable `pairs_*` legs grade `mid_bracket` — 0% reached, on every one of the
four pairs strategies. A pairs leg's stop is on the **spread**, not the leg
price, so per-leg `sl`/`tp` is very likely the wrong yardstick there rather than
evidence the sleeve never reaches its stops. This is filed rather than assumed
(`BL-20260906-PAIRS-LEGS-GRADED-AGAINST-A-PER-LEG-BRACKET-THAT-MAY-NOT-BE-THEIR-STOP`).
The real-money row above is the cleanest figure: 4 non-gradeable of 424.

**PROPOSED, Tier-2 (not applied):** a one-shot re-classification pass over the
historical `reconciler_filled` / empty-label rows that now carry a measurable
exit price, stamping `exit_reason_source` so a re-labelled row stays
distinguishable from an originally-classified one. And the duplication removal
— `_classify_broker_exit` calling `classify_bracket_outcome` instead of keeping
its own copy of the inequality. Both touch the journal writer.

---

## (c) The closed-trades route emptied instead of refusing — **LANDED, Tier-1**

### ⚠️ The briefed symptom did not reproduce, and that matters

**MEASURED**, live endpoint `https://ict-bot.duckdns.org/api/bot/trades/closed`,
read 2026-09-06:

| request | result |
|---|---|
| `limit=5` / `100` / `200` | HTTP 200, 5 / 100 / 200 rows |
| `limit=201` / `400` / `800` | **HTTP 422**, `"Input should be less than or equal to 200"` |
| `since=2026-09-01` | HTTP 200, 142 rows |
| `since=2026-01-01` | HTTP 200, 200 rows (capped by `limit`) |

The route did **not** silently return `[]` above the cap — it refused, naming
the bound. The "400→0, 800→0" reading is what a client that coerces a 422 body
into `[]`/`0` produces: the `curl … || echo '{}'` shape `CLAUDE.md` names as
unprovenanced-diagnostic sub-class **C**, an unasserted denominator. Recording
this so it is not re-derived.

### The real defects, both fixed

1. **The bare `[]` IS there — in the `except` handlers.** Missing DB, locked DB,
   sqlite error and any unexpected exception each returned `[]`, and the
   docstring said so as a feature. On the route a performance review grades
   from, an unreadable journal rendering as *"no closed trades yet"* is a clean,
   confident, wrong negative: § "Collapsed states" applied to a whole endpoint.
   Now **503 + a machine-readable `reason`** (`db_file_missing` ·
   `db_operational` · `db_read_failed` · `unexpected_error` — four remedies,
   four messages).
2. **The window above the cap was UNREACHABLE.** Raising the cap is explicitly
   not the fix. The route gains **`offset`**, plus **`X-Total-Count`** and
   **`X-Has-More`** headers so a full page is distinguishable from an exhausted
   one. Headers, not a body wrapper — the SPA's `ClosedTrade[]` contract is an
   array.

An empty list now means exactly one thing: *we looked and there is nothing.*

### The affected population, since this was found by accident

**MEASURED** by AST over `src/web/api/routers/*.py` (a probe with a positive
control: it also finds the 12 sites that carry an `allow-silent` justification,
so its silence elsewhere is meaningful): **28 empty-collection returns inside
`except` handlers, of which 16 carry no justification.** Two were
`trades_closed.py` and are fixed here. The remaining **14** span
`backtests.py` (2), `bot_config.py` (2), `dashboard.py` (3), `diag.py` (2),
`notifications.py` (1), `strategies.py` (1), `strategy_review.py` (1),
`training_center.py` (1), `work.py` (1) — filed as
`BL-20260906-FOURTEEN-READ-ROUTES-STILL-RETURN-A-BARE-EMPTY-ON-ERROR` rather
than swept blind, because each needs its own consumer-contract judgement.

Note also that `backtests.py:175` justifies its own silence by citing
*"the same contract as trades_closed.py"* — that comment is now stale. **Field
beats comment.**

---

## Tiering

| item | tier | argument | disposition |
|---|---|---|---|
| (a) R denominator + `rBasis` | **1** | `/api/bot/performance` is a read/observability path. No journal writer, order path, `config/**`, or risk cap is touched; nothing places, modifies or refuses an order. The promotion gate is a decision a human makes *from* these numbers — making them correct is the point of the cycle priority, not a change to the gate | **LANDED** |
| (b) `bracketOutcome` read-path derivation | **1** | new pure module + one read route; writes nothing | **LANDED** |
| (b) historical re-label of `exit_reason` | **2** | a journal DB write | **PROPOSED** |
| (b) `_classify_broker_exit` → `classify_bracket_outcome` | **2** | edits the close/label writer path | **PROPOSED** |
| (c) refusal + paging on `/trades/closed` | **1** | read route; response *shape* unchanged (still an array) | **LANDED** |
| (c) the other 14 bare-empty sites | **1** each | but each needs its own consumer-contract call | **FILED** |

---

## Post-deploy observation — 2026-09-06T15:05Z

PR #11131 merged as `44cc060b` (15:01Z) and reached the fleet at 15:05:17Z:
`/api/diag/version` reads `git_sha 4c00c120 == git_sha_on_disk`,
`restart_pending false`, and `44cc060b` is an ancestor of that sha on `main`.

**The content test is the endpoint, not the sha.** Before the deploy,
`/api/bot/performance` answered HTTP 200 carrying the *old* key set with no
`rBasis` and no `bracketOutcome` — a positive control establishing that the
absence was real absence and not an unreachable probe. After it, both blocks
are served.

### (a) R — the arithmetic, checked rather than eyeballed

| window / block | rBasis (declared, stored, refused, noBasis) | sum == totalTrades | declared+stored == rTradeCount |
|---|---|---|---|
| 30d real money | 39 / 0 / 0 / 0 | 39 == 39 ✅ | 39 == 39 ✅ |
| all real money | 103 / 321 / 0 / 0 | 424 == 424 ✅ | 424 == 424 ✅ |
| all paper | 503 / 362 / 0 / 0 | 865 == 865 ✅ | 865 == 865 ✅ |
| all paperPortfolio | 87 / 0 / 0 / 0 | 87 == 87 ✅ | 87 == 87 ✅ |

`expectancyR` on 30d real money moved **+0.9818 → +0.1772**, exactly the
pre-merge prediction. All-time real money now reads **−0.3152** against
`profitFactor 0.7294` and `totalPnl −69.53` — the sign agrees on the book that
matters.

⚠️ **The sign test still is not general, and the 30d window shows it.** There
`expectancyR +0.1772` sits against `profitFactor 0.9507`; the signs disagree, on
a window whose `totalPnl` is **−$3.63** (n=39, essentially flat). That is the
documented behaviour — R weights each trade by its own risk, USD does not — and
it is restated here so the caveat is not quietly dropped now that the headline
looks better.

⚠️ **`refusedWrongSide` is 0 in every window.** The refusal branch is a
backstop this data does not currently need: where a stop is contaminated the
row usually also carries a declared initial risk, which wins first. Its being
zero is not evidence it works — that is pinned by tests, not by production.

### (c) The closed-trades contract, observed

`limit=5` → `X-Total-Count: 447`, `X-Has-More: true`; `offset=5` returns a
**disjoint** page (ids verified non-overlapping); `limit=800` → **HTTP 422**
naming the cap rather than a bare `[]`; `since=2026-09-01` → 12 rows,
`X-Has-More: false`.

⚠️ **12 against the 142 recorded pre-merge is not a regression**, and was
chased down rather than waved past: the route excludes paper by default, and
`include_demo=true` returns **143** — the same population, one more close since.

### ⚠️ (b) A defect the observation itself found

The deployed bracket answer **covers a quarter of the real-money book.**
`window=all` real money publishes `reachedRatio 0.4444` over `gradeable 99`,
with `noBracketRecord 321` (`99 + 321 + 4 == 424`, so the states partition
cleanly).

Cause, measured rather than inferred — `bracketOutcome` joins `order_packages`
on `trades.order_package_id` alone:

| | real-money closed non-backtest |
|---|---|
| rows | 470 |
| carrying `order_package_id` | 120 (25.5%) |
| `order_package_id` NULL | 350 |
| …of those, reachable via `order_packages.linked_trade_id` | 325 (92.9%) |
| unreachable by **either** key | 25 |

Re-run offline with the **shipped** classifier over the route's own population
(n=428), the forward key alone reproduces the deployed **44/99 = 44.4%
exactly** — the positive control that makes the two figures comparable — while
forward-then-fallback grades **420 of 428 (98.1%)** and answers **228/420 =
54.3%**, sl:tp **187:41**.

**The 44.4% is not a wrong number.** It is a correct ratio over a denominator
the reader cannot see is a quarter of the book — the same unprovenanced-
denominator class MI-144 was dispatched to fix, reproduced by the fix itself.
`noBracketRecord` *is* published beside it, so the state is not collapsed; the
hazard is `reachedRatio` being quoted alone.

⚠️ **Do not conflate this with R.** The same fallback moves
`rBasis.declaredInitial` only **103 → 106** and `expectancyR` **−0.3152 →
−0.3153**, because the reverse-linked packages carry `sl`/`tp` but rarely a
`meta.risk_per_unit`. R is fine; the bracket half is what is under-covered.

Fixed in the successor PR (Tier-1, read path): a pre-aggregated `LEFT JOIN` on
`linked_trade_id`, consulted **only** where `order_package_id` is NULL, with
`MIN(order_package_id)` for determinism. `linked_trade_id` is currently unique
across all 4436 packages (1113 over 1113 distinct trades, **zero fan-out**), so
the pre-aggregation is defensive rather than load-bearing today — which is
exactly why it is written now, while the data happens to be kind.

### The answer to the operator's question

> **Real money, all history, n = 428, 420 gradeable: 228 (54.3%) ended at a
> declared bracket — 187 at the STOP against 41 at the TARGET, a 4.6:1 ratio.
> 192 ended mid-bracket.**

⚠️ Quote that from this measurement, not from the live endpoint, until the
coverage fix deploys — the endpoint currently answers 44.4% over 99 rows.

---

## Coverage fix observed — 2026-09-06T16:56Z

PR #11155 merged as `92aebf8c` and deployed the same minute: `/api/diag/version`
reads `git_sha 92aebf8c == git_sha_on_disk`, `restart_pending false`, captured
16:56:45Z. The fallback join (`opl_pick` / `_pkg_col` / `link_ok`) was
content-verified on `origin/main` first, and the endpoint still served the old
figures at 16:56:24Z — the positive control that makes this real
absence-then-presence rather than an unreachable probe.

**Real money, `window=all`, n = 424:**

| | before | after |
|---|---|---|
| `gradeable` | 99 (23.3%) | **420 (99.1%)** |
| `noBracketRecord` | 321 | **0** |
| `priceNotMeasurable` | 4 | 4 |
| `reachedRatio` | 0.4444 | **0.5429** |
| sl : tp : mid | 32 : 12 : 55 | **187 : 41 : 192** |

**Checked by arithmetic, not eyeballed.** The states partition —
`0+0+192+0+4+187+41 = 424 == totalTrades`; `sl+tp+mid = 420 == gradeable`; and
`228/420 = 0.5429`, equal to the published ratio.

`rBasis` moved `{103, 321, 0, 0} → {106, 318, 0, 0}`, still summing to 424 with
`declaredInitial + storedStop == rTradeCount`, and `expectancyR` **−0.3152 →
−0.3153** against `profitFactor 0.7294`. That is the predicted negligible R
effect, confirming the two halves stayed separate: the fallback bought bracket
coverage, not R coverage.

⚠️ **Every pre-merge prediction landed exactly** — 420 gradeable, 187:41,
0.5429, 106 declared, −0.3153. That is a strong result and it is **not
self-evidence that the instrument is correct**. What it establishes is that the
offline pipeline and the deployed route agree, which is precisely what makes the
before/after figures comparable. The classifier's correctness rests on its
tests, not on this agreement.

⚠️ The 4 rows grading `priceNotMeasurable` are the ones predicted unreachable by
either key. They are **declared, not hidden** — the point of the state
vocabulary.

### The answer, now quotable from the endpoint

> **Real money, all history, n = 424, 420 gradeable — 228 (54.3%) ended at a
> declared bracket: 187 at the STOP against 41 at the TARGET, a 4.6:1 ratio.
> 192 ended mid-bracket.**

The earlier instruction to quote this from the measurement rather than the live
endpoint is **spent**; `/api/bot/performance` now publishes it. Still never
quote `reachedRatio` without `gradeable` beside it.
