# The Alpaca sibling-liquidation mechanism, established from code — and why `alpaca_portfolio`/TLT is the same condition on one exit path and not on the other

> **Doc status:** `live` · category `evidence` · last verified `2026-09-08` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Scope: MEASUREMENT AND PROPOSAL ONLY.** Nothing was closed, cancelled,
> repaired or re-armed. No order-path file is edited by this unit. The remedy
> is a proposal at [`docs/design/alpaca-trade-scoped-close-PROPOSAL.md`](../design/alpaca-trade-scoped-close-PROPOSAL.md),
> `landing: hold`.

---

## 0. What this adds to MI-171

MI-171 counted the **population**. It did not establish the **mechanism**, and
said so. This unit establishes the mechanism from the code and from a live venue
read, and answers the question the dispatch put second: whether the live
`alpaca_portfolio`/TLT shape is the condition
`BL-20260907-ALPACA-CLOSE-OF-ONE-TRADE-LIQUIDATES-ITS-SIBLINGS` names, or a
different one that resembles it.

**The short answer to that second question is that it is BOTH, and the split is
the finding:** TLT satisfies the backlog row's precondition exactly on the
**monitor-driven** exit path, and does **not** satisfy it on the **venue-side**
exit path, because a resting Alpaca stop fills for its own quantity. Which path
fires decides whether the blast radius is 72 shares or 16. Nothing in the
Bybit-netting analogy predicts that split, which is why it had to be read out of
the code rather than inferred.

---

## 1. The mechanism, read from the code

Every link below was read at `origin/main` (`161ee60`) this session. No step is
taken from MI-168's prose.

```
order_monitor._send_close_to_exchange(matched_trade)          src/runtime/order_monitor.py:1682
    qty = float(matched_trade["position_size"])   ← the PER-TRADE size (16, or 56)
        │
        ▼
execute.close_open_position(..., symbol=, side=, qty=)        src/units/accounts/execute.py:2767
    guards `if qty <= 0: return error`   ← qty is validated …
    if exchange == "alpaca":
        resp = exchange_client.close(symbol)      ← … and then NEVER PASSED
        │
        ▼
AlpacaClient.close(self, symbol)                              src/units/accounts/alpaca_client.py:859
    ← the signature has no qty parameter at all
```

`execute.py`'s own comment states the discard rather than hiding it: *"Whole-position
flatten only — Alpaca's close-position endpoint closes the entire symbol position, so
the qty argument is informational here (partial-close is not wired)."* The gate
`account_supports_management(cfg, "close")` passes — `clients.py:727` reads
`"alpaca": frozenset({"modify", "close", "open_positions"})` — and
`alpaca_portfolio` is `mode: live`, so the dry-run short-circuit does not apply.
**The path is live and reachable, not theoretical.**

### 1.1 Blast radius depends on the US equity session, and only there

`AlpacaClient.close` branches on `us_equity_session()` before doing anything:

| session | what it does | blast radius |
|---|---|---|
| `closed` | returns `retCode 2` (defer). Places nothing, **cancels nothing**, leaves the bracket armed. | **0 shares** |
| `extended` | `_close_extended_hours(sym)`: `_cancel_open_orders_for_symbol(sym)`, then ONE marketable limit for `qty = int(abs(float(pos["qty"])))` — read from the **live position**, not the caller | **the whole symbol** |
| `regular` | `_cancel_open_orders_for_symbol(sym)`, then `DELETE /v2/positions/{sym}` — Alpaca's whole-symbol liquidation. `want_qty` is likewise read from the live position | **the whole symbol** |

So the over-close is not a property of "closing on Alpaca"; it is a property of
closing **while the US equity market is open or in extended hours**. At the time
of writing (`us_equity_session()` evaluated this session → **`closed`**) a close
of either TLT row would defer and touch nothing. That is a scheduling accident,
not a safeguard.

### 1.2 There are TWO destructive steps, not one

The backlog row names the flatten. The cancel that precedes it is a second,
independent effect and is not named anywhere:

`_cancel_open_orders_for_symbol` is documented as *"Cancel **every** resting
order on *symbol*"*. It is symbol-scoped with no trade identity. So a close of
trade A **cancels trade B's protective bracket** before the flatten is even
attempted. On the `regular`/`extended` paths that hardly matters, because the
flatten then removes B's position too. **But on any path where the flatten fails
or partially fills, B is left holding shares with its protection cancelled** —
i.e. naked. The `close` docstring's own contract ("the DB row then never closes
and the monitor retries the same failing close every tick") describes exactly
that state persisting.

### 1.3 The same scope defect exists on `modify`, and it is not filed

This is a mechanism finding the dispatch did not ask for and that the analogy
would not produce.

```
execute.modify_open_order(...)              src/units/accounts/execute.py:277
    resp = exchange_client.modify_protective(symbol, sl=sl, tp=tp)
                                            ← symbol only. No trade id. No qty.
```

`AlpacaClient.modify_protective(self, symbol, sl=None, tp=None)` — *"Replace the
resting SL/TP legs of the open bracket on *symbol*."* On a symbol holding two
journal rows there is no "the" bracket. One trade's trailing-stop amend
therefore rewrites whatever protective legs happen to rest on the symbol,
including a sibling's.

**This is not merely possible here; the live venue state is what that path
predicts.** See § 3.2.

### 1.4 The success log names a quantity the operation never used

`close_open_position`'s alpaca branch logs, on success:

```
"close_open_position: account=%s symbol=%s side=%s qty=%s → alpaca flatten (orderId=%s)"
```

`qty` here is the discarded per-trade size. So a whole-symbol liquidation of 72
shares logs `qty=16`. By this repo's own taxonomy that is **UNPROVENANCED
DIAGNOSTIC OUTPUT, sub-class A** (the label names a quantity the code did not
act on), and the remedy CLAUDE.md prescribes is to branch on the actual condition
rather than reword the label. It matters here beyond tidiness: this log line is
the only per-close record a reviewer would find, and it reads as a trade-scoped
close.

---

## 2. Is the Alpaca mechanism the Bybit one? No.

Stated because the dispatch warned against assuming the analogy carries, and it
does not.

| | Bybit (`BL-20260801`, 187 events) | Alpaca (this row) |
|---|---|---|
| what nets | the **venue** nets N journal rows into one position | the **venue** also nets — Alpaca holds one position per (account, symbol) |
| what goes wrong | a **position-level** exit shrinks the netted position while close detection is **per-order**, so siblings keep their full `position_size` | the **operation we chose** (`DELETE /v2/positions/{sym}`) is whole-symbol, and the per-trade qty we were given is discarded before the wire |
| is a qty-scoped close available? | yes — Bybit takes a reduce-only qty, and `close_open_position` **sends** it | **not wired.** `AlpacaClient.close` has no qty parameter |
| detection | `covered_qty` — a **quantity** | `protection_state` — **sides**, not a quantity (§ 3.3) |

The two share a symptom and not a cause. Bybit's is a *reconciliation* defect —
the venue did the right thing and the journal failed to follow. Alpaca's is an
*operation* defect — the journal is right and we send the wrong instruction. A
fix modelled on Bybit's attribution reconciler would not touch Alpaca's problem.

---

## 3. The live `alpaca_portfolio` / TLT state

### 3.1 Re-verified, and it holds

Read `2026-09-08T01:40:23Z` via `/api/diag/exchange_positions?account_id=alpaca_portfolio`,
`error: null`:

```
TLT  side=short  size=72.0  entry=82.309028  unrealised_pnl=3.530016
```

Journal (`/api/diag/journal?table=trades&limit=1000`, ids 4545–5544):

| id | strategy | dir | size | status | created |
|---|---|---|---|---|---|
| **5266** | `tlt_pullback_1d` | short | **16.0** | `open` | 2026-08-31T13:30:26Z |
| **5414** | `tlt_pullback_1h` | short | **56.0** | `open` | 2026-09-03T13:30:32Z |

**16 + 56 = 72, matching the venue exactly.** There is no journal/venue quantity
divergence here — the precondition is present in its cleanest form. Both rows are
short, so they net rather than offset. This is **PAPER money** (`account_class:
paper`) on an account that is `mode: live`, so closes execute for real against
the paper venue.

**Verdict on the dispatch's second question:** for the **monitor-driven** exit
this is the backlog row's condition exactly — `_send_close_to_exchange` would pass
`qty=16` (or `56`), `close_open_position` would discard it, and
`DELETE /v2/positions/TLT` would liquidate all 72, leaving the sibling row `open`
with nothing behind it. For the **venue-side** exit it is *not* that condition:
a resting Alpaca stop or limit fills for its own quantity, so the blast radius is
that leg's qty. Establishing which of the two would fire first is § 3.2.

### 3.2 The resting protection does not belong to either row cleanly

`/api/diag/alpaca_open_orders?account_id=alpaca_portfolio`, read
`2026-09-08T01:42:07Z`, `read_state: orders_read` — **a confirmed clean read, so
the absences below are measured, not assumed**:

TLT holds exactly **one** OCO group, of **16 shares**:

| leg | type | side | qty | price | submitted |
|---|---|---|---|---|---|
| parent | limit | buy | 16.0 | **74.67** | 2026-08-31T13:36:40Z |
| child | stop | buy | 16.0 | **82.33** | **2026-09-04T17:35:55Z** |

Cross-referenced against the journal's declared levels:

| | qty | TP | SL |
|---|---|---|---|
| trade **5266** | **16** ✔ | 74.670375 → **74.67** ✔ | 84.61875 → 84.62 ✘ |
| trade **5414** | 56 ✘ | 74.18834 → 74.19 ✘ | 82.32875 → **82.33** ✔ |
| **resting OCO** | **16** | **74.67** | **82.33** |

**The resting group carries trade 5266's quantity and take-profit, and trade
5414's stop-loss.** Its parent was submitted six minutes after 5266 opened; its
stop child was submitted four days later, on 2026-09-04 — *after* 5414 opened on
09-03.

Two explanations fit, and **I could not distinguish them**:

* **(a) cross-trade contamination** — 5414's monitor called
  `modify_protective("TLT", sl=82.32875)`, which is symbol-scoped (§ 1.3) and
  patched the only resting stop on the symbol, which was 5266's.
* **(b) journal/venue divergence** — 5266's own stop was trailed to 82.33 at the
  venue and `trades.stop_loss` was never updated, so it still reads 84.61875.

(a) is what the code path predicts and explains the 09-04 replacement timestamp;
(b) cannot be excluded because a short's trailing stop legitimately moves down
and both rows are short on the same instrument at nearly the same level. **The
evidence that would separate them is gone:** the amend would have been logged by
`modify_open_order`, and this repo's own measurement puts the systemd journal's
retention at roughly 30 minutes. I am recording the ambiguity rather than
picking the more interesting branch. Either way the row is a finding, and both
branches sit in the class CLAUDE.md already names —
`BL-20260823-MODIFY-IDEMPOTENCE-COMPARES-INTENT-TO-JOURNAL-NEVER-TO-VENUE`.

**The operational consequence does not depend on which is true:** the stop rests
at 82.33 against a position whose implied mark is ≈82.26 (derived from
`entry 82.309028` and `unrealised_pnl 3.530016` over 72 short). That is **≈0.08%
away**. It is the nearest exit trigger on this symbol by a wide margin — 5266's
declared stop at 84.62 is ~2.9% away — and it is 16-qty, so if it fires the blast
radius is 16, not 72.

### 3.3 56 of the 72 shares are unprotected, and the sweep cannot see it

72 short, 16 covered. **56 shares rest with no protective leg of any kind.**

They are invisible to the broker-naked sweep by construction.
`_check_broker_naked_equity_positions` calls `AlpacaClient.protection_state`,
and its own inline comment records the limit: `stop_qty=None,  # Alpaca grades
sides, not qty`. The sweep asks *"does a stop leg rest? does a target leg
rest?"*. On TLT both answer yes. A position carrying protective legs for **16 of
its 72 shares (22.2%)** therefore grades **fully protected**.

This is the third instalment of a class this repo has already fixed twice and
not here:

| venue | protection graded as | filed |
|---|---|---|
| Bybit | `covered_qty` — a **quantity** | fixed (`BL-20260729`, PR #8000) |
| IB | `stop_qty` / `target_qty` — **quantities** | fixed (`BL-20260816-COVERAGE-IS-ONE-SIDED`) |
| **Alpaca** | **sides only** | **not filed until this unit** |

The IB fix's own headline was *"protection is a QUANTITY and TWO-SIDED"*. Alpaca
got the two-sided half and never got the quantity half.

---

## 4. Events: still zero, and still "we looked and found none"

I did not re-run MI-171's event scan and I am not restating its result as my
own. Its finding stands as it wrote it: **zero over-close events**, readable only
because the identical scan returns **187** on Bybit as a positive control.

What I add is a **mechanism-level reason to expect zero so far**, which
strengthens rather than weakens that reading:

1. The precondition has held on exactly one Alpaca pair (§ 5).
2. It requires a **monitor-driven** close. TLT's nearest trigger is a **venue-side**
   16-qty stop (§ 3.2), which is not the defect.
3. It requires the close to land in `regular` or `extended` hours. `alpaca_portfolio`
   trades US equities on daily and hourly legs; a large share of monitor ticks fall
   in `closed`, where the path defers.

None of that is a safeguard. All three are circumstances, and the third changes
every trading day at 09:30 ET.

---

## 5. Population — independently re-derived, and it agrees with MI-171

**State the population.** Source: the newest **1000** `trades` rows from
`/api/diag/journal?table=trades&limit=1000`, ids **4545–5544**, pulled
2026-09-08T01:40Z. Of those, **600** ever held a venue position (`status` in
`open`/`closed`; the other 400 are `rejected` 370 + `exchange_rejected` 30). All
1000 have `is_backtest = 0`.

Counting `(account_id, symbol)` pairs holding **two or more rows whose
`[created_at, closed_at)` intervals overlap** (an open row's interval running to
the read instant):

> **12 pairs. 1 of them Alpaca — `alpaca_portfolio`/TLT.**

which reproduces MI-171's headline exactly, by an independently written method.
The 11 non-Alpaca pairs are `bybit_1` ×6, `bybit_2` ×2, `bybit_portfolio` ×2,
`ib_paper`/MGC ×1.

⚠️ **The definition is load-bearing and the two nearby numbers are not the
answer.** Counting pairs that merely hold ≥2 rows *at any time in the window*
gives **25** (10 of them Alpaca); counting pairs open *right now* gives **4** (1
Alpaca — TLT, `bybit_1`/ADAUSDT, `bybit_1`/AVAXUSDT, `ib_paper`/MGC). Only the
overlap count answers the backlog row's question, because the defect needs two
rows open **at the same time**.

⚠️ **`limit=1000` is a CAP, not a window.** `/api/diag/journal` accepts only
`table` and `limit`; there is no `offset` and no `since` (`diag.py:1541`), the
clamp is `_MAX_LIMIT = 1000`, and the query is `ORDER BY <col> DESC LIMIT ?` —
so this is *the newest 1000 rows*, which happen to span 2026-08-10→2026-09-08.
It is not a 28-day window and must not be quoted as one; a busier month would
cover fewer days. I verified this in the route's own source rather than
inheriting the correction.

---

## 6. What is pinned, and what is not

`BL-20260907-...` states the behaviour is *"pinned as current behaviour by
`test_alpaca_close_of_one_trade_liquidates_its_sibling_too` in
`tests/test_close_confirm_trade_scope.py`"*.

**That file does not exist on `main`.** On `origin/main` the string
`test_alpaca_close_of_one_trade_liquidates_its_sibling_too` appears in exactly
one place — inside `docs/claude/health-review-backlog.json`, i.e. the sentence
that names it. The tests live on the unmerged branch
`mi-168-trade-scoped-close-confirmation` (PR #11279, `landing: hold`).

So on `main` today the over-close is **unpinned**: nothing fails if the close
path changes shape. This is not a criticism of MI-168 — it wrote the tests and
its PR is correctly held — but a reader of the backlog row would reasonably
believe main is protected, and it is not.

---

## 7. Relationship to PR #11279 (MI-168)

Read at `2026-09-08T01:41Z`: **open, not draft, not merged**, head
`753a994`, `landing: hold`, 11 files, IB-only.

They are **disjoint, and must stay disjoint**:

| | #11279 | this unit's proposal |
|---|---|---|
| venue | IB | Alpaca |
| what is wrong | the **confirmation** is symbol-scoped while the **operation** is trade-scoped → a false **refusal** | the **operation** is symbol-scoped while the **caller's intent** is trade-scoped → a silent **over-close** |
| direction of harm | a close that succeeded is journaled as failed | a close of 16 liquidates 72 and the sibling row stays open |
| what to change | the confirmation | the operation |

**#11279 must not be extended to Alpaca, and its own body already says so** — it
declines MI-167's request to apply *"the identical change"* at
`alpaca_client.py:842`/`:1049`, on the grounds that Alpaca's confirmation and
operation are both symbol-scoped and therefore *match*. **I verified that
reasoning independently and it is correct.** Re-scoping Alpaca's *confirmation*
without first re-scoping its *operation* would confirm a 16-share reduction
against a venue that liquidated 72 — reintroducing `BL-20260707`'s false-SUCCESS
on a real-money-capable path.

**Ordering:** neither blocks the other. Both sit behind the same MI-140 coupling
— `alpaca_live` is `mode: live` / `real_money` and is immune today **only**
because it cannot place an order at all. Remediating MI-140 arms this defect on
real money.

---

## 8. What I could not establish — stated, not filled in

* **Whether any over-close has ever fired.** I did not re-run the event scan; I
  rely on MI-171's zero and did not independently reproduce it. *We looked and
  found none* remains MI-171's finding, not mine.
* **Which of (a) contamination or (b) journal divergence produced the hybrid
  resting OCO** (§ 3.2). The deciding evidence — the `modify_open_order` log line
  from 2026-09-04 — is past the journal's ~30-minute retention.
* **Whether Alpaca's REST API supports a qty-scoped position close.** The proposal
  in § 9 assumes it may, because the repo already uses one query parameter on that
  endpoint (`?cancel_orders=true`, `alpaca_client.py:1012`). I did **not** verify
  that a `qty`/`percentage` parameter exists or behaves as needed; nothing in this
  repo exercises one, and I did not query Alpaca's documentation from this session.
  **The proposal is written so that this is the first thing to establish, and so
  that it degrades honestly if the answer is no.**
* **Whether the two TLT rows' strategies are supposed to co-exist on one symbol at
  all.** `tlt_pullback_1d` and `tlt_pullback_1h` are different legs of the same
  family; whether routing both to one account is intended is a strategy question
  (Tier-3) I did not open.
* **Any account other than `alpaca_portfolio`.** `alpaca_paper` and `alpaca_live`
  were not read this session.

---

## 9. The remedy

Proposed separately, `landing: hold`, at
[`docs/design/alpaca-trade-scoped-close-PROPOSAL.md`](../design/alpaca-trade-scoped-close-PROPOSAL.md).
Nothing in it is applied.
