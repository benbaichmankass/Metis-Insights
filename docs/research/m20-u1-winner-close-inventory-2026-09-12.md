# U1 — what can end a winning trade, per leg, and what arms it

> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**Unit:** MI-278 · `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER`
**Cycle priority:** `CY-20260906-TRADING-TRUTH`
**Predecessor, built on rather than redone:** [`winner-size-collapse-2026-09-12.md`](winner-size-collapse-2026-09-12.md) (MI-277, merged `#11848`)
**Reproduce:** `python3 scripts/research/m20_u1_winner_close_inventory.py --trades <trades.json> --out <out.json> --live-only`

MI-277 established that the expectancy flip is an **R collapse** on the winner side and refuted the exit levers — **on `bybit_1`, in its window**, which its own memo says is not a general claim. It named the gap: nothing has enumerated what *can* end a winner per leg, so *"no lever fired here"* could not be distinguished from *"we did not look at the right lever."* This unit is that denominator.

---

## 0. The verdict, in five sentences

**MI-277's conclusion survives and is now better supported, and the biggest finding is about the instrument rather than the levers.** Across **44 enabled+live legs**, the mechanism that can end a winner is almost always just the take-profit: `giveback_stop` — the only lever that by construction fires on a trade that *was* winning — is armed on **1 of 44**, and seven of the eight `ict_scalp_*` legs have exactly one closer plus one stop-mover (`tp_cross` and a baseline-on break-even ratchet), with **no trailing stop at all on that unit**. Two candidate mechanisms were tested and **refuted with controls**: the break-even ratchet (the family that lacks it moved *further* into the scratch band) and the netting shared-bracket cascade (4% co-timing against a 3% control). What the inventory *did* surface is that **`reconciler_filled` — 28.6% of closed non-pairs trades and 35 of 112 winners — names no mechanism by construction**: the reconciler watches the **entry** order, and the classifier that back-fills a label refuses on **27 of 27** broker-truth rows because the fill lands a median **0.55–0.79 bp short** of the level its strict inequality tests. So the exit-reason vocabulary systematically **undercounts** stop and target hits, and every downstream measurement keyed on it — MI-277 §2.2's "reached a declared target" cell included — is computed on a censored population.

---

## 1. Population — stated first

| | |
|---|---|
| **Inventory source** | `config/strategies.yaml` + `config/accounts.yaml` at `main` `d5bf4077e`, read 2026-09-12 (**READ ONLY**) |
| **Monitor-unit map** | **IMPORTED** from `src.runtime.pipeline.monitor_unit_for`, never re-derived |
| **Legs** | **55 declared · 44 enabled + `execution: live`** |
| **Journal cross-check** | `/api/diag/journal?table=trades&limit=1000` + `…&table=order_packages&limit=1000`, pulled **2026-09-12T05:2xZ** direct over `https://ict-bot.duckdns.org` |
| **Trades window** | ids **4716–5715**, `created_at` 2026-08-17T15:29Z → 2026-09-12T05:05Z |
| **Decision population** | MI-277's `population()`, restated identically: `status=closed` AND NOT `is_backtest` AND `pnl IS NOT NULL` AND not the pairs sleeve → **294 rows** |
| **Era split** | `created_at` against **2026-08-30T08:53:19Z**, imported from MI-277 |

⚠️ **Window drift against MI-277, stated rather than hidden.** Reproducing its `bybit_1` population on this pull gives **181 rows (78 pre + 103 post)** where its memo records **180 (79 + 101)**. My pull is ~4h later and 8 ids along (it took 4709–5708 at 01:0xZ). The population is positively identified, not guessed at; every number below is on **this** pull.

⚠️ **`ib_paper` is excluded from every PnL figure**, following MI-277 — it fails the `contract_value = 1` premise. Stated because it is not a rounding matter: `ib_paper` is **8 rows carrying +$231,532** against every other account negative, and one `tp_cross` row (id 4773, MGC, +$249,185) alone would flip the fleet's sign. I built a per-reason PnL table without that exclusion first and it read `tp_cross: +$254,146`; the exclusion is what makes the number mean anything.

---

## 2. The inventory — the two gates

A per-leg mechanism can fire only when **both** hold:

* **(a) the leg's monitor unit implements the call site**, and
* **(b) the leg's YAML declares the arming key.**

`exit_levers.py`'s own docstring states (b) outright: *"THE DECLARE IS STILL THE GATE … an undeclared leg evaluates the reference cell and writes one observe-only row."* A leg satisfying (b) but not (a) is an **orphaned declare** — a YAML key nothing reads.

**The script asserts every claimed call site against the file and FAILS rather than reporting a stale inventory** (30 sites, 11 mechanisms, 5 divergences). That is deliberate: a hand-written call-site table is exactly the artifact that rots silently, and `git log` will not tell you it has.

### 2.1 Per-leg mechanisms

| mechanism | ends a winner? | units implementing | armed by |
|---|:--:|---|---|
| `tp_cross` | **yes** — the designed one | donchian · pullback · ict_scalp · squeeze · vwap | baseline (level is `min(tp_r, cap_r)`) |
| `sl_cross` | only once trailed above entry | + turtle_soup | baseline |
| `breakeven_ratchet` (moves SL) | **yes** (whipsaw) | ict_scalp · vwap · turtle_soup · hf_displacement_cont | **baseline-on, 1R, no declaration** |
| `trail_base` (moves SL) | **yes** | donchian · pullback | `trail_mult` |
| `trail_decay` (moves SL) | **yes** — arms *only* above `arm_r` | donchian · pullback | `trail_decay_arm_r` / `_stall_bars` |
| `trail_vol` (moves SL) | yes | donchian · pullback | `trail_vol_below_pctl` |
| `giveback_stop` | **yes, by construction** | donchian · pullback | `giveback_min_mfe_r` |
| `stale_stop` | not at the default | donchian · pullback (shared) · **ict_scalp (private copy)** | `stale_exit_bars` |
| `exit_head` | **yes, at any R** | donchian · ict_scalp | `exit_head_model` |
| `vwap_cross` / `time_decay` | yes | vwap | baseline / `max_hold_minutes` |

### 2.2 What that leaves, across the 44 live legs

| | count of 44 |
|---|--:|
| `tp_cross` armed | **44** |
| `trail_base` armed | 35 |
| `trail_decay` armed | 16 |
| **`giveback_stop` armed** | **1** (`uso_trend_1h`) |
| `exit_head` armed | 3 (`trend_donchian`, `…_eth`, `…_sol`) |
| `breakeven_ratchet` armed | 8 (every `ict_scalp_*` leg) |
| `stale_stop` armed | 3 (`ict_scalp_eth_15m`, `trend_donchian_eth_prop`, `trend_donchian_xrp_4h`) |
| **legs whose armed set is exactly `{tp_cross, sl_cross, breakeven_ratchet}`** | **7** — every `ict_scalp_*` leg except `…_eth_15m`, which adds `stale_stop` |

The `stale_stop`-3 / `giveback`-1 split independently reproduces the count `exit_levers.py`'s own docstring records from 2026-08-18 (*"stale-only on 3 legs, giveback-only on 1, both on none"*) — a positive control on the inventory from a source it does not read.

**Read the middle rows against M20's subject.** `giveback_stop` is the one lever whose trigger condition *is* "this trade was winning and gave some back"; it is armed on one leg in forty-four, and that leg is on Alpaca. `trail_decay` arms only above `arm_r`, i.e. exclusively on winning trades — 16 legs. Everything else in the winner-ending column is the take-profit and the ATR trail.

⚠️ **The `ict_scalp` row is the one to carry.** The eight `ict_scalp_*` legs — the family MI-277 measures as carrying two-thirds of the loss — reach their exit through `tp_cross`, the venue bracket, or a break-even ratchet at 1R. **The unit does not read `trail_mult` at all.** So a scalp winner either reaches its declared target or it does not; there is no mechanism that banks a partial run.

---

## 3. The finding: the largest winner-ending category names no mechanism

### 3.1 It is unattributed *by construction*

`reconciler_filled` is **84 of 294** closed non-pairs rows (**28.6%**) and **35 of 112** winners (**31.3%**). `_close_trade_from_order_status`'s own docstring says what it watches:

> *"Mark a trade row 'closed' when Bybit reports **the entry order** filled and the position flat."*

It never reads the **closing** order. `exit_price` comes separately from `/v5/position/closed-pnl`'s `avgExitPrice`. So the label cannot name a mechanism — not because of a bug, but because the mechanism is never asked for.

### 3.2 The back-fill classifier refuses on 27 of 27, and the reason is sub-tick

`_classify_broker_exit` back-fills `sl` / `tp` by comparing the exit price against the package bracket, with a deliberately conservative inequality (`px <= sl` / `px >= tp`, *"fills can slip through the level"*). **All 84 rows carry `exit_reason_source: "unresolved"`.**

Restricting to the **27** rows whose exit price is broker truth (`exchange_fill` / `bybit_closed_pnl`; the other 57 sit on `candle_at_close` / `recorded_exit_price`, where "between the levels" is uninformative by construction):

| | n | signed bp to the nearest level | fell **short** |
|---|--:|---|--:|
| nearest the **stop** | 20 | min −154.85 · **median −0.79** · max −0.02 | **20 of 20** |
| nearest the **target** | 7 | min −28.19 · **median −0.55** · max −0.26 | **7 of 7** |

**27 of 27 fell short. Not one went through.** 25 of 27 are within 25 bp of a declared level. Six of the eight winners land within ~1% of their declared `tp_r` — id 5170 at `R=14.156` against `tp_r=14.19`, id 5059 at `8.962` against `8.99`, id 5299 at `8.427` against `8.46`.

The inequality anticipated slippage **through** the level and got slippage **short of** it, essentially always. That is the expected shape when the venue executes TP/SL as **market-on-trigger** rather than as a resting limit: the trigger fires at the level and the fill prints a tick on the near side.

**So the exit-reason vocabulary systematically undercounts stop and target hits.** `tp` reads n=20 in this window while at least five more target fills sit inside `reconciler_filled`.

⚠️ **This lands on MI-277's own instrument.** Its §2.2 restricts to *"measured winners that reached a declared target"* — `tp`/`tp_cross` only, **n = 7 pre / 9 post** — and finds achieved R nearly **doubled** (4.835 → 9.841), the one sub-cell that moves *against* the collapse. That cell is drawn from the censored population. This memo does **not** establish that the finding reverses; it establishes that the population it rests on excludes target fills for a reason unrelated to the trade, and n=7/9 is thin enough that a handful of recovered rows could move it either way. **Re-running §2.2 with the recovered labels is a U2 task.**

### 3.3 Two competing explanations, both tested and refuted

| hypothesis | test | result |
|---|---|---|
| a **netting shared-bracket cascade** — one position-level bracket flattens sibling rows, so each reads mid-range against its own package (`BL-20260720-ICTSCALP-PASTSTOP-EXITS`) | co-timed close on the same `(account, symbol)` within 60 s | **REFUTED** — 1 of 27 (4%) against a `sl`-control of 1 of 30 (3%) |
| the **bracket could not be looked up**, so `_classify_broker_exit` returned `None` for want of levels (a genuine collapsed state in that function — *"no levels"* and *"mid-bracket"* share one return) | re-run the classifier's own logic over all 84, joined to `order_packages` | **REFUTED as the cause here** — 84/84 joined, zero missing brackets. The collapse in that function is **latent, not active**, and is reported as such |

### 3.4 The surface that would settle it exists, and nothing production reads it

`src/units/accounts/clients.py::account_bybit_raw_order_history` already returns, per order, `stop_order_type`, `cancel_type`, `order_type`, `reduce_only`, `tpsl_mode` — *"the protective-leg identity. A stop/target leg carries `stopOrderType`; an entry does not."* Its own docstring says the nearest existing caller **"normalises the answer away."**

**Measured: its only non-test caller is the diag route `/api/diag/bybit_raw_order_history`.** No production consumer. By the `exit-refinement` skill's own definition of done (#2, *a consumer exists*), that capability is **unwired** — and it is precisely the field that would turn 84 unattributed closes into named ones.

---

## 4. Findings that are corrections to the record

### 4.1 MI-277 §3.5's lever sentence is winner-scoped, and reads as account-scoped

It says: *"Measured on `bybit_1`: … `stale_stop` and `giveback_stop` appear **zero times** as an exit reason in either era."*

**MEASURED on this pull, inside MI-277's exact population** (`bybit_1`, non-pairs, closed, `pnl NOT NULL`, ids in window): **six `stale_stop` rows**, all `ict_scalp_eth_15m`, carrying **−$4,742.85**.

| id | era | opened | pnl |
|---|---|---|--:|
| 5115 | pre | 2026-08-27T08:28Z | −1,562.24 |
| 5158 | pre | 2026-08-28T06:35Z | −1,054.07 |
| 5244 | post | 2026-08-30T17:30Z | −10.45 |
| 5319 | post | 2026-09-02T01:45Z | −1,166.89 |
| 5572 | post | 2026-09-08T15:48Z | −379.83 |
| 5593 | post | 2026-09-09T09:42Z | −569.36 |

**All six are losses**, so zero is correct of the **winner** sub-population — which is what the neighbouring clause (*"0/42 pre, 0/34 post"*) counts. **MI-277's conclusion is untouched**: no lever cut a winner short on that account in that window, and this measurement confirms it rather than contradicting it. What needs its population stated is the sentence. Filed.

### 4.2 `ict_scalp` runs a private copy of the stale-stop, and it is silent

`exit_levers.py` was written to collapse exactly this duplication (*"Two copies of a window definition that every R measurement depends on is exactly what `_regime_score_semantics.py` had to be written to stop"*). `ict_scalp.py:720` defines its own `_stale_stop_verdict` anyway, and it differs in two ways:

1. **No annotate path.** The shared lever writes an observe-only `exit_lever_soak` row when an *undeclared* leg's reference cell would have fired. `ict_scalp`'s returns `None` silently. **So the seven undeclared `ict_scalp_*` legs accrue ZERO evidence about what the lever would have done** — on the family carrying most of the loss. Asserted in the script: `record_exit_lever_annotation` does not appear in `ict_scalp.py`.
2. **Inverted parameter precedence.** The shared lever reads `meta` first then `cfg`; the private copy reads `cfg` first then `meta`. Latent while the two agree, and it is the opposite resolution order for the same key.

### 4.3 Two orphaned declares

`squeeze_breakout_4h` and `fade_breakout_4h` both declare **`trail_mult`** in `config/strategies.yaml`, and neither unit contains a call site that reads it. The key is read by nothing. This is the exact shape `exit_levers.py`'s docstring names (*"declaring it would produce an ORPHANED DECLARE, a YAML key nothing reads"*) — found by the inventory rather than by reading.

### 4.4 A stale docstring that inverts a load-bearing fact

`trend_donchian.monitor`'s docstring: *"Reads all trail parameters from `open_pkg['meta']` because `run_monitor_tick` passes `cfg={}` in production."*

**Stale.** `order_monitor._load_live_strategy_cfgs` (M20 E3) threads live `strategies.yaml` into `monitor()` *precisely so* a lever declared mid-hold reaches an **already-open** package. A session reading the docstring would conclude a declared lever cannot reach an open trade — the opposite of the truth, in the direction that would stop someone declaring one. Field beats comment.

---

## 5. Negative results, recorded because they are cheap to re-derive and wrong to re-run

* **The break-even ratchet is not the collapse mechanism.** `ict_scalp` winners exiting in `[-0.05R, +0.25R]` — at the ratchet — went **0 of 45 (0%) pre → 2 of 27 (7%) post**. The **control** is the donchian family, which does **not** call `monitor_breakeven_sl` (asserted): it went **1 of 19 (5%) → 2 of 5 (40%)** over the same split. The family *without* the mechanism moved further into the band. ⚠️ **n = 5 on the post control**, so this refutes the ratchet as the *dominant* explanation and no more.
* **The collapse is a shift of the whole distribution, not a spike at any lever's level.** `ict_scalp` winners with `R > 3`: **27 of 45 (60%) → 11 of 27 (41%)**; median R 4.386 → 1.446. That is MI-277's "more movement, less follow-through" seen per-family with the per-leg denominator behind it.

---

## 6. What this does NOT establish

* **That any lever caused the winner-size collapse.** The inventory says what *could* fire; U2 attributes what *did*. Two candidates are refuted above and the largest category is unattributed.
* **That the 84 `reconciler_filled` rows are all bracket fills.** 27 of 27 *broker-truth* rows sit within a hair of a declared level, which is strong evidence they are bracket-family exits and not the *"genuine non-bracket close"* the docstring calls them. The other 57 sit on estimated anchors and are **not** graded here. And even on the 27, whether each was the venue's own SL/TP leg or a monitor-driven market close **cannot be told from the journal** — that is §3.4's point.
* **Anything about the pairs sleeve.** 270 of the 564 closed rows are `pairs_*`, excluded throughout; that executor owns its own exit path and does not use the coordinator fold.
* **Anything about `ib_paper` PnL**, per §1.
* **Any verdict for `docs/research/exit-refinement-coverage.json`.** U1 ran no sweep and produced no lever verdict, so **no cell changes** — stated explicitly because the skill requires the matrix to move in the same PR as verdict-producing work, and silence about it is what makes a missing update invisible.

---

## 7. What U2 does next, in order

1. **Recover the labels before attributing anything.** Re-grade the 84 `reconciler_filled` rows against the declared levels with a tolerance instead of a strict inequality, report how many become `tp` / `sl`, and **re-run MI-277 §2.2 on the recovered population**. This is the cycle priority applied literally: the instrument first.
2. **Attribute the post-2026-08-27 winning closes** to mechanism, provenance-graded, with the recovered labels and the `netting_attributed` doubt (`OI-20260908`) stated on every number that touches it.
3. **Name the venue-side read** that would make attribution durable rather than inferred — §3.4's `stopOrderType` / `cancel_type`, whose reader already exists and has no production consumer.

## 8. Rows filed

| id | register |
|---|---|
| `BL-20260912-RECONCILER-FILLED-IS-29-PERCENT-OF-CLOSES-AND-NAMES-NO-MECHANISM-BECAUSE-THE-RECONCILER-WATCHES-THE-ENTRY-ORDER` | health |
| `BL-20260912-CLASSIFY-BROKER-EXIT-USES-A-STRICT-INEQUALITY-AND-EVERY-BROKER-TRUTH-FILL-LANDS-SHORT-OF-THE-LEVEL` | health |
| `BL-20260912-BYBIT-RAW-ORDER-HISTORY-CARRIES-THE-EXIT-MECHANISM-AND-HAS-NO-PRODUCTION-CONSUMER` | health |
| `BL-20260912-ICT-SCALP-RUNS-A-PRIVATE-STALE-STOP-WITH-NO-ANNOTATE-PATH-SO-SEVEN-LEGS-ACCRUE-NO-LEVER-EVIDENCE` | health |
| `BL-20260912-TWO-LEGS-DECLARE-TRAIL-MULT-THAT-THEIR-UNIT-CANNOT-READ` | health |
| `BL-20260912-MI-277-S-LEVER-SENTENCE-IS-WINNER-SCOPED-AND-STALE-STOP-FIRED-SIX-TIMES-IN-ITS-POPULATION` | performance |
| `BL-20260912-GIVEBACK-STOP-IS-ARMED-ON-ONE-OF-FORTY-FOUR-LIVE-LEGS-AND-THE-SCALP-FAMILY-HAS-NO-TRAIL-AT-ALL` | performance |
