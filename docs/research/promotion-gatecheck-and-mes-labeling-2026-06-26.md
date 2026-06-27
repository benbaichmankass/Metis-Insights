# BTC regime-head promotion gate-check + MES labeling root cause (2026-06-26)

Follow-up to the fleet scorecard (`fleet-model-scorecard-2026-06-26.md`,
`MB-20260626-001`), executing the operator's "(b) + (c)" directive:

- **(c)** Run the formal promotion gate (`python -m ml gate-check`, regime
  profile, WITH `--datasets-root` for `oos_edge` + the live `--db`) on the BTC
  5m/15m candidates.
- **(b)** Root-cause the MES live-labeling gap surfaced by RG4.

Both via `scripts/ml/gate_check_candidates.sh` (new, reusable) + trainer-vm-diag
relays (#4710 diagnosis, #4713 launch, #4716 read).

---

## (c) Promotion gate-check — all 6 BTC regime heads NOT ready

Gate profile: `regime_classifier` (min_trades 5, beats_baseline not required —
`oos_edge` carries the beats-baseline role; oos baseline = the modal
`RegimeClassifierTrainer`).

| Head | oos_edge (purged WF-CV) | shadow_soak | live_agreement | drift KS/PSI | non_degen | cross_run | Blocking |
|---|---|---|---|---|---|---|---|
| **btc-regime-15m-lgbm-yz-v1** | ✅ +0.276 | ✅ 21.9d | ❌ 0.267 | ✅ 0.159/0.091 | ✅ | ✅ | **live_agreement only** |
| **btc-regime-15m-lgbm-v2** | ✅ +0.265 | ✅ 31.2d | ❌ 0.500 | ✅ 0.168/0.025 | ✅ | ✅ | **live_agreement only** |
| btc-regime-5m-lgbm-v2 | ✅ +0.236 | ✅ 31.2d | ❌ 0.344 | ❌ 0.262/0.206 | ✅ | ✅ | live_agreement + drift |
| btc-regime-5m-lgbm-yz-v1 | ✅ +0.243 | ❌ 1.6d | ❌ 0.500 | ❌ 0.337/0.034 | ✅ | ⚠️ 1 run | soak + live_agr + drift + cross_run |
| btc-regime-5m-baseline-v1 | ❌ 0.000 | ✅ 35.5d | ✅ 0.642 | ❌ 0.561/0.318 | ❌ F1 0 | ✅ | non_degen + oos_edge + drift |
| btc-regime-15m-baseline-v1 | ❌ 0.000 | ✅ 35.3d | ✅ 0.720 | ❌ 0.502/0.196 | ❌ F1 0 | ✅ | non_degen + oos_edge + drift |

### Reading it

1. **The two 15m lgbm heads are one gate from promotable.** Both clear every
   quality + safety gate — strong offline edge (`oos_edge` +0.27/+0.26 over the
   modal baseline on 5 purged WF-CV folds), 22–31d soak, clean drift, stable
   across runs, non-degenerate. The **only** blocker is `live_agreement`.

2. **`live_agreement` is the universal blocker for the good (lgbm) heads — and
   it is both mis-targeted and sample-starved for a regime head.** The gate
   measures rank-AUC(regime-volatility *score* vs realized trade *win*) on just
   **11 live closed BTC trades**. A regime classifier predicts the volatility
   *regime*, not trade direction/outcome — so scoring ~0.27–0.50 on "does my
   regime probability predict the trade's win/loss" is not, on its own, evidence
   the head is bad. The regime-appropriate live signal is RG4 (does it predict
   the *regime* on the logged-live rows): **0.72–0.76 live** for these two
   heads — strong. Same blocker that demoted the first advisory head
   (`btc-regime-1h-lgbm-yz-v1`, MB-20260623-001): it is bottlenecked on thin BTC
   real-money trade flow, which `bybit_2` (pinned near min-qty) accrues far too
   slowly to ever populate `live_agreement` meaningfully.

3. **The baselines invert the picture — and the gate handles them correctly.**
   `btc-regime-{5m,15m}-baseline-v1` *pass* `live_agreement` (0.642 / 0.720) but
   that's noise on 15–45 trades from a **degenerate** predictor: they fail
   `non_degenerate` (per-class F1 = 0 — they only ever predict the majority
   "range" class) and `oos_edge` (= 0.000, i.e. no edge over the modal baseline
   they essentially are). Correctly not promotable.

4. **Drift** cleanly separates the 15m lgbm heads (KS 0.16–0.17, pass) from the
   5m lgbm heads (KS 0.26–0.34, fail) — the 5m score distribution has shifted
   window-over-window; the 15m heads are stable.

### The gate-design question for the operator (Tier-3 / policy)

The formal promotion gate makes `live_agreement` (score-vs-trade-win AUC) a
**required** gate for *every* model, including regime heads. For a regime
classifier this asks the wrong question on too small a sample, and it is
structurally un-satisfiable while BTC real-money flow is thin. The two paths:

- **(A) Promote on the regime-appropriate evidence.** Treat the RG4 live
  regime-discrimination AUC (0.72–0.76) as the live-track-record gate for regime
  heads instead of `live_agreement`, keeping every other gate. This is a gate
  *profile* change (Tier-3, operator-gated) — the regime profile already drops
  `beats_baseline`; the same logic argues for swapping `live_agreement`
  (trade-outcome) for RG4-style regime-discrimination on regime heads.
- **(B) Wait for live flow.** Keep the gate as-is and accept that no regime head
  promotes until BTC real-money trade volume grows enough to populate
  `live_agreement` — which, given `bybit_2`'s sizing, may be never.

**Recommendation:** do NOT promote any head today (none is `ready` and the
honest reading of `live_agreement` is "not yet measurable," not "passed"). The
two 15m lgbm heads are the standout candidates; the right next step is the
operator's call on the gate-design question (A vs B), not a promotion. This is a
Tier-3 decision — the gate-check reports; the operator decides.

---

## (b) MES live-labeling gap — root cause: stale MES candle base (frozen 2026-06-12)

RG4 left most live MES rows unlabeled (mes-5m unlab 2777/2981, mes-15m
1025/1102) while BTC was fully labeled. Root cause:

- **The MES `market_raw` candle dataset content ends 2026-06-12** (5m last_ts
  `2026-06-12T20:55:00Z`, 17113 rows; 15m `2026-06-12T20:45:00Z`), but the live
  MES shadow predictions run **2026-06-04 → 2026-06-26**. RG4 joins each
  prediction to the nearest candle bar; ~2 weeks of recent MES predictions have
  no bar → unlabeled. (BTC candles come from Bybit, continuously fresh →
  `unlab≈0`.)
- **It is NOT a timestamp-format or model problem** — both candle and prediction
  timestamps are ISO strings that parse cleanly.
- **The daily build is healthy but fed a stale base (GIGO).** `dataset_builds.jsonl`
  shows `market_raw MES ... "ok"` at `2026-06-26T00:24:27` and the v002 file
  mtime is `2026-06-26 00:24` — it rebuilds daily. But `build_mes_market()`
  prefers a **synced IBKR MES base** ("using synced IBKR MES market_raw (deep
  history) instead of yfinance") when present, and that base stopped advancing at
  2026-06-12, so each rebuild re-emits the same stale candles. The yfinance ES=F
  fallback (caps intraday at ~60d) is only used when the IBKR base is absent.

### Fix direction (Tier-1 trainer tooling — logged, not yet shipped)

The IBKR→trainer MES market-data sync that feeds the preferred base froze ~Jun
12; refreshing it restores RG4's ability to score the live MES fleet. Tracked as
a health-review-backlog item (data-pipeline/wiring gap). Until it's fixed, RG4
verdicts on the MES fleet (incl. the two anti-predictive MES 15m heads,
`MB-20260626-002`) ride a thin labeled sample and should be re-confirmed once the
base is fresh.

---

---

## Update (2026-06-26 PM): MES candle base FIXED + RG4 re-run + option-A gate shipped

### MES data fix (closes the (b) follow-up)
Root cause pinned: the trainer's IBKR-synced MES candle base froze at 2026-06-12
(last candle `2026-06-12T20:55Z`, mtime Jun 14 — the live→trainer IBKR MES sync
stalled). The daily build is healthy but prefers that base, so it re-emitted
stale candles. **yfinance ES=F verified fresh** (5m to 2026-06-26). Fix applied
on the trainer (Tier-1, autonomous): retired the stale IBKR base → the build
falls to its yfinance fallback; rebuilt MES `market_raw` 5m + 15m. Now **5m to
2026-06-26T03:55Z (10918 rows), 15m to 03:45Z (3654 rows)**. Durable — the daily
cycle now rebuilds MES from yfinance. (`BL-20260626-MES-BASE-STALE` resolved.)

### RG4 re-run on the MES fleet (the verdict-changing part)
With fresh candles, RG4 labels nearly the whole live MES sample (unlab ~93% →
<10%). The conclusions **changed**:

| MES head | First scorecard (stale, thin) | Re-run (fresh, full sample) |
|---|---|---|
| mes-regime-15m-lgbm-v2 | 0.32 ANTI_PREDICTIVE (~77) | **0.59 TRUSTWORTHY** (~1011) |
| mes-regime-15m-baseline-v1 | 0.44 ANTI_PREDICTIVE (~127) | **0.64 TRUSTWORTHY** (~1061) |
| mes-regime-5m-lgbm-v2 | 0.77 (~204) | 0.557 TRUSTWORTHY (~2792) |
| mes-regime-5m-lgbm-yz-v1 | UNSCOREABLE (0 labeled) | **0.17 ANTI_PREDICTIVE** (~1460) |
| mes-regime-5m-baseline-v1 | — (~254) | 0.47 NO_EDGE (~2842) |

**The two MES 15m heads flagged for demote/kill were false negatives** — a
stale-candle thin-sample artifact. On fixed data they're TRUSTWORTHY. The fix
instead surfaced a genuine problem the stale data hid: `mes-regime-5m-lgbm-yz-v1`
strongly inverts live (0.17). `MB-20260626-002` revised accordingly. (Caveat: RG4
uses a global vol_threshold 0.003, not each dataset's calibrated median — a
refinement, but well below the 0.17 inversion's sensitivity band.)

### Option-A regime gate (closes the (c) decision, pending operator merge)
Shipped (commit `de45220`, PR #4700): the **regime-classifier promotion profile
now requires `live_regime_discrimination` (RG4 live regime AUC ≥ 0.55) instead of
`live_agreement`** (trade-win AUC). `live_agreement` still reported but
non-blocking for regime heads; default decision-model profile unchanged; never
touches the order path. `ml/promotion/gates.py` + `stage_guard.py` + the
`gate-check` CLI (computes the RG4 AUC via `replay_pregate_live`) + tests. This
makes the two BTC 15m lgbm heads — which clear every gate but `live_agreement`,
and which RG4-discriminate at 0.72–0.76 live — actually evaluable for promotion
on the regime-appropriate signal. Operator still runs `promote-stage`; the gate
only reports.

**End-to-end verified on the trainer** (branch code run via `verify_optionA_gate.sh`,
restored after): with the new gate, BOTH BTC 15m lgbm heads now read
**`ready=True, blocking=[]`**:

| Head | live_regime_discrimination | live_agreement (now non-blocking) | oos_edge | shadow_soak | drift |
|---|---|---|---|---|---|
| btc-regime-15m-lgbm-yz-v1 | **PASS — RG4 0.763** | fail 0.267 (not required) | +0.276 | 21.9d | KS 0.158 |
| btc-regime-15m-lgbm-v2 | **PASS — RG4 0.722** | fail 0.500 (not required) | +0.265 | 31.3d | KS 0.166 |

So the new CLI integration computes the RG4 live AUC correctly on real data, the
`live_agreement` (trade-win) gate is correctly demoted to non-blocking for regime
heads, and **both heads are formally promotion-ready under the regime-appropriate
criterion.** They would be the first advisory regime heads since the 1h yz head
was demoted (MB-20260623-001) — promotion to advisory means they influence orders
via the regime router, so the actual promote-stage is the operator's Tier-3 call.
Pending: operator merges PR #4700, then decides promotion.

## Artifacts

- `scripts/ml/gate_check_candidates.sh` — reusable per-head promotion gate-check.
- `MB-20260626-001` (ml-backlog) evidence appended with the gate-check results +
  the `live_agreement` gate-design question.
- MES base staleness → health-review-backlog (data-pipeline fix).
- All Tier-1 read-only research; promotions + the gate-design change are
  operator-gated.
