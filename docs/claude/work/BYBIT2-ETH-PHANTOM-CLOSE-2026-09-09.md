# bybit_2 ETHUSDT — journal closed `sl` while the operator's terminal shows the position live

> **Doc status:** `live` · category `evidence` · MI-221 · work object
> `WO-20260909-BYBIT2-ETH-PHANTOM-CLOSE-LEFT-A-REAL-MONEY-POSITION-UNPROTECTED-AND-INVISIBLE`
> · investigated 2026-09-09 · session `session_01CwHNLSX6gMjPoKUWHoYj7q`

**Every read in this document is a GET. No order was placed, modified or cancelled on
`bybit_2`. Protecting the position is the operator's action.**

---

## 0. The headline, and what it is NOT

**The two questions the manager posed as separate have ONE common cause.** The reconciler's
"is the position still open?" test and every position reader on the fleet call the **same
function** — `src/units/accounts/clients.py::account_open_positions`. The journal closed
trade 5471 *because* that function reported no ETHUSDT long on `bybit_2`, and no reader can
see the position today *for the identical reason*. There is no second, independent failure.

⚠️ **AND THE MOST IMPORTANT THING IN THIS DOCUMENT IS WHAT I COULD NOT ESTABLISH.**
Three independent venue reads say `bybit_2` is FLAT on ETHUSDT. The operator's eyes say it
is open. **I could not reconcile those, and I could not obtain the one observation that
would.** § 5 states exactly what is unresolved and § 6 names what would settle it. Do not
read § 3 as "the operator is wrong" — read it as "our reads and the terminal disagree, and
the disagreement is now localised."

---

## 1. Question (1) — why did the journal close it? THE SITE, NAMED

**It was not an inferred SL cross.** The close acted on a **Bybit closed-pnl record**, i.e.
venue evidence. The chain, in order:

| # | site | what it did |
|---|---|---|
| 1 | `src/runtime/order_monitor.py:4336-4343` | `pos = account_open_positions(cfg)` → `positions_cache = _exchange_position_set(pos)` |
| 2 | `src/runtime/order_monitor.py:4348` | `if (sym, side) in positions_cache: continue` — **`("ETHUSDT","long")` was NOT in the set**, so the row fell through to the close branch |
| 3 | `src/runtime/order_monitor.py:4371-4388` | netting-guard 2-observation confirm (`RECONCILER_CLOSE_CONFIRM_SECONDS`) — the flat **persisted** across the window, so it was not a single-tick blip |
| 4 | `src/runtime/order_monitor.py:4392` → `:6194` | `_close_trade_from_order_status` |
| 5 | `src/runtime/order_monitor.py:6260-6275` | `account_closed_pnl_for_trade(...)` returned a **real Bybit record**: `avg_exit_price 2451.27`, `closed_pnl -0.25284115` |
| 6 | `src/runtime/order_monitor.py:~6018` (`_classify_broker_exit`) | long, `px 2451.27 <= sl 2451.59428571` ⇒ **`'sl'`** |

**Step 6 is CORRECT given its inputs** and is not the defect. `_classify_broker_exit` is
deliberately conservative and graded exactly as designed. The journal row records its own
provenance honestly:

```json
{"closed_by": "monitor_reconciler",
 "closed_reason": "reconciler — Bybit reports order filled and position flat",
 "exit_price_source": "bybit_closed_pnl", "bybit_closed_pnl": -0.25284115,
 "finalised_by": "reconciler", "exit_reason_source": "price_vs_pkg_bracket"}
```

**Trade 5471, as measured** (`/api/diag/journal?table=trades&limit=400`, read 2026-09-09
~11:27Z; population = the newest 400 `trades` rows, ids 5196–5595):
`bybit_2` · ETHUSDT · long · size **0.04** · entry **2453.97** · sl **2451.59428571** ·
tp1 2698.8143 · opened **2026-09-04T14:04:14Z** · closed **2026-09-08T13:37:13.873Z** ·
exit 2451.27 · pnl **-0.2528** · `eth_pullback_2h` · pkg `pkg-c90e8cf40c904e9e` ·
`reconcile_status: null`.

**So gate (2) is the decision that closed it, and step 5 supplied the price and the reason.**
Both halves matter: had `positions_cache` contained `("ETHUSDT","long")`, step 2 would have
`continue`d and no close would have happened, whatever the closed-pnl ledger said.

---

## 2. Question (2) — why can no reader see it?

**The manager's reading of the code is correct and I confirmed it:**
`account_open_positions`'s bybit branch is **not journal-scoped**. It performs
`get_positions(category="linear", settleCoin="USDT")` and then, per
`BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND`, a **symbol-scoped cross-check** for every
configured symbol not already surfaced (`clients.py:1410-1428`).

**I verified all three preconditions for that cross-check to actually run on ETHUSDT:**

1. **ETHUSDT is configured.** `config/accounts.yaml::bybit_2.symbols =
   ['BTCUSDT','ETHUSDT','XRPUSDT','ADAUSDT']`. Confirms the manager's premise 6 — this is
   **not** a roster-scope explanation.
2. **The `symbols` key survives to the caller.** `list_accounts()` →
   `_load_yaml_accounts()` explicitly preserves `symbols` in its carry-through tuple
   (`src/units/ui/data_loaders.py:259`). (Its own docstring omits `symbols` from the field
   list — stale prose, field beats comment.)
3. **The cross-check did not fail silently.** Its `except` logs
   `"per-symbol cross-check for %s failed"`. I pulled
   `/api/diag/journalctl?unit=ict-trader-live&lines=1000`, spanning
   **11:25:53Z–11:29:35Z**, which brackets both of my diag reads (11:27:53Z, 11:28:38Z):
   **zero** cross-check warnings. **Positive control for that probe** (the search is not
   vacuously quiet): the same 1000 lines DO carry other `order_monitor` warnings/errors in
   that window, e.g. `OVER-COVER TRIP bybit_1/ADAUSDT` at 11:27:26Z and 11:29:31Z, and 17
   `ETHUSDT` lines.

**Therefore a symbol-scoped `get_positions(category="linear", symbol="ETHUSDT")` on
`bybit_2`'s live API key ran, succeeded, and returned no row with `size > 0`** — twice.

### The answer to the manager's decisive question

> *"the venue is not returning it to this API key, or the bot drops it after the read."*

**On the evidence: the venue is not returning it. The bot is not dropping it after the read.**

⚠️ **But I could not report the RAW `get_positions` response, and that is itself a defect
that blocked this investigation** — see § 4c. `_emit` does `if size <= 0: return` with no
counter and no log, so **"Bybit returned an ETHUSDT row with size 0" and "Bybit returned no
ETHUSDT row at all" are one value to me.** Both are "the venue says flat", which is why the
conclusion above holds — but *which* it was is unestablished, and that distinction is
exactly what separates a hedge-book selection bug from a venue/account mismatch.

---

## 3. Measurements (all 2026-09-09, direct HTTPS via `scripts/ops/diag_fetch.sh`, `served by https://ict-bot.duckdns.org`)

| # | read | result |
|---|---|---|
| A | `/api/diag/exchange_positions` @ 11:27:53Z | `bybit_2` → **XRPUSDT only** (58.5 @ 1.4121). `error: null`, so a clean read, not a failure. |
| B | `/api/diag/bybit_open_orders?account_id=bybit_2` @ 11:28:38Z | `read_state: orders_read`; positions = **XRPUSDT only**, `position_idx: 1`; orders = 2 XRP reduce-only Partial legs (TP 1.5535 / SL 1.3463); `order_symbols_unchecked: []`; `order_symbols_cross_checked: ["XRPUSDT"]` |
| C | journal notes on 5471 | Bybit's **own closed-pnl ledger** returned a matching close: `-0.25284115` @ 2451.27 |
| D | `/api/diag/broker_account_status?account_id=bybit_2` @ 11:31:12Z | `available_usd 231.32860365`, `read_state: coin_derived`, UID **553829655**, `shared_uid_groups: {}` |

**Manager's premise 1 — CONFIRMED, and now positively rather than by absence.**
`bybit_portfolio` holds XRPUSDT **11903.8 @ 1.4119** — a *different size* at a near-identical
entry (it is the demo mirror, `demo: true`, `BYBIT_API_KEY_3`, `account_class: paper`).
`bybit_1` holds SOL/ETH/ADA, its ETH being **Sell 41.11 @ 2492.67**. Only `bybit_2` matches
the terminal's XRP 58.5 @ 1.4121. **The terminal is `bybit_2`.** I had hypothesised the ETH
row might belong to `bybit_portfolio` sharing a UID; that hypothesis is **REFUTED** — it
holds no ETH and is a separate demo key, and `bybit_identity_summary.shared_uid_groups` is
empty.

### 3b. A suggestive margin cross-check — INFERRED, not established

`available_usd` is venue-sourced: `execute.py:873-894` derives it as
`equity − totalPositionIM − totalOrderIM` from **Bybit's own USDT coin block**, and refuses
unless all three are present. Against `equity 258.87` that implies total IM = **27.5414**.
XRP alone at the configured 3× is `58.5 × 1.4121 / 3 = 27.536` — a **0.02% match**. An
additional ETH 0.04 (~98.3 notional / 3× ≈ **32.8** IM) would put total IM near 60.3 and
`available_usd` near 198.6, not 231.33.

⚠️ **Why this is INFERRED and not MEASURED, stated rather than buried:** the `equity 258.87`
term comes from `/api/diag/exposure`, and `RiskManager.observe_exposure`
(`src/units/accounts/risk.py:519-541`) sources equity from `self.current_equity` /
`_account_equity_from_snapshot()` — **I did not establish that this is a fresh venue read
rather than a cached local snapshot**, and mixing a possibly-stale equity with a fresh
`available_usd` would be unsound. The two figures also come from different routes.

⚠️ **AND I ALMOST USED A CIRCULAR NUMBER HERE.** `/api/diag/exposure` also reports
`bybit_2.open_gross_notional: 82.7`, which equals the XRP position alone and looks like
crisp confirmation. It is **worthless as evidence**: the same function reads **notional from
the JOURNAL**, and the journal is the thing under suspicion. Quoting it would have been
proof of nothing but our own premise. Recorded because the next reader will find that field
and be tempted by it.

---

## 4. Defects found

### (a) CONFIRMED — the manager's third defect, and it is NOT merely latent

`src/units/accounts/clients.py::account_open_positions`, bybit branch, `_emit`
(`clients.py:1379-1394`): **dedupes by SYMBOL** (`if sym in seen: return`) and the emitted
dict carries **no `position_idx`**. Under hedge mode — armed on `bybit_2`/ETHUSDT since
2026-08-30 — a symbol with two non-zero books reports only the first and **silently drops
the second**.

**The manager called this "latent whether or not it caused this incident." It is stronger
than latent: it sits directly on a live real-money CLOSE decision.**
`_exchange_position_set` (`order_monitor.py:2884-2905`) keys the set on **`(symbol,
normalised_side)`**. So if the dropped book is the one the journal row is on, the test at
`order_monitor.py:4348` reads flat and **closes a live position** — the § 1 chain exactly.
Worked example: venue holds ETHUSDT idx2 short *and* idx1 long 0.04; the short is listed
first; `_emit` emits `("ETHUSDT","short")` and drops the long; the long row's test
`("ETHUSDT","long") in positions_cache` is `False`; the row closes.

⚠️ **This is a DIFFERENT SITE from
`BL-20260908-BYBIT-POSITION-PROTECTION-GRADES-A-SYMBOL-OFF-ROWS0-SO-A-HEDGE-BOOK-READS-FLAT-AND-A-LIVE-POSITION-IS-CLOSED`**,
which is
`_bybit_position_protection` at `order_monitor.py:8821` taking `rows[0]`, feeding the
**netting** reconciler. This one is `clients.py::_emit` feeding the **order-status**
reconciler. Same class, two independent sites, two different consumers — so fixing that row
does not fix this one.

⚠️ **It is NOT established that this mechanism closed 5471.** It requires a second non-zero
ETHUSDT book on `bybit_2` at 2026-09-08T13:37Z, and I have **no read from that moment** —
today's reads show no ETH book at all. Stating it as the cause would be a guess.

### (b) NEW — the protection surface's cross-check has no fallback, unlike its sibling

`account_bybit_open_orders` (`clients.py:1912`) iterates **`account.get("symbols") or []`**.
Its sibling `account_open_positions` calls **`_bybit_configured_symbols()`**, which falls
back to loading `accounts.yaml` by `account_id` — and whose own docstring names
`order_monitor._build_account_client` as a caller that "hand-builds a reduced cfg".

A caller handing in such a cfg gets a **silently empty** cross-check on the **protection**
surface: no error, no `order_symbols_unchecked` entry (that list only records *failed*
reads, not *skipped* ones), and a short `orders` list that reads as complete. Per that
function's own docstring, "a missing leg reads as missing protection", which drives a
**re-arm on a live position**. Today's diag path is unaffected because `list_accounts()`
does preserve `symbols` (§ 2.2) — this is one refactor away from a real blind spot, not a
live fault.

### (c) NEW, and it BLOCKED THIS INVESTIGATION — the decisive read collapses three states

`_emit`'s `if size <= 0: return` makes **"venue genuinely flat"**, **"venue returned a
zero-size row (the hedge-book sibling)"** and **"venue returned no row"** a single
observation, with no counter and no log line.

**This is a recurrence, one day apart, and it blocked root-cause BOTH times.**
`BL-20260908-BYBIT-POSITION-PROTECTION-GRADES-A-SYMBOL-OFF-ROWS0-SO-A-HEDGE-BOOK-READS-FLAT-AND-A-LIVE-POSITION-IS-CLOSED`
records verbatim: *"NOT
ESTABLISHED: which of `_flat`'s two triggers fired — `not rows` or `rows[0].size <= 0`. No
repo surface exposes the raw get_positions payload; saying which would be a guess."* I hit
the identical wall on a different function in a different P1. **That the same missing
instrument has now stopped two real-money investigations is the finding** — it is an
instrument gap, not a per-site nit, and it will stop the third.

---

## 5. WHAT I DID NOT ESTABLISH

1. **Whether the venue actually holds the ETH position.** Three independent venue reads
   (settleCoin page, symbol-scoped read, closed-pnl ledger) say flat; the operator says
   open. **Unreconciled.** I did not find a mechanism by which our reads could be wrong in
   the *specific* way required (a total absence, not a masked sibling — there is no second
   ETH book today for `_emit` to hide behind).
2. **Whether defect (a) caused this close**, per § 4a — no read exists from 13:37Z on 09-08.
3. **The raw `get_positions` payload** the manager asked for. Not exposable from any current
   repo surface (§ 4c). This is the single highest-value missing observation.
4. **Whether `bybit_2`'s API key and the operator's terminal are the same account context.**
   UID 553829655, `is_sub_account: null`, `shared_uid_groups: {}` — the route reports the UID
   but cannot tell me whether the key is scoped to a wallet the terminal aggregates over
   (e.g. UTA vs a Classic/Copy-Trading wallet on the same UID). The XRP match makes a
   *different UID* unlikely; a *different wallet within one UID* is untested and would
   reconcile every observation in this document at once. **I have no evidence for it — it is
   a hypothesis, not a finding.**
5. **A number in the operator's report does not reconcile with itself, and this matters.**
   Reported: `0.04 ETH, entry 2,453.97, PnL +0.6464 USDT (+1.97%), mark 2,457.59`.
   `0.04 × (2457.59 − 2453.97) = +0.1448`, **not** +0.6464. Conversely +0.6464 on 0.04
   implies a move of +16.16, i.e. a mark near **2470.13**. The PnL and the +1.97% agree with
   each other (0.6464 / (98.16/3) = 1.98% ROI on 3× margin) but **neither agrees with the
   stated mark**. So either the mark and the PnL were read at different moments, one figure
   was transcribed in relay, or the row is not live. This is the only quantitative handle
   anyone has on whether the terminal view is current, and it should be resolved with the
   operator before more engineering is spent — **it is not a basis for dismissing the
   report**, and I am flagging it rather than resolving it.

---

## 6. What would settle it (all read-only)

1. **A raw `get_positions` dump for `bybit_2`/ETHUSDT** including zero-size rows and
   `positionIdx` — the observation § 4c currently makes impossible. Highest value; it
   discriminates *venue flat* from *hedge-book masking* in one read.
2. **A fresh venue `balance()` for `bybit_2`** giving `equity`, `totalPositionIM`,
   `totalOrderIM` from one response, which would turn § 3b from INFERRED into MEASURED and
   independently decide whether ~33 USDT of ETH margin exists.
3. **From the operator:** a screenshot refresh, and confirmation of which wallet/account
   context the terminal is showing (UTA vs Classic vs Copy Trading), plus the mark/PnL
   inconsistency in § 5.5.
4. **Bybit's closed-pnl list for ETHUSDT on `bybit_2` around 2026-09-08T13:37Z** — to check
   whether the record matched to 5471 was genuinely 5471's close or a partial close of a
   larger netted long book that `account_closed_pnl_for_trade` attributed to it. Under
   netting, several journal rows share one exchange book (`_cascade_close_netted_siblings`
   exists for exactly this), so mis-attribution is a live possibility I could not exclude.

---

## 7. Why this class is worse than an ordinary protection gap

Repeating the manager's framing because the evidence bears it out: **this is not a
protection failure that alarms — the position is invisible to the thing that protects it.**
Every safety sweep is gated on a position the journal believes is open, or on a venue read
that returns the position. Here the journal says closed and the venue read returns nothing,
so `_check_broker_naked_bybit_positions` never considers it, the reverse reconciler never
adopts it, and it reaches no operator surface. It was found only because a human looked at
the exchange.

**Any fix that re-arms a stop without explaining the invisibility has fixed nothing.**

---

# ADDENDUM — 2026-09-09, after the operator directive

> **Operator, verbatim:** *"absolutely not - the bot opened the trade with brackets, the bot
> needs to fix this. the system cannot and will not rely on me manually checking and fixing
> every trade - that is a recipe for disaster and will ensure that never have useable data
> for analysis and improvement"*

**§ 6 of this document routed the remedy to the operator. That framing was wrong and is
withdrawn.** "What would settle it" listed *a screenshot refresh* and *confirmation of which
wallet the terminal shows* as if a human check were a step in the repair. It is not a step in
the repair; the repair is the bot's. What § 6 still validly contains is the list of READS
that discriminate the cause — kept, and re-scoped as instrumentation rather than as a hand-off.

## 8. The real finding is a CLASS, and it is bigger than this trade

Every protective sweep in this system is anchored to one of exactly **two** lists. There is
no third path.

| | list | who reads it | what anchors it |
|---|---|---|---|
| **A** | the **VENUE** position list | `_reconcile_orphan_exchange_positions` (`order_monitor.py:2979`) → adopt → `_rearm_broker_protection_after_recovery` (`:7575`) → `_attempt_naked_autoprotect` (`:7692`) | `account_open_positions(cfg)` at `order_monitor.py:3128` |
| **B** | the **JOURNAL-OPEN** list | `_check_broker_naked_bybit_positions` (`:9716`), and the DB-driven `_check_naked_positions` | `SELECT … FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0` (`order_monitor.py:9845`) — **verified this session, not inherited** |

**A position in NEITHER list is permanently invisible to every safety mechanism the system
has.** Trade 5471 is exactly that: `status='closed'` removes it from B, and the venue read
does not return it, which removes it from A. Nothing is broken in the repair chain — it is
never *reached*.

⚠️ **THE REPAIR CHAIN ITSELF IS ALREADY BUILT, UNCONDITIONAL, AND VENUE-CORRECT** — verified
in the code this session, not assumed. `_attempt_naked_autoprotect`'s Bybit branch
(`order_monitor.py:7706`) calls `client.set_trading_stop(tpslMode="Full", stopLoss=…,
takeProfit=…)`, is hedge-aware through `position_idx_for`, and has **no enable gate** (the
Prime Directive's "no third gate"). Full mode REPLACES rather than adds, so a re-arm can
never grow the leg count toward Bybit's 20-leg cap. **If either list had contained this
position, the bot would have re-protected it by itself, with nothing new built.** So the
deliverable is not a repair — it is a **third anchor**.

## 9. STEP 1 SHIPPED — the discriminating instrument

`GET /api/diag/bybit_raw_positions` (this PR) returns the raw `get_positions` payload
un-deduped and un-filtered, zero-size rows included, every row carrying `position_idx` and
both `size_raw` and `size`. It exists to answer the one question that decides STEP 2 —
**does the venue not return it, or does the bot drop it?** — which no surface could answer,
on two real-money P1s one day apart.

**It is an instrument, not the answer.** The answer requires reading it against the live
venue after deploy, and this document must not be read as containing it.

## 10. STEP 3 — the DETECTOR for the class, PROPOSED (Tier-2, not shipped)

The requirement is a cross-check that **does not read through either failing list**, so a
position in neither is still found.

### The proposal: a venue-anchored residual sweep, on the cadence that already exists

**The third anchor is the venue's own `settleCoin` position page, read WHOLE.** It is
neither list A (which is that page *after* `_emit`'s dedupe and zero-size filter) nor list B
(the journal). Concretely, in the existing Bybit sweep:

1. Take the raw rows for the account (the new accessor — **no dedupe, no size filter**).
2. Keep every row with `size > 0`. That is the venue's own claim about what it holds.
3. Subtract what the journal accounts for: the open rows on that `(account, symbol,
   direction)`.
4. **A venue position with non-zero size that no open journal row explains is the finding** —
   `unattributed_venue_position`. Trade 5471's ETH would land here on its first pass.
5. Route it into the **existing** adopt path (`_reconcile_orphan_exchange_positions`), which
   already re-arms protection. Nothing new is built for the repair.

### Cost per tick, stated rather than waved at

- **Broker calls added: ZERO.** The sweep at `order_monitor.py:9716` is already cadence-gated
  by `IB_BROKER_NAKED_CHECK_SECONDS`-style pacing and already fetches Bybit positions per
  account. The residual is computed from rows **already in hand**, plus one journal `SELECT`
  the same pass already runs.
- ⚠️ **An unbounded per-position broker call is the June 2026 wedge shape and is deliberately
  NOT what this proposes.** If the design ever needs a per-symbol confirm, it must ride the
  existing cadence gate, never the per-tick path.
- **Compute:** one set-difference over the account's symbols — the same order as the coverage
  sum already computed beside it.

### Three refusals that keep it safe

1. **DETECT-ONLY FIRST, and the reason is measured, not cautious-by-default.** The one prior
   auto-remediation of an adjacent class **cancelled the leg that MATCHED the journal**
   (`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`), and the netting
   reconciler reading a hedge book wrong closed a live real-money position
   (`BL-20260908-…-ROWS0`). A detector that pages and a repair that acts should not land in
   the same change.
2. **`unreadable` must never present as `unattributed`.** A failed venue read means *we did
   not look*; treating it as "the venue holds something the journal does not" would
   manufacture phantom adoptions on every transient API error. Three states —
   `attributed` / `unattributed` / `unreadable` — registered with `collapsed-state-guard`.
3. **It must be side- and book-aware from the start.** The residual is per `(symbol,
   position_idx)`, not per symbol, or it reintroduces the exact hedge-book collapse that
   caused this incident.

### What this does NOT cover, stated plainly

It closes the case where **the venue reports the position and the journal does not**. It does
**not** close the case where the **venue itself does not return the position to this API
key** — which, on the three reads in § 3, is what `bybit_2`/ETHUSDT currently looks like. If
STEP 1's raw read confirms the venue genuinely returns nothing, then **no venue-anchored
detector can see it either**, and the answer moves to STEP 2: find the key or the account
context that does return it. **That is the honest limit of this proposal and it must not be
oversold** — a detector that cannot see the motivating instance is not a fix for it, and
saying otherwise would be the "shipped ≠ working" failure this repo keeps paying for.
