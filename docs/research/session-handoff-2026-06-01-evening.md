# Session handoff — 2026-06-01 (evening — matrix complete, router design awaiting operator review)

> **Continues** `docs/research/session-handoff-2026-06-01-afternoon.md`. This
> session drained the remaining two matrix-coverage items
> (`PERF-20260601-004` and `PERF-20260601-003`); the regime × direction matrix
> is now decision-grade for every roster strategy. Three Tier-1 PRs shipped:
> #2572 (handoff + trend_donchian description), #2574 (htf_pullback row), and
> #2580 (vwap live-gated row + harness exit-side-gate flags). No live-path
> change this session.

## TL;DR

Live bot is unchanged at `1e886c6` (3a1958a-equivalent); `trend_donchian` is
LONG-ONLY (live), squeeze + fade + fvg + htf_pullback + vwap are SHADOW. The
regime router design (`docs/research/regime-router-design-2026-06-01.md`) has
its full policy table populated and is **awaiting operator review of its four
open questions** before phase-1 build.

## 1. What shipped this session

| PR | What | Tier | State |
|---|---|---|---|
| **#2574** | `scripts/backtest_pullback.py` (committed standalone harness mirroring `backtest_trend.py`) + htf_pullback regime row added to the matrix + router policy-table cells (long ON in trending/transitional, both off in chop) | 1 | **merged** |
| **#2580** | `src/backtest/run_backtest_vwap.py` exit-side selectivity gates (four new CLI flags + BE ratchet step + `_vwap_cross_gates_allow` helper mirroring the live `vwap._vwap_cross_gates_pass`) + vwap live-gated regime row + router policy-table cells (off in every regime, every direction) | 1 | **merged** |

`PERF-20260601-002` initiative step 1 (the evidence matrix) is now complete.

## 2. The matrix — where it stands now

Full doc: **`docs/research/regime-roster-matrix-2026-06-01.md`**.

| Strategy | TF | Net R | trending (L/S) | transitional (L/S) | chop (L/S) | maxDD |
|---|---|---:|---|---|---|---:|
| trend_donchian (live) | 1h | +10.9 | −5.7 (+22.3 / −28.0) | −2.3 (+21.7 / −24.1) | +18.9 (+3.3 / +15.6) | 20.6 |
| fade_breakout_4h (shadow) | 4h | +19.4 | — *(ADX-gated)* | +5.2 | +14.2 (long-led) | 30.2 |
| squeeze_breakout_4h (shadow) | 4h | +17.6 | +5.1 | +1.6 | +10.9 | **7.9** |
| fvg_range_15m (shadow) | 15m | −16.9 | — | — | −16.9 (loser both sides) | 13.2 |
| **htf_pullback_trend_2h** (shadow) | 2h | **+26.3** | **+30.1** (long owns it) | +8.4 | **−12.2** (loser) | 14.8 |
| **vwap** (shadow, live-gated) | 5m | **−10,724** | **−6,179** | −1,903 | −2,642 | n/a |

**Decision-grade for the whole roster.** No "permissive (`on`) default" cells
remain in the router policy table.

## 3. Two big structural takeaways this session

### htf_pullback is decisively trend-continuation
The strategy's edge is the **trending-long** cell (+30.1 R, 99 trades; the
short side is flat at −0.05 in trending). Transitional adds +8.4 R. Chop is
a loser both sides (−12.2 R) — same shape as fvg. The router gates it off
in chop, on in trending/transitional long-only. Confirms `PERF-20260531-002`'s
walk-forward (IS +32.7 / OOS +22.4 at tl=50; +26.3 here at the live tl=40).

### vwap with the live gates is STILL a net loser in every regime
Trainer ran the harness with `--min-r-for-vwap-cross 0.25
--min-hold-minutes-for-vwap-cross 10 --be-at-r 1.0 --be-offset-bps 15`
on `data/backtest_BTCUSDT_5m.csv` (647,585 5m bars, 2020-03 → 2026-05):

- **Trades: 40,650** (vs unfiltered 10,188 — the BE ratchet cycles trades
  faster via BE-stop exits, the cooldown shortens, trade count rises)
- **Win rate: 49.8%** (BE ratchet locks in many sub-fee small wins)
- **Gross: +3,399 R** — the gates DO work on the gross side; the
  "VWAP drifted to price at sub-fee R-capture" failure mode is real and
  they fix it
- **Fees: −14,123 R** — 4.2× gross
- **Net: −10,724 R** at exp ≈ −0.26 R/trade in every regime
- Per-regime: trending −6,179, transitional −1,903, chop −2,642
- Long and short bleed equally (−5,063 / −5,661)

Confirms `docs/audits/strategy-loss-drivers-2026-05-23.md` ("thin positive
gross edge that fees bury 4×") on the full multiyear archive with the precise
live-gate path; the prior `−3749 R unfiltered` row was directionally correct
at smaller magnitude. The router gates vwap off in every regime — the
loudest gate in the policy table.

### Scope finding — `recent_context_filter` is informational-only
While scoping the vwap re-run, surfaced that `recent_context_filter` (1h
24-bar) is explicitly informational-only in the live strategy:
`config/strategies.yaml` line 369 says "Informational only — does not block
entries", and `src/runtime/strategy_signal_builders.py` line 631 confirms
"neither side is blocked" (the filter labels the meta but the comparator
never refuses a signal). The afternoon handoff and the original
`PERF-20260601-003` framing both treated it as a selectivity filter; that
was inaccurate. The **three exit-side gates** (`min_r_for_vwap_cross`,
`min_hold_minutes_for_vwap_cross`, `be_at_r`) plus the **BE ratchet** are
the actual live difference, and that's what the harness now threads.

## 4. Backlog state

All in `docs/claude/performance-review-backlog.json`:

| id | Tier | Status | Note |
|---|---|---|---|
| `PERF-20260601-002` | 3 | open (note refreshed) | Initiative — step 1 (matrix) done; step 2 (router) awaiting operator review |
| `PERF-20260601-003` | 1 | **resolved** | vwap live-gated row landed |
| `PERF-20260601-004` | 1 | **resolved** | htf_pullback harness + row landed |
| `PERF-20260601-005` | 3 | open | squeeze re-promotion — operator-pre-approved, gate is post-#2548 multi-day window (only ~6h elapsed as of session close) |
| `PERF-20260531-002` | 3 | **resolved** | htf_pullback decision-graded |

New doc-freshness item logged:

| id | Tier | Where | What |
|---|---|---|---|
| `BL-20260601-002` | 3 | `config/strategies.yaml:318` | citation `docs/audits/vwap-viability-verdict-2026-05-23.md` doesn't exist; real source is `docs/audits/strategy-loss-drivers-2026-05-23.md`. Other propagated copies fixed this session; the YAML one is Tier-3 (operator-only) so it sits in the health backlog. |

## 5. EXACT next steps for the next session

In priority order:

1. **`PERF-20260601-002` step 2 — regime router phase-1 build, after operator
   reviews the design doc.** `docs/research/regime-router-design-2026-06-01.md`
   has its full policy table populated (every roster strategy × regime ×
   direction) and four open questions at the end:
   1. **Detector timeframe** — per-strategy TF vs one canonical (e.g. 1h)?
   2. **Gate vs weight first** — start with hard gates (mechanical, auditable)?
   3. **Keep / retire the strategy-level `long_only` flag** once the table covers trend_donchian's short cells?
   4. **Boundary hysteresis** — add dwell-time so the router doesn't thrash at the 20/25 ADX boundaries?

   After answers, phase 1 ships **`RegimeDetector`** + per-tick regime logging
   with **no enforcement** — just confirm the live regime stream matches the
   matrix's base rates (chop ~35% / transitional ~20% / trending ~45% — these
   are the bar-fraction numbers from the matrix's 5m archive; the 1h archive
   gave chop 30 / transitional 19 / trending 51). Tier-2 (new observability,
   no live-path mutation) — open as a PR, operator reviews, then deploy via
   `pull-and-deploy`.

2. **`PERF-20260601-005` — squeeze re-promotion check (operator-pre-approved,
   gated).** When the post-#2548 (debounce deployed 2026-06-01 09:59 UTC)
   window accrues multiple days, pull the orphan / `intent_noop` / same-bar
   re-entry rate via the diag relay, compare to the pre-debounce baseline.
   If the rate dropped meaningfully AND accrued shadow squeeze net-R isn't
   contradicting the +17.6 backtest, propose flipping
   `execution: shadow → live` for `squeeze_breakout_4h` at its current
   `risk_pct`. fade stays in shadow (held for the router/chop-gating given
   DD 30 R).

3. **Backlog drain.** If neither (1) nor (2) is ready, dig into the
   health/performance/ML backlogs.

## 6. How to operate (recurring reminders)

- **VM access is relay-only.** Trainer = `trainer-vm-diag-request` labelled
  issue with a `cmd:` block. Live VM reads = `vm-diag-request`. Live VM
  mutations/deploys = `system-action`. **One relay at a time — concurrency
  cancels.**
- **For long-running tasks (≥ ~5 min), use the detached runner + done-marker
  pattern.** This session #2575/#2576/#2577/#2578/#2579 demonstrated this end
  to end: the synchronous follow-up relay (#2576) was killed by an SSH-pipe
  drop at ~6 min (the failure mode the morning handoff flagged on #2566), but
  the `setsid + nohup` runner kept going independently. Three subsequent
  status pings (#2577/#2578/#2579) read the runner's log + done marker
  without disturbing it. The 22-min vwap run finished cleanly under this
  pattern.
- **Base64-encode long bash inside trainer-vm-diag bodies** to avoid
  heredoc/indent breakage (the workflow's awk parser preserves indent,
  but bash heredoc terminators don't tolerate leading spaces). #2575 used
  this; runner script ends up at `/tmp/vwap_gated_runner.sh` and is
  executable cleanly.
- **The trainer's venv python is `.venv/bin/python`** (pandas/numpy/ccxt);
  bare `python3` lacks them.
- **Tier-3** (`config/strategies.yaml`, `config/accounts.yaml`, `risk_caps`,
  order code) needs explicit operator approval; deploys
  (`pull-and-deploy`) restart `ict-trader-live` (positions persist, watchdog
  boot-grace covers it).

## 7. Relay trail for this session

#2573 (htf_pullback regime tag — quick synchronous), #2575 (vwap gated
detached kick-off), #2576 (poll relay killed by SSH drop), #2577 / #2578 /
#2579 (status pings on the detached runner — #2579 caught the done marker
+ full result).
