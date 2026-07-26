# Workplan — De-soak infrastructure + M24–M29 milestone close-out (2026-07-26)

> **Status:** operator-approved 2026-07-26. Driven under `research-driver`.
> **Anchor:** `MB-20260726-DESOAK-PROMOTION-EVIDENCE` (Phase 0), plus the existing
> M24–M29 anchors for Phase 1.
> **Companion:** the [Next — prioritized work plan](../../ROADMAP.md#next--prioritized-work-plan)
> queue in `ROADMAP.md`; this doc is the phased record the queue points at.

## Why this workplan exists

The operator asked a sharp question: *we built backtest infrastructure specifically
so things would NOT need to soak for weeks to prove themselves — a few cycles to
prove the plumbing, then history proves the edge. So why are so many things still
soaking for weeks?*

Two read-only investigations (2026-07-26) established the ground truth. **The
"weeks of soak" is almost entirely stale framing + a few un-retired policy gates —
the backtest/replay infrastructure already exists, already works, and in the key
cases has already been run.**

| "Soak that needs weeks" | Ground truth found | True nature |
|---|---|---|
| **ML regime-head promotion** (RG4, "≥40–50 volatile bars × ≥5 live episodes") | Reframed 2026-07-19: edge proven **offline** (`ml/promotion/oos_edge.py`, purged WF-CV); mechanics proven in **~20 live rows ≈ hours** (`ml/promotion/live_parity.py`). RG4 discrimination **demoted to advisory** (`ml/promotion/gates.py`); its accrual rule sits in the design doc's *SUPERSEDED* section. | **Policy leftover** — a vestigial `shadow_soak_days=7.0` required calendar gate + stale backlog wording still impose a week |
| **M28-P4 value gate** ("accrue 4–6 weeks of FRED") | `scripts/macro/valuation_snapshot_backfill.py` already reconstructed **10,125 point-in-time rows over 21 years (2005→2026)**; the gate **already ran** (`comms/macro/thesis_p4_scorecard.json`): calibration ≈ 0, **edge_vs_baseline −0.0047 (loses to naive all-long, net of zero cost)** | **Stale framing + the answer is already in hand — and it's negative.** No accrual changes an OOS-fail over 21 years |
| **M26 transition/taxonomy** ("~a week of soak rows") | `src/runtime/conflict_taxonomy.py::classify_tf_ratio` is a **pure function** of static config + a journal read → fully backfillable, already run on history in P0 (121 pairs / 585 hold-rows). The design **explicitly forbids soak-gating**; the decision is a `backtest_system.py` walk-forward (P3) | **Stale framing** — the soak log is observe-only mechanics-parity, never a decision gate |
| **EIA_API_KEY** ("not yet provisioned") | `fetch_eia_storage_dated` has **no keyless fallback** (returns `[]` without a key), yet `comms/macro/sysdyn_gas_dual_scorecard.json` (2026-07-25) holds **864 real EIA storage obs** → key is provisioned and working | **Doc contradiction to correct** |

**The genuine remaining live-only constraints (the honest residue):**
1. **Label volume** — the real eval book is ~376 real-money rows; every meta-label
   lever caps at ~11 net-positive trades on it. This is real and un-fakeable
   without more labels. *This is the one thing that needs a real infra build.*
2. **Rolling drift** (`gates.py::_gate_drift_clean`) — a live-window check (it
   correctly demoted `sol-regime-15m-lgbm-fc-pcv-v1` on KS=0.236, 2026-07-26). Real,
   but a *rolling* window, not a fixed multi-week accrual.
3. **Train/serve parity** — must run on live rows (it tests the live serving path
   itself), but needs only ~20 rows ≈ hours.

**Conclusion:** "resolve this once and for all" is ~80% governance discipline +
20% one real build (label volume) — not a large new soak-killing platform.

---

## Phase 0 — De-soak: finish the reframe + install the guard (Tier-1 autonomous; WS-1 Tier-3-adjacent → propose)

The highest-leverage work; almost all doc/policy/CI.

| WS | Item | Tier | Deliverable | Status |
|---|---|---|---|---|
| **0.WS-4** | Truth-reconcile the stale docs: EIA key provisioned; M28-P4 ran + failed OOS (not "awaiting accrual"); M26 P2 observe-only (decision = P3 backtest). Record the M28-P4 negative in the signal-research ledger. | T1 | ROADMAP + ledger corrected | **this branch** |
| **0.WS-5** | Legibility/reliability: log the silent FRED-fetch swallow (audit E-1, `fred_adapter.py`); (follow-on) trainer-vm-diag SSH-drop keepalive (`BL-20260721`); scheduled-cron liveness monitoring for the macro producers. | T1 | E-1 fix this branch; rest queued | **E-1 this branch** |
| **0.WS-1** | **Retire the vestigial `shadow_soak_days=7.0` required calendar gate** for regime heads — make it non-required (or hours) when `oos_edge`+`live_parity`+`labels_accruing`+`drift_clean` all pass. | T1 code, but **promotion-policy → propose** | Separate **draft PR** for operator review + a `gates.py` test | queued (own PR) |
| **0.WS-2** | **The durable fix — an anti-soak governance guard.** Canonical rule: *edge is proven offline (purged WF-CV / historical backfill); a live soak may only prove serving-mechanics (parity + drift), which accrue in hours; no gate may require calendar-time accrual to prove edge.* Add a `check_no_calendar_edge_gate` CI guard + a one-time de-soak sweep of the ml-review backlog + ROADMAP soak-clocks. Rewrite the stale clocks `MB-20260628-REGIME-SOAK-READINESS` + `MB-20260705-FC-ADVISORY-READINESS` to mechanics-based criteria. | T1 | rule + CI guard + reworded backlog | queued |

## Phase 1 — Genuine infra builds (after Phase 0)

| WS | Item | Tier | Why it's real |
|---|---|---|---|
| **1.1** | **Label-volume plumbing — WS-B candle-shard coverage expansion** (alt-USDT ADA/AVAX/XRP + equities/metals daily → every closed trade path-resolves → eval book grows past the ~376-row wall). Then L3 paper-book labels + L5 net-R label swap. **Scoped 2026-07-26** → [`WS-B-candle-shard-labelvol-scoping-2026-07-26.md`](./WS-B-candle-shard-labelvol-scoping-2026-07-26.md): the shard *roster* already landed (PR #6934), but the eval-book harness (`m23_phase2_labelvol.sh`) still hardcodes BTC/ETH/SOL @1h and never consumes the new shards — so the real remaining plumbing is 3b (widen the harness to the full covered roster at correct per-symbol timeframes) + 3a/3c (trainer-VM verify + rerun). Tier-1 / offline. | T1 | The single binding constraint on the whole ML program (M23/M24-P4/G1-G2 all starve without it). The one heavy investment. |
| **1.2** | **Productionize powered-offline discrimination** — wire `scripts/ml/replay_pregate_fleet.py`/`oos_edge` as the standing per-head evidence in the promotion-readiness report, so no head waits on live episodes to show discrimination. | T1 | Makes "backtest not soak" the default path in the weekly harvest |
| **1.3** | **Formalize `drift_clean` as the sole rolling live check** + fast-track the fc-pcv `v2` gate the moment parity+drift pass (don't wait the calendar week). | T1→T3 | Drift is the one legit live property; make it explicit and bounded |
| **1.4** | **ALFRED vintage adapter — DEFERRED/optional.** Only needed to widen macro to *revised* series (earnings yield, EIA storage as PIT). The 4 wired metrics (real yield, term slope, credit spread) are unrevised market rates, so the existing backfill already IS point-in-time. Backlog, not a Phase-1 build. | T1 | Future-proofing, not a current blocker |

## Phase 2 — Open items & milestone expansions (gated behind Phase 0/1)

- **M25 — ML harvest under the new fast doctrine:** fc-pcv v2 gate-check + swap; SOL vol
  head re-promote via the v2 sibling (v1 demoted 07-26 on drift); ETH 15m head;
  `mes-regime-5m-lgbm-v2` packet; a **demote/retire sweep**.
- **M24 — reopen the parked tracks as broker-truth fees accrue:** correlation feature
  (observe-only), EV-refresh dry-run, the `spy_pullback_1h/SPY` net-R sign-flip
  **Tier-3 review packet**. Broker-truth *fee* accrual (~1–2 wk) is a real, small,
  correctly-running rolling accrual.
- **M26 — skip to the decision:** run **P3 walk-forward** (policy arms vs the live
  `hold` `FLIP_POLICY`) now — taxonomy is already backfilled; no soak. Tier-3
  `*_MODE` only if P3 beats `hold` OOS.
- **M27 — promote, don't build a venue:** GLD (`alpaca_paper`) + MGC (`ib_paper`)
  timeframe sweep → walk-forward → **`alpaca_live` promotion packet** via
  `account_compat_matrix`. Drop dead XAUUSD-spot.
- **M28 — DECIDED 2026-07-26 (operator): iterate the *construction*, keep the sleeve
  observe-only.** The value thesis failed OOS, but the null is about the weak
  trailing-percentile-contrarian *cell*, not the whole idea — so re-form the thesis via
  D1 transform / D2 conditioning / D3 cross-section per `M28-signal-research-methodology.md`,
  logging each as an entry in `M28-signal-research-ledger.md`. **No order path; `sleeve.execution:
  shadow`; nothing graduates to live until a re-formed thesis beats a naive baseline OOS in the
  P4 gate.** The event/thesis-engine mechanics continue regardless.
- **M29 — P1b real calibration is unblocked now** (EIA key works) → P2 AI system-ID.
- **Audit residue + stability:** F1 `ict-mes-ibkr-pull` failed; F2 352 stranded pings
  (#6874); F3 bybit_2 smoke `place_order` NoneType; a focused **reconciler/orphan
  robustness pass** (`BL-20260618` −$23k class, IB broker-protection verification gap).

## Phase 3 — Natural next steps (roadmap only; sequenced after 0–2)

- **G1/G2 — ML-native entry generation** (the authority-ladder frontier) — **hard-gated
  on 1.1 label volume**; propose-only, paper-first.
- **M31 Track-B options-skew — `operator-hold` (Schwab).** Explicitly parked per the
  2026-07-26 operator directive; nothing downstream depends on it. Adapter is turnkey
  for whenever the app is registered.
- **M18 allocator "select" rung** — stays PARKED until a P_win/net-R ranker beats dumb
  priority OOS (the M21+M24 join is the named unlock; reopen only after L5).

## Sequencing

Phase 0 first (all safe, unblocks faster promotions at ~zero risk), with **1.1 label
shards in parallel** as the one heavy investment three milestones queue behind. Phase 2
milestone harvest follows the honest gates. Phase 3 goes on the roadmap but waits.

## One-line answer to the operator's question

We don't need to soak for weeks, and we mostly weren't *required* to — the gates were
reframed on 2026-07-19 but the calendar clocks and stale docs were never cleaned up, so
the system *looked* like it was soaking. Phase 0 finishes that cleanup, installs a guard
so it can't recur, and builds the one thing that's a genuine constraint (label volume).
