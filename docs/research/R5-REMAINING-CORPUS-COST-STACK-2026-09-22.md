# R5 — the remaining 40 evidence records, graded against `RULE-D1-STAGE0-NET-OF-FULL-COST`

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Owned by checklist row **R7** (`docs/claude/work/MANAGER-CHECKLIST.json`).
Intake: `PI-20260922-R1-REMAINING-CORPUS`.

> **This document produces EVIDENCE and a PROPOSAL. It changes no roster.**
> `config/accounts.yaml` and `config/strategies.yaml` are untouched by this lane.

---

## What was run

`scripts/ops/build_strategy_evidence.py` over the **40 legs R1 did not cover**,
with `--workdir comms/strategy_evidence/runs/2026-09-22-r5` so every record's
`source_run` names a committed file rather than a dead `/tmp` path.

**Population, MEASURED by me on `0e0a8f3` by reading all 52 committed records
in `comms/strategy_evidence/` (not carried from the dispatch):**

| | count | how identified |
|---|--:|---|
| already carry `cost_stack` + a `decision_rule` verdict (R1, `f3746ab`) | **12** | `cost_stack` non-null |
| **no `cost_stack`** — this lane's population | **40** | `cost_stack` null |
| — of which `measured` | 30 | |
| — of which `no_harness` | 9 | 8 × `ict_scalp_*` + `turtle_soup` |
| — of which `harness_failed` | **1** | `splg_trend_long_1d` |

⚠️ **The dispatch said the 40 break down as 31 `measured` + 9 `no_harness`. It is
30 + 9 + 1.** `splg_trend_long_1d` is `harness_failed`, which is a *third* state
and not a `measured` one — the producer's own four-state contract keeps *"it ran
and broke"* distinct from *"the harness ran and the numbers are real"*, and
folding it into `measured` is the collapse `docs/CLAUDE-RULES-CANONICAL.md`
§ "Collapsed states" exists to refuse. 12 + 30 + 9 + 1 = 52. ✅

**Result of this lane's run — 40 of 40 attempted, 40 of 40 written:**

```
strategy-evidence: 40 leg(s)
  measured        30
  no_harness       9
  harness_failed   1
  not_attempted    0
```

Of the 30 `measured`: **25 `pass`, 5 `fail`** against
`RULE-D1-STAGE0-NET-OF-FULL-COST` (registered `2026-09-22T05:34:08Z`, which is
before every `generated_at` in this run — the C4 ordering holds).
The five `fail`: `mes_trend_long_1d`, `spy_pullback_1h`, `squeeze_breakout_4h`,
`tlt_pullback_1d`, `tlt_pullback_1h`.

The nine `no_harness` legs stay `no_harness`. **No harness was built here** —
that is row **E28** (the `ict_scalp_*` family) and it is not this lane's work. A
`no_harness` record is a statement about us, not about the strategy.

---

## ⚠️ FINDING 1 — the 40 records were NOT pre-cost numbers, and this changes what may be claimed

The dispatch framing was *"those numbers predate the cost stack"*. **That is not
what the corpus shows, and the difference is load-bearing.**

**MEASURED, three independent ways:**

1. **`config_fingerprint` is unchanged on 40 of 40** between the committed
   record and this run. No config moved, so any difference is the measurement,
   not the leg.
2. **Three legs are byte-identical old → new** on `net_r_oos` despite a 9-day
   window shift — `fade_breakout_4h` (6.1101, n=25), `uso_trend_1h` (28.0566,
   n=55), `tlt_pullback_1h` (−4.4077, n=94). If the old run had been fee-only
   and the new one net-of-full-cost, an identical number would be impossible:
   the fee-only gap this run records for those same legs is +1.47R, +1.65R and
   **+9.35R** respectively.
3. **The cost code did not change in the window.** `git log --since=2026-09-10`
   over `scripts/backtest_trend.py`, `scripts/backtest_pullback.py`,
   `scripts/backtest_squeeze.py` and `scripts/research/regime_debt_matrix.py`
   returns exactly two commits, neither touching the cost resolution
   (`f783ae1` = E25's fvg_range route; `9ddd3d9` = a PR-queue receipt).

**So: for the `trend` / `pullback` / `squeeze` / `fvg_range` families the
2026-09-13 and 2026-09-16 numbers were ALREADY net of the full cost stack.**
The harness CLI path resolved unset `--slippage` / `--funding` to the venue-aware
defaults then too (`scripts/backtest_trend.py:74-76`). R1 said exactly this about
its own eleven — *"the corpus staleness for THESE 11 legs was in the evidence
RECORD schema, not in the harness math"* — and it holds for these 30 as well.

**What was actually missing was the RECORD, and that is still a real defect:**
nothing in a `schema_version: 1` record said the number was costed, nothing
carried `net_r_oos_fee_only` so the size of the correction could be seen, and no
rule had ever been applied to it. That is the *"we could not look"* state, and
it is now closed for these 30.

⚠️ **Two things nobody should now say.**

- **Do not say "turning costs on flipped N legs" about this corpus.** R1's
  "3 of 11 flipped" is a **fee-only vs full-cost counterfactual inside a single
  run** (`net_r_oos_fee_only` vs `net_r_oos`), not a before/after of the
  committed corpus. Quoting it as the latter would claim a corpus change that
  did not happen.
- **`CLAUDE.md` is now stale on this point and I did not edit it.** It reads
  *"the harnesses default slippage and funding to `0.0`, so today's corpus is
  fee-only and optimistic by an unknown amount"* as the condition blocking the
  real-money promotion mandate. Measured above, that is **false for the four
  families this producer reaches** and **still true** for the three E3 named as
  having no bps cost model at all (`backtest_orb.py`,
  `src/backtest/backtester.py`, `xsec_momentum`'s funding term). Correcting a
  mandate-arming sentence is the operator's call, not a research lane's, so it
  is **filed, not edited** — `PI-20260922-R5-CLAUDE-MD-FEE-ONLY-CORPUS-CLAIM-IS-STALE-FOR-FOUR-HARNESS-FAMILIES`.

---

## ⚠️ FINDING 2 — the Stage-0 verdict is not stable to the measurement date

`window_days` is 365 **rolling from the run date**. Re-running 9 days later,
with an identical config and an identical cost model, **flipped the sign of the
verdict on 2 of the 30 measured legs**:

| leg | committed (2026-09-13/16) | this run (2026-09-22) | Δ | n | what changed |
|---|--:|--:|--:|---|---|
| `squeeze_breakout_4h` | **+0.5994** `pass` | **−0.4874** `fail` | −1.09 | 9 → 10 | one trade |
| `trend_donchian_sol` | **−1.0697** `fail` | **+2.8045** `pass` | +3.87 | 56 → 57 | one trade + fold boundaries |

Nothing in either record says its verdict is date-sensitive, and
`RULE-D1-STAGE0-NET-OF-FULL-COST` is a strict `> 0` test with no power
requirement and no confidence interval — so a leg sitting near zero gets a
crisp `pass` or `fail` from what is, at these n, noise. `squeeze_breakout_4h`
sits at n=10, exactly the producer's own `MIN_TRADES_FOR_POOLED` floor.

**This is a finding about the rule and the record, not about the strategies.**
It is filed as `PI-20260922-R5-STAGE0-VERDICT-FLIPS-ON-A-9-DAY-WINDOW-SHIFT`.
It is explicitly **not** a reason to lower or widen the bar — the canonical rule
against manufacturing a verdict by moving a floor stands. The candidate
remedies (a fixed `--end` so the window is reproducible; a minimum-n or
interval-based clause; recording the flip explicitly) are for D1's owner.

---

## ⚠️ FINDING 3 — `trend_donchian_sol` and `trend_donchian_sol_prop` are NOT the same config

The dispatch asked whether two legs that "look identical" disagreeing in sign is
a harness bug. **It is not. They are different legs, and there are three
separate reasons the records disagreed — all of them real.**

**(a) The params differ.** Both are SOLUSDT 1h `donchian: 20`, `atr_period: 14`,
`atr_stop_mult: 2.5`, `min_confidence: 0.8`, `long_only: true`. Read the rest of
the block and they diverge on the entire **exit**:

| field | `trend_donchian_sol` | `trend_donchian_sol_prop` |
|---|---|---|
| `tp_r` | **50.0** | **6.0** |
| `trail_mult` | **5.0** | **3.5** |
| `exit_head_action` / `_model` / `_threshold` | `close` / `exit-head-donchian-1h-v1` / `0.1` | *absent* |
| `tp_intent` | `mode: none`, "trail is the profit exit" | *absent* |

`_sol` is a run-it-to-the-trail leg with an ML exit head on top; `_prop` banks at
6R behind a tighter trail. Different exits ⇒ different trade sets ⇒ different n.
`config_fingerprint` already recorded this: `sha256:80af06be…` vs
`sha256:7a88e0e4…`. **The mechanism that answers this question was in both
records the whole time; nobody read it.** *(Field beats comment — and
`config_fingerprint` is the field.)*

**(b) The fidelity differs, so the two numbers are evidence about different
things.** `_sol` grades **`approximate`**, omitting exactly
`exit_head_action` / `exit_head_model` / `exit_head_threshold`:
`backtest_trend.py` has no `--exit-head-*` flag and the head's exit is a
separate replay pass by design
(`scripts/research/regime_debt_matrix.py` module docstring). `_prop` grades
**`faithful`** with `omitted_levers: []`. So `_sol`'s number is evidence about a
leg that is *not quite the live one* — the live leg closes on the head, the
measured one never does. `_prop`'s number is about the leg as configured.

**(c) And the −1.0697 was 9 days stale.** Re-run today the same config reads
**+2.8045** (Finding 2). So part of "opposite conclusions" was comparing a
2026-09-13 record with a 2026-09-22 one.

**Bottom line for the operator: no harness bug, no config bug, and nothing here
is a fact about SOL.** Measured on the same day, on the same cost model, the two
legs now read `+2.8045` (n=57, `approximate`) and `+3.4484` (n=63, `faithful`) —
**they agree in sign**, and they always differed in exit design.

---

## Operator question 1 — is `trend_donchian_sol_4h` promotable?

**MEASURED this run** (`comms/strategy_evidence/trend_donchian_sol_4h.json`,
trades at `comms/strategy_evidence/runs/2026-09-22-r5/trend_donchian_sol_4h__trades.jsonl`):

| | |
|---|---|
| `net_r_oos` | **+4.1648** |
| `net_r_oos_fee_only` | +5.9593 (so the slippage+funding bite is −1.79R) |
| `n_trades_oos` | 34 |
| `fidelity` | **`faithful`**, `omitted_levers: []` |
| `folds_positive` | **2 of 4** — `−2.08 (8) · +6.86 (8) · +0.49 (8) · −1.11 (10)` |
| `decision_rule.verdict` | **`pass`** |
| roster today | `bybit_1` only (`account_class: paper`), `execution: live` |

**It does not flip.** It clears `RULE-D1-STAGE0-NET-OF-FULL-COST`.

**But three caveats belong next to that, and they are why this is a proposal and
not a promotion:**

1. **The pre-cost record read +8.9882 over n=33. This run reads +4.1648 over
   n=34 — less than half.** Both are net of the same cost stack (Finding 1), so
   the drop is the 9-day window shift, not costs. A number that halves on a
   9-day shift is exactly the instability of Finding 2.
2. **The pooled positive is carried by one fold.** Strip fold 2 (+6.86) and the
   remaining three sum to **−2.70**. `folds_positive` is 2 of 4 and **the most
   recent fold is negative**.
3. `param_selection_provenance: not_established` — if these params were tuned on
   this same history the number is optimistic and no field here can detect it.

**Comparison with the leg already on real prop money** —
`trend_donchian_sol_prop`: `+3.4484`, n=63, `faithful`, 2 of 4 folds, `pass`.
The 4h leg is *comparable*, on **half the sample**, with a more window-sensitive
number.

---

## Operator question 2 — is any BTC leg viable net of cost?

`bybit_2` has zero BTC exposure because both BTC legs ever graded failed.

**POPULATION, stated because my first draft of this table got it wrong.** Every
leg in `config/strategies.yaml` with `symbols[0] == "BTCUSDT"`: **9 legs, of
which 7 are `enabled: true`.** Of those 7, **5 now carry a verdict** and **2 do
not** — and the two that do not are the answer's honest boundary, not a
rounding error.


| leg | `execution` | `net_r_oos` | n | fidelity | folds + | verdict | graded |
|---|---|--:|--:|---|:--:|---|---|
| **`htf_pullback_trend_2h`** | `shadow` | **+2.7979** | **80** | `faithful` | **3 / 4** | **`pass`** | R5 |
| `fade_breakout_4h` | `shadow` | **+6.1101** | 25 | `approximate` | 3 / 4 | `pass` | R5 |
| `squeeze_breakout_4h` | `live` | **−0.4874** | 10 | `faithful` | 2 / 4 | **`fail`** | R5 |
| `trend_donchian` | `live` | −0.4245 | 73 | — | — | `fail` | R1 |
| `fvg_range_15m` | `shadow` | −10.2594 | 17 | — | — | `fail` | E25 |

**The two enabled BTC legs with NO verdict, which the table above cannot speak
for:**

| leg | `execution` | state | why there is no number |
|---|---|---|---|
| `ict_scalp_5m` | **`live`** | `no_harness` | `regime_debt_matrix.classify()` routes nothing for it; `scripts/backtest_ict_scalp.py` exists. Row **E28**. |
| `turtle_soup` | `shadow` | `no_harness` | same classifier gap, different family. Row **E28** names the `ict_scalp_*` eight; `turtle_soup` is the ninth unrouted leg. |

⚠️ **`ict_scalp_5m` is a BTC leg with `execution: live` and no gradeable
evidence at all.** It sits on `bybit_1` (paper), so no real money rides on it —
E28 establishes that for the whole family — but "BTC has no viable leg" and "we
never measured this BTC leg" must not be collapsed. The remaining two BTC legs
(`trend_donchian_1h`, `vwap`) are `enabled: false` and have no record; quote any
measurement about them in the past tense.

**Answer: yes — of the 5 BTC legs that can be graded today,
`htf_pullback_trend_2h` is the one that clears the bar, and it does so on the
largest sample of any BTC leg in the corpus (n=80).** It is `execution: shadow`
and sits only on `bybit_1` (paper). Two further BTC legs remain ungraded, so
this is *"the best of what we can see"*, not *"the best there is"*.

Caveats, stated rather than buried:

- **Its most recent fold is its only negative one**: `+4.09 · +0.58 · +0.88 ·
  **−2.75**` over 20 trades each. Pooled +2.80 over n=80 is thin — an
  expectancy of ~+0.035R per trade — and the last quarter gave back most of it.
- The fee-only number is +6.8365, so **60% of the gross edge is eaten by
  slippage and funding**. This leg is unusually cost-sensitive; a realized-cost
  divergence at Gate 1 would take it negative fast. That is precisely what
  row **R3** (cost fidelity as the standing Gate-1 test) is for.
- `fade_breakout_4h` pools higher (+6.11) but on n=25 and at
  `fidelity: approximate` — the harness does not model `atr_stop_buffer`,
  `pierce_min` or `timeout_bars`, three levers that all shape the exit. Its
  number is evidence about a leg that is not quite this one.
- **`squeeze_breakout_4h` is the one to look at first, and it points the other
  way.** It is `execution: live` on `bybit_1`, it now **fails**, and it flipped
  from `pass` on a 9-day shift at n=10.

---

## ⚠️ FINDING 4 — the path is far worse than the total, and this qualifies my own answer above (B6)

Added after the run, on B6's ask. The per-trade R series was already committed
by this lane (`source_run`), so **no backtest was re-run** — `path_stats` is
computed from those rows and cross-checked against the record it sits in
(`final_equity_r == net_r_oos` and `n == n_trades_oos` on 30 of 30).

**`net_r_oos` is a SUM. It says nothing about how far underwater a leg went.**
Measured over the 30 legs this lane regraded, the worst peak-to-trough
excursion in R (`path_stats.max_drawdown_r`) **exceeds the leg's entire net
result on 18 of 30**, and the two legs I named as candidates are among the
worst:

| leg | `net_r_oos` | `max_drawdown_r` | worst day | n |
|---|--:|--:|--:|--:|
| `htf_pullback_trend_2h` *(the BTC candidate)* | **+2.7979** | **12.3706** | −2.069 | 80 |
| `trend_donchian_sol_4h` *(operator question 1)* | **+4.1648** | **10.2791** | −1.1771 | 34 |
| `spy_pullback_1h` | −11.2379 | 22.0809 | −2.2228 | 57 |
| `trend_donchian_eth` | +21.6753 | 17.1990 | −2.1231 | 102 |
| `fade_breakout_4h` | +6.1101 | 5.0547 | −1.1273 | 25 |
| `squeeze_breakout_4h` | −0.4874 | 3.7821 | −1.1010 | 10 |

⚠️ **This qualifies what I wrote about both operator questions, and the
qualification is mine to make rather than the reader's to discover.** Calling
`htf_pullback_trend_2h` *"the strongest BTC candidate"* remains true **of the
Stage-0 rule as registered** — `RULE-D1-STAGE0-NET-OF-FULL-COST` is a test on
the pooled total and it passes. It is **not** a statement that the leg suits a
drawdown-bounded book, and the 12.37R excursion behind its +2.80R is a fact the
verdict field cannot express. A reader taking `verdict: pass` as fitness for
`breakout_1` would be reading a total as a path.

⚠️ **I have deliberately NOT scored any leg against `breakout_1`'s floor or
target.** B6 registers the prop bar and it must be registered *before* these
numbers are read; a lane that emitted the measurement and the grade in the same
pass would be writing the rule after seeing the result. `path_stats` imports no
threshold from `config/prop_rulesets/` and says so in its own docstring. The
numbers above are descriptive.

### ⚠️ And B6 CANNOT be answered for the prop account today — the `/tmp` defect is why

**Population: all 52 records. 30 now carry `path_stats`; 22 do not**, and the
22 split into two genuinely different reasons:

| | count | why |
|---|--:|---|
| **series unreachable** | **12** | `source_run` points into a `/tmp` dir that no longer exists (R1's 11 + E25's 1). The trades were emitted and then lost. |
| no series at all | 10 | 9 `no_harness` + 1 `harness_failed` — nothing was ever emitted. |

⚠️ **All the risk-bearing legs are in the unreachable 12** — including
`trend_donchian_sol_prop` and `trend_donchian_eth_prop`, the two legs on
`breakout_1`, **the prop account B6 is about.** So the measurement B6 needs is
missing for exactly the legs B6 exists to decide.

**This is the second, larger cost of `PI-20260922-EVIDENCE-SOURCE-RUN-IS-A-TMP-PATH`,
and it was not visible when that row was filed as a provenance defect.** A dead
locator does not only stop someone re-checking a number — it stops any NEW
question being asked of the old measurement. Every future statistic over those
12 legs costs a re-run.

⚠️ **I did not re-run those 12, and the reason is a judgement the next owner
should be able to overturn.** Re-running would fix their locators and produce
their path stats in one pass — but Finding 2 says the numbers would move on the
window shift, and those 12 are the records **R2 has already acted on** (four
roster cuts) and **R8 is about to read**. Silently replacing them mid-decision
is worse than the gap. Whether to re-run them is R8/B6's call, not this lane's.
Filed as `PI-20260922-R5-PATH-STATS-MISSING-FOR-THE-12-LEGS-WHOSE-SERIES-IS-IN-A-DEAD-TMP-DIR`.

## PROPOSAL — for the operator. Nothing below is applied by this lane.

Per `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers, every item here is
**Tier 3** and needs explicit approval. Per § "Tune before demote", the demotion
item carries its tuning precondition rather than skipping it.

**P1 — Do NOT promote anything to a real-money roster on this corpus yet.**
The binding reason is not any leg's number: it is Finding 2. A Stage-0 verdict
that flips on a 9-day window shift is not yet a promotion-grade instrument, and
two of the three candidates below sit close enough to zero to be inside that
instability. Recommend D1's owner fix the window (a pinned `--end`, or an n/CI
clause) **before** any Gate-1 crossing is taken on these records. This is the
opposite of lowering a bar and it is deliberately the first item.

**P2 — `htf_pullback_trend_2h` (BTC 2h): the strongest candidate for Gate 1 ON THE STAGE-0 RULE AS REGISTERED, once P1 is satisfied — and read Finding 4 before treating that as fitness for a drawdown-bounded book (max drawdown 12.3706R behind a +2.7979R total).** It is the only BTC leg with a `pass`,
`faithful` fidelity, 3 of 4 folds positive and n=80. The exact change would be
`config/strategies.yaml::htf_pullback_trend_2h::execution: shadow → live`
**(Tier 3 — not made here)**. Note it already runs on `bybit_1`, so this is a
Stage-0 → Stage-1 question about its *execution gate*, not a real-money one; the
`bybit_2` question is downstream of a Gate-1 cost-fidelity window it has not
had. ⚠️ Its cost sensitivity (60% of gross) makes R3 a precondition in
substance, not just in sequence.

**P3 — `squeeze_breakout_4h` (BTC 4h) is `execution: live` and now FAILS the
rule.** Under § "Tune before demote" the proposal is **not** a demotion: it is
**run the M8 sweep** (`runtime_logs/strategy_tunes/<UTC-date>/squeeze_breakout_4h__*.json`),
because no tuning attempt is on record. ⚠️ And the honest caveat cuts the other
way too: at n=10 with a verdict that flipped last week, this `fail` is as
unstable as the `pass` it replaced. **Do not demote on it either.** The right
disposition is "measure it properly", which is P1 plus a sweep.

**P4 — `trend_donchian_sol_4h`: no promotion recommended today**, and Finding 4 strengthens this rather than adding a caveat to it (max drawdown 10.2791R behind a +4.1648R total). It passes, but
on half the sample of the prop leg already carrying SOL 1h, with a number that
halved on a 9-day shift and a pooled positive carried by one fold. Adding a
second SOL trend leg to a risk-bearing book on that basis buys correlated
exposure for a weaker record. Revisit after P1.

**P5 — `splg_trend_long_1d` cannot be graded and the reason is external.**
`yfinance==1.7.0` returns no rows for SPLG while the SPY control returned rows
in the same call, so the venue is up and the gap is SPLG-specific (delisting, a
short listing history, or a wrong symbol map). ⚠️ SPLG is one of the two declared
affordability proxies carved out of the Alpaca mirror invariant, so it is a
*live* leg with **no gradeable evidence at all**. Filed as
`PI-20260922-R5-SPLG-FEED-RESOLVES-TO-ZERO-ROWS`.

---

## How to re-check any number in this document

Every record written by this lane names a committed locator:

```bash
# the per-trade rows net_r_oos was pooled from
cat comms/strategy_evidence/runs/2026-09-22-r5/<leg>__trades.jsonl
# the fee/slippage/funding bps the harness actually resolved
cat comms/strategy_evidence/runs/2026-09-22-r5/<leg>__bt.json
# regenerate
python3 scripts/ops/build_strategy_evidence.py --strategy <leg>
```

⚠️ **The 12 records R1 wrote still name `/tmp/tmp.RsNZr5lfkw`, which does not
exist.** They are schema-complete and locator-dead. Re-running the producer for
those 12 fixes it; this lane did not, and says so rather than leaving it
implied. Tracked under `PI-20260922-EVIDENCE-SOURCE-RUN-IS-A-TMP-PATH`.
