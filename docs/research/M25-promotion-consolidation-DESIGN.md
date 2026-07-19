# M25 — ML Promotion & Consolidation (harvest the maturing soaks)

> **Status:** 📋 PROPOSED 2026-07-17 (design of record). The *evidence-gathering*
> (RG4 readiness eval, promotion-readiness memos, tooling fixes) is **Tier-1** and
> autonomous. The **shadow→advisory promotion itself is Tier-3** — the live-trading
> switch — and stays operator-gated. M25 produces the decision packet; the operator
> makes the call.
> Anchors: `MB-20260705-FC-ADVISORY-READINESS` (fc heads — the lead candidate),
> `MB-20260628-REGIME-SOAK-READINESS` (ETH/SOL vol-regime heads),
> `MB-20260716-PROMOREADY-EXITHEAD-SCHEMA` (readiness-CLI schema gap),
> `MB-20260626-003` (regime-head promotion structurally blocked on live_agreement).

## Why a consolidation milestone

We have spent months **accruing shadow track records** — fc forecast heads, ETH/SOL
vol-regime heads (incl. the `eth-regime-15m-lgbm-xasset-v1` cross-asset head merged
2026-07-17, PR #6786), the M20 peak-is-in exit head, the M21 entry P_win head. That
investment only pays off when a head crosses its gate and gets **promoted to
advisory** (the only stage that influences an order). M25 is the disciplined
harvest: for each maturing soak, run the *powered* readiness eval, write the
decision packet, and either propose the Tier-3 promotion (operator approves) or
record an honest "not yet / never" and re-park. No new frontiers — bank what's soaking.

## The promotion gate (restated 2026-07-17 — SUPERSEDED, see the REFRAMED section below)

A head promotes `shadow → advisory` only on **RG4** evidence
(`scripts/ml/rg4_targeted.sh`): TRUSTWORTHY (live-vs-train agreement, no
anti-predictive skew) **and** POWERED (enough labeled outcomes of the minority
class across enough distinct episodes — the rule of thumb is ≥ 40–50 labeled
volatile-class bars/symbol across ≥ 5 distinct volatile episodes). RG3 (in-session
CV) is necessary but **not** sufficient — the M18/M21/vol-gate history is a
graveyard of RG3-passes that RG4-failed live. The operator promotes; Claude
proposes with the RG4 packet.

## The promotion gate — REFRAMED 2026-07-19 (soak = mechanics, not edge)

**Operator-approved 2026-07-19.** The edge of an ML head is proven **OFFLINE**
— the powered purged-walk-forward `oos_edge` gate (`ml/promotion/oos_edge.py`),
which never loosens. The live shadow soak's job is to prove **serving
MECHANICS**: that the live pipeline feeds the model the features it trained on
and that the logged score is the score the registered artifact actually
produces. Waiting weeks for live outcome statistics to power in a calm regime
(the `live_regime_discrimination` bottleneck, `MB-20260626-003`) re-proves
offline evidence on a slower clock while the mechanics failures that actually
burned us (the ETH-xa dead-feature bug `BL-20260628-XA-TRAINING-ZERO`; the MES
stale-candle labeling blockage, 1213/1861 rows unlabeled) are deterministic and
checkable **today** from existing artifacts.

Concretely, in the REGIME gate profile (`ml/promotion/gates.py`):

- **`live_regime_discrimination` is DEMOTED to advisory reporting** — still
  computed (RG4 Stage-2 replay) and shown in the gate report, but
  `required: false`. It is an outcome-statistics gate that takes weeks to
  power in calm regimes.
- **`live_parity` is a new REQUIRED gate** (`ml/promotion/live_parity.py`) —
  deterministic serving-mechanics checks over the head's live-logged shadow
  rows (`runtime_logs/shadow_predictions.jsonl` records with `feature_row` +
  `score`). v1 scope, all computable from existing artifacts, no new
  instrumentation:
  1. **Serving fidelity** — re-score up to 50 most-recent live rows with the
     registered model artifact; the logged score must match the recomputed
     score within a small tolerance (abs 1e-6, configurable). A mismatch
     fraction above 2% of sampled rows fails.
  2. **Dead-feature parity** (the ETH-xa bug class) — for each feature the
     model consumes, compare live rows vs the training dataset: a feature
     constant/all-zeros on ONE side but varying on the other fails, naming
     the feature(s).
  3. **Minimum sample** — fewer than 20 live rows with `feature_row` reports
     `insufficient_data` (NOT pass, NOT fail): mechanics unproven yet.
- **`labels_accruing` is a new REQUIRED gate** — the labeled fraction of the
  head's live rows must reach 0.30 once ≥ 20 live rows exist; below the floor
  fails with the fraction in the detail (catches the stale-candle-base
  labeling blockage class). Fewer rows → `insufficient_data`.
- **Non-regime profiles are UNCHANGED** — the new gates are reported
  `required: false` there unless a custom `GateThresholds` opts in.

Fail-safe direction: an ERROR while computing a gate (unreadable log, model
load failure) surfaces as `insufficient_data` with the error in the detail —
never a silent pass, never a crash of the whole gate-check. The single-model
`gate-check` CLI computes the new inputs; the fleet `stage-guard` sweep still
passes `None` (per-model candle/dataset resolution is a separate follow-up),
so a regime head is only certifiable through `gate-check` on the trainer VM —
which is where promotion packets are assembled anyway.

**The one case where a long outcome-soak stays meaningful:** when offline
history genuinely cannot represent the live distribution (a brand-new venue or
data feed with no history). Absent that, once offline walk-forward passes AND
the live parity gate passes, the packet is decision-ready — do not hold it for
outcome-window statistics that offline history already provides with far more
power. RG4's live-row replay remains the *parity/skew instrument* (its real
strength), reported as advisory context in every packet. RG3 alone remains
insufficient exactly as before — the M18/M21 graveyard was RG3-passes with
*unverified mechanics*; the parity gate is what actually closes that hole.
The operator promotes; Claude proposes with the packet.

## Scope — the soak roster (as of 2026-07-17)

| Head / family | Current stage | Readiness anchor | M25 action |
|---|---|---|---|
| **fc forecast heads** (BTC/ETH/SOL 15m quantile) | shadow (lead candidate) | `MB-20260705-FC-ADVISORY-READINESS` | Powered RG4 once ≥40–50 vol-class bars/symbol across ≥5 episodes accrue (first read ~mid-July). If PASS → Tier-3 advisory proposal + the fc→SL/TP geometry re-check (`MB-20260705-FC-SLTP-GEOMETRY`, due 2026-08-25). |
| **BTC 15m vol-regime head** (`btc-regime-15m-lgbm-v2`, live advisory; `-vt004` candidate) | advisory (shipped 0.005) / candidate (0.004) | `MB-20260701-001` | **Operating-curve study COMPLETE + NEGATIVE (2026-07-17)**: the denser-label 0.004 candidate improved the classifier but the gate-4 money A/B lost net PnL vs 0.005. **Live threshold stays 0.005**; no promotion. Recorded — do not re-run without a new lever. |
| **ETH/SOL 15m vol-regime heads** (incl. `eth-regime-15m-lgbm-xasset-v1`) | shadow | `MB-20260628-REGIME-SOAK-READINESS` | Re-check RG4 ~2026-08-01. The xasset head just merged (small in-session lift, +0.023 macro_f1); it needs its OWN post-soak RG4, not the in-session RG3, before any vol-gate go-live for ETH cells (`MB-20260628-VOLGATE-GOLIVE`). |
| **M20 peak-is-in exit head** (`exit-head-donchian-peak-1h-v1`) | shadow | M20 Phase-4 (roadmap) | Parity check → E3 advisory proposal once the shadow soak + first-fire mechanics confirm. Time-gated, not an M25 build. |
| **M21 entry P_win head** (`entry-pwin-donchian-1h-v1`) | shadow (annotate) | M21 E-3 | Accruing decision-time track record; M18 allocator P_win use stays PARKED until it clears. M25 just watches the soak. |

## Phased plan

| Phase | Scope | Tier | Gate |
|---|---|---|---|
| **P1 — Readiness tooling repair** | ~~Fix the promotion-readiness CLI so it can parse the exit-head/peak-head shadow records (`MB-20260716-PROMOREADY-EXITHEAD-SCHEMA`, missing `row_keys`)~~ **✅ ALREADY DONE** — landed via #6570 (`ml/shadow/inspector.py::_parse_record` derives `row_keys` from `feature_row` when absent; 37 tests pass); the backlog entry was stale, now marked resolved. Remaining P1: confirm `rg4_targeted.sh` runs clean against the current mirror; fix the frozen trainer MES candle base if still stale (`BL-20260626-MES-BASE-STALE`, GIGO-blinds RG4). | T1 | Readiness CLI parses every live shadow family (done); RG4 harness green on fresh data. |
| **P2 — Powered RG4 sweep** | Run `rg4_targeted.sh` for each roster head whose soak has matured; produce a per-head TRUSTWORTHY×POWERED verdict + a promotion-readiness memo under `docs/research/`. | T1 | A committed per-head verdict table; each head is PROMOTE-PROPOSE / WAIT (+re-check date) / NEVER (+reason). |
| **P3 — Tier-3 promotion packets** | For each head that PASSES powered RG4, draft the exact `ml promote-stage` + any YAML wire (e.g. vol-gate cell authoring) as a Tier-3 proposal with the evidence. | T3 | Operator approves each promotion individually; rollback = demote-stage / kill-switch (`REGIME_ROUTER_DISABLED`, `REGIME_ML_VERDICT_MODE=shadow`). |
| **P4 — Demotion / retire sweep** | The other half of consolidation: heads that RG4-fail or drift (KS/PSI) get demoted or retired so the shadow roster doesn't accrete dead weight (the `MB-20260626-*` MES-head skew class). Structural blocker `MB-20260626-003` (regime promotion gated on trade-win live_agreement) gets a decision: fix the gate or accept it. | T1 (demote is safe) / T3 (retire a live-influencing head) | Roster is all live-relevant; every stale head has a recorded verdict. |

## Non-goals / honesty

- **M25 does not lower any gate to force a promotion.** If nothing is powered yet,
  the honest output is "everything WAIT, re-check dates set" — that is a complete,
  successful M25 pass, not a failure.
- **Promotion is the operator's switch.** Claude assembles the RG4 packet and
  proposes; it never flips shadow→advisory autonomously (VM authority split gate 2).
- Composes with **M24** (a promoted head's live influence should be re-graded on
  net-R after the fact) and **M23** (a matured meta-head enters this same roster).
