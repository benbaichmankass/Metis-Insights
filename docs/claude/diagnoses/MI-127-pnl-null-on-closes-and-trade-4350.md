# MI-127 — the upstream cause: closes landing with `pnl` NULL, and MES trade 4350

Unit: MI-127. Parent diagnosis: `docs/claude/diagnoses/MI-124-never-firing-legs-diagnosis.md`.
Predecessor merged: MI-126 (`5eb59917`) took `retire_candidate` 9 → 1.
**READ-AND-DIAGNOSE. Nothing here closes, flattens, modifies or cancels anything on any account.**

Two defects, deliberately kept apart. **(A)** is an accounting/provenance defect
with a named mechanism and a one-line proposed fix. **(B)** turns out **not** to
be a defect at all, and the evidence for that is broker truth, not the journal.

---

## 0. Headline

**(A) The mechanism is the sweep's scan window, and it is keyed on the wrong
timestamp.** `_sweep_local_pnl_for_unpriced` — the pass that exists to price a
close the broker cannot price — bounds itself by `created_at`, which is when the
position was **opened**. Any position held longer than 14 days is already
outside the window at the moment it closes. The sweep never selects it, never
attempts an anchor, and never reaches the `unmeasured` declaration branch. The
row lands `pnl` NULL **with no provenance key at all** — a *silent* null, which
is precisely the state `src/runtime/provenance.py` was built to make impossible.

This is **not** the coin-flip MI-124 inferred. It is deterministic, and hold
duration is the discriminator. Measured over the whole journal, the pnl-NULL
share of closes runs **2.0% → 1.5% → 25.5% → 90.9%** across hold buckets
`<1d / 1–7d / 7–14d / >14d`, and **not one of the 20 rows held >14 days has ever
been declared** (0 of 20). Every `_1d` trend and pullback leg in the fleet is
long-horizon by design, so the defect selects for exactly those legs — which is
why it looked like a property of the five legs rather than of the window.

**(B) Trade 4350 is not stuck, and it is not unprotected.** Read against the
venue on 2026-09-05T03:06:19Z: the MES position is genuinely open on `ib_paper`
(15 long), and it rests a **complete, quantity-matched OCA bracket** — STP
7533.75 + LMT 8390.50, both GTC, in one group, 15 against a 15-lot position,
matching the journal's declared levels to the 0.25 tick on **both** legs. It is
an open, protected, trailing trend position on a leg configured with `tp_r:
50.0`. **The 7 orphaned packages are not its debris** — they are all from
2026-06-01, sixty-three days *before* the trade, unlinked to it, and already
terminal. My proposal for (B) is therefore an observability change, not an
intervention.

---

## 1. Population — stated on every count

| | |
|---|---|
| Journal | live `trade_journal.db` via `https://ict-bot.duckdns.org` |
| Surfaces | `/api/bot/db/table/{trades,order_packages}`, `/api/diag/exchange_positions`, `/api/diag/ib_open_orders`, `/api/diag/status` |
| Read at | 2026-09-05T03:00Z–03:10Z; trader `git_sha 5eb59917` (= `main` HEAD) |
| `trades` | **5,493 rows**, fetched complete by pagination (`limit=500`, MAX_LIMIT) — not a tail |
| Filter discipline | every filtered count asserted `filter_state: "applied"` before being trusted |
| (A) analysis population | non-backtest `status='closed'`, `intent_reduce` legs excluded, hold duration computable → **1,373 rows** |
| (B) population | `mes_trend_long_1d`: **1** trade row, **8** order packages (`filter_state: applied`, total 8) |

`intent_reduce` legs are excluded throughout because their `pnl` is deferred
NULL *by design* (`apply_intent_reduce_partial_close`); counting them would
inflate the defect with rows that are behaving correctly.

---

## 2. (A) The mechanism, named

### 2.1 The two-stage contract

The alpaca/IB reconciler close is deliberately a **two-stage** protocol.
`_reconcile_orphan_exchange_positions` (`src/runtime/order_monitor.py:3416-3425`)
closes a snapshot-confirmed-flat position writing `status`, `exit_reason`,
`closed_at` and `notes` — and **no `pnl`, no `exit_price`**:

```python
db.update_trade(tid_int, {
    "status": "closed",
    "exit_reason": "exchange_flat_reconciled",
    "closed_at": now_iso,
    "notes": dump_capped({...}, 500),
})
```

Its own note says why: *"PnL filled by the local-PnL sweep (mark-to-market)."*
`tests/test_position_snapshot_reconcile.py:206` pins it: *"pnl stays NULL"*.
**Stage one is correct and is not the defect.**

### 2.2 Stage two never runs for a long-held position

`_sweep_local_pnl_for_unpriced` (`src/runtime/order_monitor.py:10457`) is stage
two. Its scan query (`order_monitor.py:10533`) ends:

```sql
AND datetime(created_at) >= datetime('now', '-14 days')
ORDER BY datetime(created_at) DESC
LIMIT 100
```

`created_at` is the **open**. The value being recovered is a property of the
**close**. For a position held longer than 14 days the row is *already* outside
the window on the first tick after it closes, and stays outside forever after.

The consequence is not merely "no price". The sweep is also the only writer of
the honest terminal state: the `no_anchor` branch (`order_monitor.py:10637-10655`)
stamps `pnl_source: unmeasured` + `unmeasured_reason: no_close_time_anchor`,
which INV-2 accepts and INV-2b counts. A row that is never *scanned* never
reaches that branch either. So the long-held close is not "declared
unmeasurable" — it is **silent**, and `provenance.py` is explicit that the
difference between those two is the whole accountability axis:

> `UNVERIFIED` … Deliberately NOT `MEASURED`: absence of a provenance record is
> not evidence of measurement.

### 2.3 The measurement — the fingerprint

Population: the 1,373 rows in §1.

| hold bucket | n | pnl NULL | null % |
|---|---:|---:|---:|
| < 1 day | 1103 | 22 | 2.0% |
| 1–7 d | 196 | 3 | 1.5% |
| 7–14 d | 47 | 12 | 25.5% |
| **> 14 days** | **22** | **20** | **90.9%** |

And the split that names the mechanism, over the **57** pnl-NULL rows in that
population:

| | declared `unmeasured` | **SILENT** (no `pnl_source`) |
|---|---:|---:|
| held ≤ 14 d | 15 | 22 |
| **held > 14 d** | **0** | **20** |

**Zero of 20.** No row held longer than the window has ever been declared —
not because the anchor failed, but because the sweep never asked.

The two rows held >14d that *do* carry a pnl were filled by **other paths**, and
neither is this sweep: `id=3870` (`pairs_half_open_cleanup`, which wrote its own
`exit_price`) and `id=4163` (`exit_price_source: bybit_closed_pnl`, i.e. broker
truth). So `_sweep_local_pnl_for_unpriced` has filled **exactly zero** rows held
>14 days, ever.

### 2.4 The reproduction

`tests/test_local_pnl_sweep_window_is_open_keyed.py` (this branch). Two rows,
**identical in every respect except hold duration**, both closed 2 days ago,
both `pnl` NULL, with a **working close-time anchor** monkeypatched in so that
no negative can be blamed on venue coverage:

* `test_short_hold_row_is_priced` — **positive control**: held 3 days → priced.
  The negatives below have a denominator.
* `test_long_hold_row_is_never_even_scanned` — held 38 days → `scanned == 1`
  (not 2), `pnl` NULL, **and `"pnl_source" not in notes`**, and
  `declared_unmeasured == 0`. That is the defect, reproduced.
* `test_the_window_predicate_keys_on_the_open` — structural pin on the SQL, so
  the predicate and the behavioural expectations move together.
* `test_a_recent_close_is_priceable_regardless_of_hold_duration` — the fix's
  acceptance criterion, `xfail(strict=True)`.

Verified both ways: green on `main` as written, and with the proposed patch
applied locally the strict xfail **XPASSes** (i.e. the long-held row is priced),
which is the fix working.

### 2.5 Why this stayed invisible

The same window is already documented one file over, in
`tests/test_sweep_no_mark_fabrication.py`, as a **"TIME BOMB, DEFUSED
2026-08-13"** — twice it aged a *fixture* out of the window and reddened `main`
with no related diff. Both times it was correctly diagnosed as a fixture-clock
problem and the fixture was made relative. Nobody then asked what the same
predicate does to a **production** row that is long-lived by design rather than
by accident. That file even pins the property that makes it expensive —
*"an aged-out row is SILENT, not loud … byte-identical to a correctly-behaving
sweep over a clean book"* — which is exactly the signature I measured in
production, two months later.

### 2.6 The proposed change (NOT applied — tier)

`src/**` is **not** in `TIER1_SURFACE` (`scripts/ci/check_pr_landing.py:162`,
which lists `docs/** tests/** comms/** .github/** scripts/{ci,ops,research,reports}/** *.md`
and config files). By this repo's own guard the change is therefore **not a
Tier-1 writer fix**, so per the unit's done-condition it is **proposed with the
exact change**, not applied. **Tier-2.**

```diff
--- a/src/runtime/order_monitor.py
+++ b/src/runtime/order_monitor.py
@@ (_sweep_local_pnl_for_unpriced scan query, ~line 10533)
-                "   AND datetime(created_at) >= datetime('now', '-14 days') "
+                "   AND datetime(COALESCE(closed_at, created_at)) >= datetime('now', '-14 days') "
```

`COALESCE` is load-bearing: the sweep also selects `status='orphaned'` rows,
which may have no `closed_at`; those must keep their current open-keyed
behaviour rather than becoming unbounded.

⚠️ **One existing test breaks under this change and must move with it.**
`tests/test_sweep_no_mark_fabrication.py::test_the_scan_window_check_can_fail`
ages out only `created_at` and leaves `closed_at` recent, so under the fix the
row is legitimately back in window and `scanned` reads 1, not 0. Its *intent*
(prove the window filter can actually fail) is unaffected — the repair is to age
out **both** timestamps. I verified this is the only test in the suite affected.
It is named here rather than fixed, because fixing it in this unit would ship a
test change that only makes sense alongside a `src` change this unit is not
landing.

### 2.7 Back-fill — the recommendation is **do not**, and there is a trap

**Recommendation: do not back-fill any historical row, and do not treat the fix
as a licence to.** Two separate reasons.

1. **The honest ones are already correct.** 15 of the 57 pnl-NULL rows carry
   `pnl_source: unmeasured`. The system asked, could not anchor, and said so.
   Those NULLs are the anti-fabrication guard working. Filling them would undo
   the 2026-07-30 remedy directly.
2. **For the 20 silent rows, the only available value would be an estimate, not
   a measurement.** The sweep's own anchor is a bar close covering `closed_at`,
   stamped `ESTIMATED` and explicitly *"not a fill: the bar says where the market
   was, not where THIS order filled"*. Reconstructing a close from months ago on
   that basis is defensible as an estimate and **indefensible as a realised
   PnL**, and `/api/bot/performance` aggregates `pnl` without regard to bucket.
   Per CLAUDE.md § "Number provenance": a fabricated number is worse than a gap.

⚠️ **THE TRAP, and it is the reason this section is not academic.** Deploying
§2.6 will *itself* back-fill a rolling 14-day tail. The moment the predicate
changes, every row **closed** within the last 14 days becomes eligible and the
sweep will price it from an anchor on the next tick — with no separate approval
and no announcement. Against the 2026-09-05 read that is **5 rows**: `4169`,
`4170` (`tlt_pullback_1d`, closed 08-28), `4422`, `4423` (`iwm_trend_long_1d`,
closed 08-28) and `4484` (`gld_pullback_1h`, closed 08-24). Whoever approves the
Tier-2 change is approving those five valuations too. That is a defensible
decision — they are recent, anchorable, and will be stamped `ESTIMATED`, not
`MEASURED` — but it must be a **decision**, not a side effect nobody costed.

If the operator wants the fix without the tail, the fix can ship gated so the
widened window applies only to rows closed **after** the deploy. I have not
written that variant; it is more code than the one-liner and should only exist
if the operator asks for it.

### 2.8 Secondary observation (recorded, not filed separately)

The `unmeasured_reason` key is being **truncated away**. All 15 declared rows
carry `pnl_source: unmeasured` but `unmeasured_reason: None`, and every one of
them also carries `_truncated` in its notes — `dump_capped(notes, 500)` is
dropping the reason on rows whose `closed_reason` prose already fills the cap.
So the declaration survives but its *cause* does not, on 15 of 15. This is a
small provenance loss inside a mechanism built for provenance, and it belongs
with whoever lands §2.6.

---

## 3. (B) Trade 4350, established against broker truth

### 3.1 Venue truth, read directly

`/api/diag/exchange_positions`, `ib_paper` (IB account `DUQ325724`), captured
2026-09-05T03:06:19Z:

```json
{"symbol":"MES","side":"long","size":15.0,"entry_price":7630.872,"unrealised_pnl":null}
```

**The position is genuinely open on the venue.** Not a phantom, not a journal
artifact.

`/api/diag/ib_open_orders`, same account, captured 03:06:20Z — both protective
legs are resting:

| order_id | perm_id | type | action | qty | price | oca_group | tif | status |
|---:|---:|---|---|---:|---:|---|---|---|
| 6 | 1510669210 | LMT | SELL | 15 | **8390.50** | `oca-protect-408` | GTC | Submitted |
| 410 | 1510669209 | STP | SELL | 15 | **7533.75** | `oca-protect-408` | GTC | PreSubmitted |

**The position is protected, correctly and completely:**

* Both legs present, **one** OCA group, so they are mutually cancelling.
* Quantity **15 against a 15-lot position** — exact, no over- or under-cover.
* Against the journal (`trades.stop_loss = 7533.69642857`,
  `take_profit_1 = 8390.59025`) and MES `tick_size: 0.25`
  (`config/instruments.yaml:223`): 7533.696 → nearest tick **7533.75** ✓;
  8390.590 → nearest tick **8390.50** ✓. **Both legs match the journal's
  declared intent to the tick.** The apparent divergence is tick rounding, not a
  protection gap.
* `PreSubmitted` on the STP is IB's normal state for a resting stop that has not
  triggered, not a fault.

⚠️ **Caveat carried, not dropped.** `/api/diag/ib_open_orders` returns
`stale_read_caveat` — it serves a stale monotonic order view
(`BL-20260826-DIAG-IB-OPEN-ORDERS-SERVES-A-STALE-MONOTONIC-ORDER-VIEW`), so
presence here is not proof a leg is live *right now*. Anyone acting on this must
re-verify against a fresh client first. It is good enough to establish the
diagnosis and **not** good enough to authorise an order.

⚠️ **What I did NOT establish.** IB returns `unrealised_pnl: null` in the venue
snapshot, and `/api/bot/positions` returned no MES row I could parse. **I did
not establish the position's current mark-to-market and am not going to compute
one from a stale price** — that is the fabrication this whole unit is about.
What is known: entry 7630.872 (venue) / 7634.75 (journal), stop 7533.75, target
8390.50, multiplier $5.00/point on 15 contracts, so the distance from venue
entry to stop is 97.122 pts × 15 × $5 = **$7,284.15** of declared risk from
entry. That is a geometry statement, not a P&L statement.

### 3.2 Is it *stuck*? No — and the journal already knew

| evidence | reading |
|---|---|
| `exit_plan.stop.price = 7382.20535714` vs `trades.stop_loss = 7533.69642857` | the stop has **trailed up 151.5 pts** from its original level — the leg is being actively managed, not frozen |
| package meta `stuck_alert_emitted_at: 2026-08-08T13:47:39`, `stuck_position_alive_seen_at: same` | the stuck-position watchdog **already fired**, looked, and recorded the position as **ALIVE**. It did its job |
| `tp_r: 50.0`, `trail_mult: 3.0`, `timeframe: 1d` | a 50R target on a daily donchian trend leg is a *hold-until-trailing-stop* configuration. It is not designed to close in 33 days |
| `signal_logic.donchian: 30` | a 30-day channel breakout follower. Multi-week holds are the strategy, not a symptom |

A leg holding one position and therefore not re-entering is the netting guard
working, as the unit brief already notes. Combined with the above, **33 days
open is inside this strategy's designed behaviour**, not evidence of a fault.
What made it *look* stuck is the (A) defect on its siblings plus the absence of
any fleet view that distinguishes "long-horizon leg holding by design" from
"leg wedged" — see §3.5.

### 3.3 The 7 orphaned packages — not 4350's debris

All seven, with their own recorded diagnosis:

| created (2026-06-01) | entry | confidence | `orphaned_reason` |
|---|---:|---:|---|
| 09:06:57 | 7613.25 | 0.0199 | BUG-049 — no linked_trade_id after 5 min; package was never executed |
| 09:14:51 | 7615.75 | 0.0530 | ″ |
| 09:22:42 | 7613.50 | 0.0232 | ″ |
| 09:30:38 | 7613.00 | 0.0166 | ″ |
| 09:38:19 | 7612.00 | 0.0033 | ″ |
| 09:46:08 | 7612.25 | 0.0066 | ″ |
| 09:56:04 | 7612.25 | 0.0066 | ″ |

Established facts:

* **All seven are from 2026-06-01, within a 49-minute span** — sixty-three days
  *before* trade 4350 was opened (2026-08-03T18:21).
* **All seven have `linked_trade_id: None`.** They are not linked to 4350 or to
  anything else. `orphaned_by: monitor_reconciler`.
* **None was ever executed** — that is the recorded reason, not my inference.
* All seven carry `"sized_qty_by_account": {}` — **empty**. The package that
  *did* work (`pkg-1d9598b29f38440c`, the one linked to 4350) carries
  `{"ib_paper": 15.0}`.

So the cause is legible: **sizing resolved to nothing**, no order was placed, no
trade linked, the reconciler orphaned the package after 5 minutes, and the leg
re-emitted the same daily signal on the next tick — seven times over 49 minutes
until it stopped. All seven share one `entry_time: "2026-06-01 00:00:00"` and
one `donchian_hi: 7611.75`: this is **one** daily signal re-emitted seven times,
not seven signals.

**They carry no venue state** (never executed), they are in a terminal status,
and the leg subsequently traded normally on 2026-08-03. **They are not a live
risk and they are not part of trade 4350.** The unit brief's framing of "trade
4350 open, alongside 7 orphaned order packages" reads them as one situation;
they are two, sixty-three days apart. The residual question they raise — why a
`1d` leg re-emits on every tick when sizing returns empty — is a real one, and
it is about the retry/sizing path, not about 4350.

### 3.4 Reconciling the two prior records — as instructed

**`PROTECTION_REASSERT_MODE` (root `CLAUDE.md`).** The row records the MES 4350
divergence and marks it **RESOLVED, verified 2026-08-29**. It does **not** claim
an established mechanism — it says so explicitly and at length:

> ⚠️ **This gate cannot be credited for that** … `PROTECTION_REASSERT_MODE`
> ships at `annotate` and `PROTECTION_REASSERT_ACCOUNTS` is empty, which means
> NONE here — so the decision ran and cancelled/re-placed nothing. **What closed
> the gap is NOT established**; the candidates are an ordinary re-arm on a
> subsequent open or a naked-sweep repair, and neither was observed.

**So the record and MI-124 do not actually conflict, and the reconciliation is
short.** The record's own measurement (STP 7533.75 + LMT 8390.50, one OCA group,
qty 15 vs a 15-lot position, matching the journal to the 0.25 tick on both legs)
is **exactly what I independently re-measured seven days later**. MI-124 did not
contradict it; MI-124 cited `BL-20260820` for 4350's protection state and did
not re-derive it, which was the right call.

What I add is the thing a single read could not establish: **the state has now
held stable for 7 days** (2026-08-29 → 2026-09-05), across two independent
reads. The row's structural claim — an idempotence filter comparing intent to
the journal rather than the venue makes a divergence permanent by construction —
is untouched by any of this and remains the point. The gate remains
**unexercised**, and nothing here is evidence for arming it.

**`OI-20260830-E35-GEOMETRY`.** ⚠️ **The unit brief's premise here is wrong, and
I am reporting that rather than working around it.** The brief states that
`OI-20260830-E35-GEOMETRY` names trade 4350. It does not. I scanned all **53**
rows of `docs/claude/OPEN-ITEMS.json` for the string `4350`: **zero hits.** The
two E35 rows (`OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED`
and `OI-20260829-E35-REVERSED-LEGS-ARE-A-TIER-3-PROPOSAL-SET-NOT-APPLIED`) are
about exit-geometry shipping and live verification; the trades they name are
**4904** and a `trend_donchian_eth_4h` row, on `bybit_portfolio`. Neither
mentions MES, `ib_paper`, or 4350. There is consequently **nothing to reconcile
between E35 and 4350** — they are unrelated records. The live references to 4350
are in MI-124, `BL-20260820`, the 2026-08-20/23 full-system audits, and
`docs/design/ib-protective-price-reconciler-PROPOSAL.md`.

### 3.5 Proposal for (B), with tier

**Do nothing to the position. Tier-1 observability instead.**

1. **No intervention on trade 4350 — Tier-3 if anyone disagrees, and it is the
   operator's call, not mine.** The position is open, protected to the tick,
   quantity-matched, trailing, and inside its strategy's designed holding
   behaviour. Closing or modifying it would be acting on a misreading. I did
   not, and I recommend against it.
2. **Re-verify before any action.** If the operator does decide to act, the
   `ib_open_orders` staleness caveat means the bracket must be re-read from a
   fresh client first. Presence in a stale monotonic view is not authorisation.
3. **The real gap is that nothing distinguishes "holding by design" from
   "wedged".** That is what turned a healthy 33-day trend position into an
   escalation. A leg's expected holding period is derivable from what it already
   declares (`timeframe`, `donchian`, `tp_r`, `trail_mult`), so a
   "days open vs. expected holding period" column on the open-positions view
   would separate the two without any new measurement. **Tier-1** (a read-only
   view), and filed as backlog rather than built here — it is outside this
   unit's scope and should not be smuggled in.
4. **The 7 orphaned packages need no resolution.** Terminal, unexecuted, no
   venue state, correctly diagnosed at the time. The live question they leave is
   the re-emit-on-empty-sizing loop, filed separately.

---

## 4. Where this agrees and disagrees with MI-124

MI-124 is good work and most of it stands. Three corrections, all mine, all
measured.

| MI-124 | this pass |
|---|---|
| The pnl-NULL closes are a **coin-flip** — *"they are not different in kind from `iwm`, only in luck"* | **Disagree — and this is the substantive correction.** It is deterministic. `iwm 2771` (held 0.0d) priced; `iwm 2772` (held 15.0d) did not — same leg, same exit path, same close date. Hold duration decides, at 0 of 20 above the window |
| **Five** legs affected (`gdx`, `gld`, `iaum`, `scha`, `spy` `_1d`) | **Understated.** The silent >14d defect touches **14 distinct strategies across 5 accounts** (`alpaca_paper`, `alpaca_portfolio`, `bybit_1`, `bybit_portfolio`, `ib_paper`) and 5 exit reasons — not just the alpaca reconciler path. MI-124's five are the subset that were *also* sunset candidates, which is a selection effect of the packet it was auditing, not a property of the defect |
| All five are `broken` — pnl accounting | **Two of the five are not.** `gdx_pullback_1d` (3937, 3938 @ 12.0d) and `scha_trend_long_1d` (4710 @ 7.0d) carry `pnl_source: unmeasured` — the sweep **did** ask, could not anchor, and declared it. Those NULLs are honest and **must not be filled**. Only `gld` (2634), `iaum` (3491) and `spy` (4348) are the silent defect. `gld_pullback_1d` has one of each |
| `mes_trend_long_1d` is `broken` — exit/lifecycle accounting; *"Resolve trade 4350 and the 7 orphaned packages"* | **Disagree on both halves.** The position is healthy and correctly protected (§3.1–3.2); the 7 packages predate it by 63 days and are unrelated (§3.3). Nothing here needs resolving |
| The upstream cause is likely `BL-20260807-BULK-RECONCILER-CLOSE-NO-EXIT-NO-PNL` (the 2026-08-05 bulk event) | **Partly.** 2026-08-05 is a real cluster, but it is not the mechanism — `gld 2634` (2026-07-07) and `spy 4348` (2026-08-18) are the same defect on other dates. The window is the common cause; the bulk event is one day that produced several instances of it |
| Everything else — the `sunset_pass.py:288` collapse, the `pnl IS NOT NULL` filter in `performance.py`, `splg` candle starvation, `tqqq` `not_established`, `trend_donchian_sol` starvation, `turtle_soup` shadow+unrouted | **Confirmed, not re-derived.** MI-124's §2 chain is the reason the (A) defect was invisible to the sunset pass, and it is correct |

MI-124's core claim — that these legs traded and the reporting hid it — **holds,
and is strengthened**: the reason the reporting hid it is now named, and it is
upstream of the sunset pass, affecting `/api/bot/performance`, the M7 gate and
every consumer of `pnl` identically.

---

## 5. Guards

Run on this branch:

* `python3 -m pytest tests/test_local_pnl_sweep_window_is_open_keyed.py` —
  **3 passed, 1 xfailed** (the xfail is the fix's acceptance criterion, strict).
* `python3 -m pytest tests/test_sweep_no_mark_fabrication.py` — **13 passed**,
  unchanged by this branch. Under the *proposed* patch (applied locally, then
  reverted) exactly one of them fails, named in §2.6.
* `python3 scripts/ci/run_guards.py --base main` — result recorded in the PR
  thread; any guard that could not run in this sandbox is **named there rather
  than reported green**.

Not run, and named rather than assumed: nothing in this branch touches the order
path, `config/strategies.yaml`, `config/accounts.yaml`, `config/risk_caps.yaml`
or `src/runtime/orders.py`, and no `src/` file is modified at all.

---

## 6. Filed

* `BL-20260905-LOCAL-PNL-SWEEP-WINDOW-KEYS-ON-THE-OPEN-SO-A-LONG-HELD-CLOSE-IS-NEVER-PRICED` (high) — the (A) defect.
* `BL-20260905-A-LONG-HORIZON-LEG-HOLDING-BY-DESIGN-IS-INDISTINGUISHABLE-FROM-A-WEDGED-ONE` (medium) — the (B) observability gap.
* `BL-20260905-MES-TREND-LONG-1D-RE-EMITS-A-DAILY-SIGNAL-EVERY-TICK-WHEN-SIZING-RETURNS-EMPTY` (medium) — the 2026-06-01 retry burst.

Deliberately **not** filed, because each restates an existing open row: the
pnl-NULL close class (`BL-20260807`, `BL-20260825`) and trade 4350's protection
state (`BL-20260820`, and the `PROTECTION_REASSERT_MODE` row itself).

**This document is the PR description.**
