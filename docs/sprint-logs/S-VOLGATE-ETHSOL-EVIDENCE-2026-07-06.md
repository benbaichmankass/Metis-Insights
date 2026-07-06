# S-VOLGATE-ETHSOL-EVIDENCE-2026-07-06 — ETH/SOL vol-gate go-live evidence (honest negative) + harness vol-replay regression fix

## Date Range
- Start: 2026-07-06
- End: 2026-07-06

## Objective
- **Primary:** produce the per-symbol evidence to extend the Design-A vol-gate
  money win beyond BTC (operator-directed deep-research session): per-symbol
  `(strategy, trend, vol, side)` cell-attribution vol-splits for ETHUSDT and
  SOLUSDT under each symbol's OWN 15m regime head
  (`eth/sol-regime-15m-lgbm-v1`, both @ shadow), then per-symbol confirmation
  A/B + fixed-cell + cell-selection walk-forwards against the BTC promotion
  gate. Explicitly NOT copying BTC's cells across symbols.
- **Secondary:** RG4 power check on the fc heads (run only if powered —
  `MB-20260705-FC-ADVISORY-READINESS`); log counts otherwise.

## Tier
Tier 1 — research + analysis on the trainer VM, a research-harness fix, tests,
docs, backlog updates. **No live-path file changed; no promotion executed; the
Tier-3 bundle was evaluated and (on the evidence) NOT proposed.**

## Starting Context
- `MB-20260628-VOLGATE-GOLIVE` (resolved for BTC; ETH/SOL extension is its
  evidence-log remainder) + `MB-20260628-REGIME-SOAK-READINESS` (open).
- Soak/clock inventory in `S-M19-SOL-FC-GRADUATION-2026-07-06` § "Soak / clock
  inventory" (clock #4 = this session's mandate).
- BTC reference method: `docs/research/A-vol-gating-OFFcell-design-2026-06-27.md`
  (BTC gate live-enforced since 2026-06-28).
- Known risk called out in the backlog: the 2026-06-27 ETH draft cells were
  derived under the RG4-failed 1h head and must be re-derived.

## Repo State Checked
- Branch `claude/eth-sol-volgate-evidence-84bkhz` off main (local base commit
  `fa3072f`-era main); trainer VM synced to `5150c81e` (pulled during phase 0
  — it was 2 commits behind).
- Canonical docs read: CLAUDE.md, the OFFcell design doc, the A/B evidence
  doc, `regime_policy_eth_trend_vol-2026-06-27.yaml` (stale draft),
  `src/runtime/intents.py` (hard gate + #4896 guard),
  `src/runtime/regime/policy.py`, `scripts/backtest_system.py`,
  `scripts/ml/walkforward_*`.

## Files and Systems Inspected
- `scripts/backtest_system.py` (ROSTER incl. the `*_eth`/`*_sol` research
  entries at lines ~137-153; router wiring ~672-695; `_MlVolResolver` ~363-470;
  per-cell attribution ~1085-1112).
- `src/runtime/intents.py::_hard_regime_gate` (~1037-1110: the ML-only-enforce
  guard `vol_is_ml = mode=="use" and ml_vol in (...)`) +
  `_decision_vol_regime`; `src/runtime/regime/policy.py::{load_policy,
  _evaluate_vol_cell, would_gate}`; `src/runtime/runtime_flags.py::
  _regime_ml_verdict_mode` (env read fresh per call — patchable).
- Trainer VM via `trainer-vm-diag` relays (#5721, #5725, #5726, #5728, #5729,
  #5731, #5733, #5735, #5738, #5740, #5741, #5745): data files
  (`data/ETHUSDT_5m.csv` 2021-03-15→2026-06-18; `data/SOLUSDT_5m.csv`
  2021-10-15→2026-06-18), registry (both 15m heads @ shadow), run artifacts
  under `runtime_logs/volgate_ethsol/`.
- Live VM via `vm-diag-snapshot` (#5722): `shadow_stats` full aggregate.

## Work Completed
1. **Phase 1 — per-symbol attribution (trainer #5726/#5728).** Ungated
   full-history runs, 15m clock, vol label pinned to each symbol's own shadow
   15m head (`--vol-verdict ml --ml-model-id … --ml-stage shadow`); both heads
   scored every bar (ETH `scored=3007, fell_back=0`; SOL `scored=2004,
   fell_back=0`). ETH book: net $850 / maxDD $1876 / 1004t. SOL: $1831 /
   $1063 / 688t. Evidence cells (mechanical ≥10t net-negative rule): 7 per
   symbol. **Cross-symbol finding: BTC's winning cell
   (`trend_donchian|trending|calm|long`, +$1238 on BTC) is ETH's biggest loser
   (−$435/121t) and negative on SOL (−$190/49t) — cells do NOT transfer.**
2. **Found + fixed a real harness regression (BL-20260706-VOLGATE-REPLAY).**
   The first phase-2 A/B returned byte-identical arms. Root cause: since the
   #4896 ML-only-enforce guard, `_hard_regime_gate` vol-enforces only when
   `REGIME_ML_VERDICT_MODE=use` AND the LIVE per-symbol advisory resolver
   returns calm/volatile — neither exists offline, so every
   `--regime-router on` vol-cell replay silently equalled ungated (all
   vol-gating walk-forward scripts were no-ops since 2026-06-28; the BTC
   evidence predates the guard). Fix in `scripts/backtest_system.py`:
   `--regime-router on` now sets the mode env in-process and points the gate's
   decision hook at the label the run stamped on each intent, restored on
   teardown. Regression test
   `test_regime_router_on_enforces_trend_vol_cell_on_stamped_label` added
   (the prior test only covered the 1-D trend axis — why this went unnoticed).
   Verified: mechanism unit-checked locally (calm intent in an off cell drops
   with the patch, kept without) + live on the trainer (ETH ev-frozen arm
   $850→$1363 after the patch vs identical before).
3. **Phase 2 — A/B + walk-forwards (trainer #5733/#5735/#5738/#5740/#5741/
   #5745; SOL first pass discarded — its policy file was missing after the
   phase-2 kill, caught and re-run as phase 2c).** Full results in
   `docs/research/A-vol-gating-ETH-SOL-OFFcell-evidence-2026-07-06.md`:
   - ETH: A/B $850→$1374 net, DD −23%; fixed-cell WF net 4/4 but **maxDD
     2/4**; cell-selection net 2/3, DD 3/3.
   - SOL: A/B $1831→$1633 (−$198 net for −50% DD); fixed-cell WF **net 2/4**,
     DD 4/4; cell-selection 1/3 net, 1/3 DD (unstable cells).
   - **Verdict: NEITHER symbol clears the operator's promotion gate (ev-ml
     net ≥ ungated AND lower maxDD in every fold). Honest negative recorded;
     no Tier-3 bundle proposed; both 15m heads stay at shadow; no ETH/SOL
     `trend_vol` cells authored.** Post-hoc cell cherry-picking deliberately
     declined (the overfitting move the BTC follow-ups rejected).
4. **Secondary — fc-head RG4 power check (live diag #5722).** Counts:
   `btc-…-fc-pcv-v1` 303 / `eth-…-fc-pcv-v1` 174 / `sol-…-fc-pcv-v1` 7 preds.
   UNPOWERED (needs ≥40–50 labeled volatile bars/symbol across ≥5 episodes);
   powered read stays ~mid-July. Logged to
   `MB-20260705-FC-ADVISORY-READINESS`; no RG4 forced.
5. **Docs/backlogs:** evidence doc (above); `MB-20260628-VOLGATE-GOLIVE` +
   `MB-20260628-REGIME-SOAK-READINESS` evidence logs updated (the latter
   snoozed to 2026-08-01); `BL-20260706-VOLGATE-REPLAY` opened+resolved in the
   health backlog; ROADMAP header + ledger row; this log.

## Validation Performed
- Harness fix: 10/10 `tests/test_backtest_system_evidence.py` (incl. the new
  regression test), 15/15 `test_aggregate_intents_regime_hard.py` +
  `test_regime_ml_vol_use_substitution.py` — run locally in the session
  sandbox (pandas/numpy/pytest installed).
- Gate mechanism unit-verified against `src/runtime/intents.py` directly
  (drop-with-patch / keep-without) before relaunching the trainer runs.
- Trainer runs verified by artifact inspection (attr JSONs' `data_start/end`,
  `fell_back=0` counters, authored YAMLs printed) — not just exit codes.
- **Gaps not yet verified:** trainer-side session copies
  (`scripts/backtest_system_volgate_patched.py`,
  `scripts/ml/walkforward_cell_selection_volgate.py`) are untracked files on
  the trainer worktree — cleanup relay dispatched at session close (see
  Follow-ups). The branch's harness fix is NOT yet on main (draft PR pending
  review/merge), so stock-main walk-forward scripts remain no-ops on the vol
  axis until it merges.

## Documentation Updated
- `docs/research/A-vol-gating-ETH-SOL-OFFcell-evidence-2026-07-06.md` (new).
- `docs/claude/ml-review-backlog.json` (3 items updated) +
  `docs/claude/health-review-backlog.json` (BL-20260706-VOLGATE-REPLAY).
- `ROADMAP.md` Last-Updated header + Historical Sprint Ledger row.
- This sprint log.

## Contradictions or Drift Found
- **The vol-gating research scripts contradicted their own documented
  behaviour** (claimed to exercise the hard gate; actually no-ops on the vol
  axis post-#4896) — fixed, not routed around.
- The 2026-06-27 ETH draft cells doc is superseded; the new evidence doc says
  so explicitly (the draft yaml already carried its own gating caveats, so no
  edit needed there).

## Risks and Follow-Ups
- **Tier-3 decision for the operator: none proposed** — the evidence says
  don't promote. If the operator wants a DD-first variant (SOL's gate halves
  drawdown at modest net cost), that is a different acceptance bar and should
  be an explicit operator decision, not a session inference.
- ETH's underlying book is the real issue (3 of 4 ungated yearly folds
  net-negative) — belongs to the strategy-review track (`/performance-review`
  + M7 gate), not the vol gate.
- Re-run trigger recorded in `MB-20260628-REGIME-SOAK-READINESS` (snoozed
  2026-08-01): materially more history, a head retrain, or ETH strategy-review
  action.
- Trainer cleanup relay (remove the two untracked session copies) dispatched
  at close; verify on the next trainer session if the relay result wasn't
  awaited.

## Deferred Items
- Powered RG4 on the fc heads (~mid-July, per plan).
- ETH strategy-book review (surfaced, not started).

## Next Recommended Sprint
- The **fc-head powered RG4 + money-gate walk-forward** (~mid-July,
  `MB-20260705-FC-ADVISORY-READINESS`) — it is the next evidence-gated
  promotion candidate now that the ETH/SOL vol-gate extension is a recorded
  negative.

## Wrap-Up Check
- [x] Code inspected directly (file:line cites above; gate mechanism unit-run).
- [x] Docs reviewed/updated (evidence doc, backlogs, ROADMAP, this log).
- [x] TRADE-PIPELINE untouched (no pipeline stage changed; research + harness only).
- [x] Roadmap checked + updated.
- [x] Contradictions recorded (harness self-contradiction fixed).
- [x] Unknowns stated plainly (trainer cleanup pending; fix not yet on main).
- [x] No promotion past shadow; Tier-3 evaluated and NOT proposed on the evidence.

---

# Addendum — same-day follow-up (post-#5747 merge): M18 P1 stale-claim correction + ETH book review

## Work Completed (addendum)
1. **PR #5747 merged** (squash `fe41951`, CI 17/17, merge-slot protocol observed)
   — the harness vol-replay fix + the honest-negative evidence records are on
   `main`.
2. **M18 P1 "next build" closed WITHOUT building — it already exists.** The
   soak-inventory line "M18 P1 EV_net scorer (buildable)" (this repo's
   S-M19-SOL-FC-GRADUATION log + my own session recommendation) was **stale**:
   P1 shipped in #5098 and the live allocator soak has scored with
   `score_kind="ev_net_r"` since (verified `src/runtime/intent_multiplexer.py:726`
   → `allocator_ev.candidate_ev_score`), and the 2026-06-30 backtest findings
   already tested that scorer's SELECTION edge and found it negative (picks
   ≈96–97% identical to priority; sizing-normalized edge −$7; ranker OOS AUC
   ≈ 0.51) → P2/P3 PARKED. Doc drift fixed in PR #5784: CLAUDE.md's
   allocator-soak row now states the live `ev_net_r` state + the parked
   verdict; the stale sprint-log lines carry record-preserving correction
   annotations (not rewrites). The real M18 unblocker is **T1.3** (a learned
   P_win ranker that materially beats OOS AUC 0.51 in the sizing-normalized
   harness) — a research item.
3. **ETH strategy-book review — verdict `hold` ×3, no Tier-3 proposed.** M7
   packets generated against the live journal via the sanctioned
   `generate-strategy-review-packets` system-action (issue #5766, 30-day
   window, exit 0): `trend_donchian_eth` 13 decisions / 0 fills / 0 closed;
   `trend_donchian_eth_4h` 5 / 1 / 0; `eth_pullback_2h` 66 / 3 / 2 closed
   (+$0.24, 100% WR on n=2). The gate's low-n rule refuses to act — correctly
   (the PR #1358 lesson). The backtest fold weakness from the morning session
   is anchored in **`PB-20260706-ETH-BOOK-RECHECK`** (performance backlog,
   snoozed 2026-08-01): re-run the gate at ~≥20 combined closed with the
   backtest anchor attached; expected proposal IF live confirms =
   `demote_shadow` for `trend_donchian_eth`. Observation flagged: ETH fill
   rates are 0–4.5% of decisions — if that looks anomalous it is a
   health-review question.
4. **PR #5784 merged** (squash `81e2fc9`, CI 17/17, slot protocol observed).

## Soak / clock inventory — REFRESHED at session close (supersedes the
## S-M19-SOL-FC-GRADUATION-2026-07-06 table; next /system-review walks THIS one)

| # | Clock | State at 2026-07-06 ~15:45Z | Gate / next read | Backlog item |
|---|---|---|---|---|
| 1 | **fc shadow soak — BTC+ETH+SOL 15m fc heads** | BTC 303 / ETH 174 / SOL 7 preds (diag #5722); UNPOWERED, as expected | **Powered RG4 ~mid-July** (≥40–50 labeled volatile bars/symbol, ≥5 episodes) → money-gate walk-forward → Tier-3 fc→advisory | `MB-20260705-FC-ADVISORY-READINESS` |
| 2 | **D1 fc-geometry soak** | accruing since 07-05 | months-scale n; Tier-3 on any geometry change | `MB-20260705-FC-SLTP-GEOMETRY` |
| 3 | **D2 label-wall accrual** | paper ramping | re-run pooled meta-label spike when paper n_train ≈ real n_train (~weeks) | `MB-20260705-META-LABEL-WALL` |
| 4 | **ETH/SOL vol-gate base-head soak** | eth 861 / sol 825 preds @ shadow; **cell evidence = HONEST NEGATIVE this session (PR #5747)** — no promotion, no cells | re-check ~2026-08-01 (more history / head retrain / ETH book action) | `MB-20260628-REGIME-SOAK-READINESS` (snoozed 08-01) |
| 5 | **ETH live book** | M7 `hold` ×3 on low-n (issue #5766) | re-run M7 gate at ~≥20 combined closed, backtest anchor attached | `PB-20260706-ETH-BOOK-RECHECK` (snoozed 08-01) |
| 6 | **Allocator soak** | scoring with `ev_net_r` since P1 (#5098); **selection edge tested NEGATIVE 06-30 — P2/P3 PARKED** | T1.3 learned ranker must beat OOS AUC 0.51 in the sizing-normalized harness before any routing plumbing | M18 roadmap section + T1.3 row |
| 7 | **Exit-ladder soak** | accruing | P4 Tier-3 + backtest-gated | `PB-20260617-002` |
| 8 | **Conviction sizing/arbitration soaks** | accruing; symmetric FAILED its A/B — stays off | P4/P5 operator+backtest-gated | roadmap M14/conviction rows |
| 9 | **News-layer soak** | rss active, veto armed | `NEWS_INFLUENCE_MODE` graduation Tier-3 | news roadmap rows |

## Validation Performed (addendum)
- `score_kind="ev_net_r"` verified in code before correcting any doc.
- M7 packets generated by the live-VM system-action (not recomputed by hand);
  proposed_action echoed per strategy in the issue reply.
- Both merges: CI 17/17 green pre-merge; merge slot claimed/released on the
  session board (commits `8426c0d`/`df549e1`, `c3f96f2`/`e47093c`).

## Wrap-Up Check (addendum)
- [x] Code inspected directly (intent_multiplexer.py:726; allocator_soak.py).
- [x] Docs reviewed/updated (CLAUDE.md drift fix, sprint-log annotations, this addendum, ROADMAP).
- [x] TRADE-PIPELINE untouched.
- [x] Roadmap checked + updated.
- [x] Contradictions recorded (the stale "P1 buildable" claim — fixed at source).
- [x] Unknowns stated plainly (ETH fill-rate observation → health-review question).
- [x] No Tier-3 action taken; M7 hold respected.
