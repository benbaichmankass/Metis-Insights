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

---

## 11. AMENDMENT — a THIRD anchor that is blind to neither failing list

⚠️ **§ 10's detector reads the venue POSITION LIST, so it cannot see this position either.** That
limit was stated when it was written and is now **CONFIRMED, not hypothetical**: a symbol-scoped
`get_positions(category, symbol="ETHUSDT")` on `bybit_2` returned no `size > 0` row on three
separate reads (2026-09-09 at 11:27:53Z, 11:28:38Z, 12:20:40Z; all `error: null`, i.e. clean reads).
A detector anchored on any position read is therefore **not a fix for the motivating instance**, and
§ 10 must not be shipped as if it were.

**This amendment proposes an anchor that does not read the position list at all.**

### The wallet's own margin accounting is a third, independent source

Bybit reports, per coin, `equity` / `totalPositionIM` / `totalOrderIM` — **what the account has
PLEDGED**. That is a different endpoint and a different question from *"list my positions"*:

> **If the venue is holding initial margin that the positions we know about do not account for,
> then a position exists that we cannot see — whether or not the position list returns it.**

**This is not a new technique here; it is already validated in this repo.**
`src/units/accounts/execute.py::_derive_available_from_coin_block` (docstring, 2026-08-13,
`BL-20260813-ICTSCALP-BTC-BYBIT2-BALANCE-REJECTS`) records that the venue's own `totalPositionIM`
sat **0.22%** from a modelled notional/leverage reconstruction, and reproduced a journal
reconstruction from open legs to **0.05%** ($226.69 vs $226.80). So *modelled IM vs venue IM* is a
comparison this system has already shown agrees to a fraction of a percent.

### The detector

1. Read the wallet ONCE per account (`equity`, `totalPositionIM`, `totalOrderIM` from **one**
   response — they must come from the same read or the subtraction is unsound).
2. Model the IM the positions we KNOW about should pledge: `Σ (notional / leverage)` over the
   venue rows we can see, plus the journal-open rows.
3. `residual = venue_totalPositionIM − modelled_IM`.
4. A residual materially above the tolerance band means **unaccounted pledged margin** — a position
   is held that no list returned.

### Why this survives the exact failure that defeats everything else

Both failing anchors are enumerations of *things*: the venue's position list (A) and the journal's
open rows (B). This is an **aggregate** the venue computes over its own book. A position missing
from a listing still consumes margin, so it cannot hide from the sum. **It is blind to neither list
because it reads neither.**

Applied to this incident: ETH 0.04 at ~2454 on 3× isolated is **≈32.8 USDT** of IM. `bybit_2`'s
whole equity is ~259, so an unaccounted ETH would move `totalPositionIM` by **~12.7%** — an order of
magnitude outside the 0.22% agreement the technique has already demonstrated. **This detector would
have fired.**

### What it does NOT do — stated before anyone builds it

- ⚠️ **It detects EXISTENCE, not IDENTITY.** It says *"~33 USDT of margin is pledged that nothing
  explains"*, never *which symbol*. That is still the difference between a loud alarm and the
  five days of silence this incident actually got, but a design that promises symbol attribution
  from this signal is overselling it.
- ⚠️ **It cannot itself re-arm anything.** With no symbol it has nothing to pass to
  `set_trading_stop`. It is the DETECTOR half; the repair still needs an identity, which is why
  STEP 1's raw read stays the critical path rather than being superseded by this.
- ⚠️ **The tolerance band is a real design problem and is not solved here.** Leverage and
  margin-mode assumptions make the model approximate; cross-margin, a mid-flight order's
  `totalOrderIM`, and funding all move it. The 0.05%/0.22% figures above are **n = 2 observations
  on one account on one day** — a precedent that the approach works, *not* a calibrated band. Any
  build must measure the residual's distribution across accounts and days FIRST and set the band
  from that, or it becomes the desensitised alarm this repo already calls a P1.
- **Cost:** one wallet read per account per cadence window — the same call
  `/api/diag/broker_account_status` already makes. **No per-position broker call**, so it is not
  the June 2026 wedge shape.

### Honest status

**PROPOSED, not shipped, and not yet costed against real data.** It is Tier-2 (a new alarm on the
live tick). It is offered because § 10's detector demonstrably cannot see the case that motivated
it, and shipping only that one would be the "shipped ≠ working" failure this repo keeps paying for.

---

## § 12 — STEP 1 RESULT: the raw payload, and BOTH pre-registered branches are falsified

**Measured 2026-09-09T13:12:31Z** via `/api/diag/bybit_raw_positions`, on live sha `d87db750`
(`git_sha == git_sha_on_disk`, `restart_pending: false` — verified before the read, so this is the
new code and not a stale process). **Population: 5 queries** on `bybit_2` (one `settleCoin=USDT`
page + one symbol-scoped read for each of the 4 configured symbols), then the same instrument
across **all 3 Bybit accounts**. Zero query errors — every `query_state` read `rows_returned`, so
there is no `could_not_look` hiding in this result.

### The answer

`symbol:ETHUSDT` on `bybit_2` returned **`row_count: 2`** — *both* hedge books:

| book | `size_raw` | `size_parsed` | `side` | `avg_price` | `updated_time` |
|---|---|---|---|---|---|
| `position_idx: 1` (hedge-long) | `"0"` | true | `""` | `null` | **2026-09-08T13:37:13.873Z** |
| `position_idx: 2` (hedge-short) | `"0"` | true | `""` | `null` | 2026-09-03T13:34:01.018Z |

**I pre-registered two branches before this read and the result is NEITHER. I am recording that
rather than retro-fitting the outcome to whichever branch it most resembles.**

- ❌ **"`zero_size_rows ≥ 1` ⇒ the position is on a hedge book our readers discard, so the fix is a
  filter/book-selection change."** There ARE zero-size ETH rows, so this branch's *trigger* fired —
  but its *consequence is false*. `size_raw` is the literal string `"0"`, `avg_price` is `null` and
  `side` is empty. That is not a live book we filter out; it is the venue stating FLAT. Had I read
  only `zero_size_rows ≥ 1` and stopped, I would have shipped a book-selection fix for a position
  that is not on either book.
- ❌ **"no ETHUSDT row at all ⇒ nothing in our code can see it."** Rows ARE returned. Bybit is
  answering about ETH to this key.

**The third outcome: the queries WE SENT return ETHUSDT FLAT on both `bybit_2` books, and have
since 2026-09-08T13:37:13.873Z.** That timestamp is the close instant this investigation was opened
about — so the venue's own book agrees the close happened then, and records nothing since.
`bybit_portfolio`'s ETH long book stamps **13:37:13.868Z**, 5 ms earlier: the paper mirror closing
in the same action, which is the roster-sync invariant behaving.

### Across every Bybit account this system holds keys for

| account | class | UID | open ETHUSDT |
|---|---|---|---|
| `bybit_1` | paper | 553832447 | **none** (idx1 flat 09-09T04:46:38Z, idx2 flat 09-09T12:46:36Z) |
| `bybit_2` | **real money** | 553829655 | **none** (above) |
| `bybit_portfolio` | paper | 576206494 | **none** |

Three distinct UIDs, `shared_uid_groups: {}`, `unread_bybit_accounts: []`.

### What this means — and it is NOT a manual-fix hand-off

The operator's directive stands and is not being routed around: *the bot must fix its own trades.*
The finding is that **on every book the bot's credentials can reach, ETH is flat** — so there is no
unprotected position here for the bot to re-arm, and § 10 / § 11's detectors would correctly stay
silent. The defect this exposes is one level up and is still the bot's to fix:

> **Nothing in this system verifies that the set of books its keys can READ equals the set of books
> the operator's capital is actually ON.** Every protective sweep, every detector in § 10 and § 11,
> and the whole two-list class in § 9 are scoped to books we can see. A position on a book outside
> that set is not merely unprotected — it is outside the coverage of every mechanism we have,
> including the ones proposed to fix this incident. That gap has never been measured.

That is a **coverage assertion**, mechanisable and automatic — not a request for the operator to
check trades by hand.

### ⚠️ A reporting trap in my own instrument

`nonzero_size_rows: 2` on `bybit_2` is **ONE position (XRPUSDT 58.5) seen by TWO queries** — the
settleCoin page and the symbol-scoped read. The instrument deliberately does not dedupe, because
dedupe is exactly what destroyed the evidence in the readers it was built to replace. Correct for
its purpose, but **the counter is rows, not positions**, and a reader who treats it as a position
count will double every position. Read `source_query` before counting.

### WHAT I DID NOT ESTABLISH

1. **Where the operator's ETH position actually is. Untested, not refuted.** Three live candidates:
   (a) a **different UID / sub-account** — `is_sub_account` reads `None`, which is *we could not
   look*, **never** *no*; (b) a **different product category** — every read in this document is
   `category: "linear"`, so an `inverse` or Unified/spot-margin ETH book is invisible to all of it;
   (c) the terminal was stale when screenshotted.
2. **Whether the 13:37:13Z close was CORRECT.** The venue confirms a close *occurred*; that says
   nothing about whether `exit_reason='sl'` describes it truthfully. Trade 5471's stated reason
   remains unvalidated against the actual fill — do not read § 12 as clearing it.
3. **The margin residual.** `/api/diag/broker_account_status` returns only the DERIVED
   `available_usd` (232.20, `coin_derived`), not the `totalPositionIM` / equity components § 11's
   detector needs, so the third anchor is still uncomputed here.
4. **The operator's own arithmetic still does not close** — `0.04 × (2457.59 − 2453.97) = +0.1448`
   against a reported `+0.6464`. This is now *more* interesting, not less: it is consistent with the
   numbers coming from a book none of these reads cover. It is **not** a reason to doubt the report.

**The one question that discriminates (1) and is answerable only outside this repo:** which Bybit
**UID** is the operator's terminal signed into, and is that ETH row on the **linear perp** book?
`bybit_2` is UID **553829655**. This is an identification question, not a repair instruction.

---

## § 13 — Incidental, and it is about a LOUD UNFIXED REAL-MONEY bug: what `rows[0]` actually returns

Not part of MI-221's mandate. Recorded because the STEP 1 instrument can measure the exposure of
`OI-20260908-A-HEDGE-BOOK-FLAT-READ-IS-CLOSING-LIVE-BYBIT-POSITIONS-AND-THE-FLAP-GUARD-CANNOT-SEE-IT`
directly, and "not my task" is not a valid disposition in this repo.

That row's root cause is `src/runtime/order_monitor.py:8821` — `pos = rows[0]` on a symbol-scoped
`get_positions`, with no zero-size skip and no book selection. What it leaves open is *when* `rows[0]`
hands you the wrong book. **Measured 2026-09-09T13:12Z, population: 14 symbol-scoped queries across all
3 Bybit accounts, 11 of which returned two rows.**

### 1. The venue's row order is NOT `positionIdx`-ascending

13 of 14 queries returned idx 1 before idx 2 — and **`bybit_1` / `ETHUSDT` returned idx 2 first**. Any
reasoning of the form *"`rows[0]` is the long book"* is therefore false, and one counter-example is
enough to establish that.

### 2. The order is consistent with `updated_time` DESC — 9 discriminating observations, 9 consistent

Of the 11 two-row queries, **2 are timestamp ties and discriminate nothing**, so they are excluded rather
than counted as support. The remaining 9 all order newest-first, and the single non-idx1-first case is
exactly the one whose idx 2 was touched more recently (`09-09T12:46:36Z` vs `09-09T04:46:38Z`).

**So `rows[0]` returns the MOST RECENTLY UPDATED book, not the live one.**

### 3. Which makes the trigger concrete, and it is not exotic

> A live position on one book **+ any more-recent touch on its flat sibling** ⇒ `rows[0]` is the flat
> row ⇒ `size 0`, `side ''`, empty `sl_leg_ids` ⇒ the position reads FLAT.

Flat books demonstrably do get touched: `bybit_1`/ETHUSDT's flat idx 2 was updated **8 hours after** its
flat idx 1, with no position on either. This is not a narrow race; it is a matter of time.

### 4. Currently live and NOT firing — by luck, not by safety

All 3 open positions sit at `rows[0]` right now (`bybit_1` ADA idx 0, `bybit_2` XRP idx 1,
`bybit_portfolio` XRP idx 1). Nothing is being mis-read at this moment. **That is the ordering happening
to favour us, not the code being correct**, and it must not be cited as evidence the path is sound.

### WHAT THIS DOES NOT ESTABLISH

- **Bybit documents no ordering guarantee, and 9 consistent observations at one instant are not a
  contract.** That is the finding, not a caveat to route around: `rows[0]` depends on an ordering the
  venue never promised, so it can change with no notice and no deploy on our side.
- **I did not observe the bug fire.** No mis-read close was caught in the act.
- **Whether `updated_time` is the actual sort key** — any key correlated with recency fits these 9 equally
  well. The remedy does not depend on knowing which: select the book **explicitly by `positionIdx`**.

### Disposition — NOT fixed here

**Tier-2, declaring `hold`.** `order_monitor.py:8821` is a live real-money order path and MI-221's
constraints are reads-only. This is measurement handed to whoever owns `OI-20260908`. It sharpens that
row's `clears_when`: a fix must select on `positionIdx` explicitly, and **must not be validated by "the
live row came back first"** — that condition holds today and proves nothing.


---

## § 14 — CORRECTION to § 12: I reported my own instrument's silence as a fact about the venue

**Operator, 2026-09-09, looking at the position on the Bybit platform while § 12 was being written**
(verbatim, relayed): *"Do not tell me that the trade does not exist. That is false... you might not be
seeing it... it might be an issue with how we've built our API integration, but it's on the platform.
There's no way that you cannot pull that data. Figure it out, make it happen."*

**They are right.** § 12's headline read *"the venue **truthfully** reports ETHUSDT FLAT"*. That word
asserts the venue told us the whole truth, which is not something an empty read can establish — and
the reading it was based on came from an instrument that, I now know, **sent the same two queries the
production readers send**. It could discriminate HOW a row was dropped and was blind to any book those
filters never ask about. **An empty read is evidence about the READ.** The § 12 sentence is corrected
in place above; this section records why, rather than letting the edit pass silently.

### What was actually wrong with the instrument

Three filters, live in the production readers, each able to hide a real position — and the first
version of the raw route inherited all three:

1. **`settleCoin` hardcoded `"USDT"`** (`clients.py:1374`, `clients.py:1892`). A **USDC-settled**
   contract is the SAME `linear` category and is excluded.
2. **`category` from a single configured `market_type`** (`bybit_2` is pinned `linear`), so
   **inverse / spot / option were never queried at all**.
3. **The per-symbol cross-check iterates only the configured roster**, and **no position read anywhere
   passes `baseCoin`** — verified by grep across `src/`, whose only `baseCoin` occurrences are
   `marketUnit="baseCoin"` on the ORDER path. On `/v5/position/list`, `baseCoin` matches every contract
   on that base regardless of settlement, so it is the one call that reaches USDT, USDC and inverse at
   once.

**And a fourth, found while fixing those:** `grep -rn "nextPageCursor" src/` returns **zero hits**. The
client is `pybit.unified_trading.HTTP`, one request per call with no auto-pagination, so **every
position read in this repo was page-1-only** and a truncated page was indistinguishable from a complete
one.

### What SURVIVES § 12 and what does NOT

Stated explicitly, because a finding that quietly keeps a falsified exhibit is worse than no finding.

**SURVIVES — it rests on CODE, read this session, not on the ETH instance:**

- § 9's structural claim: every protective sweep anchors on either the venue-position list or the
  journal-open list, and a position in neither is invisible. Verified with line numbers.
- § 13's measurement of what `rows[0]` returns (`order_monitor.py:8821`) — a separate, still-unfixed
  real-money defect, measured across 14 queries.
- The three filters above and the missing cursor — all four are code facts, and all four are the reason
  the instance could not be seen.
- `BL-20260909-NOTHING-VERIFIES-THAT-THE-BOOKS-OUR-KEYS-CAN-READ-ARE-ALL-THE-BOOKS-THE-CAPITAL-IS-ON`.
  This row was filed BEFORE the correction and is **strengthened** by it: it named exactly this gap.

**DOES NOT SURVIVE:**

- Any reading of § 12 as *"there is no ETH position"*. It never said that in its NOT-ESTABLISHED list,
  but its headline implied it, and the implication is withdrawn.
- The framing that the remaining explanation was *identification* (which UID is the operator on).
  Hypothesis 4 (**wrong ACCOUNT**) is **refuted** on evidence: `/api/diag/bybit_open_orders` returns the
  operator's XRP position with their exact brackets (TP 1.5535 / SL 1.3463, qty 58.5), so our key and
  their screen are on the same book. ⚠️ **That refutes wrong-ACCOUNT and NOT wrong-or-partial KEY
  SCOPE** — a key can be correctly bound and still be scoped so `/v5/position/list` omits books it is
  not permissioned for, and `is_sub_account` still reads `None` (*we did not look*). Weakened, not
  closed.

### The sharpened question

**On an account our key demonstrably reads correctly, why does `/v5/position/list` not return a position
the web UI shows?** The widened sweep exists to answer that, and if every widened query comes back empty
**and** the wallet's initial margin is fully explained by the visible XRP position, then the answer is a
**key/permission scoping** problem — reported with the UID and the margin numbers, **never** as "no
position exists."
