# Bybit reduce-only close is rejected: an un-quantised `qty` reaches the wire

**Session** `session_01C6Lh24HDdkn6p14qsaeNrA` (sub-session of manager `session_01HrmZ1RRNM4UnEUaFdrPEjj`, registry key `pending-20260906T100427Z-2`) · **2026-09-06** · **Tier-3 PROPOSAL — no code fix applied.**

> ⚠️ **Parent object not found.** The dispatch names
> `WO-20260906-A-BYBIT-REDUCE-ONLY-CLOSE-IS-REJECTED` and says its `done_condition`
> binds this session. **That file does not exist** — absent from the working tree and
> from `origin/main` (`git ls-tree --name-only origin/main docs/claude/work/objects/ |
> grep -i 20260906` → empty, on a clone deepened to 2000 commits). Its done-condition
> could not be read, so this note is written against the dispatch prompt's stated scope.
> Flagged to the manager on board #6927.

---

## Verdict, up front

| question | answer |
|---|---|
| Does a legalizer already exist? | **Yes** — `src/units/accounts/qty_legalize.py`. It is **not un-wired by accident: the close path was never in its scope.** |
| Root cause | **Two defects, one at the source and one at the wire.** See § 2. |
| Is anything unflattenable right now? | **No.** MEASURED, full open set, n=29, 0 affected. See § 4. |
| Do other venues share it? | **No.** Bybit only. IB floors to whole contracts; Alpaca/OANDA never put a qty on the wire. See § 6. |
| Is the obvious fix safe? | **No — the obvious fix is actively harmful.** Flooring `33.299999999999955` gives **33.2**, under-closing by one full step and orphaning dust. See § 7. |

---

## 1. The legalizer exists, and the close path was never in its scope

`src/units/accounts/qty_legalize.py::legalize_qty` is the single seam for venue lot
rules, shipped by `docs/sizing-legalization-DESIGN.md`. It is wired at **three** call
sites — `coordinator.py:1827` (sized-qty guard), `coordinator.py:2285` (intent-delta
guard), `execute.py:1388` (the `_submit_order` pre-flight) — plus `pairs_executor.py:1013`.

**All four are entry/sizing-side.** The string `close_open_position` appears **zero**
times in `docs/sizing-legalization-DESIGN.md`; the design's own enumeration of the four
sites it consolidates is `coordinator.py:1500`, `coordinator.py:1900`,
`execute._submit_order:958`, and `risk.py`. The exit path was never a target.

So this is **not** `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED` and **not** a regression.
It is a scope gap: the consolidation covered how a quantity is *sized*, never how a
quantity is *closed*.

### 1a. The guard cannot see this class — and its own docstring names it

`scripts/check_qty_legalization_guard.py` fails a build when a file outside the seam
*calls* `get_lot_rule` or `quantize_qty`. That is a **negative** guard — "do not build a
second legalizer". It is **not** a coverage guard — "every order path must use the one
legalizer".

`close_open_position` calls neither function, so it **passes the guard cleanly** while
putting a raw `str(float)` on the wire. Meanwhile the guard's docstring says it exists
for *"the recurring 'a sub-lot qty reached the order path' bug class"* — which is
precisely what happened, at a site it is structurally unable to observe.

*A guard that names a class it cannot detect is worse than one that claims nothing*,
because a reviewer reads the green and concludes the class is covered. Filed as a
secondary finding in § 9.

---

## 2. Root cause — the exact call path

### The wire (defect **D2**)

`src/runtime/order_monitor.py:1682` → `src/units/accounts/execute.py:2746`:

```python
# order_monitor.py:1682  (_send_close_to_exchange)
return close_open_position(
    client, cfg,
    symbol=matched_trade.get("symbol"),
    side=matched_trade.get("direction"),
    qty=float(matched_trade.get("position_size") or 0.0),   # <- straight from the journal
    ...
)
```

```python
# execute.py:2806  (close_open_position, bybit branch)
kwargs = {
    "category": category,
    "symbol": symbol,
    "side": close_side,
    "orderType": "Market",
    "qty": str(qty),          # <-- THE WIRE. No legalization, no quantization.
}
```

**`str(qty)` is the exact point the un-quantised float survives.** `close_open_position`
calls `exchange_client.place_order(**kwargs)` **directly** — it never goes through
`_submit_order`, which is the only Bybit submission path that carries the legalizer.

Contrast `_submit_order`, which does it correctly (`execute.py:1388`), resolves the rule
with `prefer_live=True`, and submits the seam's step-precise `qty_str` (`execute.py:1525`).

### The source (defect **D1**)

`src/units/accounts/execute.py:1853-1855`, in `apply_intent_reduce_partial_close`:

```python
else:
    # Partially consumed → shrink the row, leave it open.
    db.update_trade(parent_id, {
        "position_size": parent_qty - consumed,      # <-- raw float subtraction, NO rounding
    })
```

**This is the only `position_size` writer in the tree that persists an unrounded float
subtraction.** All ten dict-literal writers were enumerated; there are no dynamically
built ones (30 `update_trade(` call sites, none assembling `position_size` in a variable).

### The elimination proof

`33.299999999999955` is exactly reproducible as a **single IEEE-754 subtraction of two
step-0.1 SOL quantities** — 222 such pairs exist on the 0.1 grid, e.g. `289.4 − 256.1`.

The two *other* subtracting writers both round, and **rounding destroys every one of the
nine observed artifacts**:

| observed wire value | `round(x, 8)` | `round(x, 10)` |
|---|---|---|
| `659.8999999999942` | `659.9` | `659.9` |
| `292.2999999999997` | `292.3` | `292.3` |
| `253.29999999999973` | `253.3` | `253.3` |
| `0.42400000000000004` | `0.424` | `0.424` |
| `0.0020000000000000018` | `0.002` | `0.002` |
| `0.029999999999999995` | `0.03` | `0.03` |
| `6.640000000000001` | `6.64` | `6.64` |
| `512.2000000000007` | `512.2` | `512.2` |
| `33.299999999999955` | `33.3` | `33.3` |

So none of the nine can have come from `order_monitor.py:568` (`round(current_pos −
actual_filled_qty, 8)`) or `order_monitor.py:9345` (`round(qty − take, 10)`).
**`execute.py:1855` is the only writer left.**

**Corroborating timing (INFERRED, not measured):** journal row **`id=5513`,
`bybit_1`/`SOLUSDT`, `exit_reason=intent_reduce_executed`, created+closed
`2026-09-06T03:32:39`** — the audit leg of an `apply_intent_reduce_partial_close` run,
**five minutes before the 03:37:22 rejection** on the same account and symbol. This ties
the path to the window; it does not by itself prove this particular run wrote this
particular value.

⚠️ **`round(x, 8)` is not step alignment.** It happens to clean artifacts of this
magnitude. A genuinely off-step value (e.g. `33.35` on a 0.1 step) survives it and is
still rejected. D1 and D2 are therefore **both** needed; neither subsumes the other.

---

## 3. The failure, and how the position was actually rescued

The affected row is **`id=5512`, `bybit_1`/`SOLUSDT`, `position_size =
33.299999999999955`, created `2026-09-06T01:56:16`, closed `2026-09-06T03:38:43`,
`exit_reason = tp`.**

The close failed three consecutive times (`Qty invalid`, ErrCode 10001). It closed at
03:38:43 — **81 seconds after the last rejection — with `exit_reason = tp`.**

**The bot's close path never flattened it. The resting take-profit leg at the venue did.**
Had that TP not been in range, the row would have stayed open and kept retrying.

That generalises. Of the nine affected rows, **not one** closed via a successful
`close_open_position`:

| exit_reason | n | what it means |
|---|--:|---|
| `reconciler_filled` | 4 | a resting protective leg filled at the venue |
| `sl` / `tp` | 3 | the venue's own bracket fired |
| `netting_attributed` | 1 | **bookkeeping** — the journal row was closed by attribution |
| `stuck_strategy_watchdog` | 1 | **bookkeeping** — the watchdog force-closed the journal row |

The last two are the manager's hypothesis made concrete: **a position that cannot close
gets its journal row closed by a bookkeeping path while the venue state is not
established to have changed.** That is the shape that manufactures orphans. *(INFERRED —
I did not verify the venue position persisted in those two cases.)*

---

## 4. Is anything unflattenable right now? — **No**

**MEASURED.** `GET https://ict-bot.duckdns.org/api/bot/db/table/trades?filter_col=status&filter_op=eq&filter_val=open&limit=500`, read **2026-09-06 ~10:12Z**.
Response carried **`filter_state: "applied"`** and **`total: 29`** — so this is the
**complete** open set, not a window.

* **29** open non-backtest rows.
* **0** carry a `position_size` with a fractional tail long enough to be off-grid.
* Every open `bybit_*` row reconciles against the venue read at
  `/api/diag/exchange_positions` (**2026-09-06T10:08:41Z**): `bybit_1` ETH 0.31 /
  SOL 3.6 / ADA 79855.0 (= journal 79227 + 628) / XRP 7114.1 / AVAX 3054.0;
  `bybit_2` ETH 0.04 / XRP 58.5; `bybit_portfolio` ETH 9.41 / XRP 11903.8.

**`bybit_2` (real money) specifically: 2 open rows, both clean.** Nothing on any account
is stuck in this failure mode as of that read.

> The narrower `/api/diag/journal?table=trades&limit=1000` window (ids 4519–5518) misses
> one open row — `id=4350`, `ib_paper`/MES, size `15.0`. The `filter_state: applied`
> query above **does** include it. Recorded so the two populations are not confused.

---

## 5. Blast radius — this is recurring, not a one-off

**POPULATION:** `bybit_*` rows, non-backtest, `position_size > 0`, in
`trade_journal.db::trades` ids **4519–5518** (the newest 1000 rows, pulled via
`/api/diag/journal?table=trades&limit=1000`, **2026-09-06 ~10:10Z**). **n = 631.**

**CRITERION:** `str(position_size)` has a fractional tail ≥ 12 digits. This is
deliberately conservative: such a value is **not a multiple of ANY plausible Bybit
step** — asserted in code against all of `{1, 0.1, 0.01, 0.001}`, assertion passed for
all nine — so it is rejected regardless of which step actually applies.

**RESULT: 9 of 631 = 1.43%**, spanning **4 symbols** and **3 accounts**:

| id | account | symbol | wire string | exit_reason | closed |
|---|---|---|---|---|---|
| 4527 | bybit_1 | XRPUSDT | `659.8999999999942` | reconciler_filled | 2026-08-10 |
| 5050 | bybit_1 | SOLUSDT | `292.2999999999997` | reconciler_filled | 2026-08-26 |
| 5052 | bybit_1 | SOLUSDT | `253.29999999999973` | sl | 2026-08-26 |
| 5308 | bybit_1 | BTCUSDT | `0.42400000000000004` | reconciler_filled | 2026-09-02 |
| 5328 | bybit_1 | BTCUSDT | `0.0020000000000000018` | stuck_strategy_watchdog | 2026-09-02 |
| **5342** | **bybit_2** | ETHUSDT | `0.029999999999999995` | reconciler_filled | 2026-09-02 |
| 5343 | bybit_portfolio | ETHUSDT | `6.640000000000001` | netting_attributed | 2026-09-03 |
| 5358 | bybit_1 | XRPUSDT | `512.2000000000007` | sl | 2026-09-03 |
| **5512** | bybit_1 | SOLUSDT | `33.299999999999955` | tp | **2026-09-06** |

⚠️ **`id=5342` is on `bybit_2`, which is REAL MONEY.** The class is not confined to demo.

⚠️ **This is a lower bound.** It counts only rows still in the newest-1000 window, and
only the ≥12-digit criterion. A *genuinely* off-step value with a short repr (say
`33.35` on a 0.1 step) is equally rejected and is **not** counted here — I could not
grade those, for the reason in § 9b.

---

## 6. Other venues — Bybit only

| venue | close path | affected? |
|---|---|---|
| **bybit** | `place_order(qty=str(qty))` — `execute.py:2806` | **YES** — the observed defect |
| **interactive_brokers** | `IBClient.close` → `close_qty = float(math.floor(close_qty))`, and refuses below 1 whole contract | **No** — floors before placing |
| **alpaca** | `AlpacaClient.close(symbol)` — whole-position flatten, `qty` is informational and never reaches the wire | **No** |
| **oanda** | `OandaClient.close(symbol)` — v20 closeout, `qty` never reaches the wire | **No** |

IB is immune by accident rather than by design — `math.floor` is an ad-hoc step-1
alignment sitting outside the seam. Correct here; worth noting as a fifth informal copy
of "align the quantity".

### 6a. Two Bybit sibling paths carry the same wire defect

Both are **un-observed** — I found no rejection for either in the digest — but they are
the same code shape and the same venue:

1. **`execute.py:2578,2580`** — `modify_open_order` sends `kwargs["slSize"] = str(qty)` /
   `kwargs["tpSize"] = str(qty)`. A rejected `set_trading_stop` means **a protective
   tightening silently fails**, which is worse than a failed close: the close alarms, this
   does not.
2. **`order_monitor.py:9967`** — `uncovered = size - bound_covered`, a raw float
   subtraction of two exchange-derived floats, passed to
   `_bybit_top_up_partial_sl` (`order_monitor.py:8829`) → `modify_open_order(qty=...)` →
   the `slSize` above. A rejected top-up **leaves a position partially naked**.

---

## 7. ⚠️ The obvious fix is harmful — read this before proposing one

The natural move is "wire `legalize_qty` into `close_open_position`". **Do not do that
unmodified.** `legalize_qty` **floors** (`precision.quantize_qty` uses `ROUND_DOWN`,
deliberately: *"realised risk must never exceed the sized cap"*). Verified:

```
33.299999999999955  --floor(step=0.1)-->  33.2      # loses 0.1 SOL
33.3                --floor(step=0.1)-->  33.3      # correct
```

Flooring the artifact **under-closes by one entire step**, so the close "succeeds" while
leaving a 0.1 SOL residue on the venue with no journal row backing it.

**That converts a LOUD failure into a SILENT one**, and the silent one is exactly the
orphan class this investigation was dispatched to kill.

The asymmetry is real and is the crux: **flooring an ENTRY is risk-reducing; flooring a
CLOSE is risk-increasing.** `quantize_qty`'s rounding rule is correct for the site it was
written for and wrong for this one.

---

## 8. PROPOSED FIX — Tier-3, NOT APPLIED

Order-path code. The operator approves; this session proposes only. Two parts, and
**both are needed** (§ 2).

### Part 1 — the source. `src/units/accounts/execute.py:1853-1855`

```diff
         else:
             # Partially consumed → shrink the row, leave it open. Status is
             # NOT passed so update_trade fires no close ping.
+            # Round the residual to 8dp before persisting. A raw IEEE-754
+            # subtraction of two step-aligned quantities yields values like
+            # 33.299999999999955 (= 289.4 - 256.1), which `close_open_position`
+            # later puts on the wire verbatim as `str(qty)` and Bybit refuses
+            # with `Qty invalid` (10001) — leaving the position unflattenable by
+            # the normal path. This matches the two sibling writers that already
+            # round (`order_monitor._apply_partial_close`, 8dp;
+            # `order_monitor.py:9345`, 10dp) and was the ONLY position_size
+            # writer performing an unrounded subtraction.
+            #
+            # NOT step alignment — see Part 2, which is what makes an off-step
+            # value legal. This only stops us MANUFACTURING one.
             db.update_trade(parent_id, {
-                "position_size": parent_qty - consumed,
+                "position_size": round(parent_qty - consumed, 8),
             })
```

**Evidence it changes no currently-legal value:** `round(x, 8)` is the **identity** on any
float with ≤ 8 decimal places. The finest Bybit step in play is `0.001` (BTCUSDT), so
**every legal quantity on every wired symbol has ≤ 3 decimals** and is untouched.
And it demonstrably kills all nine observed artifacts (table in § 2).

### Part 2 — the wire. New seam function + one call site

**2a. Add to `src/units/accounts/qty_legalize.py`** (in the seam, so
`qty-legalization-guard` stays satisfied — this is the only module permitted to call
`quantize_qty`/`get_lot_rule`):

```python
def snap_artifact_qty(
    qty: float, *, account_cfg: dict, symbol: str, client: Any = None,
) -> Tuple[float, str, str]:
    """Repair a float ARTIFACT in a close quantity. Returns (qty, qty_str, state).

    ⚠️ THIS DELIBERATELY DOES NOT FLOOR, and that is the whole point.
    `legalize_qty` floors because realised risk must not exceed the sized cap —
    correct for an ENTRY. On a CLOSE the polarity inverts: flooring
    33.299999999999955 to 33.2 UNDER-closes by a full step and orphans 0.1 SOL
    on the venue with no journal row, turning a loud rejection into a silent
    naked residue. So this snaps to the NEAREST step multiple, and only when the
    deviation is unambiguously float noise.

    Three states, never collapsed:
      "snapped"      an artifact was found and repaired
      "unchanged"    the qty is already on the grid — THE IDENTITY, the common path
      "not_graded"   no lot rule resolved (we could not look), OR the value is
                     genuinely off-step and not an artifact. Both PASS THROUGH
                     untouched, byte-for-byte today's behaviour. A genuinely
                     off-step qty keeps failing LOUDLY at the venue, because
                     silently moving it is a decision no evidence supports.
    """
    rule = _resolve_venue_lot_rule(symbol, account_cfg, client, prefer_live=True)
    if rule is None:
        return float(qty), str(float(qty)), "not_graded"
    step, _vmin, _vmax, _mstate, _src = rule
    s = Decimal(str(step))
    if s <= 0:
        return float(qty), str(float(qty)), "not_graded"
    d = Decimal(str(qty))
    nearest = ((d / s).to_integral_value(rounding=ROUND_HALF_UP) * s).quantize(s)
    if nearest <= 0:
        return float(qty), str(float(qty)), "not_graded"
    if d == nearest:
        return float(nearest), str(nearest), "unchanged"
    tol = max(s * Decimal("1e-6"), abs(d) * Decimal("1e-12"))
    if abs(d - nearest) <= tol:
        logger.warning(
            "snap_artifact_qty: %s close qty %s is a float artifact — "
            "sending %s (step=%s)", symbol, str(d), str(nearest), step,
        )
        return float(nearest), str(nearest), "snapped"
    return float(qty), str(float(qty)), "not_graded"
```

**2b. `src/units/accounts/execute.py`, `close_open_position` bybit branch (~2800):**

```diff
         try:
             category = _bybit_category(account_cfg)
+            # Repair a float artifact before it reaches the wire. `str(qty)`
+            # on a raw subtraction result sends e.g. "33.299999999999955",
+            # which Bybit refuses (`Qty invalid`, 10001) and the position
+            # cannot be flattened by this path.
+            # NEAREST, not floor — see snap_artifact_qty's docstring.
+            from src.units.accounts.qty_legalize import snap_artifact_qty
+            _q, _q_str, _snap_state = snap_artifact_qty(
+                float(qty), account_cfg=account_cfg, symbol=symbol,
+                client=exchange_client,
+            )
             kwargs = {
                 "category": category,
                 "symbol": symbol,
                 "side": close_side,
                 "orderType": "Market",
-                "qty": str(qty),
+                "qty": _q_str,
             }
```

### Why this cannot break an order that works today

| property | argument |
|---|---|
| **Identity on every legal qty** | A quantity the venue accepts *is by definition* a step multiple, so `d == nearest` and the function returns it unchanged. Verified on `33.3 · 3.6 · 7114.1 · 0.018 · 0.31 · 79855.0 · 58.5`. |
| **Blast radius = currently-FAILING orders only** | The `snapped` branch is reachable only when `d != nearest` — i.e. off-grid — and Bybit rejects every such order today. Structurally the same safety argument as `qty_legalize`'s venue-max clamp. |
| **Never over-closes** | The order is `reduceOnly=True`; the venue clamps to the live position. Maximum movement is half a step (0.05 SOL). |
| **Never orphans dust** | It never floors. This is the property the naive fix loses. |
| **Fails loud, not silent** | A genuine off-step value returns `not_graded` and passes through untouched — it keeps being rejected, visibly. |
| **No new lot-rule copy** | The resolution lives in the seam and reuses `_resolve_venue_lot_rule`, so `qty-legalization-guard` is satisfied rather than worked around. |
| **Degrades to today** | Unresolvable rule → `not_graded` → `str(float(qty))`, byte-identical to the current line. |

**Verified predicate behaviour** (run this session):

* all 9 observed artifacts → repaired, given the correct step;
* all 7 sampled live legal quantities → **identity**;
* `33.35 / 659.85 / 0.0025 / 7656.059` (genuine off-step) → **untouched**.

### What I deliberately did NOT propose

* **Extending it to the § 6a siblings.** `modify_open_order`'s `slSize`/`tpSize` and the
  naked top-up have the same shape but **no observed failure**, and they are a *protective*
  order path. They deserve their own change with their own evidence, not a widening
  smuggled in here.
* **Clamping the close to the live position size.** That needs a venue read on the close
  path — the shape of both June 2026 wedges.
* **Changing `quantize_qty` or `legalize_qty`.** Their flooring is correct for the entry
  sites that use them. Touching them would alter four working call sites to fix a fifth.

---

## 9. Secondary findings — filed, not fixed

### 9a. `qty-legalization-guard` cannot detect the class it names

It forbids *building a second legalizer*; it does not require *using the one legalizer*.
`close_open_position` passes it while sending a raw `str(float)`. A coverage-side
companion — every Bybit `place_order` / `set_trading_stop` call must source its `qty`
string from the seam — would have caught this. Severity **medium**, tier **1**.

### 9b. ⚠️ `config/instruments.yaml` states an XRPUSDT `qty_step` the venue contradicts

`config/instruments.yaml` declares `XRPUSDT: qty_step: 1.0, min_qty: 1.0`. The venue read
at **2026-09-06T10:08:41Z** shows Bybit holding **fractional** XRP positions on three
accounts: `bybit_1` **7114.1**, `bybit_2` **58.5**, `bybit_portfolio` **11903.8**. A
linear-perp position size changes only by fills, so a position of `7114.1` cannot exist if
the step were `1.0`. **The true step is ≤ 0.1; the config is wrong.** Field beats comment.

**This is not cosmetic and it interacts directly with the fix above.** `legalize_qty`
resolves the `InstrumentProfile` first when `prefer_live=False`, and a *floor* against a
`1.0` step would turn `7114.1` into `7114.0` — orphaning 0.1 XRP on every close. It is
also why the § 8 proposal specifies `prefer_live=True`: the **live** lot rule must be
authoritative, with the profile only an added fallback, exactly as `_submit_order` does it.

It is also what stopped me grading the wider population: my first pass, using
`instruments.yaml`, reported **82 off-grid rows (13.00%)**. That number is **wrong** and
must not be quoted — it is an artifact of the bad XRP/AVAX steps. The **9 / 1.43%** in § 5
uses a criterion that holds under every plausible step and is asserted in code.

Severity **high**, tier **1** (a config-data correction, verifiable against the venue).
`AVAXUSDT` should be re-checked in the same pass — several rows carry 3-decimal sizes
against a declared `0.1` step, which I could not adjudicate.

### 9c. Every affected position was rescued by something other than the close path

§ 3. Two of the nine were closed by *bookkeeping* paths (`netting_attributed`,
`stuck_strategy_watchdog`) whose venue-side effect I did not verify. If the venue position
outlived the journal row, that is the orphan-manufacturing step. Worth a targeted check.

---

## 10. Reproduction

```bash
python3 - <<'EOF'
# 33.299999999999955 is a single IEEE-754 subtraction of two step-0.1 quantities
target = 33.299999999999955
print(289.4 - 256.1 == target)          # True  (222 such pairs on the 0.1 grid)
print(str(target))                       # '33.299999999999955'  <- what reaches the wire
print(round(target, 8))                  # 33.3   <- what Part 1 persists instead

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
s = Decimal("0.1"); d = Decimal(str(target))
print((d/s).to_integral_value(ROUND_DOWN)*s)      # 33.2  <- the NAIVE fix: loses a step
print((d/s).to_integral_value(ROUND_HALF_UP)*s)   # 33.3  <- what Part 2 sends
EOF
```

**Sources for every measured claim in this note**

| claim | where it came from |
|---|---|
| venue positions, all accounts | `/api/diag/exchange_positions`, read **2026-09-06T10:08:41Z** |
| open-set completeness (n=29, 0 affected) | `/api/bot/db/table/trades?filter_col=status&filter_op=eq&filter_val=open&limit=500`, `filter_state: applied`, `total: 29`, read **2026-09-06 ~10:12Z** |
| the 9 artifact rows / n=631 denominator | `/api/diag/journal?table=trades&limit=1000` (ids 4519–5518), read **2026-09-06 ~10:10Z** |
| the rejection itself | `docs/claude/ERROR-FEED-DIGEST.json`, `groups[1].sample` |
| all code line numbers | this repo at `origin/main` `8a16786d` |
