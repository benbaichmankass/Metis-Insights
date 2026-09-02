# The evidence floor — what would have to change, and what would not

**Status: PROPOSAL. Nothing here is enacted.** No leg is retired, `config/strategies.yaml`
is untouched, `MIN_CLOSED_FOR_ACTION` is unchanged at 20, and the review window is
unchanged at 7 days. This document is the evidence behind an operator decision, filed
because the row `OI-20260901-REVIEW-PACKET-CANNOT-PROPOSE-AN-ACTION-AND-ITS-EVIDENCE-BLOCK-IS-UNEXERCISED`
cannot clear until that decision is recorded.

**The operator's instruction this answers** (2026-09-02): *fix the evidence floor first* —
make the floor reachable and the mismatch visible, **not** by lowering the bar until
something passes.

---

## 0. The short version

**There is no window that fixes this, and the number that makes it look urgent is
paper money.**

- At the review window of 7 days, **0 of 52 legs** were gradeable. Widening the window
  to 30 days would grade **4**; to 35 days, **7**; to 140 days, **18**. It never grades
  more than 18, at any width, because **34 of the 52 legs closed nothing at all** and
  8 of those cannot close a trade at any window by construction.
- The **−35,446** of losing PnL quoted as the cost of the gap is a **blended real+paper**
  figure, which this repo's own P4 contract forbids (*"real and paper performance are
  never blended"*). Split: over the same window the fleet's closed real-money PnL is
  **−3.11 across 6 closed trades on one account**. The paper side is −36,219.07 across
  136 closes.
- So the honest recommendation is **not** a wider window. It is to **stop asking one
  mechanism to dispose of three different populations**, and to settle the
  account-class question before the window question, because the window question cannot
  be answered over a blended pool.

---

## 1. Populations (stated before any number below is used)

| population | what it is | n |
|---|---|---:|
| **P1 — the committed index** | `comms/strategy_reviews/2026-09-01/INDEX.json`, `generated_at` `2026-09-01T12:51:37Z` (run #10656) | 52 legs |
| **P2 — the committed packets** | `comms/strategy_reviews/2026-09-01/*.json`, `generated_at` span `12:03:11Z .. 12:03:37Z` (run #10652) | 52 packets |
| **P3 — the window's closed trades** | `trades` joined to `order_packages` on `order_package_id`, package `created_at` inside `2026-08-25T12:03:05Z .. 2026-09-01T12:03:05Z`, `is_backtest != 1`, `status = 'closed'`, `pnl NOT NULL` | 142 trades |

**P3's read is bounded and the bound is stated.** It comes from a 1000-row
`/api/diag/journal?table=trades` page (the route cap) spanning `2026-08-03T01:28Z` ..
`2026-09-02T04:11Z`. The window starts 2026-08-25, **inside** that span, so the window is
fully covered; rows older than 2026-08-03 are excluded and are irrelevant to it.

⚠️ **P1 and P2 ARE DIFFERENT RUNS AND THE COMMITTED DAY DIRECTORY DOES NOT SAY SO.**
Run #10656 rewrote `INDEX.json` only; the 52 packets are still #10652's, written 48
minutes earlier. **1 of 52 legs already disagrees between them** — `qqq_pullback_1h`
reads `n_closed=1, pnl=-212.52` in the index and `n_closed=0, pnl=0.0` in its own packet.
Any reader joining index rows to packet files crosses a run boundary with nothing
stamping that it did. `n_closed` below is always the **index's**; the funnel counts
(`n_decisions`/`n_filled`) exist only in the packets. Filed as
`OI-20260902-COMMITTED-REVIEW-DAY-JOINS-TWO-RUNS-SILENTLY`; the code fix in this PR
makes the index self-sufficient so the join is no longer needed.

⚠️ **P3's per-leg sums differ from P1's, correctly.** P1 applies the provenance filter
(`pnl_is_trustworthy`) and P3 does not, so e.g. `ict_scalp_sol_5m` is 8 closes /
−6,241.05 in P1 and 9 closes / −6,378.79 in P3. Both are right for what they cover; the
account-class split in §3 is over P3, and its conclusion does not turn on the difference.

---

## 2. Finding 1 — the window is not the binding constraint for 34 of 52 legs

Over **P1 (52 legs)**, at window 7 days and floor n>=20, every leg falls into exactly
one of four classes (`src/runtime/evidence_horizon.py`):

| class | legs | what a wider window does for it |
|---|---:|---|
| `gradeable_now` | **0** | — |
| `reachable` — closed >=1 trade, so a rate exists | **18** | **accumulates evidence. The only class waiting reaches.** |
| `unbounded_no_closes` — closed nothing, so **no rate was measured** | **26** | nothing follows from this window. Not "a rate of zero" — an absence of measurement. |
| `structurally_ungradeable` — `execution: shadow`, no fills | **8** | **nothing, ever.** A shadow leg does not reach the order path by design. |

And the funnel, over **P2 (52 packets)**, says where each leg stops: **18 closed at least
one trade · 4 filled but closed nothing · 7 signalled but filled nothing · 23 produced no
decision at all**. (The 18/4 split uses P1's `n_closed`; on P2's own counts it is 17/5,
the one-row disagreement above.)

### What each candidate window actually buys

Legs graded at window `W` are those whose projection to n=20 fits inside `W`. At the
**point estimate** of each leg's own observed close rate — over P1's 52 legs:

| window | legs graded (of 52) | of the 13 losing legs | share of the losing PnL |
|---:|---:|---:|---:|
| 7d (today) | 0 | 0 | 0% |
| 21d | 1 | 1 | 17.6% |
| **30d** | **4** | 2 | 20.2% |
| **35d** | **7** | 3 | 28.8% |
| 90d | 10 | 6 | 30.7% |
| **140d** | **18** | 13 | 100% |
| 365d | 18 | 13 | 100% |

**The column stops at 18 and never moves again.** 34 legs are graded by no window.

⚠️ **And the point estimate is the optimistic reading of a one-sample forecast.** Eight of
the 18 `reachable` legs have `n_closed = 1`: their point projection is 140 days and the
95% interval consistent with that single close runs **29.5 days to 2,729 days**.
Requiring the *conservative* bound to fit instead, the same table reads **30d → 0 · 35d → 0
· 70d → 2 · 140d → 7**, and 18 only at ~3,000 days. **Quoting "140 days grades 18 legs"
without that interval is the low-n error moved from the grade to the forecast** — which is
the trap named in the instruction: a KILL fired off a window widened until something
passes is the same hazard the floor exists to prevent, one level up.

### Independent corroboration: the sunset pass already found the same population

The Phase-G sunset pass (E3, 2026-09-01) named **10 retirement candidates** without any
reference to this model. Classified here: **9 are `unbounded_no_closes`, 1 is
`structurally_ungradeable`, and 0 are `reachable`.** Two mechanisms built for different
questions agree on exactly which legs the closed-trade gate cannot serve.

---

## 3. Finding 2 — the urgency figure is blended real+paper, and the real-money half is −3.11

The measured motivation for widening the window is that **13 losing legs carry −35,446 of
provenance-trusted PnL that no window could act on**. That figure is real (it reproduces
exactly over P1) and its **interpretation does not survive an account-class split**.

Over **P3 (142 closed trades in the window)**:

| | closed trades | PnL |
|---|---:|---:|
| **paper** | 136 | **−36,219.07** |
| **real money** | **6** | **−3.11** |

Restricted to the 13 losing legs the same split is **paper −40,655.54 / real money −4.82**.

The entire real-money population for that review window is **6 closed trades, all on
`bybit_2`, across 2 legs** — `ict_scalp_5m` (5 trades: −4.91, −5.10, +1.14, +4.27, +6.30)
and `trend_donchian_xrp_4h` (1 trade: −4.82).

**And 59.4% of the −35,446 is a single leg with a single closed trade.**
`ict_scalp_mgc_15m` carries **−21,070.00** off `n_closed = 1`. Verified against the
journal (trade `5259`, `ib_paper`, MGC long, entry 4454.8 → exit 4405.8, 43 contracts,
`sl_cross`): −49.0 points × 43 × the 10 oz MGC multiplier = **−21,070.00 exactly**, so the
arithmetic is correct — and it is a **paper** account, one trade, and the leg's horizon is
140 days point / 2,729 days conservative. A gate that killed it today would be killing on
n=1.

⚠️ **`scripts/ml/strategy_review_packet.py` filters only `is_backtest`.** It never
consults `account_class` or `is_demo`, so real and paper PnL are blended in every packet
and in every index row — against CLAUDE.md's P4 live-trade-management contract (*"real and
paper performance are never blended"*), which `/api/bot/stats`, `/api/bot/performance`,
`/api/bot/trades/closed` and `/api/bot/strategy/attribution` all honour. Filed as
`OI-20260902-STRATEGY-REVIEW-PACKET-BLENDS-REAL-AND-PAPER-PNL`. **Not fixed here** — it
changes what every packet reports and is the operator's call, which is exactly what this
document exists to put to them.

---

## 4. The proposal

Four parts. **(A) is the one that matters and it is not a window change.**

### A. Split the gate's population by account class, before choosing any window

The window question cannot be answered over a pool where 136 of 142 closes are paper. The
two populations have different sizes, different rates, and different consequences for
being wrong:

- **Real money**: 6 closes/week across 2 legs. `ict_scalp_5m` at 5 closes/7d projects to
  n=20 in **28 days** (interval 13.3d – 71.1d); `trend_donchian_xrp_4h` at 1/7d projects
  to **140 days** (interval 29.5d – 2,729d). Nothing else on real money closed at all.
- **Paper**: 136 closes/week. This is where a 30–35 day window genuinely earns something.

**Proposed:** the packet reports the two populations side by side and grades on the
real-money one, with the paper one published beside it as a labelled second grade. This
is a Tier-3 change to what the gate decides on and is **proposed, not enacted**.

### B. Keep `MIN_CLOSED_FOR_ACTION = 20`. Do not lower it.

Nothing in the evidence argues for a lower floor. The 13 losing legs sit at n = 1–8; a
floor low enough to act on them is a floor low enough to act on noise, and the largest
single loss in the set is an n=1 paper trade.

### C. Add a **secondary 35-day window**, published beside the 7-day headline — never replacing it

35 days, not 140, and the reason is stated rather than tuned:

- it is the shortest window at which more than a token number of legs clear (**7 of 52**
  at the point estimate, vs 4 at 30d and 1 at 21d);
- 140 days spans five months and several config eras, so a KILL off it would be graded
  partly on a strategy that no longer exists in that form;
- it is short enough that a leg clearing it has closed >=20 trades **recently**.

⚠️ **It buys 7 legs, not the fleet, and it clears zero legs on the conservative bound.**
Any KILL or DEMOTE proposed off the 35-day window must carry
`days_to_floor_conservative` beside it, so a reader can see how deep the evidence
actually is. That is a reporting requirement, not a nicety: for 8 of the 18 `reachable`
legs, that number is 2,729 days.

### D. Route the other two classes to mechanisms that fit them — the review packet is the wrong tool

- **`unbounded_no_closes` (26 legs)** — the question is not *"is this leg losing money?"*
  but *"has this leg produced anything at all?"*, which is the **sunset pass's** question
  and which it already answers (9 of its 10 candidates are in this class). Proposed: the
  review packet stops proposing an action for these legs and instead names the sunset
  pass as their owner, so `hold` stops meaning two different things.
- **`structurally_ungradeable` (8 shadow legs)** — a shadow leg's closed-trade count is 0
  **by design and forever**. Its evidence is its signals, not its fills:
  `avax_pullback_2h` produced 11 decisions and 0 fills; `mgc_trend_1h` 10 and 0. Proposed:
  a shadow-soak disposition keyed on decision volume and signal quality. **No window and
  no floor will ever grade these legs, and saying so plainly is the finding** — engineering
  around it would mean inventing a closed-trade count that does not exist.

---

## 5. What this PR actually changes (all Tier 1, all additive)

| change | effect |
|---|---|
| `src/runtime/evidence_horizon.py` (new) | the horizon model — five never-collapsed classes, three-number intervals, stdlib only |
| `scripts/ml/strategy_review_packet.py` | **publishes** `evidence_horizon` per packet + per index row, plus `window_days`, `n_decisions`, `n_filled`, `execution` on the index, and an `evidence_horizon_summary`. **The floor value and every grade are byte-identical.** |
| `src/web/api/routers/strategy_review.py` | `evidence.horizon` reads the published block and aggregates it; grades `unknown` on an index that predates it |
| `scripts/ml/evidence_floor_report.py` (new) | regenerates every table in this document from the committed record |
| `scripts/ci/check_collapsed_states.py` | registers `strategy_reviews.horizon_class` |

**Nothing changes what any leg is graded, and nothing writes to `comms/strategy_reviews/`.**
The committed 2026-09-01 index is deliberately **not** back-filled: it is the record of
what that run said, and adding a field to it would make a later reader believe the run
published something it did not. That is why the offline report exists.

## 6. What clears the open row

`OI-20260901-REVIEW-PACKET-CANNOT-PROPOSE-AN-ACTION-AND-ITS-EVIDENCE-BLOCK-IS-UNEXERCISED`
needs all three of its conditions, and this PR reaches **none of them on its own**:

1. a committed index from a **scheduled** run — unaffected by this work;
2. that index carrying `min_closed_for_action` with `floor_state != "unknown"` — this PR
   makes the generator publish more, but only a real run exercises it;
3. **an operator decision on the window/floor pair recorded** — this document is what makes
   that answerable; it is not the answer.

⚠️ **And a run that simply emits an action does not clear it either.** That is the row's
own warning and it is the reason §4 recommends against the widest window on offer.

---

_Regenerate every table here with `python3 scripts/ml/evidence_floor_report.py --date 2026-09-01`._
