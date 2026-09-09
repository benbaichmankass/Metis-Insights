# The partial-close producer has never fired — why, and what that means

> **Doc status:** `live` · category `evidence` · last verified `2026-09-09` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> MI-209c · intent `IN-20260903-TRADING-SYSTEM-HEALTH` · cycle priority `CY-20260906-TRADING-TRUTH`
>
> **Successor to** MI-209b (#11501) and **addendum to**
> [`exit-trailing-and-banking-measurement-2026-09-08.md`](exit-trailing-and-banking-measurement-2026-09-08.md)
> (MI-188b) and [`exit-trailing-banking-broker-truth-2026-09-09.md`](exit-trailing-banking-broker-truth-2026-09-09.md)
> (MI-209). Read MI-188b § 3 first — this does not restate it.

**MEASUREMENT ONLY.** No parameter proposed as armed, no exit-matrix cell re-graded,
no lever armed, no strategy leg retired, no live position touched. Every read was a
GET. Findings are FILED, not fixed. The one forward action in § 6 is a **Tier-3
proposal for the operator** and has not been enacted.

**Reproduce:** `python3 scripts/research/partial_close_reachability.py`
(`--self-test` runs 14 assertions offline). All live figures read **2026-09-09
between 07:35Z and 07:46Z**.

---

## 0. Two corrections to my own dispatch, before anything else

**(a) The work object does not exist.** I was dispatched to "read
`WO-20260909-THE-PARTIAL-CLOSE-PRODUCER-IS-BUILT-AND` first — its `done_condition`
is the contract." **There is no such file on `origin/main`** (checked at `ba5fccc`;
`docs/claude/work/objects/` contains only two `20260909` objects, neither of them
this one). I worked to the done-condition as stated in the dispatch prompt. I am
saying so rather than implying I read an object I could not open.

**(b) The question had already been answered — the day before I was dispatched.**
My brief said MI-209b "left a sharper question standing". It did not.
**MI-188b answered it on 2026-09-08**, in
`exit-trailing-and-banking-measurement-2026-09-08.md` § 3, which already names the
sole emitter (`turtle_soup.py:537`), already reports turtle_soup's 3 rows, already
states it never opened a position, and already lists four gates. MI-209 re-confirmed
the zero on 2026-09-09.

I re-established all of it independently rather than inheriting it, and it
reproduces. **But the honest headline is that roughly two thirds of this unit was
re-derivation of work that had already landed**, and the dispatch chain did not know
that. That is the same defect MI-209 reported one day earlier about its *own*
dispatch — a landed result whose object was never closed out, so the question got
re-asked. It has now happened twice in two days on the same subject. Filed as
`BL-20260909-PARTIAL-CLOSE-QUESTION-RE-DISPATCHED-TWICE-IN-TWO-DAYS-AFTER-IT-WAS-ANSWERED`.

**What is genuinely new here** is § 4 (the *routing* fact, which is independent of
and stronger than the `execution: shadow` fact MI-188b cited), § 5 (the margin
distribution the done-condition explicitly asked for and which nobody had measured),
and the two instrument defects in § 7.

---

## 1. The answer, in one line

**Clause (b) of the done-condition: reachable in principle, unreachable as
configured.** The partial-close producer has never fired because its *only* input
is a strategy that is routed to **zero of eleven accounts** and additionally pinned
to `execution: shadow`. It is not a threshold no trade cleared. **The population
that could reach the threshold is empty by construction.**

The chain, and where it dies:

| # | link | state | verdict |
|---|---|---|---|
| 1 | a verdict carrying `close_qty_pct` < 1 | emitted at **exactly one** site in `src/` | built |
| 2 | that site is `turtle_soup.monitor()` | 1 of 55 configured legs | built |
| 3 | turtle_soup produces a signal → package | 3 packages of 4500, **none since 2026-07-01** | ⛔ |
| 4 | the package **opens a position** | **0 of 3 ever opened** | ⛔ |
| 5 | `order_monitor` calls `monitor()` on it | never reached | unreachable |
| 6 | price crosses TP1 with `meta.tp2` set | never evaluated | unreachable |
| 7 | venue supports `partial_close` | **bybit only** — 4 of 5 integrations cannot | latent |

Links 3 and 4 are each independently sufficient. Link 7 binds any future use.

---

## 2. What was read

| source | how | population bound |
|---|---|---|
| `trades` | `GET /api/bot/db/table/trades` | **complete census, n=5589** (`total`, `filter_state` asserted) |
| `order_packages` | same route | **complete census, n=4500** |
| `position_telemetry` | same route, `limit=500` | **complete table, n=193** (193 fetched = 193 `total`) |
| live strategy + account config | `GET /api/bot/config` | `as_of 2026-09-09T07:44:46Z`, 55 legs / 11 accounts |
| code | read at `ba5fccc` | `monitor_verdict`, `order_monitor`, `exit_plan`, `coordinator`, `clients`, `turtle_soup`, `position_telemetry` |

**The census reproduces MI-209b exactly** — `trades` 5589, `order_packages` 4500. I
re-measured rather than inheriting; clause (d) of the done-condition ("the census is
wrong") is **NOT** met. MI-188b's 5550 / 4467 the previous day is consistent
ordinary growth, not a disagreement.

---

## 3. The zero is real — probes with positive controls

A search returning nothing is not proof of absence. Every zero below is paired with
a control **of the same shape** that must fire.

| | probe | n |
|---|---|---:|
| CONTROL | `notes LIKE '%"trade_id"%'` | **5364** |
| CONTROL | `notes LIKE '%"is_dry"%'` | **5276** |
| CONTROL | `notes LIKE '%"confidence"%'` | **5276** |
| probe | `notes LIKE '%partial_closes%'` | **0** |
| probe | `exit_reason = 'tp1_partial'` | **0** |
| probe | `exit_reason LIKE '%partial%'` | **0** |
| probe | `exit_reason LIKE '%tp2%'` | **0** |
| probe | `order_packages.close_reason LIKE '%partial%'` | **0** |

Every row above returned `filter_state: applied`; an ignored filter would have
returned the whole-table count and read identically to a universal match
(`BL-20260813-DB-EXPLORER-SILENTLY-IGNORES-UNKNOWN-FILTER-COLUMN`).

**One near-miss, checked rather than assumed.** `notes LIKE '%partial%'` returns
**24**, not 0. All 24 are Bybit's `"tpslMode": "Partial"` API field quoted inside
*order-rejection error text* — a venue TP/SL mode string, on rows with
`status: exchange_rejected`. None is a strategy scale-out. The zero survives.

---

## 4. Why it cannot run — the config, and the accounts (this is the new part)

MI-188b cited `execution: shadow`. That is true and it is **not the binding
constraint**, and the difference matters for what the operator does next.

**Fact 1 — routing is zero.** Read against the **live** `/api/bot/config`
(`as_of 2026-09-09T07:44:46Z`), not the YAML:

```
turtle_soup    -> routed to 0 of 11 accounts: NONE
trend_donchian -> routed to 3 of 11 accounts: ['bybit_1','bybit_2','bybit_portfolio']   <- CONTROL
ict_scalp_5m   -> routed to 1 of 11 accounts                                            <- CONTROL
```

The controls fire, so the zero is a real zero and not a broken roster probe. It was
de-routed from `bybit_1` on **2026-07-01** (Tier-3, operator-approved,
`config/accounts.yaml:159`) and from `ib_paper` on **2026-07-07**
(`config/accounts.yaml:600`), where it had been an inert no-op anyway because
BTCUSDT is not tradeable on IBKR.

**Fact 2 — `execution: shadow`.** `config/strategies.yaml:52`, Tier-3
operator-approved demote 2026-07-07, on evidence that turtle_soup is net-negative at
*every* stop setting on BTCUSDT. `src/core/coordinator.py:1349` forces
`effective_dry = True` for any `execution: shadow` strategy **on every account,
regardless of the account's `mode: live`**.

**These are independent locks, and only the first one stops the strategy from
running at all.** `execution: shadow` is explicitly designed to keep logging order
packages for data collection while sending no live order — its own config comment
says it "logs packages for data, never sends a live order on ANY account/symbol".
Zero routing means it does not even get that far.

**The consequence is a finding.** turtle_soup has produced **0 order packages since
2026-07-01**. The shadow demote's stated benefit — continued data collection — **is
not being realised**, and has not been for 70 days. The config comment describes a
behaviour the routing makes impossible. Filed as
`BL-20260909-SHADOW-DEMOTE-CLAIMS-DATA-COLLECTION-WHILE-ZERO-ROUTING-COLLECTS-NOTHING`.

**And the three trades that do exist never opened.** All predate the de-route:

| id | date | status | `closed_at` | size | account |
|---|---|---|---|---|---|
| 1142 | 2026-05-10 | `rejected` | NULL | 0.0 | bybit_1 (demo) |
| 2264 | 2026-06-02 | `rejected` | NULL | 0.0 | bybit_1 (demo) |
| 3073 | 2026-07-01 | `exchange_rejected` | NULL | 7.34 | bybit_1 (demo) |

Rejection reasons, from `notes`: `below_min_balance` (gate balance $0.00 vs $50
floor); `intent_noop:flip_suppressed_hold_policy`; and Bybit `ErrCode 110007`
("ab not enough for new order"). Their packages read `rejected/no_fill_all_accounts`,
`orphaned`, `orphaned`. turtle_soup has **0 rows** in `position_telemetry`.

So `monitor()` was never called on an open turtle_soup package. **The TP1 branch has
never been evaluated even once** — it is not that it evaluated false.

---

## 5. The margin — would a 1.0R TP1 be reachable if it *were* routed?

The done-condition asks for the distribution of the quantity tested against the
threshold, so the margin is visible. turtle_soup has no excursion data of its own,
so the only available read is the **fleet's**.

> ⚠️ **This is a PROXY and must not be quoted as turtle_soup's.** It is the whole
> fleet's maximum-favourable-excursion distribution, on other strategies' geometry.
> It bounds whether a 1.0R rung is *reachable in this market at all* — nothing more.

> ⚠️ **Every one of these values is `peak_provenance: ESTIMATED`. ZERO are
> `MEASURED`** (193 of 193). Per `src/runtime/provenance.py` these are different
> facts and must not be folded. **No decision should be made on this table alone.**

**Population: `position_telemetry`, complete table n=193. 63 rows have `peak_r`
NULL. 12 carry the `-1e18` sentinel (§ 7a) and are excluded and counted, not
silently dropped. Clean n = 118.**

| statistic | peak_r |
|---|---:|
| min | +0.000 |
| p25 | +0.241 |
| **median** | **+0.787** |
| p75 | +1.525 |
| p90 | +2.391 |
| max | +6.709 |

Against turtle_soup's live rungs:

| threshold | meaning | share clearing it |
|---|---|---:|
| ≥ 0.75R | `be_at_r` (break-even trail) | 61/118 = **51.7%** |
| ≥ 1.00R | **`tp1_at_r` — the partial-bank rung** | 43/118 = **36.4%** |
| ≥ 3.00R | `tp2_at_r` (the runner's target) | 6/118 = **5.1%** |

**Reading:** the TP1 threshold is *not* geometrically unreachable — on ESTIMATED
data, about a third of fleet positions reach it. So this is **not** a case of a
threshold no live trade could ever satisfy. It is a case of a mechanism whose input
was switched off. That distinction is the whole answer: **fixing the threshold would
change nothing; only routing would.**

Note also the shape: 36.4% reach 1.0R but only 5.1% reach 3.0R. A TP1/TP2 ladder at
1.0R/3.0R would bank on roughly one position in three and let ~86% of those runners
fail to reach TP2. That is an argument *for* partial banking as a concept and
against *this* leg's particular 3.0R runner — but it is ESTIMATED data on the wrong
strategy's geometry, and I am explicitly **not** proposing a parameter from it.

---

## 6. Is partial banking ever appropriate here? — the null, and the Tier-3 proposal

**A null is an answer, and this is close to one.** As the fleet is configured today,
partial banking is **correctly** dormant:

1. Its only producer is a leg the operator **deliberately demoted** on measured
   evidence that it loses money at every stop setting. Reviving it to exercise the
   partial path would be re-arming a known money-loser to test a mechanism.
2. `partial_close` is granted to **bybit only** (`EXCHANGE_MANAGEMENT_CAPS`,
   `src/units/accounts/clients.py:703`). `interactive_brokers`, `ib`, `alpaca`,
   `oanda` and `breakout` all resolve to unsupported. By account, the addressable
   share of the census is:

   | venue group | n trades | share of 5589 |
   |---|---:|---:|
   | bybit (`bybit_1` 2258 + `bybit_2` 1656 + `bybit_portfolio` 91) | **4005** | **71.7%** |
   | alpaca (405 + 338 + 203 + 54) | 1000 | 17.9% |
   | ib (`ib_paper` 283 + `ib_live` 0) | 283 | 5.1% |
   | oanda 8 + breakout 23 | 31 | 0.6% |
   | unattributed (`account_id` NULL/other) | 270 | 4.8% |

   (The 270 unattributed rows are reported rather than dropped; they do not change
   the ordering.)

**Recommendation: STOP HERE, and do not build on top of the partial-close path.**
The mechanism is sound, dormant for a good reason, and cannot be exercised without
either reviving a demoted loser or wiring a new producer. Neither is this cycle's
work, and this cycle's rule is to repair measurement before building.

**Tier-3 proposal — the operator's call, NOT enacted.** *If* the operator wants
partial banking to be a live capability rather than dead code, the cheapest honest
route is **not** to re-route turtle_soup. It is to declare a TP1 rung on a leg that
already runs on bybit and already has excursion data. That is a strategy change,
Tier-3, and requires its own offline backtest gate under
`docs/CLAUDE-RULES-CANONICAL.md` § "Promotion evidence". **I am naming the shape of
the change, not proposing a value, and nothing here should be merged as config.**

**Explicitly NOT proposed, per standing constraints:** `be_floor_r` stays DECIDED as
NONE and is not armed at any value; `ICT_SCALP_EXIT_HEAD_MODE` is not armed;
`TP_VENUE_CAP_PCT` is untouched; no exit-matrix cell `status` is re-graded; no
strategy leg is retired.

---

## 7. Instrument defects found (FILED, not fixed)

**(a) `position_telemetry.peak_r` still holds 12 fabricated `-1e18` values, and the
backlog row for it is marked `resolved`.**
The sentinel is a sort key that must never persist as a value
(`BL-20260818-TELEMETRY-PEAK-R-STORES-COALESCE-SENTINEL`, severity **high**, status
**`resolved`**). The write-path `CASE` fix landed **2026-09-08** (`e1d24f6`) — three
weeks after the row was opened. **There is no backfill**, and 12 live rows dated
2026-08-17 → 2026-08-24 still carry `peak_r = -1e18` with `peak_state: thin_window`
(strategies: `avax_pullback_2h` ×6, `mgc_trend_1h` ×2, `slv_trend_1h`,
`ada_pullback_2h`, `trend_donchian_sol_4h`). Any consumer taking `MIN(peak_r)` or
`AVG(peak_r)` over that column today gets a fabricated number — I hit exactly this
on my first pass, which reported a "minimum excursion" of −1e18. The row reading
`resolved` is what makes it dangerous: nobody will look again. Filed as
`BL-20260909-PEAK-R-SENTINEL-ROWS-STILL-LIVE-WHILE-ITS-BACKLOG-ROW-READS-RESOLVED`.

**(b) `peak_provenance` is `estimated` on 193 of 193 rows — never once `measured`.**
The provenance field exists precisely so a consumer can restrict to broker truth.
On this table that restriction yields the empty set, so *every* excursion-based
statement in the repo — including § 5 above — rests on ESTIMATED data with no
measured control to check it against. This is not a bug in a line of code; it is
the instrument having never produced its trusted grade. Given the cycle priority is
to repair measurement before acting on it, this bounds what the exit-management work
can honestly conclude. Filed as
`BL-20260909-PEAK-PROVENANCE-IS-ESTIMATED-ON-EVERY-ROW-SO-BROKER-TRUTH-EXCURSION-IS-THE-EMPTY-SET`.

**(c) The re-dispatch defect** — see § 0(b), filed as
`BL-20260909-PARTIAL-CLOSE-QUESTION-RE-DISPATCHED-TWICE-IN-TWO-DAYS-AFTER-IT-WAS-ANSWERED`.

**(d) The dead data-collection claim** — see § 4, filed as
`BL-20260909-SHADOW-DEMOTE-CLAIMS-DATA-COLLECTION-WHILE-ZERO-ROUTING-COLLECTS-NOTHING`.

---

## 8. What I did NOT establish

Stated plainly, because the absence of these is part of the result:

- **I did not read the work object** — it does not exist on `main` (§ 0a).
- **I did not fire the partial path**, in a harness or otherwise. The done-condition
  rules a harness out, and I did not build one.
- **I did not measure turtle_soup's own excursion distribution.** It has zero
  telemetry rows. § 5 is the fleet as a proxy and is labelled as such throughout.
- **I did not establish a MEASURED excursion figure anywhere.** All 193 rows are
  ESTIMATED (§ 7b). Nothing in § 5 is broker truth.
- **I did not verify the `execution: shadow` gate by observing it suppress a live
  order.** I read the branch (`coordinator.py:1349`) and established that no
  turtle_soup package has reached it since the de-route — which is a code read plus
  an absence, not an observation of the gate acting. Since routing is zero, that
  gate is currently untestable on this leg.
- **I did not check whether any *other* venue integration could be wired for
  `partial_close`.** § 6 reports the capability map as it stands; whether alpaca's
  documented `qty` parameter makes a scale-out cheap to wire is noted in
  `clients.py` and was not investigated here.
- **I did not re-grade, retire, arm or propose any live value.** § 6 names the shape
  of a change and stops.

---

## 9. Bottom line for the operator

The partial-close producer is **built, correct, and dormant for a decided reason**.
It has never fired because the one strategy that can drive it was de-routed to zero
accounts on 2026-07-01 and demoted to shadow on 2026-07-07, both Tier-3 and both
operator-approved on evidence that it lost money at every stop setting. **Nothing is
broken.** The recommendation is to leave it alone and **not** build active-management
work on top of it, because there is nothing underneath it to build on.

The two things genuinely worth your attention are not the partial path at all: the
excursion instrument has **never produced a measured value** (§ 7b), and it currently
serves **12 fabricated ones** under a backlog row that reads `resolved` (§ 7a).
