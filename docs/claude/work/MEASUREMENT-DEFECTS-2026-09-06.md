# The measurement defects, prioritized — 2026-09-06

**Operator-requested**, 2026-09-06: *"we're still having a lot of problems with wiring
and the mechanics and making sure that we're actually measuring the correct things, and
that's obviously top priority because we can't make good decisions if we have bad tools
for making those decisions... first of all, give me a list, and let's prioritize."*

This is that list. Ranked by **how badly a decision made on the number would go wrong**,
not by effort. Every figure carries its population. Live figures were read from
`https://ict-bot.duckdns.org/api/bot/performance` at **2026-09-06 ~11:10Z**.

⚠️ **One correction to what the manager said earlier today.** I previously described the
R inversion as "published +2.84 against confirmed-initial −0.53". That figure came from
a different window and **does not reproduce on the all-time window**, which currently
reads `expectancyR −0.1717` against `profitFactor 0.7294` — sign-consistent. The defect
is real and reproduces on the **30d** window; the earlier number should not be re-quoted.

---

## P1 — `expectancyR` disagrees with `profitFactor` on the same rows

**Measured, live, three windows:**

| window | n | totalPnl | profitFactor | **expectancyR** | contaminated | confirmedInitial |
|---|---|---|---|---|---|---|
| 7d | 11 | −$39.38 | 0.0 | **−0.9774** | 0 | 2 |
| **30d** | **39** | **−$3.63** | **0.9507** | **+0.9818** | **12 (30.8%)** | 7 |
| all | 424 | −$69.53 | 0.7294 | −0.1717 | 14 (3.3%) | 29 |

**The 30d row is the defect.** `expectancyR +0.98` says *we make almost a full R per
trade*. The same window, same rows, lost money (`profitFactor 0.9507`, PnL −$3.63).
Both cannot be true.

**And the contamination rate predicts it** — the only window that inverts is the only
window with material contamination: **12 of 39 rows (30.8%)** on 30d, against **14 of 424
(3.3%)** all-time and **0 of 11 (0%)** on 7d. That is the causal signature, not a
coincidence. ⚠️ Note the denominator: n=39 is a small window, and this is one observation
of the pattern, not a fitted relationship across many windows.

**Mechanism:** `r_multiple` divides by `|entry − stop|`, and `trades.stop_loss` holds the
**final trailed** stop. A trade trailed toward breakeven collapses its own denominator,
so the winners that trail hardest get the largest R. Max observed on one row: **+3672**.

**Why it is P1: R feeds the promotion gates.** A leg promoted on `expectancyR` in a
30d-shaped window is promoted on a number whose sign is an artifact of its own trailing.

**Owner:** MI-144 (`session_01L7dTAuGMdn3CTGeyYV5Mox`).

---

## P2 — `rCoverage` reports 1.0 while ~90% of rows have no verified risk basis

**Measured, live, all-window:** `rCoverage: 1.0` — perfect coverage — against its own
`rProvenance` block on the same payload:

```
confirmedInitial 29 · contaminated 14 · unverified 381 · noBasis 0     (n = 424)
```

**381 of 424 = 89.9% `unverified`.** On 7d it is 9 of 11 (81.8%) and `rCoverage` still
reads **1.0**.

This is the repo's own **collapsed-states** class: `rCoverage` is answering *"did we
compute an R for every row?"* while presenting as *"do we know the risk basis for every
row?"* — and those are different questions with a 90-point gap between them. Note the
sibling on the same payload does it right: `pnlCoverage` reads **0.7594**, with
`measured 322 / estimated 20 / fabricated 4 / unverified 78` — an honest denominator.

**Why it is P2 and not P1:** it does not by itself produce a wrong number; it removes the
warning that P1's number is untrustworthy. It is the instrument that should have caught P1.

**Also:** on `window=24h` (n=0), `pnlCoverage` correctly returns `None` — *we did not
look* — while `rCoverage` returns `0.0`, a real reading. Same absence, two encodings.

**Owner:** MI-144.

---

## P3 — `exit_reason` is frozen at the one moment the answer cannot be known

**91 of 155 (58.7%)** `reconciler_filled` closes had in fact reached a declared bracket.
Nothing re-classifies when a price arrives late. Only **26 of the last 200 closes
(13.0%)** are labelled `sl` or `tp`.

**This is why the operator's central question has no trustworthy answer.** *"Are trades
ending at their brackets?"* cannot be answered from a field that is written before the
evidence exists and never revised.

**Owner:** MI-144. **Blocks:** any verdict on the exit-mechanics thesis, i.e. P5 below.

---

## P4 — `/api/bot/trades/closed` silently returns `[]`

**Bisected with a positive control:** `limit=5 → 5`, `100 → 100`, `200 → 200`,
**`400 → 0`**, **`800 → 0`**. Any `since=` → `0`.

No error, no warning, no partial result — a bare empty list. **This is the route a
performance review grades from**, so a review that asks for a wide window is graded on
nothing and reports a clean negative.

⚠️ **Raising the limit cap does NOT fix this. The defect is the silence.** The route must
serve the window or refuse it with a reason.

⚠️ **The affected population is UNMEASURED** — this was found by accident, so every other
read route needs the same probe.

**Owner:** MI-144.

---

## P5 — the exit mechanisms the strategy thesis depends on are mostly not built

**Measured** from `docs/research/exit-refinement-coverage.json` (`updated_at 2026-08-30`),
52 legs × 9 levers = **468 cells**, probe asserted against a positive control:

| state | cells | share |
|---|---|---|
| `honest_negative` | 320 | 68.4% |
| **`shipped`** | **39** | **8.3%** |
| `blocked` | 36 | 7.7% |
| `n/a` | 36 | 7.7% |
| `pending` | 20 | 4.3% |
| `passed_unshipped` | 10 | 2.1% |
| `shipped_gate_failed` | 7 | 1.5% |

Per lever, shipped / never-wired-or-blocked, out of 52 legs:

| lever | shipped | never wired or blocked |
|---|---|---|
| `bracket_geometry` | 17 | 10 |
| `trail_decay` | 12 | 10 |
| `trail_geometry` | 5 | 9 |
| `stale_stop` | 3 | 0 |
| **`giveback_stop`** | **1** | 1 |
| `exit_head_ml` | 1 | 18 |
| **`exit_ladder`** | **0** | 3 |
| **`regime_flip_exit`** | **0** | 8 |
| **`vol_trail`** | **0** | 13 |

**24 of 52 legs have no shipped exit lever at all.** `giveback_stop` — the lever that
directly implements the operator's *"stop giving back R"* — is shipped on **one** leg.

⚠️ **`n/a` and `honest_negative` are opposite findings and the matrix renders both as a
non-shipped cell.** *"The backtest says it does not help"* and *"there is no code to run
it"* demand different responses, and 72 cells are in the second category.

**Owner:** MI-146 (`session_014jrW67rFyJNnHr7y6dEhec`).

### ⚠️ CORRECTION 2026-09-06 — P5's own headline is computed off a lossy column

MI-145 (#11136) measured the sweep **corpus** rather than the matrix, and the premise
above is wrong in the direction that closes questions. **`honest_negative` does not mean
"the sweep found nothing."**

**Population: 1,376 corpus cells / 225 distinct `(leg, lever)` pairs**, joined to the
matrix by `leg → rows[].strategy`. **91 of 1,376 cells (6.6%) are passing**, giving
**50 of 225 pairs (22.2%) with at least one passing cell** — and of those 50, **35 read
`honest_negative` in the matrix.** Best OOS deltas among them: `gld_pullback_1h
trail_decay` **+8.326**, `tlt_pullback_1d trail_decay` +8.316, `gld_pullback_1h vol_trail`
+8.267.

**This is not a guard failure and not dishonesty** — `check_matrix_corpus_agreement.py`
passes, and each of the 35 carries its counter-evidence in the cell's `ref` **prose**,
which the guard's `ACK` regex requires. The matrix is doing something defensible: a
passing *cell* is not a passing *lever disposition*, and changing a live leg's disposition
is Tier-3.

**The defect is that the status WORD cannot carry the distinction** — *"measured and it
lost"* and *"measured, it WON across a walk-forward, disposition withheld pending an
operator decision"* share one value. That is a **collapsed state** in this repo's own
sense. And the consequence is concrete: **every aggregate reads the status column, not the
prose.** So P5's "320 `honest_negative` (68.4%)" above, and MI-146's *"`vol_trail`:
honestly failed everywhere … nothing to build"*, are both lossy the same way —
**`vol_trail` passed a cell on 13 of those 35 pairs.**

⚠️ **This does NOT say 35 levers should ship.** A Path B pass buys net R by spending
drawdown, and `beats()`'s own docstring records `path_b_wf_pass` going **0-for-3** on that
exchange rate in one measured run. It says the one-word summary closes questions it should
leave open. Remedy proposed by MI-145 (Tier-1, status-only): a distinct
`negative_disposition_withheld`, or a `corpus_pass: true` flag on the `basis` field.

**This is the same disease as the meta-finding below, one layer down: research that found
something, recorded as having found nothing.**

---

## P6 — the backtest → soak chain is inverted on the live ict_scalp family

The operator's stated model: *"soaking is only for mechanical verification of what we
already have tested to be verifiably true in back testing."*

All eight `ict_scalp_*` legs carry `bracket_geometry: pending` — **never swept** — while
the leg trades real money. The sweep is **explicitly not blocked** (the free
`data.binance.vision` lane covers crypto). So the leg's bracket geometry was soaked on
live money before it was backtested, which is the stated order reversed.

**Owner:** MI-147, queued behind the WIP ceiling.

---

## ⚠️ P3 AND P5 INTERACT, AND IT CHANGES A HEADLINE NUMBER

MI-146's audit landed 2026-09-06 (#11124, `docs/research/exit-lever-wiring-audit-2026-09-06.md`)
and measured the realised close reasons: **directional book, n = 120, 2026-08-27→09-06** —
bracket **29.2%**, of which take-profit **5.8%**; active-management levers **7.5%**;
reconciliation and plumbing **63.3%**.

**That measurement reads `exit_reason`, which is the field P3 says is frozen.** MI-144
measured that **91 of 155 (58.7%)** `reconciler_filled` closes had in fact reached a
declared bracket. MI-146 counts **29 `reconciler_filled`** rows in its 120. If the same
58.7% holds on that subset, roughly **17** of them belong in the bracket bucket, which
would move bracket to ~43% and plumbing to ~49%.

**So state them as bounds, not point estimates**, on that same n = 120 directional book:
bracket **≥ 35 of 120 (29.2%)**, plumbing **≤ 76 of 120 (63.3%)**. Neither audit is wrong;
they were measured against the same defective field, and only P3 landing makes either
number final. **Do not quote 76 of 120 as settled.**

**What does NOT depend on the frozen field, and therefore stands:** MI-146's take-profit
finding is read from the *running config*, not from close labels — **25 of 44 enabled live
legs (56.8%) have no reachable take-profit** (15 carry a `tp_r ≥ 20` sentinel clamped by
`TP_VENUE_CAP_PCT = 0.099`, Bybit's rejection boundary; 10 declare none at all). On those
legs *"it was right and it hit the take profit"* is structurally impossible regardless of
how any close is labelled.

## What is already actioned

- **P1–P4** → MI-144, in flight, operator's top priority.
- **P5** → MI-146, in flight.
- **P6** → MI-147, queued.
- `ict_scalp_5m` off `bybit_2` (operator decision, narrow lever) → MI-143b.
- Bybit close-qty artifact (operator approved both parts) → MI-139b.

## What this list does NOT establish

- Whether trades *are* ending at their brackets. **P3 must land before that number can be
  trusted**, and no number quoted before then should be treated as an answer.
- The population affected by P4. Unmeasured by construction.
- Whether the 320 `honest_negative` cells are real negatives — the sweep harness has **no
  positive control**, so a harness that silently returns "no improvement" for every input
  is indistinguishable from one that works. That is MI-145, and until it runs, "the
  backtest says it does not help" is itself an unverified claim.
