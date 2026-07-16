# Sprint Log: S-M22-D2-PAIRS-READINESS-2026-07-16

## Date Range
- Start: 2026-07-16
- End: 2026-07-16

## Objective
- Primary goal: build the real-money-readiness infrastructure for the M22 D2
  market-neutral pairs sleeve ("build it out so we don't loop on bugs") and
  answer whether any pair can trade on real money given the sizing issues.
- Secondary goals: fix the observed naked-leg unwind bug (G6); produce the
  $-and-lots evidence (G2); apply the operator's resulting routing decision.

## Tier
- Tier 3 (order-path alert + config/pairs.yaml execution flips) + Tier 1
  (research tooling + docs).
- Justification: G6 touches the isolated pairs order path; the config flips
  change live routing (paper). Both operator-approved this session. G2/G1
  tooling + docs are Tier 1.

## Starting Context
- Active roadmap items: M22 D2 pairs sleeve (paper go-live had surfaced the
  min-qty half-placement bug; #6591 min-qty gate + #6585 defensive shadow).
- Prior sprint reference: `S-M22-D2-PAIRS-SLEEVE-2026-07-15.md`.
- Known risks at start: the sleeve had never been $-and-lots validated; a naked
  BNB paper leg had been observed after a failed unwind.

## Repo State Checked
- Branch: `claude/scalping-chop-strategies-b9n05u` (recreated from `main` after
  each merge).
- Deployment state: verified post-deploy via `/api/diag/status`
  (git_sha e35081e0, heartbeat running, bybit_1 live).
- Canonical docs reviewed: CLAUDE.md (pairs + prop + tiers), the readiness doc.

## Files and Systems Inspected
- Code: `scripts/backtest_pairs.py`, `src/units/strategies/pairs_sizing.py`,
  `src/units/strategies/pairs_executor.py`, `src/units/accounts/qty_legalize.py`,
  `scripts/research/pairs_universe_scan.py`.
- Config: `config/pairs.yaml`.
- Live state: `pairs_soak` log, `/api/diag/status` (via the vm-diag relay).

## Work Completed
- **G6 — naked-leg unwind fix** (`pairs_executor.py`): `_unwind_legs` now checks
  the `close_open_position` result (which returns `{ok:False}` without raising)
  and returns un-flattened legs; `_alert_partial_placement` escalates a naked
  leg to CRITICAL. Merged in #6600.
- **G2 — $-and-lots backtest** (`scripts/research/pairs_dollar_lots.py`, new):
  reuses the validated engine via a non-invasive `collect_rows` seam on
  `run_backtest` (parity-tested), sizes off the canonical balance × risk_pct
  basis via `pair_notionals`, floors to venue lots, computes the true two-leg $
  P&L, sweeps balances, and (`--ideal-no-floor`) isolates strategy-vs-lots.
- **G1 core** (`pairs_sizing.plan_pair_sizing`, new): the min-qty-aware sizer
  geometry, `max_risk_multiple=1.0` default == the #6591 skip (behaviour-
  preserving). Reframed as counterproductive for the current pairs by the G2
  finding.
- **Config**: `config/pairs.yaml` → paper-only, edge-gated per operator
  directive: SOL/ETH + BNB/BTC `live` on bybit_1 paper (positive fee-free edge),
  SOL/BTC + ETH/BTC `shadow` (negative). Merged + deployed.
- Docs: `docs/research/pairs-sleeve-real-money-readiness-2026-07-16.md` (G1–G7
  gap analysis + the G2 findings + fee decomposition).

## Validation Performed
- 66 pairs tests green (engine parity, $-and-lots sim, G1 sizer, executor);
  `ruff` + `pairs-sizing-basis` guard clean; all 27 required checks green on #6600.
- **G2 evidence** (trainer-diag #6612/#6615, real candles at bybit_2 risk_pct
  0.015): R-space edge (net_R +513…+825) collapses to net-negative in $;
  fee-free fixed-β-hold edge is thin/mixed (SOL/ETH +$1279, BNB/BTC +$357,
  SOL/BTC −$295, ETH/BTC −$855); 7.5bps taker × 2 legs × ~2800 trades tips all
  four deeply negative. **The loss is fees** — same wall as the small-TF program.
- Post-deploy: `/api/diag/status` git_sha e35081e0, heartbeat running.

## Documentation Updated
- `docs/research/pairs-sleeve-real-money-readiness-2026-07-16.md` (findings +
  decomposition).
- `config/pairs.yaml` header + per-pair rationale comments.
- `docs/claude/health-review-backlog.json` (BL-20260716-DEVNULL-DEPLOY-PERM).

## Contradictions or Drift Found
- The D2 validation was R-space (rolling-β) and optimistic vs the live fixed-β
  execution — noted in the readiness doc; the paper soak will confirm.

## Risks and Follow-Ups
- **BL-20260716-DEVNULL-DEPLOY-PERM**: `/dev/null` unwritable on ict-bot-arm
  broke the deploy git-fetch (non-fatal this time; git-sync + restart landed the
  code anyway). Needs a privileged chmod / reboot; no autonomous wire exists.
- Real-money routing to bybit_2 is OFF THE TABLE until the sleeve is
  net-positive in $. BNB/BTC's BTC leg floors sub-min even on the paper balance
  (β≈0.04) so it mostly `skip_size`s cleanly; SOL/ETH is the only clean placer.

## Deferred Items
- Forward path is a RESEARCH question (not wiring): maker execution (post-only →
  ~0 Bybit maker fees, the deferred `maker_band_post_only`) + live β re-hedge /
  Kalman-β to recover the rolling→fixed-β gross gap. Connects to the
  maker-economics thread (`docs/research/small-tf-directions-2026-07-15.md`).
- G4 (compat-matrix), G5 (PnL isolation), G7 (risk-cap routing) are moot until
  the sleeve is net-positive.

## Next Recommended Sprint
- Scope the maker-execution + β-rehedge research for the pairs sleeve (extend G2
  to model periodic re-hedging + its fee cost; test whether any cadence flips the
  sleeve net-positive under maker fees).

## Wrap-Up Check
- [x] Work committed + merged (#6600).
- [x] Deploy verified (`/api/diag/status`).
- [x] Docs + backlog updated.
- [x] doc-freshness intent: no canonical-doc contradiction introduced (research
  doc + tooling + a paper-config flip consistent with CLAUDE.md's pairs section).
