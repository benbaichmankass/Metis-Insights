# MI-278 U12 — 21 surfaces publish a price-path rate on a row denominator, and three of them are the dashboard

> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U12 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Discharges the "name the surfaces" clause of** `BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY`.
> **Tier-1.** Read-only over the repo plus one `/api/diag/journal` pull. No `src/` write, no config, no order path, nothing enacted.

---

## 0. The answer, in six sentences

That row asks for something specific and unusual — **not a guard, and not a patched memo, but the surfaces NAMED** — because the row count is the *right* denominator for account questions and wrong only for price-path ones, and no checker can read which question is being asked. **They are named here: of 36 surfaces that read the live journal's `trades` and form a rate, 21 publish a rate whose numerator is what PRICE did, over a denominator of rows.** Three of those 21 are **live dashboard surfaces** — `/api/bot/stats`'s `winRate`, which is the first number both apps render; `/api/bot/performance`'s `winRate`, `expectancy`, `expectancyR`, `rCoverage` and `bracketOutcome.reachedRatio`; and `/api/bot/attribution`'s per-strategy real-money win rate. **Only 2 of the 36 already reduce rows to one per package**, and one of those two — MI-275's own script, the file that discovered this defect and states in its docstring that *"every rate in this script is therefore reported per package"* — **breaks that rule in exactly one function**, `dose_response()`, which is the function that produced the inverted dose result. **The inflation is 1.264× overall on the current window and decomposes into two different things that a naive fix silently merges.** And a discriminator the row does not state: **restricted to a single account it is ~1.00×**, because fan-out is by definition across accounts — so two of the 21 are safe today only because they happen to require an `--account` flag.

---

## 1. Population, stated first

| | |
|---|---|
| repo scan | `scripts/research/`, `scripts/ops/`, `scripts/reports/`, `scripts/ml/`, `src/web/api/routers/` at `origin/main`, read 2026-09-12 |
| candidate rule | reads `trades` **and** forms a rate — deliberately over-collecting, because the mechanical half cannot tell a price rate from an account one |
| candidates | **57**, of which **36 read the LIVE journal** and 21 read only a backtest/synthetic corpus |
| journal | `/api/diag/journal?table=trades&limit=1000`, read 2026-09-12 — **1000 rows, all non-backtest, 791 distinct packages, 0 rows without a package id** |
| classification | **all 36 declared**, by reading the code, evidenced to a `path:line` rate expression |

⚠️ **A 1000-row tail, not the lifetime.** Every inflation figure below is a property of that window.

⚠️ **The 21 backtest-only surfaces are excluded on a stated ground, not by hand-waving:** a backtest corpus has no accounts, so it has no fan-out. If one of them later grows a live arm the mechanical half will re-surface it as `undeclared`.

---

## 2. The two inflations, which are not one number

| term | value | what it is |
|---|--:|---|
| **overall** | **1.264×** | rows ÷ distinct packages — *what a naive package-dedupe removes* |
| **cross-account** | **1.238×** | one row per `(account, package)` ÷ packages — **the fan-out**, 97 of 791 packages span >1 account |
| **intra-account** | **1.021×** | rows ÷ `(account, package)` pairs — a **different** thing: a partial close, a re-adoption, a rejected row beside a filled one |

**Deduplicating by package removes both, silently.** They differ by an order of magnitude here (97 spanning packages against 16 duplicated `(account, package)` pairs), and whether the second *should* be collapsed depends on the question — those rows are not extra views of one price path in the way fanned-out siblings are. A single "inflation" figure hides it entirely, which is why `inflation()` returns all three and never a scalar.

**The discriminator the row does not state:** fan-out is *by definition* across accounts, so an analysis restricted to one account has essentially none of it — `bybit_1` 1.029×, `ib_paper` 1.012×, `alpaca_paper` 1.019×, and **exactly 1.000× on `bybit_portfolio`, `bybit_2`, `alpaca_portfolio`, `alpaca_live` and `alpaca_options_paper`**. Real money as a class reads 1.000×. That is a cheap and reliable test a reader can apply before worrying about any of this.

---

## 3. The surfaces, named

**21 of 36 publish a price-path rate on a row denominator.** The full list with its per-surface rate is emitted by the instrument (`scripts/research/fanout_denominator_survey.py`) and is not re-transcribed here — a hand-copied list is the thing that goes stale. The ones that change how a reader should act:

### 3a. Three are live dashboard surfaces

| route | the figure | denominator |
|---|---|---|
| `/api/bot/stats` (`dashboard.py:460`) | **`winRate`** — the first number both apps render | `trades` rows |
| `/api/bot/performance` (`performance.py:977-987`) | `winRate`, `expectancy`, `expectancyR`, `rCoverage`, `bracketOutcome.reachedRatio`, and the same shape again per strategy, per exit path, per asset class, per symbol | `trades` rows |
| `/api/bot/attribution` (`attribution.py:210`) | per-strategy lifetime real-money win rate | `trades` rows |

⚠️ **`performance.py` already guards this hazard — in the opposite direction, and its own comment names the casualty.** At `performance.py:270-274`:

> *"A raw `LEFT JOIN order_packages ON linked_trade_id = t.id` **FANS OUT** when a trade has >1 linked order package … that trade's pnl/win would then be counted N times, **inflating totalTrades / winRate / totalPnl**."*

So the code knows fan-out inflates `winRate`, and pre-aggregates **packages per trade** to stop it — while leaving **trades per package** unguarded. Same word, same casualty, opposite direction. `dashboard.py:416-431` does the identical thing. **This is not an oversight nobody could have caught; it is half of a defence that was already written.**

### 3b. The e35 analysis commits it, on a p-value

`e35_break_attribution.py` computes a pre/post win rate per arm and then a **one-sided binomial p-value** from it — `P(≤ k wins in n)` where `n` is a **row** count — and the string `order_package_id` does not appear in the file. A binomial test treats each row as an independent Bernoulli draw, which is precisely the assumption fan-out breaks, and the row's own finding is that the arms are inflated *unequally*, so the error does not cancel.

`bleed_attribution_2026_09_11.py` is the same shape at larger scale — adjudicated stop rate, Wilson intervals, Fisher exact on pre/post 2×2, and a difference-in-differences — pooled across **all** accounts, with `order_package_id` never read.

### 3c. Two are safe by accident, not by design

`monitor_miss_analysis.py` and `strategy_performance_audit.py` both **require** `--account` and filter `account_id = ?`. Per § 2 that blunts the inflation to ~1.00×. **Neither names the issue**, so the protection is a side effect of an unrelated ergonomic choice, and a future change that makes the flag optional would silently reintroduce the defect with no diff that looks like it did.

### 3d. And one already does it right — the pattern is not hypothetical

`scripts/research/ict_scalp_phase0/build_percell.py` iterates **order packages**, groups the trade legs by package, and reduces each package to a single outcome (real-money leg preferred, paper fallback) before computing `win_rate` / `expectancy_r` / `total_r`. It reports package-level counts rather than a row count. **A correct exemplar already exists in this repo**, which means the remedy has a shape to copy rather than one to invent.

---

## 4. The file that discovered this breaks its own rule, in the function that matters

`stop_width_counterfactual_2026_09_11.py` is MI-275's script and the origin of this backlog row. Its docstring states the rule as a universal:

> *"15 post-deploy e35 stop-out ROWS collapse to 8 distinct PACKAGES. **Every rate in this script is therefore reported per package**, with the row count stated beside it."*

Its `units()` really does group on `order_package_id`, and rows without one are bucketed as excluded rather than counted. **But `dose_response()` computes `win_rate = wins / n` and `stop_rate` with `n = len(rs)` — rows — while printing `"packages": len({...order_package_id...})` on the line immediately above.** The correct denominator is computed, and then not used.

⚠️ **That matters beyond tidiness, because of what `dose_response()` produced.** Its inverted result — the 20% tightening giving a stop rate of 0.80 at p<0.0001 against the 40% tightening's 0.25 at p=0.47 — is cited by `BL-20260911-BOTH-OF-THE-E35-EXPERIMENTS-DESIGNED-CONTROLS-HAVE-ZERO-OBSERVATIONS` (see MI-278 U11) as *"evidence against a stop-width mechanism"*. **This unit does not re-grade that result** — recomputing it per package is a separate measurement and is not done here — but a p<0.0001 built on a row denominator in a file that promises package denominators is a claim that should be re-derived before it is leaned on again.

---

## 5. Why this ships as a registry and not a guard

The row is explicit: *"A GUARD IS PROBABLY THE WRONG SHAPE HERE … no checker can read which question is being asked."* That is correct, and it was tested rather than assumed — the mechanical half of this instrument over-collects by design (57 candidates for 36 real ones, and it matched **itself**), and nothing in the source distinguishes `wins / len(rows)` where the numerator is a price fact from `refused / len(rows)` where it is a routing fact.

So the classification is **declared, per surface, in one table**, and the instrument's job is to (a) find candidates mechanically, (b) grade anything unclassified as **`undeclared` — the finding, never a pass**, and (c) supply the correction so a surface can state it. A new script that reads `trades` and forms a rate shows up as `undeclared` the next time anyone runs it.

**This is not a claim that the registry cannot rot.** It can, in the ordinary way: someone adds a rate to an already-declared file and the declaration no longer describes it. What the design buys is that *adding a surface* is caught and *adding a rate to an existing one* is not — stated here rather than discovered later.

---

## 6. What this unit does NOT do

- **It fixes nothing.** No denominator is changed. The three dashboard routes are `src/web/` — **Tier-2** — and changing a number the operator reads is a decision, not a side effect of a survey.
- **It does not re-grade any published figure.** Not MI-275's dose response, not the e35 attribution, not the SPA's win rate. Naming a surface is not measuring its error, and § 2's 1.264× is the *population* inflation, not the error in any particular rate — a rate's own distortion depends on which cut it takes.
- **It does not claim the 21 are all wrong.** A price-path rate on a row denominator is wrong *as an estimate of what price does*; several of these surfaces may be read for other purposes where it is not the binding concern.
- **It does not grade the 21 backtest-only surfaces**, on the stated ground that a corpus with no accounts has no fan-out.
- **The `--account` mitigation is described, not endorsed.** Two surfaces are safe today because of a flag that exists for another reason.
