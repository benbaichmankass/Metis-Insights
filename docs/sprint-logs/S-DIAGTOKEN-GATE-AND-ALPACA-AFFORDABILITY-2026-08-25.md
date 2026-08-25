# Sprint Log: S-DIAGTOKEN-GATE-AND-ALPACA-AFFORDABILITY-2026-08-25

## Date Range
2026-08-25 (06:37Z–09:00Z), single session `session_01Gi3mgq`.

## Objective
P0: gate the diag-token emitter so a rotation cannot re-leak, then P1/P2 as the
operator redirected them — verify which symbols `alpaca_live` can actually trade
on a no-margin $200 account, and stamp provenance on the pairs sleeve's decided
closes.

## Tier
Tier-1 throughout **except** one Tier-3 change: `config/accounts.yaml::alpaca_live.risk`
(#10253), operator-directed in-session with all three values stated explicitly.
`mode:` was **not** touched — `alpaca_live` remains `dry_run`. The M22 pairs
provenance stamp (#10251) was Tier-2, operator-approved in-session.

## Starting Context
Inherited P0–P3 priority order. `BL-20260818-GET-DIAG-TOKEN-EMITS-SECRET-TO-PUBLIC-SURFACE`
open: the emitter had written a live bearer into a world-readable issue comment
(#1615, 2026-05-21) that still authorized three months later. Rotation was
blocked behind it — rotating through an ungated emitter re-leaks immediately.

## Repo State Checked
`main` at `44eeef93` at session start; ended at `beb15473` + PR #10254.
Concurrent `/system-review` session announced mid-session; board notice posted
naming `health-review-backlog.json` as the sole contention point.

## Files and Systems Inspected
`.github/workflows/{get,set}-diag-token.yml` · `src/units/strategies/pairs_executor.py`
· `src/units/accounts/risk.py` (`position_size`, `_ROUND_UP_BUDGET_MULT`,
`_MARGIN_SAFETY_BUFFER`, `evaluate`) · `config/accounts.yaml` ·
`config/instruments.yaml` · `ml/datasets/adapters/yf_symbols.py` ·
`.github/workflows/yfinance-lane-proof.yml` · live journal via the Data Explorer
over `https://ict-bot.duckdns.org` · `S-PROXY-EQUITIES-ALPACA-LIVE-2026-07-07.md`
· `S-M20-DISPERSION-ISOLATION-AND-QUEUE-2026-08-15.md`.

## Work Completed
- **#10251 (`c61d718`)** — `get-diag-token.yml` gates delivery on a three-state
  visibility verdict with `unknown` on the **refusing** side, requiring the event
  payload and a live `repos.get` to agree; `always()` removed from the artifact
  step; the failure comment branches on the actual failure **stage**.
  `set-diag-token.yml` reports a measured `rotated`/`unchanged`/`unknown_before`/`failed`
  verdict from a before/after fingerprint of what the VM **serves**. M22
  `pairs_executor._close_pair` stamps `exit_price_source`/`pnl_source` on its
  decided closes.
- **#10253 (`beb15473`)** — Tier-3: `alpaca_live.risk` `risk_pct 0.02→0.05`,
  `daily_loss_pct 0.05→0.10`, `max_dd_pct 0.05→0.10`.
- **#10254** — ticker map 24→74, batched `yfinance-lane-proof`, 51 tests, the
  affordability research doc, one backlog row.

## Validation Performed
- **P0 live-verified by dispatch, not by a green test run.** Run
  [32818860134](https://github.com/benbaichmankass/Metis-Insights/actions/runs/32818860134):
  `payload=false live=false → visibility_state: public → REFUSING TO DELIVER`,
  and critically **`Resolve token + write env block` was SKIPPED** — the refusal
  lands before the secret reaches the runner's disk at all.
- **Both P0 defects planted and confirmed caught** (collapse `unknown` into the
  permissive branch → 5 of 9 gate cases fail; make `unchanged` report `rotated`
  → the no-op case fails).
- **Affordability measured on a runner** (run
  [32828398224](https://github.com/benbaichmankass/Metis-Insights/actions/runs/32828398224)):
  53 requested / **51 sound** / 1 unsound (`SPLG`) / 1 no_data (`DDG`), then
  through `RiskManager.position_size` at the live config: **42 of 51 reachable**.
- Full guard suite green; 141 tests in the yfinance file (was 40), with a planted
  control on the not-tradeable assertion.

## Documentation Updated
`CLAUDE.md`, `docs/claude/diag-relay.md`, `docs/github-actions-workflows.md`
(diag-token delivery path — `get-diag-token` is no longer a delivery path on a
public repo); `docs/research/alpaca-proxy-signal-vs-order-symbol-2026-08-25.md`;
`docs/research/alpaca-200-affordability-sweep-2026-08-25.md`; ROADMAP entry;
this log.

## Contradictions or Drift Found
- **`S-PROXY-EQUITIES-ALPACA-LIVE-2026-07-07.md`'s "No QQQ proxy. Nasdaq-100 has
  no sub-$100 ETF" is prose that was quoted as a measurement for 7 weeks, and its
  operational conclusion is FALSE.** Nasdaq/tech sizes four ways today (`ONEQ`
  $102.40, `VGT` $116.42, `IGM` $156.61, `QQQJ` $45.64); Russell 2000 sizes as
  `VTWO` $120.47. The literal sub-claim was narrowly true (`QQQM` $290.81). Filed
  as `BL-20260825-PROXY-UNAFFORDABILITY-CLAIM-REFUTED-BY-MEASUREMENT`. The sprint
  log itself is **not** rewritten — it is the historical record; the backlog row
  is the correction.
- **`CLAUDE.md`'s diag-relay section described `get-diag-token` as the delivery
  path** while the repo has been public since 2026-07-07. Corrected.
- **Three wrong claims made BY THIS SESSION and caught by external checks, not by
  me** — recorded because the pattern matters more than the instances:
  (1) *"5% adds GDX"* — hand arithmetic (`budget / stop >= 1`) cannot see
  `_ROUND_UP_BUDGET_MULT = 1.5`; the sizer refuted it. GDX was already reachable
  at 2%, **overshooting its declared per-trade risk by 1.32×**.
  (2) *"QQQ and IWM have no cheap declared long proxy"* — `SCHA` is declared and
  is held off `alpaca_live` on **performance** grounds, not affordability.
  (3) *"XLK/XOP are risk-bound"* — they are **cash-bound against `0.9 × equity`**
  (`_MARGIN_SAFETY_BUFFER`), a $180.00 wall that `XLK` misses **by five cents**.

## Risks and Follow-Ups
- ⚠️ **The 2026-08-23 audit's four `alpaca_live` blockers stand and are LARGER
  than affordability** — read them before treating this session's sweep as
  unblocking anything: `shorting_enabled: False` against **60.0% short flow**;
  **7 of 15 legs exceed 100% of account notional at EVERY funding level**
  (sizing is scale-invariant, so money does not fix it) with **no
  `max_gross_exposure_pct` declared**; the paper record **net-negative except
  `uso_trend_1h`** (10 of 11 legs negative); and **both silent-failure detectors
  skip this account**. Funding arithmetic is secondary: **$2,500 admits all 15
  priced legs, $1,000 admits 14 of 15**.
- `daily_usd: 200` on a $200 account is 100% of it on the no-equity-snapshot
  path — flagged for the operator, deliberately unchanged.
- `scripts/prop/account_compat_matrix.py` is owed at the new 5% `risk_pct`.
- ⚠️ `DGZ` at 29 shares consumes **$9.69 of a $10.00 budget (4.8% of equity)**,
  ATR **6.96%** of price — 2–70× every other candidate.
- `DDG` returned `fetch_failed`; drop or correct the ticker.

## Deferred Items
- **M39(A) criterion 2 — UNEXERCISED, do not mark verified.** Needs a decided
  close with NO exit price; the post-fix cohort is **9 rows / 5 decided**.
  Baseline: **113/419 = 27.0%**.
- **P3 (TP venue-scope) — not started.** A `0.099` Bybit ErrCode-10001 boundary
  applied to legs on venues Bybit does not carry; 13 sites; Tier-3.
- The `DIAG_READ_TOKEN` rotation itself (operator — secret origination).
  `BL-20260818-DIAG-READ-TOKEN-PUBLIC-EXPOSURE-UNREMEDIATED` stays open; whether
  the leaked token still authorizes was **not** re-measured this session.
- `PROTECTION_REASSERT_MODE` and `target_extension_soak` both sit at `annotate`
  with nothing reading them back.

## Next Recommended Sprint
⚠️ **Corrected after the operator read the first version of this section: it
named a verification errand where the program has a LANE.** The narrow answer
("re-run the 2026-08-23 alpaca blocker set") is a task *inside* the real one and
must not be handed off as the whole of it.

**Continue [`docs/research/WORKPLAN-2026-08-14.md`](../research/WORKPLAN-2026-08-14.md)
Lane 0 — Live-capability integrity (P0, IN PROGRESS).** That doc supersedes
ROADMAP's "Next" table for the non-M20 track, and **M36** governs above it:
consolidate and integrate the open M25→M30 threads *before* opening new
frontiers. Do not start a new milestone.

Lane 0's done-condition is the frame this session's alpaca work belongs to:
*every `mode: live` account × enabled strategy leg is either (a) demonstrably
able to place an order, or (b) carries a filed row saying why not — **and a
standing check exists**.* That last clause is the lane's own stated real
deliverable (`scripts/ops/dead_leg_audit.py`).

**Lane 0 items re-verified live 2026-08-25 ~09:20Z — the doc is 11 days old and
two have MOVED, so read these, not the table:**

| item | doc | live now |
|---|---|---|
| **0.3** `alpaca_paper`/`alpaca_portfolio` `balance()` → None | queued | ⚠️ the **balance-SNAPSHOT** path is healthy ($83,947.32 / $99,240.45, `api_ok: true`, 6 open each, non-zero `delta_1h`) — but that is the **DB snapshot writer, NOT the sizing path's `balance()`**. `BL-20260813-ALPACA-BALANCE-NONE-WHILE-ACCOUNT-READS-ACTIVE` must **not** be closed on this surface alone. |
| **0.5** `ib_paper` MGC uPnL $119,490 vs ~$37.80 truth | queued | ⚠️ **not presenting** — MGC/MHG/MES all `uPnL: None`, `src: unavailable`, the honest degraded state. Fix vs transient IB read failure is **undetermined**. |
| **0.6** `breakout_1` prop status 25 d stale | queued | 🔴 **STILL OPEN** — 41.2 h stale, `freshness: stale`, `api_ok: false`. The $150 daily-loss / $4,700 DD guard computes off that snapshot. The one unambiguously live item. |

`alpaca_live` is Lane 0's done-condition **case study**, not a separate track:
affordability is measured and is NOT the blocker (42 of 51 size); the four real
blockers are in the 2026-08-23 ROADMAP row and are listed under "Risks and
Follow-Ups" above.

## Wrap-Up Check
Two PRs merged, one open with auto-merge armed on green. Board `START`,
merge-slot claims/releases, a concurrent-session heads-up, and `DONE` posted to
issue #6927. Backlog row appended via `scripts/ops/backlog_append.py` (12
insertions, 0 deletions — no reformat, so the concurrent `/system-review`'s
backlog work is not re-attributed). Nothing half-landed.
