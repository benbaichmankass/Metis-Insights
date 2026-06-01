# Session handoff — 2026-06-01 (afternoon — regime-router groundwork)

> **Continues** `docs/research/session-handoff-2026-06-01.md` (the morning
> handoff). This session executed `PERF-20260601-002` end-to-end: produced the
> roster regime×direction matrix, brought three Tier-3 decisions to the
> operator, shipped one of them live, and parked the other two with concrete
> triggers.

## TL;DR

Live bot **HEAD = `3a1958a`**, `trend_donchian` is now **LONG-ONLY** (shipped
+ deployed + verified). All research, tooling, and decision artifacts are on
`main`.

## 1. What shipped this session

| PR | What | Tier | State |
|---|---|---|---|
| **#2561** | Roster regime×direction matrix doc + `regime_tag_emitted.py` (engine-agnostic tagger) + vwap `--emit-trades` + pandas-3.0 fixes (`_adx` float dtype, fvg `15m` resample) + regime-router design proposal + 3 backlog items | 1 | **merged** |
| **#2570** | `trend_donchian` LONG-ONLY (config + builder gate + 3 tests) | 3 | **merged + deployed + verified live** |

Live deploy verified clean: `ict-trader-live` active on `1e886c6`, pipeline
ticking, all units restarted (issue #2571).

## 2. The roster regime × direction matrix — the foundation everything else rests on

Full doc: **`docs/research/regime-roster-matrix-2026-06-01.md`** (with caveats,
method, reproduction). Driven with **exact live params** from
`config/strategies.yaml` (the `min_confidence`-gate reconciliation lesson),
tagged by ADX-14 regime at each trade's entry bar. BTC, 2021 → 2026.

| Strategy | TF | Net R | trending (L/S) | transitional (L/S) | chop (L/S) | maxDD |
|---|---|---:|---|---|---|---:|
| trend_donchian (live) | 1h | +10.9 | −5.7 (**+22.3** / **−28.0**) | −2.3 (+21.7 / −24.1) | **+18.9** (+3.3 / **+15.6**) | 20.6 |
| fade_breakout_4h (shadow) | 4h | **+19.4** | — *(ADX-gated)* | +5.2 | **+14.2** (long-led) | 30.2 |
| squeeze_breakout_4h (shadow) | 4h | **+17.6** | +5.1 | +1.6 | +10.9 | **7.9** |
| fvg_range_15m (shadow) | 15m | **−16.9** | — | — | −16.9 (loser both sides) | 13.2 |
| vwap (shadow) | 5m | ⚠️ −3749 | — | — | — | **NOT decision-grade** — unfiltered; see PERF-20260601-003 |

**Structural finding:** edges are complementary across regimes — trend-long
wins trending, the mean-reverters win chop. The short side of trend is
effectively a different strategy that only earns in chop. Both demoted
strategies (squeeze, fade) are clearly net-positive multiyear — the demotion
was a ranging-month + re-entry-storm artifact (storm fixed by #2548).

## 3. The three Tier-3 decisions — current state

### Decision 1: trend_donchian direction — **SHIPPED LIVE** ✅
Operator chose **long-only** (drops the −37 R short drag, keeps the +47 R
long edge). Shipped as #2570 (config + opt-in `long_only` flag in the
builder + 3 tests), merged to `main` (`1e886c6`), deployed via
`pull-and-deploy` (#2571), `ict-trader-live` healthy. The chop-only +16 R
trend-short will be reclaimed later as a cell of the regime router (not by
special-casing the strategy).

### Decision 2: squeeze re-promotion — **PRE-APPROVED, GATED** ⏸
Operator pre-approved promoting `squeeze_breakout_4h` shadow→live (it's the
strongest re-promotion candidate: +17.6 R, net-positive every regime,
lowest DD). **Gated on verifying #2548 (bar-close debounce, deployed
2026-06-01 09:59 UTC) reduced the orphan/`intent_noop`/re-entry rate vs
baseline** — `BL-20260601-001`. As of this session there was only ~2.6 h of
post-#2548 data — far too short for a meaningful before/after, so the
re-promotion is parked as `PERF-20260601-005` with the exact trigger.
fade is held for the router/chop-gating given its 30 R DD.

### Decision 3: regime router — **DESIGN PROPOSAL DELIVERED** 📐
**`docs/research/regime-router-design-2026-06-01.md`**. Concrete design
(operator chose "design first"): RegimeDetector (one source of truth) +
declarative `config/regime_policy.yaml` table seeded from the matrix +
enforcement in `aggregate_intents` as phase-1 hard gates → phase-2 soft
weights. Subsumes `trend_donchian`'s `long_only` and fade/fvg's hardcoded
ADX gates into one policy. Four open questions for the operator at the
end. Awaiting decision.

## 4. Backlog state — what feeds the next session

All in `docs/claude/performance-review-backlog.json`:

| id | Tier | Status | Trigger |
|---|---|---|---|
| `PERF-20260601-002` | 3 | open (note appended) | Original regime-router initiative item; updated with this session's progress + pointers |
| `PERF-20260601-003` | 1 | open | Re-run vwap regime matrix with its LIVE selectivity params (current run was unfiltered → not decision-grade) |
| `PERF-20260601-004` | 1 | open | Commit/port a standalone `htf_pullback_trend_2h` harness so it joins the matrix |
| `PERF-20260601-005` | 3 | open | **squeeze re-promotion** — operator-pre-approved, fires after the post-#2548 window accrues (multi-day) and `BL-20260601-001` is verified |

`BL-20260601-001` (orphan/re-entry rate verify) stays in
`docs/claude/health-review-backlog.json` — same gate.

## 5. Doc-freshness sweep (session-end per CLAUDE.md)

- `config/strategy_descriptions.json::trend_donchian` was stale (claimed 2h /
  trail 3.5 / "1h whipsawed" / two-sided) — **updated to** 1h / trail 5.0 /
  long-only, with a pointer to the matrix + router design doc.
- `CLAUDE.md`, `docs/ARCHITECTURE-CANONICAL.md`, `docs/CLAUDE-RULES-CANONICAL.md`,
  `ROADMAP.md` — no stale `trend_donchian` directionality claims (grep clean).

## 6. EXACT next steps for the next session

In priority order:

1. **`PERF-20260601-005` (squeeze re-promotion) — first thing to check.** If the
   post-#2548 window is now ≥ a few days, pull the orphan / `intent_noop` /
   same-bar re-entry rate via the diag relay, compare to the pre-debounce
   baseline. If the rate dropped meaningfully, propose flipping `execution:
   shadow → live` for `squeeze_breakout_4h` (Tier-3 PR, current `risk_pct`).
   `BL-20260601-001` is the same gate.
2. **`PERF-20260601-003` — re-run vwap with its live selectivity params**
   (`recent_context_filter` 1h/24-bar, `threshold: 0.01`,
   `min_r_for_vwap_cross`, `be_at_r`). The current vwap row of the matrix is
   the unfiltered harness (~11 trades/day, −3749 R) — useless for routing.
   Thread the live gates into `src/backtest/run_backtest_vwap.py`, re-tag, drop
   the row into the matrix.
3. **`PERF-20260601-004` — commit/port the standalone `htf_pullback` harness**
   so `htf_pullback_trend_2h` joins the matrix (the overnight `backtest_pullback.py`
   was never committed). Same emit-trades JSONL schema; drive with live params
   (`trend_lookback 40, pullback_lookback 10, pullback_frac 0.5, trail 5.0`).
4. **Regime router (initiative step 2 build) — operator review of
   `docs/research/regime-router-design-2026-06-01.md`**, then ship phase 1
   (RegimeDetector + observability, no enforcement). Open questions in the
   doc: detector timeframe (per-strategy vs canonical), gate-vs-weight first,
   keep/retire the `long_only` flag once the table exists, boundary
   hysteresis.

## 7. How to operate (recurring reminders)

- **VM access is relay-only.** Trainer = `trainer-vm-diag-request` labelled
  issue with a `cmd:` block (base64 long bash to avoid heredoc/indent
  breakage; **one relay at a time** — concurrency cancels). Live VM reads =
  `vm-diag-request`. Live VM mutations/deploys = `system-action`.
- **vwap (and any long task) is too slow for synchronous relays.** Use a
  **detached** runner (`setsid bash runner.sh >/tmp/log 2>&1 </dev/null &`)
  that writes a done-marker file; a follow-up relay reads the result. This
  session's #2569 used that pattern after #2563 was preempted at ~10 min.
- **The trainer's venv python is `.venv/bin/python`** (pandas/numpy/ccxt);
  bare `python3` lacks them. Trainer may be on a different branch — use
  `git worktree add --detach $WT origin/<branch>` to run the tooling
  branch's code without disturbing the trainer's checkout.
- **Tier-3 (strategies.yaml / accounts.yaml / risk / order code)** needs
  explicit operator approval; deploys (`pull-and-deploy`) restart
  `ict-trader-live` (positions persist, watchdog boot-grace covers it).

## 8. Relay trail for this session

#2562 (discovery), #2564 (trend/fade/squeeze in one batch), #2565 (fvg
re-run after pandas-3.0 fixes), #2567 (vwap detached launch — #2563 was
preempted, #2566 broke SSH pipe), #2568 (vwap progress check), #2569
(vwap bypass-tag), #2571 (`pull-and-deploy`).
