# MI-278 U13 — the sign disagreement is live today, and both of the row's first two candidate fixes are already shipped

> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U13 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Bears on** `PB-20260906-R-CONTAMINATION-QUANTIFIED-AND-THE-HEADLINE-SIGN-IS-WRONG` (clause 1) and on MI-278 U12.
> **Tier-1.** One live endpoint read plus one journal pull. The fix lives in `src/web/api/routers/performance.py` — **Tier-2** — so this unit measures and proposes; it does not take it.

---

## 0. The answer, in six sentences

**Clause (1) of that row is failing right now.** Read live on 2026-09-12, `/api/bot/performance?window=30d` publishes `totalPnl −14.5552` and `profitFactor 0.8251` beside `expectancyR **+0.1113**` — a losing month with a positive R expectancy — six days after the row was filed and on a window that has since moved, so this is an independent re-confirmation rather than a re-quote. **But two of the three candidate fixes the row proposes are already shipped, and neither closed it.** The payload's own `rBasis` reads `declaredInitial=41, storedStop=0` on the real-money block: **every** real-money row's R is already computed from the declared initial risk, not from the trailed `trades.stop_loss`, so the mechanism the row describes is **not** what produces today's real-money contamination; and `refusedWrongSide=0` shows the wrong-side refusal is present and fired zero times this window. What remains is the row's **third** option — exclude or separate the `contaminated` rows, which are **11 of 41 (26.8%)** and are still inside the aggregate the endpoint publishes them beside. **The defect does not compound with MI-278 U12's fan-out on this block** — real-money inflation is exactly 1.000× — **but only because the two live real-money accounts trade disjoint universes**, crypto on `bybit_2` and US ETFs on `alpaca_live`, with **0** packages spanning them; that is a routing coincidence, not a property of the filter. And a methodological finding that will otherwise cost the next session an hour: **the row's own reproducer can no longer be run.**

---

## 1. Population, stated first

| | |
|---|---|
| primary source | the **live** `GET /api/bot/performance?window=30d`, read 2026-09-12, HTTP 200 — `window=30d`, `since=2026-08-13T15:56:03Z`, `demo=False` (real money) |
| grading | the endpoint's **own** `rProvenance` and `rBasis` blocks, which it already publishes |
| secondary | `/api/diag/journal?table=trades&limit=1000` and `…&table=order_packages&limit=1000`, read 2026-09-12 |

⚠️ **This reads the endpoint rather than reproducing it, and that is a change of method forced by the data.** See § 4.

---

## 2. Clause (1), graded per block

| block | n | totalPnl | profitFactor | expectancyR | sign |
|---|--:|--:|--:|--:|---|
| **real money** | **41** | **−14.5552** | **0.8251** | **+0.1113** | **DISAGREE** |
| demo / paper | 606 | +203,799.44 | 2.5454 | +0.0127 | agree |
| paperPortfolio | 60 | −7,423.51 | 0.7446 | −0.0159 | agree |

**Only the real-money block disagrees, and it is the block the promotion gates read.** `profitFactor` is a third independent statement of direction and it sides with `totalPnl`, so the disagreement is 2-against-1, not a coin toss between two figures.

⚠️ **Do not read the fall in magnitude as progress.** The row measured `expectancyR +0.9818` on 2026-09-06; today it is `+0.1113`. The number is nine times smaller and **the sign is still wrong**. A session glancing at `+0.11` beside a negative PnL might conclude the problem is nearly gone; it is not, and the criterion is about the sign.

⚠️ **The paper blocks agreeing is not evidence they are clean.** They carry **85 contaminated rows of 606 (14.0%)**. Their PnL is so strongly positive (+203,799) that contaminated R points the same way by luck. Agreement here is a coincidence of magnitude, not a passing grade.

---

## 3. Two of the three candidate fixes are already in, and the row does not know

The row lists three: *"read the declared initial risk from `order_packages.meta`… or refuse a wrong-side row rather than `abs()`-ing it; or publish an expectancyR computed over `confirmed_initial` only."*

**The payload answers the first two, from `rBasis`:**

| block | `declaredInitial` | `storedStop` | `refusedWrongSide` |
|---|--:|--:|--:|
| **real money** | **41** | **0** | 0 |
| demo / paper | 287 | **319** | 0 |
| paperPortfolio | 60 | 0 | 0 |

- **Candidate 1 is SHIPPED on real money and did not close clause (1).** All 41 rows use the declared initial risk. So the row's stated mechanism — *"`trades.stop_loss` holds the FINAL stop … `|entry−stop|` collapses toward zero and R explodes"* — **is not the live cause of the real-money contamination**, whatever it was on 2026-09-06. A session that reads the row and goes to fix the `storedStop` path will find it already bypassed here.
- ⚠️ **It IS still live on the paper blocks — 319 of 606 rows.** So the mechanism is real and reachable, just not on the block that fails.
- **Candidate 2 is present and inert this window:** `refusedWrongSide=0` on every block. That is the guard working *or* the condition not occurring, and the payload cannot tell those apart — recorded as such rather than claimed as either.
- **Candidate 3 is what is left**, and it is the one the row's clause (2) is written against.

---

## 4. The row's own reproducer can no longer be run — measured, not inferred

The row's strongest feature is an **exact** reproduction: *"Applying exactly those to the live journal on 2026-09-06 reproduces the endpoint on all four headline fields: n=39 (endpoint 39)…"*

That path is closed. **`/api/diag/journal` hard-caps at 1000 rows whatever limit is requested** — verified by asking for 3000 and 5000 and receiving 1000 both times, with an identical oldest row — and 1000 rows now reaches back only to **2026-08-18**, while the 30d window opens on **2026-08-13**. A journal-side rebuild of the real-money block therefore yields **n=38 against the endpoint's 41**, and no filter change can recover the missing five days.

**This is a real infrastructure finding, not a local inconvenience:** a documented, load-bearing reproducer silently stopped working because the fleet's row rate outgrew a fixed cap, and nothing announced it. Filed as `BL-20260912-THE-DIAG-JOURNAL-1000-ROW-CAP-SILENTLY-BROKE-A-DOCUMENTED-30-DAY-REPRODUCER`.

**The workaround is better than the original**: the endpoint already publishes `rProvenance` and `rBasis` for its own population, so grading its self-report is **exact by construction** and cannot drift from the endpoint's predicates the way a re-implemented filter can.

---

## 5. The interaction with U12: they do not compound here, for a contingent reason

MI-278 U12 established that `expectancyR` is also **row-denominated**, so account fan-out inflates its n. Two defects on one figure. Do they compound?

**On the real-money block, no.** Measured over the reachable real-money slice: **38 rows / 38 distinct packages = exactly 1.000×**, 0 packages carrying more than one real-money row.

⚠️ **But the protection is a routing coincidence, not a property of the filter, and the distinction decides whether it survives.** All 38 of those packages **do** span accounts — every one also carries a paper row — so fan-out is genuinely present in the data. It cannot double-count *inside* a real-money-only filter because the two live real-money accounts trade **disjoint universes**: `bybit_2` on BTC/ETH/XRP and `alpaca_live` on GLD/QQQ/SLV/SPY/TLT/USO, with **0** packages carrying both. **The moment one signal routes to both, the real-money block acquires fan-out with no diff that looks like it did.** This is the same shape as U12's `--account` finding.

**On the paper blocks it does bite** — 1.097× over the reachable slice.

⚠️ **Those inflation figures are `partially_reachable`, not `measured`, and the instrument refuses to render them as the block's own.** They are computed over ~25 days of a 30-day window. An inflation over a shorter window is a different quantity, and reporting it as the block's would be exactly the unprovenanced-diagnostic error this repo names.

---

## 6. The Tier-2 proposal — stated, not taken

**Publish `expectancyR` over `confirmed_initial` rows only, and publish the excluded count beside it** (the row's third candidate). On today's real-money block that is 8 rows of 41, which is a thin denominator and must be published as such rather than hidden — `rTradeCount` and an `rCoverage` already exist for exactly that purpose and are the right precedent.

**What must NOT be done, on this unit's own evidence:**

- **Do not re-point the R basis at the declared initial risk on real money — it is already there** (§ 3), and doing it again would look like a fix while changing nothing.
- **Do not read `refusedWrongSide=0` as proof the guard works.** It is equally consistent with the condition not arising.
- **Do not correct fan-out on the real-money block first.** It is 1.000× there; the gain is zero and the contamination is untouched.
- ⚠️ **Do not widen the window to get a bigger denominator.** That admits more contaminated rows at the same 26.8% rate and is the low-n hazard one level up.

**This is `src/web/api/routers/performance.py`, i.e. Tier-2, and changing a number the operator reads is a decision. It is not taken here.**

---

## 7. What this unit does NOT establish

- **It does not re-derive the row's decomposition.** The row's `n=39 / +44.83 contaminated R` figures are from 2026-09-06 and are neither confirmed nor refuted here; § 4 explains why they cannot be re-run from the diag surface.
- **It does not measure what the corrected expectancyR would be.** That needs the per-row R the endpoint does not publish, and the journal cannot span the window.
- **It does not clear either clause.** Clause (1) needs the live endpoint to agree with itself; clause (2) needs the `|R|>10` control to be shown not to move a corrected aggregate. Both require the Tier-2 change first.
- **One window, one read.** Everything here is `window=30d` on 2026-09-12.
