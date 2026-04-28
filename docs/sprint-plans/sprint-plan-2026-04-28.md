# ICT Bot Sprint Plan — Live Trading Hardening + Repo Cleanup

**Sprint start:** 2026-04-28
**Owner:** Ben Baichman-Kass
**Project:** [the-lizardking/ict-trading-bot](https://github.com/the-lizardking/ict-trading-bot)
**Previous sprint:** [`sprint-plan-2026-04-27.md`](https://github.com/the-lizardking/ict-trading-bot/tree/main/docs/sprint-plans) — VWAP Stabilization (completed)

---

## Sprint Checkpoint

Resume state for Claude Code is tracked in
[`docs/claude/checkpoints/CHECKPOINT_LOG.md`](../claude/checkpoints/CHECKPOINT_LOG.md).
Rules: [`docs/claude/checkpoint-workflow.md`](../claude/checkpoint-workflow.md).

- **Current sprint phase:** Phase 0 — workflow scaffolding complete, backlog not yet started.
- **Last completed checkpoint:** `CP-2026-04-28-00` (workflow scaffolding).
- **Next checkpoint:** `CP-2026-04-28-01` — begin **M1 Auto-deploy timer verification**.
- **Blocked:** none.

Do not restart from M1 every session — read the checkpoint log first.

---

## Sprint Theme

Last sprint stabilized the trading runtime and repaired the deployment pipeline. This sprint converts that working infrastructure into **production-grade live trading** (risk caps, kill-switch, multi-strategy support) and trims technical debt that accumulated during the firefighting phase.

---

## Status entering this sprint

✅ **Completed last sprint (PRs 21–26):**
- VWAP zero-volume candles → no-trade safe handling
- Telegram bot token redaction in logs
- Bybit dry-run vs. live runtime logging clarified
- Broken `ict-git-sync.service` (46k+ failed restarts) repaired
- `deploy_pull_restart.sh` rewritten for correct VM environment
- Live trader resynced from 38 commits behind → current main
- Zombie cron job removed from VM

⏳ **In flight:**
- Claude PR — `ict-git-sync.timer` (auto-deploy schedule, sprint M1 below)
- Claude PR — sprint planning audit (sprint M2 below)

✅ **Just completed (research phase):**
- Gemini-in-Colab ICT research notebook — first run produced viable signal:
  - **FVG detection:** 160 events on QQQ 5m (105 bullish, 55 bearish) at 0.01% min gap
  - **Backtest:** 13 trades, 61.5% win rate, +0.85 avg R, +11.4% return, -2.0% max DD (~5.7x return/DD)
  - **OB detection:** 0 events — `OB_BODY_THRESHOLD=1.5` is too strict, needs tuning
  - **Caveat:** Single asset/timeframe, naive entry logic (any FVG = enter), 13 trades is thin
  - **Verdict:** Pipeline works end-to-end and signal has positive expectancy → green light to port to repo and validate at scale
  - Artifacts saved to `MyDrive/ict-bot-research/colab-reports/`

🔴 **Operational reality check:**
The bot is **already live trading on Bybit mainnet** (`DRY_RUN=False`, `ALLOW_LIVE_TRADING=True`). No documented risk caps, no kill-switch, no hard-coded position ceiling. **This is the single highest priority of the sprint.**

---

## Sprint Guardrails

Do **not** do these without explicit approval:
- Do not reset the VM
- Do not stop the existing live trader
- Do not overwrite `/home/ubuntu/ict-trading-bot`
- Do not paste secrets into Claude, Gemini, GitHub, notebooks, or chat
- Do not run long training/backtests inside Claude Code
- Do not promote any new strategy from dry-run to live without documented risk caps in place first

---

## Default Execution Model

| Tool | Job |
|---|---|
| **Claude Code** | Small focused repo PRs, tests, docs, safety gates |
| **Gemini-in-Colab** | Notebook generation + iteration (replacing Studio for this workflow) |
| **Colab** | SSH cells against the VM, artifact pulls, notebook runs |
| **Oracle VM** | Live trader + VWAP dry-run staging |
| **Hugging Face** | Dataset publishing once research artifacts are ready |

Optimization rule: maximize free compute (Gemini, Colab, HF) for analysis and research. Reserve paid compute (Claude) for repo PRs, tests, and design decisions.

---

## Milestones

### M1 — Auto-deploy timer verification
**Owner:** Colab + Ben
**Depends on:** Claude's pending timer PR (in queue)
**Why first:** Every milestone below benefits from hands-off auto-deploy.

Steps after timer PR merges:
1. Manual one-time pull on VM
2. Install timer per the PR's `deployment-ops.md` instructions
3. Wait 10 minutes, verify auto-fire in journal
4. Confirm a no-op docs PR appears on the VM within ≤5 minutes hands-off

**Done when:** Timer is enabled, one auto-fire is visible in `journalctl -u ict-git-sync.service`.

---

### M2 — Sprint audit (Claude)
**Owner:** Claude Code (in progress as of sprint start)
**Why critical:** Risk-cap and cleanup work below depend on knowing actual repo state. Audit produces evidence base + ordered PR sequence.

Output: `docs/sprint-plans/2026-04-28-audit.md` with:
- **Section 1:** Live trading posture — order placement path, sizing logic, risk guards (or absence), strategy multiplexing, ICT detection library wiring
- **Section 2:** Repo hygiene — duplicate backtesters, duplicate telegram bots, stale services, orphaned strategies, test coverage gaps
- **Section 3:** Recommended ordered PR sequence (5–8 small focused PRs)

Every claim in the audit must reference a file path + line number + commit SHA.

**Done when:** Audit doc merged, sequence is concrete enough that future Claude sessions execute each PR independently.

---

### M3 — Risk management foundation 🔴 NON-NEGOTIABLE
**Owner:** Claude Code
**Why first after audit:** Live mainnet trading without documented hard caps is the single highest operational risk in the project right now.

Likely PR sequence (final scope determined by audit):

**M3a:** Add config-level guards (`MAX_POSITION_USD`, `MAX_DAILY_LOSS_USD`, `MAX_OPEN_POSITIONS`)
- Defined in `config/master-secrets.template.yaml`
- Enforced in `src/runtime/orders.py` at `place_order()`
- Hard refusal (raise/abort), no soft warning

**M3b:** Telegram kill-switch
- Slash commands: `/halt`, `/resume`, `/status`
- `/halt` writes a runtime flag (file or in-memory) that pauses order placement on next tick
- `/resume` clears the flag
- `/status` reports current flag state, today's P&L, open positions

**M3c:** Tests proving refusal
- Unit test: `place_order` refuses when over `MAX_POSITION_USD`
- Unit test: refusal when daily-loss exceeded
- Unit test: refusal when halt flag set

**Done when:** All three PRs merged, deployed via timer, tests prove refusal at order layer.

---

### M4 — Repo hygiene (Claude, sequence determined by audit)
**Owner:** Claude Code

Likely targets (final list per audit):

**M4a:** Resolve duplicate backtester (`src/backtester.py` vs `src/backtest/backtester.py`)
**M4b:** Delete unused `src/bot/telegramquerybot.py` (canonical is `telegram_query_bot.py`)
**M4c:** Resolve `src/strategies_manager.py` (540 bytes — flesh out as registry, or delete)
**M4d:** Remove stale `deploy/ict-bot.service` and `config/fly.toml` if confirmed dead
**M4e:** Add tests for `turtle_soup_mtf_v1.py` and untested `src/ict_detection/` modules

Each PR is small (one concern per PR), auto-deploys via timer.

**Done when:** No duplicate logic in src/, all systemd unit files map to live services, test coverage gaps documented or closed.

---

### M5 — VWAP staging redeploy + smoke test
**Owner:** Colab + Ben
**Depends on:** M1
**Why this sprint:** Carries over from last sprint's deferred Milestone 6. Validates the VWAP no-trade fix in continuous real-data conditions.

Steps:
1. Re-render `vwap_btcusd_dryrun` env from encrypted Drive secrets
2. Pull main into staging checkout (`/home/ubuntu/ict-trading-bot-vwap-staging`)
3. One-shot smoke test (`LOOP=false`) → expect `simulated` order or `no_trade`
4. If clean: start `ict-vwap-dry-run.service` for 2–3 cycles
5. Verify zero-volume candles produce `no_trade`, not exceptions
6. Verify no Telegram token URL appears in any journal

**Done when:** VWAP dry-run runs ≥2 cycles cleanly, no token leakage, live trader untouched throughout.

---

### M6 — VWAP graduation to live
**Owner:** Claude Code (PR) + Ben (deploy)
**Depends on:** M3 risk caps in place AND M5 dry-run validated
**Why now:** With safety-tested VWAP and risk caps live, this is incremental strategy diversity — not new risk exposure.

Tasks:
- Strategy multiplexing in `src/runtime/pipeline.py` (run `breakout_confirmation` and `vwap` simultaneously, or selectable per env)
- Per-strategy position sizing so VWAP doesn't compound on top of breakout exposure
- Test coverage for the multi-strategy code path

**Done when:** Live trader runs configured strategies in parallel, risk caps respected across all of them.

---

### M7 — ICT strategy port + multi-symbol validation
**Owner:** Claude (port) + Gemini-in-Colab (validation runs) → Claude (live wiring if go)
**Why now:** Phase 1 research done — FVG signal showed positive expectancy on QQQ 5m. Bottleneck is no longer Colab iteration; it's getting the logic into the repo where it can be tested systematically with CI and run across multiple symbols/timeframes.

**Phase 1 — COMPLETE (Gemini-in-Colab):**
- ✅ FVG detection + vectorized backtest engine working
- ✅ Positive expectancy on first run (61.5% WR, +0.85R avg, 13 trades)
- ⚠️ OB detection inactive (threshold too strict)
- ⚠️ Single-symbol sample is too thin for live promotion

**Phase 2 — Repo port (Claude):**
- Port FVG detection + backtest harness from research notebook into `src/ict_detection/` (use existing module — currently unused at runtime)
- Add backtest CLI entry point so the harness can be re-run from CI on multiple symbol/timeframe pairs
- Lower `OB_BODY_THRESHOLD` from 1.5 toward ~0.6–0.8 and verify OB detection produces non-zero events
- Add basic confluence filters: session-time gate, higher-timeframe trend filter
- Tests for FVG detector, OB detector, and entry-filter logic

**Phase 3 — Multi-symbol validation (Gemini-in-Colab using ported harness):**
- Run on 5–10 symbol/timeframe pairs (BTC, ETH, EURUSD, GBPUSD, GC, CL, SPY, QQQ across 5m / 15m / 1h)
- Target: ≥50 trades total with consistent positive expectancy before considering live
- Document results as `docs/sprint-plans/2026-04-28-ict-validation.md`

**Phase 4 — Dry-run staging (only if Phase 3 confirms edge holds, Claude):**
- New `strategies/ict_signal_builder.py` wired into pipeline
- New `ict-dry-run.service` mirroring VWAP staging pattern
- Live promotion gated behind: M3 risk caps active + ≥50 trades validated + ≥2 weeks dry-run clean

**Done when:** Either FVG/OB logic ported and validated across multiple assets with documented dry-run staging, OR documented decision to drop based on broader-sample backtest evidence.

**Explicitly out of scope this sprint:** Live promotion of the ICT strategy on Bybit. That requires the 50+ trade validation gate to pass first. (Note: there is no paper-trading mode in this repo — see CP-2026-04-28-16 through CP-2026-04-28-19. The validation gate is met via dry-run runs on a small live account.)

---

### M8 — Hugging Face dataset push
**Owner:** Claude (PR) + Ben (Colab to run)
**Why now:** PR 20 added the HF workflow. This sprint's research notebook produces the first publishable artifacts. Closes the loop on the research → datasets pipeline.

Tasks:
- Define dataset schema `ict_backtest_results_v1`
- Run `notebooks/templates/hf_dataset_push.ipynb` with research notebook v2 outputs
- Document the workflow in `docs/claude/huggingface-workflows.md`

**Done when:** At least one dataset published to Hugging Face under Ben's namespace.

---

## Parallel Execution Plan

```
Phase 1 (immediate):
  ├─ Oracle/Colab : M1 timer verification
  └─ Claude       : M2 audit

Phase 2 (after audit):
  ├─ Claude            : M3 risk guards (sequential PRs)
  ├─ Claude            : M7 Phase 2 — port FVG/OB detection into repo
  └─ Colab/Oracle      : M5 VWAP smoke test

Phase 3:
  ├─ Claude            : M4 cleanup PRs (after risk caps in)
  └─ Gemini-in-Colab   : M7 Phase 3 — multi-symbol validation runs

Phase 4:
  ├─ Claude            : M6 VWAP graduation (after M3 + M5)
  └─ Claude            : M7 Phase 4 ICT dry-run staging (only if validation passes)

Phase 5:
  └─ Colab + Claude    : M8 HF dataset push (publish ICT validation dataset)
```

---

## Definition of Done

- ✅ Auto-deploy timer running, validated by hands-off PR appearance
- ✅ Audit doc exists with concrete PR sequence
- ✅ Risk caps enforced at order layer, test-proven
- ✅ Telegram kill-switch operational
- ✅ No duplicate files in `src/`, no orphan systemd units
- ✅ VWAP either running live alongside breakout, OR documented as not-yet-ready with clear go-criteria
- ✅ ICT runtime strategy: implemented OR formally rejected (no longer in limbo)
- ✅ At least one HF dataset published
- ✅ Live trader uptime maintained throughout — no unplanned outages

---

## Notes / Risks

- **M3 is non-negotiable urgency.** Live mainnet trading without documented risk caps is the single highest operational risk surface. Do not let M4 or M5 leapfrog M3.
- **M6 and M7 should NOT graduate to live simultaneously.** Two new live strategies in one sprint is too much variance. Do M6 first, observe a week, then consider M7 live promotion if both go-criteria are met. (M7 port + dry-run staging in parallel with M6 is fine — only live promotion is serialized.)
- **M7 live promotion is gated behind ≥50 validated trades.** First-run research had only 13 trades on a single symbol. Sample size must grow via dry-run runs on a small live Bybit account before flipping to real order placement. (There is no paper-trading mode — see CP-2026-04-28-16→19.)
- **Audit findings may reorder this plan.** Treat M3–M8 as best-guess sequencing; M2's output is authoritative.
- **`turtle_soup_mtf_v1.py`** (15kB) was untouched last sprint with no tests — flagged for M4e.
- **`config/fly.toml`** suggests an abandoned Fly.io deploy — flagged for M4d audit.
- **Auto-deploy validation** requires the timer PR (M1). Until M1 is done, every merged PR still requires a manual `systemctl restart ict-git-sync.service` to deploy.

---

## File Conventions

- Sprint plans live at `docs/sprint-plans/sprint-plan-YYYY-MM-DD.md` in the repo
- Mirrored in this Space for cross-session continuity
- Audit docs use `YYYY-MM-DD-audit.md` suffix when produced mid-sprint
- Old sprint plans are kept in the repo for historical reference, not deleted
