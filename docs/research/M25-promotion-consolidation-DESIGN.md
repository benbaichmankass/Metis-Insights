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

## The promotion gate (unchanged, restated)

A head promotes `shadow → advisory` only on **RG4** evidence
(`scripts/ml/rg4_targeted.sh`): TRUSTWORTHY (live-vs-train agreement, no
anti-predictive skew) **and** POWERED (enough labeled outcomes of the minority
class across enough distinct episodes — the rule of thumb is ≥ 40–50 labeled
volatile-class bars/symbol across ≥ 5 distinct volatile episodes). RG3 (in-session
CV) is necessary but **not** sufficient — the M18/M21/vol-gate history is a
graveyard of RG3-passes that RG4-failed live. The operator promotes; Claude
proposes with the RG4 packet.

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
| **P1 — Readiness tooling repair** | Fix the promotion-readiness CLI so it can parse the exit-head/peak-head shadow records (`MB-20260716-PROMOREADY-EXITHEAD-SCHEMA`, missing `row_keys`); confirm `rg4_targeted.sh` runs clean against the current mirror; fix the frozen trainer MES candle base if still stale (`BL-20260626-MES-BASE-STALE`, GIGO-blinds RG4). | T1 | Readiness CLI parses every live shadow family; RG4 harness green on fresh data. |
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
