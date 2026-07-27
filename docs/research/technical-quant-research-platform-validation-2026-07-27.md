# M30 Technical Quant-Research Platform — Validation & Readiness (2026-07-27)

**Purpose.** Before the first real deep-research session runs on this platform,
verify — by *execution*, not by reading docs — that the discovery tooling
actually works and its anti-overfit guards actually bite, so a null it reports
is a real null and an edge it reports is real. Operator-directed full audit +
pipeline validation.

**Method.** Three independent passes: (1) **executable** validation driving the
real C2 CLI on synthetic panels with known ground truth; (2) a read-only
**C1/C2 correctness audit** (claimed-vs-actual, adversarial); (3) a read-only
**eval-pipeline + docs-vs-code audit**. All three are reflected below with their
concrete evidence.

---

## 1. Executable validation — ALL PASS

Drove `scripts/research/analyze_research_panel.py` (the C2 CLI) on hand-built
panels with injected ground truth (`scratchpad/validate_c2.py`; deterministic
seed):

| Test | Ground truth | Result | Verdict |
|---|---|---|---|
| **Signal recovery** | one feature drives `win` (+ 3 noise) | OOS AUC **0.886** (folds 0.87/0.87/0.94/0.86/0.89), injected feature sole FDR survivor + top permutation importance (0.386 vs noise ≈0) | ✅ recovers a true edge out-of-sample |
| **Null rejection** | `win` random, independent of all features | OOS AUC **0.415** (~coin-flip), **zero** FDR survivors, all importances ≈0/negative | ✅ manufactures nothing on noise |
| **Leakage refusal** | manifest declares outcome `win` as a feature | hard error `refusing to regress an outcome on itself`, **no regression block emitted** | ✅ refuses, not cosmetic |

Plus the committed suites: **`tests/test_build_research_panel.py` +
`tests/test_analyze_research_panel.py` + the eval-pipeline suites = 106 tests
pass** locally under numpy 2.4.6 + the real canonical splitter (the numpy-gated
regression/CV/VIF tests actually run, not skipped).

**Conclusion:** the discovery engine's core guarantees — purged/embargoed
walk-forward CV, out-of-sample discrimination, BH-FDR, leakage refusal, honest
`not_computed` degradation — hold under adversarial inputs.

---

## 2. Correctness audit — C1 panel + C2 toolkit

**Core is SOUND** (no defect that could produce a false "finding"):

- Purged/embargoed WF-CV uses the genuine canonical `ml.experiments.splitters.iter_folds` (not a homemade split); folds are time-ordered with a real purge+embargo gap; no train/test overlap.
- OOS AUC / R² are computed on the **held-out** fold; permutation importance shuffles the **test** column and re-scores OOS.
- BH-FDR math correct; `m` = the count actually tested (not a hardcoded 15).
- Leakage refusal is a real guard (verified in §1).
- Honest degradation (numpy-absent / 0 complete-case / missing DB) returns `not_computed`, never a faked number.
- C1 join is correct: R-multiple basis + `contract_value`, epoch-ms-aware close-time expr (the #7687 null-`closed_at` bug is fixed), correct cohort tagging.

**Peripheral defects — fixed or triaged:**

| id | defect | severity | status |
|---|---|---|---|
| B1 | C2 was cohort-blind — a `--cohort both` panel pooled real+paper silently | moderate | **FIXED** — #7696 merged (`ae811be`): cohort discipline (refuses a mixed panel unless explicitly isolated/pooled; single-cohort unchanged) |
| B2 | zero-variance feature → `NaN` written as a literal in JSON (invalid strict JSON) | minor | **FIXED** — #7696 (sanitized to `null` + recursive `_json_safe`) |
| B3 | manifest-asserted path trusts `feature_cols` verbatim; a malformed manifest listing an outcome as a feature would slip the overlap guard | low (not reachable from C1 output) | **OPEN** — defensive assert; logged, not blocking |
| — | `MIN()` per-column collapse can build a "Frankenstein" row if a trade has >1 order package (inherited from `component_edge_report.py`) | low (no-op for the normal 1-OP case) | **OPEN** — logged, not blocking |

---

## 3. Eval-pipeline audit + the real/paper seam

The eval/label substrate (`setup_candidates::_load_live_trades` →
`split_live_holdout` → `m23_ev_gate`) is otherwise correct (tagging, split,
gate all verified). One real finding:

**★ Real/paper filter was `is_demo=0` alone, not account_class-authoritative.**
A paper row with `account_class='paper'` but `is_demo=0` (e.g. a
portfolio-mirror book) would leak into the real-money held-out eval / EV gate.

- **Severity: LATENT — 0 contamination today.** Live-quantified (relay #7695):
  CURRENT real (`is_demo=0`) = **401** == account_class-authoritative real =
  **401**; contamination set = **0**. Every real row is `is_demo=0`, every paper
  row is `is_demo=1` — the predicates agree on current data. Not an active
  incident; a fragility fix + the **L3 prerequisite**.
- **Status: DRAFT #7697** (account_class-authoritative, schema-tolerant, +2
  regression tests). Held for operator review (touches the ml/ eval pipeline
  feeding promotion gates).

**Docs-vs-code drift: LOW.** Both scoping + ledger docs are unusually honest and
flag their own gaps. Confirmed accurate: C1/C2/ledger built and their CLIs match
the docs; N4 macro-liveness workflow + N2 offline-discrimination field are built
and observe-only as claimed. Confirmed **honestly unbuilt** (not false claims):
SHAP, the C3 backtest bridge, the per-bar panel, and the scoping's "first
studies 1-5" (the ledger ran a different pragmatic sequence — 0 synthetic / 1
pooled / 2 vwap).

---

## 4. Studies run so far (ledger)

- **Study 0** — synthetic platform-validation (guards recover an injected edge, reject noise). Pipeline proof, not a live finding.
- **Study 1** — pooled all-strategy real book (377 real / 612 both). 2 univariate FDR leads (`vwap_deviation_std`, `model_score_mean`); multivariate blocked (block-sparse features → 0 complete-case rows). Headline: the real book is 84% one strategy (`vwap`).
- **Study 2** — `vwap` per-strategy (318 rows). Multivariate ran; `vwap_deviation_std` weak/unstable on `win` (OOS AUC 0.593, 2/5 folds < 0.5), null on `r` (R² −0.54) → **not a confirmed finding**. Real yield: even the richest book instruments only 2 graded features → the binding gap is **feature-capture breadth**.

---

## 5. Infra-readiness inventory (what deep research still needs)

Prioritized (from the platform readiness audit):

| P | item | size | why it matters | status |
|---|---|---|---|---|
| **P1** | C2 `--features` common-core selector | small | unblocks **pooled** multivariate discovery (Study 3) — without it every pooled study is stuck leads-only | **not built** |
| **P2** | per-strategy sweep driver (C1→C2 across strategies) | medium | turns "hand-run each strategy" into a self-serve sweep | not built |
| **P3** | C3 hypothesis→backtest→walk-forward bridge | medium–large | closes the loop — routes a confirmed feature into the entry gate | not built |
| **P4** | widen C1 decision-time feature capture (killzone/session + more) | medium | Study 2's finding — thin per-strategy features starve multivariate discovery | not built |
| **P5** | per-bar panel variant (vs per-trade) | large | exit-timing / regime-conditioned studies | not built |
| **P6** | SHAP importance | small | interpretation nicety (permutation already gates) | not built |
| **L3** | paper-book eval population (tagged, never blended) | medium | breaks the ~376 real-row wall — the label-volume lever | **prereq #7697 draft; build next** |

---

## 6. GO / NO-GO

**GO — with a scoped finish-line.** The discovery engine is **validated and safe
to trust**: on a **single-cohort / per-strategy** panel it produces honest,
guarded feature→outcome discovery today (Studies 1–2 are real, honest outputs).
The anti-overfit guards demonstrably bite.

**Before the deep-research session is fully self-serve**, finish (recommended
order):
1. **#7697** (eval account_class-authoritative) — operator review + merge; the substrate for everything paper.
2. **L3** paper-eval population — the label-volume lever (breaks the ~376 wall).
3. **P1** C2 `--features` selector — unblocks pooled multivariate discovery (Study 3, where `model_score_mean` finally gets its OOS test).

P2 (sweep driver) and P3 (backtest bridge) are the difference between
"hand-run each study" and "turn the crank"; P4–P6 are quality/coverage
follow-ons. None block starting; all are Tier-1 observe-only research tooling.

**What is NOT safe to assume:** a *pooled* multivariate result until P1 lands
(complete-case rows collapse on the block-sparse pooled panel); and no feature
has cleared the full lead→finding bar yet, so nothing should touch a live config
(that remains Tier-3, operator-gated, via the unbuilt C3 bridge).
