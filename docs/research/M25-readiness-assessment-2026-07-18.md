# M25 promotion-readiness assessment — 2026-07-18

> **Status:** M25 evidence-gathering pass (Tier-1, autonomous). **Promotion past
> `shadow` is Tier-3, operator-gated — nothing is promoted here.** This records
> the honest READY / WAIT / NEVER-here verdicts per the M25 mandate ("a complete
> pass, not a failure"). Anchors `MB-20260705-FC-ADVISORY-READINESS` +
> `MB-20260628-REGIME-SOAK-READINESS` + `MB-20260718-BTCREGIME-V2-DRIFT-DEMOTE`.

## What ran

No new eval was needed — the trainer already generates a **daily
promotion-readiness report** (`runtime_logs/trainer_mirror/promotion_readiness/<date>/`:
`SUMMARY.md` + `report.json` + `cli_stdout.json`). This pass reads the newest
good one — **2026-07-17T17:35:39Z, 84 models reviewed** (issue #6835) — and maps
it to the M25-named candidate heads.

## Headline — 0 promote · 1 demote · 83 hold

**No head is ready for `shadow → advisory` this cycle.** The report proposes
zero promotions. The one actionable proposal is a **demote of a live head**, and
it is the important finding.

## The one live-affecting flag — Tier-3 operator decision

**`btc-regime-15m-lgbm-v2` (advisory → shadow) — score-distribution drift verdict
is `significant`.**

This is **the live BTC vol-gate advisory head.** Per the canonical vol-gate
contract, BTC has exactly this 15m advisory head, so **every** BTC regime cell
(`trend_donchian` 1h, `squeeze_breakout_4h`, …) resolves *its* ML vol label into
the real-money gate decision (`REGIME_ML_VERDICT_MODE=use`, BTC enforce is LIVE).
The daily readiness eval now says its score distribution has **drifted
significantly** vs its reference window — i.e. the live BTC vol-gate is running
on a **drifted** head.

- **This is a Tier-3 order-routing decision — NOT actioned here.** The options
  are the operator's: (a) demote `advisory → shadow`
  (`python -m ml promote-stage btc-regime-15m-lgbm-v2 --new-stage shadow …`),
  which is **fail-permissive** — with no advisory BTC head the gate reverts to
  the frozen `vol_detector` label (`ml_vol_regime_for_symbol` → `unknown` →
  frozen), it does **not** strand any signal; or (b) retrain/replace the head
  (a fresh `btc-regime-15m-lgbm-v2` run or promoting a cleaner sibling once one
  clears its gates); or (c) hold and re-check next daily report if the drift is
  a transient window artifact.
- **Connection to the vol-gate operating-curve study (`MB-20260701-001`, NEGATIVE):**
  that study concluded the 0.005 threshold is the best available and the gate
  A/B was negative. A significantly-drifted driving head is a coherent partial
  explanation — the gate can only be as good as the head feeding it. Recorded as
  `MB-20260718-BTCREGIME-V2-DRIFT-DEMOTE`.

## Per-M25-head verdicts (WAIT across the board)

| Head (M25 target) | Registry stage | Verdict | Blocking gates (2026-07-17 report) |
|---|---|---|---|
| fc-forecast regime heads — `btc-regime-15m-lgbm-fc-pcv-v1` | shadow | **WAIT** | `live_regime_discrimination` |
| `eth-regime-15m-lgbm-fc-pcv-v1` | shadow | **WAIT** | `cross_run_stability`, `live_regime_discrimination` |
| `sol-regime-15m-lgbm-fc-pcv-v1` | shadow | **WAIT** | `cross_run_stability`, `live_regime_discrimination`, `drift_clean` |
| ETH vol-regime — `eth-regime-15m-lgbm-v1` | shadow | **WAIT** | `live_regime_discrimination` |
| ETH x-asset — `eth-regime-15m-lgbm-xasset-v1` | shadow | **WAIT** | `cross_run_stability`, **`shadow_soak`** (still soaking), `live_regime_discrimination`, `drift_clean` |
| SOL vol-regime — `sol-regime-15m-lgbm-v1` | shadow | **WAIT** | `live_regime_discrimination` |
| M20 peak/exit head (`exit-head-donchian-peak-1h-v1`) | — | **NOT IN SCOPE** | tracked in the M20 exit-head E2→parity→E3 flow, not this report |
| M21 entry head (`entry-pwin-donchian-1h-v1`) | — | **NOT IN SCOPE** | tracked in the M21 shadow-annotate flow, not this report |

## The systemic finding — `live_regime_discrimination` is the fleet-wide wall

Of the ~30 shadow-stage regime heads, **nearly every one blocks on
`live_regime_discrimination`** (the RG4 gate: does the head actually separate
regimes on *live* data, not just offline holdout?). Many also fail
`drift_clean`. So the shadow regime fleet is **broadly not promotable**, and the
reason is uniform: the heads train acceptably offline but **do not discriminate
live regimes** to the gate's bar.

**Implication for M25:** promotion is *not* the lever this cycle — there is
nothing powered to promote. The forward lever for the regime fleet is
**improving live regime discrimination** (better features / labels / the
cross-asset and funding-feature lines already in flight), which is forward ML
research (its own Tier-1 track), not a promotion action. The `xasset` head still
owes `shadow_soak` time regardless.

## Honest bottom line

- **Promotions available: 0.** A complete WAIT pass — correct, not a failure.
- **One operator decision teed up (Tier-3):** the `btc-regime-15m-lgbm-v2` drift
  demote — the live BTC vol-gate head is drifted. Exact commands + the
  fail-permissive consequence above; `MB-20260718-BTCREGIME-V2-DRIFT-DEMOTE`.
- **The regime fleet's blocker is `live_regime_discrimination`, fleet-wide** —
  the same wall the operating-curve study hit. Fixing it is forward research,
  not promotion.
- **M20/M21 heads** are tracked in their own soak→parity flows, not this report.
