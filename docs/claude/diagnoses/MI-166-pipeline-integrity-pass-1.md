# MI-166 — pipeline integrity, pass 1: the journal is protecting a position the venue does not hold

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Unit: MI-166. Lane: standing pipeline-integrity review under `CY-20260906-TRADING-TRUTH`.
Pass 1 of N. Dispatched by the manager (`session_01HrmZ1RRNM4UnEUaFdrPEjj`) 2026-09-07.
Filed as PR [#11265](https://github.com/benbaichmankass/Metis-Insights/pull/11265).

**All measurements below are mine, taken live 2026-09-07 16:57–17:05Z through
`scripts/ops/diag_fetch.sh` (served by `https://ict-bot.duckdns.org`) and the
dashboard REST feed. Nothing here is carried over from the dispatch on trust; where
I reproduced a figure the manager had already measured, I say so.**

---

## The one-line finding

**The over-cover on `ib_paper`/MGC is not a protective-logic defect. The protective
legs are a faithful mirror of the journal — both OCA groups match their journal rows
to the tick. The journal holds 54 MGC lots. The venue holds 11.** The defect is
upstream of protection, in what the journal believes it owns, and it lands squarely
on the instrument the operator asked about: the record the trades are graded from.

---

## Population and provenance

| read | endpoint | state | captured |
|---|---|---|---|
| IB connection state | `/api/diag/ib_state` | 3 clients, all `connected`, `breaker_open: false`, `likely_wedged: false` | 16:57:42Z |
| IB resting orders | `/api/diag/ib_open_orders?account_id=ib_paper` | **`read_state: "orders_read"`** — a real read, 8 orders | 16:57:56Z |
| IB positions | `/api/diag/exchange_positions?account_id=ib_paper` | 3 positions, `error: null` | 16:58:29Z |
| journal trades | `/api/diag/journal?table=trades&limit=3000` | served 1000 rows, ids **4538–5537**, 2026-08-10T09:03Z → 2026-09-07T16:00Z | 17:00Z |
| operator ERROR+ feed | `/api/bot/logs?level=error&limit=400` | 48 rows, 2026-09-02T09:06Z → 2026-09-07T16:18Z | 17:01Z |
| trader journal | `/api/diag/journalctl?unit=ict-trader-live.service` | windowed pulls, 1000-line cap per pull | 17:03Z |

⚠️ **`read_state: "orders_read"` was checked before any conclusion was drawn from the
order list.** `_open_trades` swallows exceptions and returns `[]`, which would collapse
*we could not look* into *nothing rests*. It did not happen here: this is a real read.

⚠️ **Two negatives below are stated with the probe's positive control**, because a
search returning nothing is not proof of absence:
- *MES has no journal row.* The same pull returns 83 `ib_paper` rows and 81 of them are
  MGC, so the probe demonstrably finds `ib_paper` rows.
- *`maxMktOrderQty` appears nowhere in the repo.* The identical grep for `maxOrderQty`
  returns 14 hits in `src/`.

⚠️ **`restart_pending` was live during this pass** (`git_sha 59bfa099` running,
`git_sha_on_disk b596ac4f`). I established it does **not** confound Finding 2, the only
finding where it could have: `git merge-base --is-ancestor` confirms the AVAX clamp
commits `87af13f5`, `855397f6` and `9604b8a5` are all **ancestors of the running sha**,
and the trader restarts every ~30–90 min (systemd lifecycle lines, 2026-09-02), so the
2026-09-03 fix was running well before the 09-04 and 09-06 rejections.

---

## Finding 1 — `ib_paper`/MGC: the journal holds 43 lots the venue does not (CRITICAL)

### What the venue holds

| symbol | position | resting legs | groups |
|---|---:|---|---|
| MHG | long 30 | STP 30 @ 6.3995 · LMT 30 @ 7.3215 | `170199976` |
| MGC | **long 11** | STP 11 @ 4210.7 · LMT 11 @ 4807.5 · STP 43 @ 4394.5 · LMT 43 @ 4485.0 | `oca-protect-t5353`, `oca-protect-t5531` |
| MES | long 15 | STP 15 @ 7533.75 · LMT 15 @ 8390.5 | `oca-protect-408` |

MGC stop coverage 54 / 11 = **491%**. This reproduces the manager's table exactly.

### What the journal holds

The journal has exactly **three** open `ib_paper` trades in ids 4538–5537:

| id | opened | symbol | qty | entry | `stop_loss` | `take_profit_1` | strategy |
|---|---|---|---:|---:|---:|---:|---|
| 5258 | 2026-08-31T05:05Z | MHG | 30 | 6.662 | 6.39928571 | 7.321538 | `mhg_pullback_1d` |
| 5353 | 2026-09-02T13:41Z | MGC | **11** | 4374.4 | **4210.70714286** | **4807.4656** | `mgc_pullback_1d` |
| 5531 | 2026-09-07T08:26Z | MGC | **43** | 4430.7 | **4394.47142857** | **4485.04285714** | `ict_scalp_mgc_15m` |

### The join, which is the finding

**Every resting group matches its journal row to the tick.**

- `oca-protect-t5353` → STP 4210.7 / LMT 4807.5 ≡ trade 5353's `stop_loss` / `take_profit_1`
- `oca-protect-t5531` → STP 4394.5 / LMT 4485.0 ≡ trade 5531's `stop_loss` / `take_profit_1`
- `170199976` → STP 6.3995 / LMT 7.3215 ≡ trade 5258's `stop_loss` / `take_profit_1`

`place_protective` did its job. Both MGC groups are *correctly sized for the trade that
owns them*. **The journal sums to 54 MGC long; the venue reports 11.** The 43-lot
divergence is trade 5531, opened 08:26Z this morning.

⚠️ **This inverts the framing the pass was dispatched under, and the inversion is the
actionable part.** The dispatch reads the 491% as a protection defect whose remedy lives
in `place_protective`'s pre-cancel scoping. It is not. Protection is downstream and
faithful. **The defect is that the journal recorded a 43-lot fill it does not have**, and
`place_protective` then correctly armed 43 lots of protection for it.

### The live hazard, and why it is worse than "one group could go naked"

The oversized group carries the **tighter** stop. `t5531` stops at **4394.5**; `t5353`
stops at 4210.7. Position entry (venue avg) is 4423.02. So `t5531` is the nearest
protective level to the market and **fires first**: a touch of 4394.5 sells 43 against a
position of 11 → **net short 32**, with `t5353`'s 11-lot stop still resting below it.
OCA cancels only within a group, so nothing in `t5353` is cancelled by `t5531` firing.

`ib_paper` is paper, so no capital is at risk. The mechanism is shared with `ib_live`.

### What I did NOT do, deliberately

I did not cancel a leg. Beyond the standing instruction, the alarm's own remedy text is
**inapplicable here** and that is itself a finding:

> `Detect-only: cancel the leg that does NOT match trades.stop_loss.`

**Both legs match `trades.stop_loss`.** A responder following that instruction finds no
non-matching leg — or picks one arbitrarily and re-commits
`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG` in a new form.
The remedy text assumes the over-cover is always a stale-leg problem. This instance is
not, and the text has no branch for it.

### Arming the stray-group sweeper would not help, and aims at the wrong groups

Derived from `src/runtime/stray_oca_groups.py::classify_leg` against the live book:

- **MGC** — both groups are keyed (`oca-protect-t…`). On any re-arm, the other grades
  `SIBLING_KEYED` → **preserved by construction**. The sweeper cancels **nothing** on MGC
  and cannot touch the one real hazard.
- **MHG** (`170199976`) and **MES** (`oca-protect-408`) — non-keyed, so they *are* the
  `STRAY_UNKEYED` set. Both are at **100% coverage and correctly protected**.

So arming `PROTECTION_STRAY_GROUP_MODE` / `PROTECTION_STRAY_GROUP_ACCOUNTS` today would
leave the hazard untouched and point the only cancelling machinery at the two books that
are correct. **Recommendation: do not arm it as a remedy for this.** (Nothing is armed
today — `annotate` + empty allowlist = NONE. That is the right state and should stay.)

Note also that MES's `oca-protect-408` can never be reached even in principle: the
sweeper only runs on a **keyed re-arm for that symbol**, and per Finding 3 no journal
trade owns MES, so no re-arm will ever fire for it.

---

## Finding 2 — the AVAX venue-max clamp is on its FOURTH occurrence, and every fix so far clamped to the wrong ceiling (P1)

**Population: 6 `bybit_place_order_failed` rows in the 48-row ERROR+ feed.** Five are the
same defect; the sixth is an unrelated insufficient-balance rejection (BTCUSDT,
ErrCode 110007).

| when | symbol | qty sent | venue cap | overshoot |
|---|---|---:|---:|---:|
| 2026-09-02T09:06Z | AVAXUSDT | 22,995.1 | 22,000 | +4.52% |
| 2026-09-02T11:37Z | AVAXUSDT | 23,374.8 | 22,000 | +6.25% |
| 2026-09-04T17:15Z | AVAXUSDT | 23,083.0 | 22,000 | +4.92% |
| 2026-09-06T00:23Z | AVAXUSDT | 22,107.9 | 22,000 | +0.49% |
| 2026-09-06T00:32Z | AVAXUSDT | 22,078.1 | 22,000 | +0.35% |

(The `order_qty:2207810000000` in the rejection text is Bybit's 1e8 fixed-point display,
not a units bug on our side: `22078.1 × 1e8` exactly. `max_qty:2200000000000` ⇒ 22,000.)

**These are Market orders with attached TP/SL, so the rejection loses the entry *and* its
protection together — five signals produced no position at all.** The overshoot is
0.35–6.25%: a working clamp would have placed all five at 93.7–99.7% of intended size.

### Why the existing clamp did not fire

At the 00:32:43 rejection the trader journal shows `execute_pkg … qty=22078.1000`
followed immediately by the `_submit_order(bybit)` rejection, and **no `qty_legalize`
line on any branch** — not the `clamped` WARN, not `venue maxOrderQty UNRESOLVED`, not
`venue publishes no maxOrderQty`. The clamp block at `execute.py:1386` is unconditional
for `exchange == "bybit"`, so it ran; it simply found no ceiling to apply.

**Root cause: we never read the ceiling that binds a Market order.**

`precision.py:191` reads `lotSizeFilter.maxOrderQty` — the **limit-order** ceiling.
Bybit publishes a separate, smaller **`maxMktOrderQty`** for market orders.
**`maxMktOrderQty` appears nowhere in this repository** (0 hits across `.py`/`.yaml`/
`.json`; positive control: 14 hits for `maxOrderQty` in `src/`).

And the value that *would* have caught it is deliberately outranked:
`config/instruments.yaml` carries `AVAXUSDT.max_qty: 22000` — correct, and measured from
Bybit's own rejection text — but `_submit_order` calls `legalize_qty(..., prefer_live=True)`,
which makes the live `maxOrderQty` authoritative and the profile a fallback only for
symbols the live path cannot resolve. AVAXUSDT resolves, so the correct static ceiling is
never consulted.

⚠️ **This is why three prior fixes did not take.** `BL-20260810` shipped the clamp and was
marked `resolved`; `BL-20260821` recorded it rejecting again at ~34,000; `BL-20260902`
diagnosed `venue_max=None` as three collapsed states and shipped the three-state fix on
2026-09-03. **Every one of them clamped to the limit-order ceiling.** The three-state work
made the *absence* of a ceiling legible, but on this path the live lookup does not return
absence — it returns a ceiling that is real and simply not the binding one. No amount of
`None`-state legibility can catch a wrong number.

The module header of `qty_legalize.py` asks for exactly this: *"it is now legible instead
of silent, which is what lets a fourth occurrence be seen."* This is the fourth occurrence.

### The one link I could not close

I could not read Bybit's live `lotSizeFilter` for AVAXUSDT to confirm
`maxOrderQty > maxMktOrderQty = 22000`. The public endpoint is CloudFront
geo-blocked from this container (HTTP 403), and the diag surface is read-only and does
not expose instruments-info. **So I have not distinguished which of two shapes holds:**

- **(a)** the live lookup returns a *larger* `maxOrderQty` → grades `published` → 22,078
  is under it → no clamp, no log; or
- **(b)** AVAXUSDT publishes no `maxOrderQty` at all → grades `MAX_STATE_ABSENT` → the
  only log is `logger.debug` (invisible at INFO) → placed unclamped.

Both are consistent with every observation, and **both have the same root cause and the
same fix**. (b) is the more interesting one, because it means the three-state model has a
**fourth** state it does not name: *the venue published no LIMIT ceiling, and we never
asked about the MARKET ceiling* — which `MAX_STATE_ABSENT` currently licenses as "safe to
place unclamped."

**The read that discriminates** (one line, from a host with venue reachability):
`GET https://api.bybit.com/v5/market/instruments-info?category=linear&symbol=AVAXUSDT`
→ compare `result.list[0].lotSizeFilter.maxOrderQty` vs `.maxMktOrderQty`.

### Proposed fix — **Tier-3, order path. Proposed only, NOT applied.**

Three parts, smallest first:

1. **`src/units/accounts/precision.py`** — read both ceilings from the same
   `lotSizeFilter` payload already fetched, and return the one that binds the order type.
   `maxMktOrderQty` for `orderType: Market`, `maxOrderQty` for `Limit`. This is the actual
   fix; the rest is containment.
2. **`src/units/accounts/qty_legalize.py`** — add a fourth max-state,
   `MAX_STATE_ORDER_TYPE_UNKNOWN` (or thread the order type through), so that "no limit
   ceiling published" can no longer be read as "no ceiling binds a market order."
   Preserve the existing refusal discipline: `could_not_look` still places, still says so.
3. **`execute.py`** — raise the `MAX_STATE_ABSENT` branch from `logger.debug` to
   `logger.info`. Placing unclamped on an unverified ceiling is a decision; it should not
   be the one branch that leaves no trace at the live log level. (This alone would have
   made occurrence #4 visible in ~20 minutes instead of 4 days.)

I have written no code for these. The exact diff should be authored against a confirmed
answer to the (a)/(b) question above, not ahead of it.

---

## Finding 3 — `ib_paper`/MES: 15 lots and a full bracket that no journal row owns (P1)

The venue holds **MES long 15** with a complete resting bracket (STP 15 @ 7533.75,
LMT 15 @ 8390.5, group `oca-protect-408`).

**There is no MES row of any status in trade ids 4538–5537** — 1000 trades,
2026-08-10T09:03Z → 2026-09-07T16:00Z. Not open, not closed, not rejected. The 83
`ib_paper` rows in that same window are 81 MGC + 2 MHG.

This is *we looked and it is not there*, not *we did not look*. The position predates the
window or its row was never written; I cannot tell which from a 1000-row cap, and I did
not guess.

Consequences, in order of how much they matter:

1. **It is invisible to every journal-driven instrument.** Its P&L, its R, and its exit
   never enter `expectancyR` or any promotion/demotion gate, because no row exists to
   carry them.
2. **No strategy owns it, so no re-arm will ever fire for it.** Its non-keyed
   `oca-protect-408` group is therefore permanently outside the stray-group path, armed
   or not (Finding 1).
3. It is the strongest single instance of the `journalTrust` class that T-3 already
   tracks, and it is on a *paper* account, which that flag does not currently cover
   (`accountsKnownDivergent: ["bybit_2"]`, `accountsUnrecorded: ["alpaca_live"]`).

---

## Finding 4 — 7 of the 48 operator ERROR+ rows are the alarm reporting a position it has itself graded as fine (P1, and it is root-causable)

**Population: the full 48-row ERROR+ feed, 2026-09-02T09:06Z → 2026-09-07T16:18Z.**
All 48 rows are level `error`.

| class | rows | % of feed |
|---|---:|---:|
| `*_over_cover detected` | 33 | 68.8% |
| — of which `SAME-BOOK LEG OVER-ACCUMULATION` (a real condition) | 25 | 52.1% |
| — of which **"THIS position is NOT over-protected … 100% of the position"** | **7** | **14.6%** |
| — unclassified | 1 | 2.1% |
| `*_target_naked` | 6 | 12.5% |
| `bybit_place_order_failed` | 6 | 12.5% |
| other | 3 | 6.3% |

I reproduce the manager's 68.8% / 33-of-48 exactly, and ~4/5 of the feed being this one
class. **The sub-split is the new part, and it changes the disposition.**

Seven rows page the operator at ERROR to say, in their own text:

> `THIS position is NOT over-protected: legs that reduce it total 0.31 across 1 leg(s)
> (100% of the position). … (side-blind SL total across all books: 38.07, 12281% of the
> position, 2 leg(s) — this is the figure that TRIPPED the check, not a claim about the
> graded book.)`

### Root cause

`order_monitor.py:9740` trips on `covered > size * _BYBIT_OVERCOVER_FACTOR`, where
`covered` is the **side-blind sum across both hedge books**. It then calls
`_bybit_over_cover_condition(...)`, which grades the book correctly and returns the honest
prose above — **but that verdict is used only to compose the message.** The
`logger.error(...)` and `_emit_bybit_over_cover_alert(...)` that follow are
**unconditional** once the side-blind trip has fired.

So the alarm's own grading already knows these seven are not findings. The information
exists, in the right variable, one line above the page.

### Why this is not the forbidden fix

The standing instruction is: do not silence or widen a cooldown without root-causing the
condition, because the condition is real and currently true on the venue. **That holds for
the 25 `OVER-ACCUMULATION` rows and I propose no change to them.** These 7 are a different
population: the condition the alarm exists to report is, by the alarm's own computation,
**absent**. Gating on the graded verdict is not silencing a true alarm; it is stopping a
false one, which is precisely what protects the 25 true ones from being walked past.

### Proposed fix — **Tier-3, order-path-adjacent. Proposed only, NOT applied.**

In `order_monitor.py`, gate the `logger.error` + `_emit_bybit_over_cover_alert` on the
graded verdict already computed in `_split_fields`, rather than on the side-blind trip:

- graded book over-covered → **ERROR + page**, exactly as today (all 25 rows unchanged);
- graded book at ≤100% and the excess is other-book legs only → **WARN, no Telegram**.
  The real risk in that shape is proximity to Bybit's 20-leg combined TP+SL cap, which
  the message already reports and which is not an ERROR-grade condition — it is the same
  risk-reducing / does-not-page reasoning the `venue_max_qty_clamped` WARN already uses.
- split not computed / any leg or position side unreadable → **stays ERROR.** *We could
  not look* must not be downgraded into *we looked and it was fine.*

Expected effect on the measured feed: 7 of 48 rows (14.6%) leave the ERROR channel; **0
of the 25 real over-accumulation rows are affected.**

---

## Finding 5 — two IB pages disagree about the same book 11 minutes apart (flag, not a verdict)

| when | page | says |
|---|---|---|
| 2026-09-07T11:49:36Z | `ib_target_naked` | `ib_paper/MGC: position 11.0 has **0.0** of take-profit coverage against a declared TP of 4807.4656` |
| 2026-09-07T12:00:59Z | `ib_stop_over_cover` | `ib_paper/MGC: position 11.0 but resting STOP qty totals **54.0**` |

My 16:57Z venue read shows MGC carrying **both** stop and target coverage in both groups —
LMT 11 @ 4807.5 **and** LMT 43 @ 4485.0. The 4807.4656 the 11:49 page grades against is
trade 5353's own declared TP, and an 11-lot target at that price was resting five hours
later.

I am **not** asserting the target reader is wrong. I did not read the venue at 11:49, and
targets could have been placed in the 11-minute gap. What is established is that two pages
over one account/symbol disagree, and neither matches the venue read taken the same day.
Given that `ib_target_naked` is 6 of the 48 ERROR+ rows, whether it is measuring what it
claims is worth one focused pass. **Handed to pass 2 rather than graded here.**

---

## Also observed (logged, not investigated this pass)

- **`ict-ib-gateway-watchdog.timer` is `inactive`** in `/api/diag/services`, while every
  other watchdog timer (`liveness`, `web-api`, `db-integrity`) is `active`. IB
  connectivity is healthy right now, so this is not currently biting — but the watchdog
  for the venue carrying every finding in this document is the one that is not scheduled.
- **5 of the 6 `*_target_naked` rows are Alpaca** — `alpaca_portfolio`/`alpaca_paper` on
  USO, QQQ, SPY, 2026-09-03/04. **This is not MI-140 and must not be folded into it:**
  MI-140 owns `alpaca_live` being unable to *place*. These are different accounts with
  positions that placed fine and carry no reachable target. Noted for routing, not worked.

---

## What pass 2 of this lane should check

The lane compounds only if each pass hands the next a sharper question. Pass 1 started
from *"is the protection correct?"* and found that it is, and that the journal underneath
it is not. So:

1. **Reconstruct trade 5531's fill end to end — this is the top item.** Pull
   `order_packages` for `pkg` of trade 5531 and the trader journal for
   2026-09-07T08:26Z±5min, and answer: did the 43-lot entry fill, partially fill, or not
   fill? The venue's MGC average cost is **4423.02** on 11 lots, which sits far closer to
   5531's entry (4430.7) than to 5353's (4374.4) — **so the surviving 11 lots may be
   5531's partial fill and 5353's position may be the one that left the book unrecorded.**
   Pass 1 could not settle this and did not guess. It decides whether the bug is *"we
   journal a fill we did not get"* or *"we miss a close"* — two different repairs.
2. **Then ask the general question, which is the lane's real prize:** how many other
   `status='open'` journal rows disagree with their venue position *right now*, across all
   accounts? Pass 1 found 2 divergences (MGC 43 lots, MES 15 lots) by hand on one account.
   A `journal-open vs exchange_positions` diff, run over every account, is the instrument
   this lane actually needs and does not have. Build it read-only; it is Tier-1.
3. **Close the (a)/(b) question in Finding 2 with the one-line Bybit read**, then take the
   Tier-3 clamp diff to the operator. Until then the fifth occurrence is scheduled.
4. **Grade `ib_target_naked` against a same-instant venue read** (Finding 5).
5. **Do not re-derive Finding 1's join.** Both MGC groups match their journal rows to the
   tick, and `place_protective` is not the defect. Start upstream of it.

### One framing to carry forward

Every instrument in this pass was honest about its *own* computation and wrong about what
it *meant*: `place_protective` correctly armed 43 lots for a trade that did not have 43
lots; the over-cover page correctly computed a side-blind sum and paged on a book it had
already graded clean; the qty clamp correctly applied the ceiling it was given and was
given the wrong one. **None of these is a calculation bug, and none would be caught by
checking the arithmetic.** Each is a value that is right about a question nobody asked.
Pass 2 should assume the numbers are correct and interrogate the *joins*.
