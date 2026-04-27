# ICT Bot Sprint Plan — Live Trading Hardening + Repo Cleanup

**Sprint start:** 2026-04-28
**Owner:** Ben Baichman-Kass
**Project:** [the-lizardking/ict-trading-bot](https://github.com/the-lizardking/ict-trading-bot)
**Previous sprint:** [`sprint-plan-2026-04-27.md`](https://github.com/the-lizardking/ict-trading-bot/tree/main/docs/sprint-plans) — VWAP Stabilization (completed)

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
- Gemini-in-Colab — ICT research notebook with vectorized backtest

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

### M7 — ICT runtime strategy decision
**Owner:** Ben + Gemini-in-Colab (research) → Claude (implementation if go)
**Why now:** `src/ict_detection/` library exists but is unused at runtime. Decide: use it or drop it.

**Phase 1 — Research (Gemini-in-Colab):**
- Use research notebook (currently being built) to backtest ICT-driven entries on 5+ random datasets
- Compare against `breakout_confirmation` on same data
- Document go/no-go in a follow-up to the audit doc

**Phase 2 — Implementation (only if Phase 1 says go, Claude):**
- New `strategies/ict_signal_builder.py` consuming `src/ict_detection/` modules
- New dry-run staging service mirroring VWAP's pattern
- Promote to live only after dry-run validates

**Done when:** Either documented no-go, or new ICT dry-run staging service running cleanly.

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
  ├─ Gemini-in-Colab   : M7 Phase 1 research (parallel)
  └─ Colab/Oracle      : M5 VWAP smoke test

Phase 3:
  ├─ Claude            : M4 cleanup PRs (after risk caps in)
  └─ Gemini-in-Colab   : Research notebook v2

Phase 4:
  ├─ Claude            : M6 VWAP graduation (after M3 + M5)
  └─ Claude            : M7 Phase 2 ICT runtime (only if research says go)

Phase 5:
  └─ Colab + Claude    : M8 HF dataset push
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
- **M6 and M7 should NOT happen simultaneously.** Two new live strategies in one sprint is too much variance. Do M6 first, observe a week, then consider M7 if both go-criteria are met.
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
