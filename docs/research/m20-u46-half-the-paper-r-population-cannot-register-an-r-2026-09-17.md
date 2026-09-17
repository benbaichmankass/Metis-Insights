# M20 U46 — PB-20260821's clause 1 now PASSES, and checking it found that half the paper R population cannot register an R at all

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U46** · RESEARCH lane · Tier-1 · works `PB-20260821-R-AND-DOLLARS-DISAGREE-IN-SIGN` (performance backlog, `severity: high`, `tier: 1 (measurement)`, `status: open`) — the oldest open `high` row in that backlog this lane had not yet worked.

Instrument: [`scripts/research/r_denominator_admissibility.py`](../../scripts/research/r_denominator_admissibility.py)
(37 self-test controls; 20 pytest controls in
[`tests/test_r_denominator_admissibility.py`](../../tests/test_r_denominator_admissibility.py);
5 planted defects, 5 caught by a named control).

---

## Input provenance

* `GET https://ict-bot.duckdns.org/api/bot/performance?window=30d`, read **2026-09-17T11:4xZ**, browser-direct. `window: 30d`, `since: 2026-08-18T11:43:52.914895+00:00`, `error: false`. Four blocks: real money (n=41), `demo` and `paper` (both n=631, byte-identical), `paperPortfolio` (n=66).
* `GET /api/diag/journal?table=order_packages&limit=1000`, read 2026-09-17T11:03Z, `rowset_digest: sha256:e2d5d71311bf3c349ef4c3bd932c51a5a43b5cec3fcc82b3ca15e586b20d38b3` (the same pull U45 declares).

Every figure below is reproducible with
`python3 scripts/research/r_denominator_admissibility.py --performance <perf> --packages <pkg> --bound 3.0`.

---

## 1. Clause 1 PASSES today — at a stated bound, over 57 leg-rows

`PB-20260821`'s `resolution_criteria` is conjunctive:

> (1) no per-strategy `expectancyR` exceeds a stated plausible bound without an explicit outlier annotation, **and** (2) the real-money `totalR` sign matches its `totalPnl` sign.

**MEASURED**, across all four blocks — 6 real-money legs + 37 paper + 14 `paperPortfolio` = **57 leg-rows**:

| block | n trades | largest \|expectancyR\| | leg |
|---|--:|--:|---|
| real money | 41 | **1.4821** | `trend_donchian_eth_4h` |
| paper / demo | 631 | **1.4364** | `trend_donchian` |
| paperPortfolio | 66 | **2.3586** | `trend_donchian` (n=4) |

**Nothing anywhere exceeds 2.36.** The row's headline outliers are gone: 62.615 (filed), 63.183 (2026-08-29), 73.697 and **206.920** (2026-09-02). At a stated bound of **3.0 R/trade** — chosen because every `ict_scalp` leg declares `tp_at_r: 1.5` and a donchian trail can run past it, so 2× the declared target is generous — clause 1 holds with no annotation needed.

**Why it holds** is visible in `rBasis`, which the endpoint now publishes per leg: real money reads `declaredInitial 41 / storedStop 0`, `paperPortfolio` `66 / 0`. MI-144's provenanced R is binding on **every non-pairs leg**, so the trailing-stop collapse the row root-caused cannot operate there. That is a real repair, not a window artifact.

**Clause 2 still fails** on real money (`totalPnl −21.529` against `totalR +1.3983`). MI-278 U36 already established that disagreement as **risk-size heterogeneity** and says in terms *"do not 'fix' the endpoint to make them agree"*. This unit does not re-litigate it.

---

## 2. ⚠️ But checking clause 1 found a defect neither clause names, and it points the OTHER way

`rCoverage` answers *"does this row have a risk denominator?"*. It does **not** answer *"is that denominator a risk distance?"* — and on this fleet the two come apart.

### The measurement

| population, `|entry − sl| / entry` | n | min | p25 | median | p75 | max |
|---|--:|--:|--:|--:|--:|--:|
| **pairs** packages | 323 | **0.5000** | 0.5000 | **1.0000** | 1.0000 | 1.0000 |
| every other package | 677 | 0.0001 | 0.0057 | **0.0107** | 0.0220 | **0.1440** |

**The two distributions do not overlap** — the pairs minimum (0.5000) is 3.5× the non-pairs maximum (0.1440). 162 of the 323 pairs packages (50.2%) sit at **exactly 1.0000**: a stop at 100% of the entry price. And **0 of 323 carry a `risk_per_unit`**, which is why `r_multiple_provenanced` has nothing to fall back on but the mutated `stop_loss` column.

A stop at 50–100% of price is not a risk level; it is a sentinel. `R = pnl / (qty × entry × 0.5..1.0)` is a **return on NOTIONAL**.

### What that does to the paper aggregate

`rBasis.storedStop` on the paper block is **312**, and the per-leg breakdown accounts for **all** of it:

```
pairs_sol_eth_a 88 + pairs_sol_eth_b 86 + pairs_bnb_btc_a 70 + pairs_bnb_btc_b 68 = 312
```

| | n | totalR | totalPnl | mean R/trade |
|---|--:|--:|--:|--:|
| the four pairs legs | 312 | **+0.0240** | −18.60 | **+0.00008** |
| every other paper leg | 319 | +9.4682 | +165,865.51 | +0.02968 |
| **sum** | **631** | **9.4922** | | |

against the endpoint's reported `totalTrades 631` / `totalR 9.4923` — **reconciles**.

So on the paper block:

* **49.4% of the R population (312 of 631) is structurally incapable of registering an R**, contributing a mean of **+0.00008** R per trade;
* those rows are **0.4% of the block's \|PnL\|** — they are nearly all of the denominator and almost none of the money;
* `expectancyR` divides by the row COUNT, so the reported **+0.01504** is **1.97×** understated against the **+0.02968** the admissible rows alone produce;
* and `rCoverage` reads **1.0000**.

### ⚠️ This is the MIRROR of the defect the row was filed about

`PB-20260821` feared a denominator collapsing toward zero, making R **explode** — its worst case was 206.920 R/trade. A sentinel denominator makes R **vanish**. That failure is quiet, looks healthy, and drags every aggregate above it toward zero. It would never trip clause 1, which only bounds R from above.

---

## 3. What the instrument refuses to do

`rBasis` alone cannot tell a sentinel from a risk level — `storedStop` says *where* the denominator came from, not *what it is*. So **without a `--packages` pull the module publishes no admissible share at all** and prints `SCALE NOT GRADED — we did not look`. Deriving one from `rBasis` would reproduce the exact collapse the module exists to expose; the endpoint already reports 1.0 there.

Three axes, never collapsed: `basis` (5 states, read from the endpoint's own `rBasis`) · `scale` (`risk_level` / `notional_sentinel` / `unknown`) · `admissible` (only when both are known and neither disqualifies). A **mixed** leg — some packages sentinel, some not — grades `unknown` and is **named**, because "half this leg's denominators are sentinels" is a finding, not a rounding decision. An **ungraded** leg counts toward neither bucket.

The restated aggregate is printed **beside** the reported one, never instead of it, and a reconciliation of the per-leg table against the endpoint's own block totals prints on every run — the control that caught U45's own first draft counting two different populations.

The sentinel bar is **0.25**, a **chosen** value with a measured basis: it sits in the empty gap between the two distributions above, 1.7× above the non-pairs max and 2× below the pairs min. It is a flag, not a constant.

---

## 4. What this does NOT establish

* It does **not** say the pairs sleeve is mis-sized, mis-stopped, or losing money. Its sentinel stop is the isolated executor's own design (`src/units/strategies/pairs_executor.py` — a spread position's exit is the joint spread exit, not a per-leg stop), and on this window the four legs net **−$18.60** over 312 closes. The finding is that **an R statistic computed over them is not an R**.
* It does **not** propose excluding them from the endpoint. Whether an R aggregate should drop a leg is a decision, and a decision about what a **promotion gate** reads is **Tier-3**. `src/web/api/routers/performance.py` and `_clean_trades.py` are read only.
* It does **not** close `PB-20260821`. Clause 1 passes; clause 2 is disputed by U36 rather than met; and this unit adds a third condition the row does not yet name. The row's `updates` records all three.
* **Clause 1 passing is a statement about THIS window.** The mechanism that produced 206.920 is retired on the non-pairs book (`declaredInitial` is 100% there), but the bound was not *enforced* — nothing rejects an implausible `expectancyR` today, so a leg whose declared risk is missing would fall back to the mutated column again. The pairs legs are the standing proof that the fallback is still reachable: **312 rows are on it right now.**
