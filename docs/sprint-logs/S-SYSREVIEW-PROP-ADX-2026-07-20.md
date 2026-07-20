# Sprint Log: S-SYSREVIEW-PROP-ADX-2026-07-20

## Date Range
- Start: 2026-07-20
- End: 2026-07-20

## Objective
- Primary goal: run a `/system-review` work session (all three reviews + the
  promotion/training/soak/flags/backlog mandate), log an inbound closed prop
  trade, and act on the operator's three scoped follow-ups.
- Secondary goals (operator-directed, in order): (1) optimize the **live prop
  portfolio** of strategies on `breakout_1`; (2) **verify the ada/xrp losers**
  are regime-conditioned (not structurally bad) BEFORE drafting any demotion;
  (3) do an **ict_scalp_5m** review and write a **full modernization research
  plan** ("one of the first strategies we designed; our methodology has improved
  since then").
- Standing operator directive framing all strategy work: *do not demote a good
  strategy that merely had a bad week when the right move is turning it off in
  the wrong regime and back on later.*

## Tier
- Tier 3 (three live-config changes to `config/{accounts,strategies}.yaml`) +
  Tier 1 (review, report, research plan, backlog, docs) + Tier 2 (prop
  report-back DB write + deploy).
- Justification: the ADX raises + the prop de-dup edit `config/*` live-path
  files → Tier-3, each merged only with explicit operator approval.

## Starting Context
- Active roadmap items: M7 (strategy review gate), M8 (strategy tuning),
  M16/Design-A regime work; prop-accounts architecture (Tier-3 live wiring).
- Prior sprint reference: S-STRAT-REFINE-0618 (ported the ADX≥25 gate into the
  live `htf_pullback_trend_2h` unit + set `adx_min:25` on the 5 pullback alts);
  S-REGIME-DIVERSIFY-2026-06-18.
- Known risks at start: the operator's explicit caution against demoting
  regime-conditioned strategies on a bad week; prop account near its DD floor.

## Repo State Checked
- Branch: `claude/system-review-prop-trade-ffe8wj` (restarted off `origin/main`
  after each merge, per the merged-PR follow-up rule).
- Deployment state: live trader verified pre/post each deploy via the
  pull-and-deploy workflow log + boot audit.
- Canonical docs reviewed: CLAUDE.md, CLAUDE-RULES-CANONICAL.md,
  ARCHITECTURE-CANONICAL.md, ROADMAP.md (doc-freshness at wrap).

## Files and Systems Inspected
- Code files inspected: `src/prop/prop_report.py`, `scripts/backtest_pullback.py`
  (htf_pullback unit levers), `scripts/ml/strategy_tune_sweep.py` (HarnessSpec
  registry — no (backtest_pullback, adx_min) entry → manual per-year OOS used).
- Config files inspected: `config/accounts.yaml`, `config/strategies.yaml`
  (pullback family blocks), `config/prop_rulesets/breakout.yaml`.
- Docs inspected: performance-review-backlog.json (PB-20260618-015,
  PB-20260630-ICTSCALP-DEGRADE), the M7 packets.
- Services/timers inspected: `ict-trader-live.service` (post-deploy active),
  boot audit strategy enumeration.
- GitHub Actions workflows inspected: `prop-report.yml`, `system-actions.yml`
  (grade-closed-trades, pull-and-deploy), the trainer-vm-diag relay (config-exact
  backtests run on the trainer `.venv`).

## Work Completed
- **Prop trade logged** — the inbound closed ETHUSD short reported back via the
  `prop-report` relay (fill id 25) + a fresh account-status snapshot
  (rule_distance ≈ $125.61 above the $4,700 static-DD floor).
- **`/system-review` report published** — `comms/reports/since-last/20260720T081500Z/`
  (report.{html,json,md}) + index.json, roll-up **caution**; consolidated ping
  sent. Grade rows appended to `comms/claude_strategy_scores.jsonl`.
- **Prop-portfolio de-dup (Tier-3, #7060, MERGED+DEPLOYED)** — removed the
  redundant `eth_pullback_2h` from `breakout_1.strategies` (kept the
  swap-robust twin `eth_pullback_prop_2h`: +$421 vs +$166 5y, 32% vs 57% swap
  drag, 3.1× margin). Fixed a stale `trend_donchian_eth` "shadow" comment (live
  since 2026-06-17 — field-beats-comment). `eth_pullback_2h` stays live on the
  bybit books.
- **ada/xrp regime verification (research finding, NO demote)** — every
  actionable ada/xrp `_pullback_2h` entry in 2026-06-13→07-20 fired at
  `regime=trending`, ADX 25–32; the `adx_min:25` gate is provably blocking
  ADX<25. The losses are **marginal-trend / transition fakeouts** (ADX just
  clears 25 then the move fails), not chop — the operator's thesis confirmed.
  M7 verdict HOLD; **zero demotions drafted.**
- **Per-symbol ADX tuning of the pullback family (Tier-3):**
  - **ADA `adx_min` 25→28 (#7060, MERGED+DEPLOYED)** — config-exact grid + per-year
    sequential OOS walk-forward: 28 wins 2024/25/26, ties 2023, ~flat 2022;
    lowers maxDD 4/5 yrs, halves the worst year (2025 net −7.6→−2.2).
  - **SOL `adx_min` 25→30 (#7096, MERGED+DEPLOYED)** — CONFIG-EXACT re-run
    (matched to the live block: `trail_mult 5.0` + M20 decay `stall10/tight2.5`,
    NO `vol_skip`). Grid IS net_R 25→47.4 / 28→39.8 / **30→61.8** / 32→36.8 —
    30 is the peak and stronger than the earlier non-exact run (61.8 vs 28.3R).
    Per-year OOS 25 vs 30: net 55.8R vs 43.4R, lower maxDD 4/5 yrs.
  - **XRP / AVAX / ETH held at 25** — their grids degrade or wash on a raise
    (XRP monotonically degrades; AVAX 28 was an in-sample peak that did not
    replicate OOS). A blanket floor raise would help ADA and hurt XRP → the
    optimum is symbol-specific. Per-symbol tuning COMPLETE.
- **ict_scalp_5m modernization research plan** —
  `docs/research/ict_scalp_5m-modernization-research-plan-2026-07-20.md`: a
  6-phase exit-first plan (P0 honest baseline + clean per-cell dataset, P1 R:R
  diagnosis, P2 M20 exit-refinement, P3 M21 entry-refinement, P4 2-D regime
  cell, P5 symbol/tf expansion, P6 k-fold + parity + re-promotion) with an
  explicit kill off-ramp. ict_scalp is ALREADY `execution: shadow` — the plan is
  the execution of `PB-20260630-ICTSCALP-DEGRADE`, not a new demotion.

## Validation Performed
- **Config-exactness discipline:** the SOL WF was re-run after self-catching
  that the first pass used `trail 5.0 + vol_skip 0.1` while live `sol_pullback_2h`
  carries M20 decay + no vol-skip; the config-exact re-run confirmed 30 (stronger).
- **Walk-forward:** per-year sequential OOS (train-past / test-next-year) for
  ADA and SOL; both beat the incumbent 25 net-R with lower maxDD in 4/5 years.
- **Real-money safety:** the raises touch only the paper `*_pullback_2h` alt
  cells on bybit_1; the real-money BTC `htf_pullback_trend_2h` cell has no
  `adx_min` and is untouched.
- **CI:** #7096 18/18 checks green (incl. strategy-risk-guard,
  strategy-coverage-guard, dry-run-guard) after a clean branch-update over #7082.
- **Deploy:** pull-and-deploy log shows post-deploy HEAD == the merge SHA
  (#7060 `89c9b48`; #7096 `4ac8e1e`), `ict-trader-live.service` active, clean
  restart + startup validation, both cells in the boot-audit enumeration.
- Gaps not yet verified: the first live SOL/ADA fill under the raised gate
  (market-timing) — light residual watch on PB-20260618-015.

## Documentation Updated
- Roadmap updates: Historical Sprint Ledger row (this sprint).
- Research doc: `docs/research/ict_scalp_5m-modernization-research-plan-2026-07-20.md`.
- Backlog: `performance-review-backlog.json` PB-20260618-015 (full ADX grid, ADA
  + SOL WF verdicts, config-exact SOL confirmation, family status COMPLETE);
  PB-20260630-ICTSCALP-DEGRADE (research plan is its execution).
- Canonical docs: no change needed (no gate/topology/tier/architecture change).

## Contradictions or Drift Found
- None in the canonical set (doc-freshness `canonical-doc-coherence` checker
  passed all four checks; raw-grep residue is pre-existing historical/allow-listed
  content, untouched this session).
- Corrected two in-session over-flags before they shipped: (a) an early report
  draft framed ict_scalp_5m as a live DEMOTE_SHADOW candidate — it is already
  shadow; (b) it framed ada/xrp as demote candidates — they are M7-HOLD +
  already ADX-gated. Both corrected in the report + backlog; zero demotions drafted.

## Risks and Follow-Ups
- **Prop cushion:** `breakout_1` flat at ~$4,825.61, ~$125.61 above the $4,700
  static-DD floor — no sizing action while flat; watch on the next fill.
- **First live fill under the raised ADX gates** (ADA 28 / SOL 30) not yet
  observed — light residual watch (PB-20260618-015).
- **ict_scalp_5m** modernization is queued research (P0 first: kill the
  measurement skew, then diagnose R:R) — Tier-3 re-promotion gated on the plan's
  Phase-6 evidence.
