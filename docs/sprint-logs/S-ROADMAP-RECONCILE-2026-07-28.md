# S-ROADMAP-RECONCILE-2026-07-28 — Overnight roadmap reconciliation + forward-plan rebuild + M24 correlation feature

- **Session:** `01RZE6wD` (Claude Code web, overnight autonomous `research-driver`)
- **Branch:** `claude/roadmap-research-planning-n1rr1d`
- **Window:** 2026-07-28 evening → overnight
- **Milestone mapping:** cross-milestone (M20/M21/M23/M24/M25/M27/M28/M29/M0a) reconciliation + M24 build

## Objective

Operator directive: *"run an autonomous all-night research session. look at the
roadmap to knock off any open items, [and] make a full plan that you can drive
beyond the roadmap as well (add new things to the roadmap)."*

Two asks: **(1)** knock off genuinely-open items; **(2)** rebuild the forward
plan. Governed by the `research-driver` skill. The roadmap's "Next — prioritized
work plan" was last refreshed **2026-07-11** and had drifted materially out of
date, so job #1 required first **reconciling what is actually open vs already
shipped** before anything could be "knocked off."

## Method

Fanned out three read-only mapping sub-agents (per `delegate-work`) to reconcile
the roadmap's claims against repo reality (git log, modules-and-tests on disk,
coverage JSONs, sprint logs, the three review backlogs), covering:
(A) ML milestones M23/M24/M25; (B) strategy/exit milestones M20/M21/M27;
(C) macro/platform M28/M29/M0a + the three review backlogs. Each finding was
re-verified against disk, not taken from the roadmap text (the roadmap is the
thing under audit). Board `▶️ START` posted to #6927; no concurrent session was
live (last three sessions had all wrapped by 19:38Z). No live-VM or trainer
mutation this session; diag was not reachable from this environment (no
`DIAG_BASE_URL`), so all VM-dependent items are *surfaced*, not executed.

## Reconciliation findings — roadmap vs reality (the "stale-plan" audit)

The 2026-07-11 "Next" plan overstates open work; several "next" items shipped
weeks ago. Corrected state:

| Item in the stale "Next" plan | Roadmap said | Reality (verified) |
|---|---|---|
| M20 Exit Refinement | "essentially COMPLETE" **and** table row "IN PROGRESS" (self-contradiction) | Near-complete but done-condition **not literally met**: `exit_ladder` lever **un-built fleet-wide**; several `exit_head_ml` cells `pending`; `xauusd_trend_1h` hard levers `blocked` on candle task #27. |
| M24 P1/P2 (net-R label + re-grade) | "P1 START HERE" | **DONE** — `src/runtime/net_r_label.py`, `scripts/research/net_r_regrade.py`, `tests/test_net_r_{label,regrade}.py` present + green (17 tests). One sign-flip (`spy_pullback_1h/SPY`) filed Tier-3. |
| M24 P3/P4 | pending build | **BLOCKED** on broker-truth cost coverage (~99% *estimate* — needs fee accrual) + Tier-3. |
| Model-quality quick win `MB-20260701-001` (BTC-15m vol head) | open | POSITIVE first-gate 2026-07-16; live threshold stays 0.005; remaining gates Tier-3/trainer. |
| M23 meta-labeling | P2 open | P1 DONE; **P2 gate NOT met** — wall is real-money closed-trade *count* (BTC-dominated), not shard coverage; eval book 383→400 rows still EV-gate FAIL. P3 already NO-GO. |
| M25 promotion/consolidation | P1/P2 open | P1/P2/P3 **DONE** (executed 2026-07-20; SOL later demoted 07-26). P4 demote/retire IN-PROGRESS. Recurring harvest needs a fresh trainer shadow-log mirror. |
| M27 Scalp Expansion | "P0 COMPLETE" | Correct; P1 (15m) also done; several 15m crypto legs live on `bybit_1`. Next: ict_scalp_eth_15m stale12 (Tier-3), crypto-scalp `exit_head_ml` heads (trainer), P2/P3. |
| M28/M29 macro | P4 "blocked on FRED producer" | **P4 RAN → OOS-NULL** (phantom gate; producer wired). Value construction space **EXHAUSTED/CLOSED**. M29 P1a/P1b done; **P2 (AI system-identification) genuinely open, Tier-1, offline**. |
| M21 Entry Refinement | "IN PROGRESS" | **Dormant** — coverage frozen since 2026-07-14; pullback-family `depth_threshold` cells `pending`/null; squeeze/fvg entry blocked. |
| `BL-20260726-FRED-ADAPTER-SILENT-SWALLOW` | open (add logging) | **Already fixed** — `fred_adapter.py` lines 188/218 log `_log.warning`. Marked resolved (no code change needed). |

**Headline:** the frontier is not "build the next thing on the list" — it is
mostly **data-accrual + trainer-gated + Tier-3 promotion** waits. The
genuinely-open *buildable-tonight* Tier-1 surface is narrow (below), which is
itself the most important planning finding.

## Work completed this session

1. **M24 decision-time correlation feature — BUILT (`MB-20260629-ALLOC-CORR`).**
   `src/runtime/allocator_corr.py` + `tests/test_allocator_corr.py` (18 tests,
   green; ruff clean; **stdlib-only** → import-linter-safe by construction).
   Pure, **observe-only** — closes the M18/M24 gap that *nothing live computes
   correlation between the book's symbols*, so two highly-correlated same-
   direction positions are sized as independent `risk_pct` trades and the caps
   never see the correlated exposure as one number. Provides `pearson`,
   `pairwise_correlations`, `correlated_exposure` (→ `max_abs_corr`,
   `corr_weighted_aligned_risk`, `corr_concentration`, `effective_independent_bets`,
   coverage counts) + a fail-permissive `candidate_correlated_exposure` adapter
   mirroring `allocator_ev`'s style. **Not wired into any live path** — the
   feature is the reusable primitive the M24 P3 EV refresh + P4 within-tick net-R
   ranker consume; graduating it to influence a live size/selection is Tier-3
   (backtest-A/B-gated), tracked as the P3/P4 follow-up.
2. **Roadmap forward-plan rebuild** — the "Next — prioritized work plan" and
   changelog refreshed to the reconciled state above (see `ROADMAP.md`).
3. **Backlog hygiene** — `BL-20260726-FRED-ADAPTER-SILENT-SWALLOW` marked
   resolved (already fixed on disk); `MB-20260629-ALLOC-CORR` annotated with the
   feature-built status.

## Rebuilt forward plan (drives beyond the stale roadmap)

**A — Buildable-tonight-class Tier-1 / offline (no VM, autonomous):**
1. **M24 correlation feature — DONE this session** (above). Next Tier-1 increment:
   an offline EV-refresh dry-run harness feeding measured per-cell `net_R` +
   this correlation block into `allocator_ev.candidate_ev_score` (still
   observe-only; the live flip is Tier-3).
2. **M20 `exit_ladder` harness lever** — the one un-built M20 lever, fleet-wide.
   Pure-Python add to the `backtest_*` harnesses + `m20_fleet_exit_sweep.py`
   `FAMILY_HARNESS` + unit tests (the #7849 stale/giveback pattern). *Compute to
   validate is a runner/trainer job*, but the lever code + tests land Tier-1.
3. **M25 PROMOREADY exit/peak-head schema-parse residual** — finish the readiness
   report parse for exit/peak-head records (code + fixture).
4. **M25 MES full-session candle-base builder fix** (`MB-20260719-MES-BASE-RTH-ONLY`)
   — base is RTH-only (~90 bars/day) vs ~260 served ⇒ 65% of MES live rows
   unlabeled. Base-builder code + unit test land tonight; the rebuild/relabel run
   is trainer-side.
5. **M29 P2 — AI system-identification layer** (pure `src/sysdyn/`, offline,
   import-linter-locked). Larger, phase-opening; scope before diving.

**B — Soak / data-accrual clocks (check on cadence, do NOT rush):** broker-truth
fee coverage → M24 P3/P4; M23 P2 real-money label volume; fc/ETH/SOL vol heads
toward their RG4 gates; SOL `-v2` re-gate ~2026-07-28.

**C — Tier-3 surfaced for the operator (proposed, not executed):**
- `ict_scalp_eth_15m` stale12 flip (already filed `MB-20260728-ICTSCALP-EXIT-LEVERS`;
  the only ict_scalp lever to clear the full M20 gate).
- `xauusd_trend_1h` `p_win_head` (gate PASS 6/6, live AUC 0.792) → wire into the
  M18 allocator.
- M24 `spy_pullback_1h/SPY` net-R **sign-flip** vs estimate → Tier-3 review.
- Pairs sleeve `PB-20260715-PAIRS-SLEEVE` (HIGH); `layer-guard` → REQUIRED_CONTEXTS
  (`BL-20260726-LAYER-GUARD-NOT-REQUIRED`).
- Draft PRs #7848 / #7849 await operator Tier-3 approval (not touched this session).

## Validation

- `pytest tests/test_allocator_corr.py` → 18 passed; `tests/test_net_r_{label,regrade}.py`
  → 17 passed (M24 P1/P2 re-verified green). `ruff check` clean on new files.
  New module imports cleanly; stdlib-only imports confirmed.

## Docs updated

- `ROADMAP.md` (Next-plan reconciliation + changelog), this sprint log,
  `docs/claude/{health,ml}-review-backlog.json` (FRED resolved; ALLOC-CORR annotated).

## Follow-ups / open

- Forward-plan item A2–A5 are the next buildable Tier-1 sessions.
- Tier-3 items in section C await operator decisions.
- M21 coverage is stale since 2026-07-14 — a dedicated entry-refinement session
  should refresh it or the milestone should be explicitly parked.
