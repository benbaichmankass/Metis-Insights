# E38 — under what market conditions is each leg expected to perform?

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Checklist row **E38** (Tier 1).

**Answer, 2026-09-25: with the registered two-feature method, NO measured leg has
a detectable regime dependence, and none of the six real-money-gated legs can
be told when it is expected to work.** The mechanism is built and runs; its
answer for every testable leg is `no_regime_signal`. That is a null, not a
permission: the operator's condition for wiring the six (*"only when this
mechanism can say when each is expected to work"*) is **not met for any of them.**

| file | what it is |
|---|---|
| [`e38-regime/REGISTRATION.yaml`](e38-regime/REGISTRATION.yaml) | the method, committed **before** any feature was computed (commit `d70e049`, pushed 13:25Z, at `research/results/e38-regime/`; moved here unchanged because `research/results/` is a `.jsonl`-only store that `research-results-guard` R3 enforces). The script reads its constants from it. |
| [`scripts/research/e38_regime_classification.py`](../../scripts/research/e38_regime_classification.py) | the analysis. `--current` adds today's regime read; `--self-test` is the offline positive/negative control. |
| [`e38-regime/results.json`](e38-regime/results.json) | every number below, per leg: per-trade test, walk-forward per fold, fold-level descriptive, won-vs-lost medians, candle provenance. |
| [`e38-regime/RESULTS.md`](e38-regime/RESULTS.md) | generated table of `results.json`. Do not hand-edit. |

## The registered method (short form — the YAML is binding)

- **Features (2, from candles only):** `rv20` = std-dev of the last 20 daily log
  returns; `er20` = Kaufman efficiency ratio over 20 daily closes (trend
  strength, 0 = chop, 1 = straight line). Daily USDT-M futures closes from
  data.binance.vision — the archive `build_strategy_evidence.py` itself fetches.
  **No look-ahead:** only daily bars whose close time ≤ the trade's entry time
  (MEASURED spot-check: `trend_donchian_sol` trade entered 2025-10-26 09:00Z
  used the bar closing 2025-10-26 00:00Z).
- **Test:** Spearman ρ(feature at entry, per-trade `net_r` net of full cost),
  two-sided, 10,000 iid permutations **and** a circular-shift permutation
  (≤ 0.10) to survive trade clustering; Bonferroni per family. n ≥ 30 trades or
  `insufficient_n`.
- **Out-of-sample:** fold walk-forward — the rule applied to fold *k* (side +
  median threshold) is learned only from folds < *k*; pass = net gain from
  gating over the evaluated folds, majority of folds improved, gated sum > 0.
- **"A regime explains this leg"** = in-sample bar cleared **and** walk-forward
  passes. Only that verdict yields a rule and a current-regime call.
- **Fold-level (the unit the E38 row describes):** min 8 folds. Every record has
  4, so this was declared `insufficient_n` **in the registration, before the
  run** — the fold record alone cannot answer E38.

**Positive control** (RULE ONE: a quiet probe must be shown able to fire):
`--self-test` plants ρ≈0.73 on an autocorrelated n=107 series → detected
(p_iid 0.0001, p_circ 0.009, walk-forward pass); pure noise → rejected.

## Population (MEASURED — `results.json`, generated 2026-09-25T13:32Z)

52 evidence records → **49 `measured`** (3 not: `ict_scalp_mgc_15m`,
`splg_trend_long_1d` harness_failed; `turtle_soup` no_harness). The brief said
42 measured legs; the directory held 49 by the time this ran.

| verdict | primary (the six) | secondary | total |
|---|---|---|---|
| `regime_explains` | **0** | **0** | 0 |
| `in_sample_only` | 0 | 0 | 0 |
| `no_regime_signal` | 5 | 17 | 22 |
| `insufficient_n` (< 30 trades) | 1 | 4 | 5 |
| `not_attempted` (candles unreachable) | 0 | 22 | 22 |

`not_attempted` = all 22 non-crypto legs (equities, ETFs, CME micros): `yfinance`
is not installed in this sandbox and Yahoo returned HTTP 429. **We did not look
at them** — that is not a finding about them. None of the six is non-crypto.

22 legs tested × 2 features = **44 tests; the smallest p is 0.0495**
(`htf_pullback_trend_2h` er20), next 0.051 (`avax_pullback_2h` rv20). Under a
pure null ~2.2 of 44 fall below 0.05 by chance; **1 did.** Nothing is close to
a corrected bar (primary α = 0.005, secondary α = 0.05/34 ≈ 0.0015).

## The six gated legs

⚠️ **The E38 row's figures are STALE, and for three of the six the change is
decisive.** The row quotes the 2026-09-22 r5 run. The records on `main` today
(MEASURED, `comms/strategy_evidence/<leg>.json`, fields `net_r_oos`,
`folds_positive`, `decision_rule.verdict`) read:

| leg | row said | record today (source run) | RULE-D1 today | E38 verdict | min detectable \|ρ\| | best ρ (p) |
|---|---|---|---|---|---|---|
| `trend_donchian_eth` | +21.68R n=102 1/4 | **+16.18R n=107 2/4** (09-25) | pass | `no_regime_signal` | 0.27 | rv20 +0.03 (0.77) |
| `sol_pullback_2h` | +7.28R n=16 1/4 | **+4.59R n=18 2/4** (09-25) | pass | **`insufficient_n`** (18 < 30) | — | — |
| `trend_donchian_avax_4h` | +9.69R n=48 2/4 | **−9.14R n=50 0/4** (09-24-e69) | **fail** | `no_regime_signal` | 0.39 | rv20 +0.20 (0.16) |
| `trend_donchian_ada_4h` | +8.53R n=38 2/4 | **−8.61R n=46 1/4** (09-24-e69) | **fail** | `no_regime_signal` | 0.40 | rv20 +0.06 (0.70) |
| `trend_donchian_sol_4h` | +4.16R n=34 2/4 | **−2.80R n=35 1/4** (09-24-e69) | **fail** | `no_regime_signal` | 0.46 | rv20 +0.22 (0.21) |
| `trend_donchian_sol` | +2.80R n=57 2/4 | **+4.46R n=59 1/4** (09-25) | pass | `no_regime_signal` | 0.36 | rv20 +0.18 (0.16) |

**Status of each, stated plainly:**

- **`trend_donchian_avax_4h`, `trend_donchian_ada_4h`, `trend_donchian_sol_4h` —
  the regime question is moot today.** Each FAILS the Stage-0 bar (RULE-D1,
  net_r_oos ≤ 0) on its current record. A regime mechanism decides *when* a leg
  with an edge trades; it cannot rescue a leg without one. (INFERRED from the
  records above; what changed between r5 and e69 is not this lane's to
  re-derive — the records name their runs.)
- **`sol_pullback_2h` — cannot be tested.** 18 trades in a year. No regime test
  at this n is honest.
- **`trend_donchian_eth`, `trend_donchian_sol` — tested, no signal.** Neither
  volatility nor trend strength at entry separates their winners from their
  losers at a strength this sample could detect (|ρ| ≥ 0.27 / 0.36).

→ **None of the six meets the operator's wiring condition.** They stay held.
Wiring any of them is Tier-3 and goes to the manager either way.

## What the null does and does not say

- **It is a POWER-bounded null** (INFERRED from the `min_detectable_abs_rho`
  column). At n = 35–107 trades the registered test detects only |ρ| ≳ 0.27–0.46.
  A regime effect of ρ ≈ 0.1–0.2 — which would still matter for sizing — is
  invisible at one year of one leg. *"No regime signal"* means "no regime
  dependence this strong", not "regime does not matter".
- **Walk-forward "passes" without an in-sample signal are noise, as
  registered.** 12 of 44 descriptive walk-forwards pass (27%), including
  `trend_donchian_eth` rv20 (+3.27R over 3 folds). None is backed by an
  in-sample relationship (its ρ is +0.03), and a rule that can gate a leg on one
  side of a median will "improve" some folds by chance. Not a verdict.
- **The fold-level pattern is confounded with time.** For all six crypto
  symbols, mean daily rv20 in folds 3–4 is below folds 1–2 (MEASURED,
  `results.json` `fold_level`; e.g. ETH fold means 0.035 → 0.037 →
  0.025 → 0.026; `trend_donchian_eth` won folds 1–2 and lost 3–4). "Wins in
  higher vol" and "decayed over the year" are the same four points and cannot
  be told apart at n = 4 folds. This is the exact reading the E38 row warned
  against, and the registration refused it in advance.

## "Can we tell which regime is current?" — yes, mechanically; there is no rule to apply

`--current` reads today's features per symbol from the last closed daily bar
(table in [`e38-regime/RESULTS.md`](e38-regime/RESULTS.md); as of 2026-09-25 00:00Z, e.g. ETHUSDT
rv20 0.0242 / er20 0.258, SOLUSDT 0.0365 / 0.253). For a `regime_explains` leg
the script would print ON/OFF against the leg's rule. **No leg has a rule, so
today there is no call to make** — and saying so is the correct output.

## What would change this answer (routed, not parked)

1. **More n per test, not more features.** Adding features buys multiple
   comparisons, not power. The lever is sample size: (a) multi-year history for
   the same legs — needs E4's committed corpus; (b) a **pooled family test**
   (e.g. all 10 `trend_donchian_*` legs, 664 trades) that could detect |ρ| ≈ 0.09
   at face value — less once the overlapping same-symbol legs (eth / eth_prop /
   eth_4h) are de-duplicated —
   answering "does the trend family depend on regime" even where no single leg
   can. Filed as a pipeline item; it needs its **own** registration before it
   runs.
2. **Non-crypto legs** (22) — rerun once a Yahoo/yfinance path is available
   (the script handles them the moment `fetch_daily` can reach them).
3. **The three Stage-0 failures** — the row's premise (six legs passing
   RULE-D1, held on fold consistency) no longer holds for them; that is a roster
   fact for the manager, noted on the E38 row.
