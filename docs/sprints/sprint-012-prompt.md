# Sprint S-012 — Production Wiring Audit & Full Live Activation

> **Sprint type:** All-night, slow-and-thorough audit + remediation.
> **Owner:** Claude Code (autonomous).
> **PM:** Ben.
> **Tech Lead:** Perplexity (this prompt).
> **Created:** 2026-04-29.
> **Non-negotiable goal:** Every component that exists in the repo today must be production-ready and running fully live (no dry-run, no orphan service references, no contradictory configs). Strategy roster reduced to **turtle_soup** and **vwap** only — nothing else runs in production after this sprint.

---

## Why this sprint exists (context for Claude)

The PM ran a Telegram start-all-services command tonight and got:

```
✅ ict-trader-live started. Status: active
❌ Failed to start ict-trader-bak: Unit ict-trader-bak.service not found.
❌ Failed to start ict-trader-example: Unit ict-trader-example.service not found.
```

That output proves we have **architectural drift** between what the configs say should run, what systemd unit files exist on disk, and what the live VM actually runs. The repo has accumulated four sprints of feature work (S-008 through S-011) on top of a foundation that was never fully wired. Before any new feature work, every existing piece must be coherent and production-live.

The PM's **explicit current intent**: the only strategies in the repo should be **turtle_soup** and **vwap**. Anything else (`breakout_confirmation`, `killzone`, `ict`, `breakout`, etc.) must either be removed or, if recently added intentionally, escalated for PM decision before deletion. Default action: **remove**, unless evidence shows the PM merged it deliberately in S-008+ and the PM confirms keep.

---

## Confirmed evidence of drift (from tech-lead audit, 2026-04-29)

Use this as your starting evidence base — verify each item before acting.

1. **Strategies referenced but no systemd unit file exists in `deploy/`:**
   - `config/strategies.yaml` declares services `ict-trader-breakout`, `ict-trader-vwap`, `ict-trader-ict`. None of these `.service` files exist in `deploy/`. Only `deploy/ict-trader-live.service` exists.
2. **Two parallel strategy roots:**
   - Legacy `strategies/` (repo root): `breakout_confirmation.py`, `vwap_signal_builder.py`, `turtle_soup_mtf_v1.py`.
   - Modern `src/units/strategies/`: `breakout_confirmation.py`, `vwap.py`, `ict.py`, `killzone.py`, `_base.py`. **No `turtle_soup.py`.**
   - Plus `src/runtime/strategies/ict.py`. Three strategy directories.
3. **Two strategy registries:**
   - `src/strategy_registry.py` (YAML-driven, used by S-007+).
   - `src/strategies_manager.py` (in-memory dict, imports from legacy `strategies/` root).
4. **`turtle_soup` is nowhere in the production wiring.** It exists as a 15 KB legacy file in `strategies/` but is not referenced by `config/strategies.yaml`, `config/units.yaml`, `src/units/strategies/`, or any service file. The PM expects it to be a live production strategy.
5. **Stale shell scripts:**
   - `run_trader.sh` calls `python -m src.core.automated_trading_loop` — the live systemd unit calls `python -m src.main`. Different entrypoints.
   - `check_bots.sh` greps for `automated_trading_loop.py` — also wrong process name.
6. **Phantom service names referenced somewhere on the VM:**
   - `ict-trader-bak` and `ict-trader-example` are NOT in any current YAML, but the Telegram bot tried to start them. Source unknown — investigate. Likely a stale state file, an old hardcoded list in a Telegram handler, or a leftover alias.
7. **DRY_RUN code paths are still pervasive** in `src/runtime/pipeline.py`, `src/runtime/orders.py`, `src/main.py`. PM goal is "no dry run" for any in-scope strategy.
8. **`config/units.yaml` and `config/strategies.yaml` disagree** with PM intent (both list ict + vwap + breakout + killzone instead of turtle_soup + vwap).
9. **`src/main.py` runs a single pipeline tick** — there is no actual multi-service multiplexing, despite the registry implying one systemd service per strategy. Either the architecture is "one process, multi-strategy in pipeline" or "one service per strategy" — pick one and make it consistent end-to-end.

---

## Sprint scope (in)

- **Phase A — Audit & Decision Doc (no code changes):** produce evidence-based audit doc and a PM-decision request listing each ambiguity.
- **Phase B — Reconcile configs:** strategy roster reduced to turtle_soup + vwap across every config, registry, and code path.
- **Phase C — Reconcile code:** one strategy directory, one registry, one entrypoint. Turtle Soup ported into the modern strategies layout. Legacy duplicates deleted.
- **Phase D — Reconcile services:** systemd unit files match the actual chosen architecture (single multi-strategy process OR per-strategy services — pick one and make it the only truth). Phantom `ict-trader-bak` / `ict-trader-example` source identified and removed.
- **Phase E — Live mode hardening:** dry-run code paths removed or hard-disabled for in-scope strategies. Risk caps enforced. Live-trading guard preserved.
- **Phase F — Verification:** full test suite, secret scan, deploy-readiness check, smoke test on VM (controlled — see guardrails).

## Sprint scope (out)

- New strategies, new symbols, new exchanges, new ML models, prop accounts, mobile app, or any S-013+ work.
- Changing the actual order-placement logic in `src/runtime/orders.py` beyond removing dry-run branches and verifying caps fire.
- Touching `master-secrets.template.yaml` semantics.

---

## Guardrails (HARD STOPS)

1. **Do NOT stop the live trader (`ict-trader-live.service`)** at any point. Live trading must continue uninterrupted unless the PM explicitly approves a maintenance window in a separate exchange.
2. **Do NOT delete files in Phase A or B without writing the deletion list to the audit doc and getting it self-approved per the criteria below.** Self-approval is allowed when:
   - the file is a strict duplicate (same logic, lower test coverage, not imported by any current service entrypoint), OR
   - the file references a service name that is not in the final reconciled `config/strategies.yaml` and the PM intent (turtle_soup + vwap) is unambiguous.
   Anything else: leave it, document it under "PM decisions needed" in the audit doc.
3. **Do NOT change live order-placement logic** beyond removing dry-run branches. If a refactor is needed for caps to fire correctly, write the proposed diff into the audit doc and stop for PM review.
4. **Do NOT promote turtle_soup to live in this sprint** unless: it has unit tests, a backtest harness entry, and the existing risk caps (`max_dd_pct`, `daily_usd`, `pos_size` from `accounts.yaml`) demonstrably refuse oversized orders for it. If those gates are not met, leave turtle_soup in dry-run mode and flag for the next sprint.
5. **Do NOT paste secrets into chat, PRs, or commit messages.** Use env-var references only.
6. **PR size limit:** one concern per PR, ≤ 400 LOC diff per PR (excluding generated/lock files). Self-merge per the existing `CLAUDE.md` rules.
7. **Commit cadence:** target one PR per ordered task below. If a task naturally splits, split it.
8. **Time pacing:** this is an all-night sprint. Prefer slow-and-correct over fast-and-broken. Pause and re-audit after every two PRs.

---

## Phase A — Audit (do this first, single PR)

Produce **`docs/audit/sprint-012-wiring-audit.md`** with the following sections. Every claim must cite a `path/to/file.py:LINE` reference and, where relevant, the commit SHA that introduced the issue.

1. **Strategy inventory** — every `*.py` file that defines a strategy class or signal generator, across `strategies/`, `src/units/strategies/`, `src/runtime/strategies/`, and anywhere else. For each: imported by? referenced by config? has tests? last commit SHA touching it?
2. **Registry inventory** — every place strategies are registered or enumerated. Identify the canonical one and the duplicates.
3. **Service-to-config mapping** — table of (config name in `strategies.yaml`) × (config name in `units.yaml`) × (`.service` file in `deploy/`) × (actually running on VM, if knowable from logs/heartbeat). Highlight every mismatch.
4. **Phantom service investigation** — find where `ict-trader-bak` and `ict-trader-example` come from. Search the repo, git history, and any state files referenced by the Telegram start-services command. Document.
5. **Entrypoint reconciliation** — list every script/unit that claims to start the bot. Identify the canonical entrypoint and every stale alternative (`run_trader.sh`, `check_bots.sh`, etc.).
6. **Dry-run surface area** — every file/branch where `DRY_RUN`, `ALLOW_LIVE_TRADING`, `dry_run`, `paper`, or `simulate` flags are read or branched on. Classify each as (a) keep — safety guard, (b) remove — orphan path, (c) PM decision needed.
7. **Risk-cap enforcement audit** — trace from `config/accounts.yaml` → `RiskManager` → order placement. Confirm caps fire for **both** turtle_soup and vwap when oversized orders are simulated in tests. List any gaps.
8. **PM decisions needed** — explicit list of ambiguities that block reconciliation. Each entry: ambiguity, options, recommendation, blast radius.
9. **Recommended PR sequence** — ordered list of PRs for Phases B–F. Each PR: title, files touched, acceptance criteria, dependencies on prior PRs.

**Audit PR acceptance criteria:**
- Doc exists at `docs/audit/sprint-012-wiring-audit.md`.
- Every section above is present and non-empty.
- PR sequence is concrete enough that another Claude session could execute each PR independently.
- PM decisions list is empty OR contains items that genuinely require human judgement (not items Claude could decide).

After Audit PR merges, Telegram post: `/sprintlet_status S-012 audit merged, beginning reconciliation`.

---

## Phase B — Config reconciliation (small PRs)

**B1.** Rewrite `config/strategies.yaml` to contain ONLY `turtle_soup` and `vwap`. Both `enabled: true`. Both with explicit `risk_pct`, `timeframe`, `symbols`, and any strategy-specific params turtle_soup needs (port from `strategies/turtle_soup_mtf_v1.py`). VWAP keeps current threshold. Service names: pick the architecture (see Phase D first if needed).

**B2.** Rewrite `config/units.yaml` strategies section to match B1 exactly.

**B3.** Update `config/accounts.yaml` so the live account's `strategies:` list = `[turtle_soup, vwap]`. Confirm risk caps (`max_dd_pct`, `daily_usd`, `pos_size`) are present and non-zero.

**B4.** Update every test file that hard-codes the old roster (`ict`, `breakout_confirmation`, `killzone`) to use the new roster — **only** in tests that assert against production roster. Tests asserting registry/parsing behaviour with synthetic YAML fixtures should keep their fixtures unchanged.

Acceptance: `pytest --collect-only` passes; the only roster-asserting tests now expect turtle_soup + vwap.

---

## Phase C — Code reconciliation (small PRs)

**C1.** Port `strategies/turtle_soup_mtf_v1.py` into `src/units/strategies/turtle_soup.py`, conforming to the `_base.py` interface used by `vwap.py` / `ict.py`. Keep the original file in place until C5.

**C2.** Add unit tests for `src/units/strategies/turtle_soup.py` covering: signal generation on a synthetic happy-path fixture, no-signal case, and edge cases (empty df, single candle, all-zero volume).

**C3.** Wire `turtle_soup` into the runtime pipeline (`src/runtime/pipeline.py`). It must produce signals through the same path VWAP does, write to the same signals DB, and route through the same `RiskManager` → order layer.

**C4.** Decide and implement: **single multi-strategy process** OR **one service per strategy**. Recommendation: **single multi-strategy process** (`ict-trader-live` already exists, runs `src.main`, and the registry per-strategy service names appear to be aspirational — confirm in audit). If single-process: collapse `service:` fields in `strategies.yaml` to all reference `ict-trader-live`, OR drop the `service:` field entirely and remove every code path that tries to map strategies to systemd units.

**C5.** Delete:
- `strategies/turtle_soup_mtf_v1.py` (replaced by C1).
- `strategies/breakout_confirmation.py` (out of scope per PM).
- `strategies/vwap_signal_builder.py` if `src/units/strategies/vwap.py` is the canonical version (verify).
- `src/units/strategies/breakout_confirmation.py`, `src/units/strategies/ict.py`, `src/units/strategies/killzone.py` (out of scope per PM).
- `src/runtime/strategies/ict.py` (out of scope per PM).
- `src/strategies_manager.py` (replaced by `src/strategy_registry.py`).

For each delete, run `grep -rn` first to prove no live code path imports it. If anything still imports it, fix the import in the same PR.

**C6.** Reconcile entrypoints:
- Make `run_trader.sh` call `python -m src.main` (matching the systemd unit) OR delete it.
- Make `check_bots.sh` reference the actual running process name OR delete it.
- Document the canonical entrypoint in `docs/claude/deployment-ops.md`.

---

## Phase D — Service reconciliation (small PRs)

**D1.** Based on C4's decision, either:
- (a) Add the missing `.service` files to `deploy/` for `ict-trader-vwap` and `ict-trader-turtle-soup` (or whatever names B1 chose) and the corresponding systemd install steps in `docs/claude/deployment-ops.md`. Each service must have `EnvironmentFile`, `Restart=always`, log redaction, and the right entrypoint module.
- (b) Confirm the single-process architecture and remove every reference to per-strategy services from `strategies.yaml`, `units.yaml`, `strategy_registry.py`, the Telegram bot's start-services command, tests, and docs.

**Strong recommendation: (b)**. Per-strategy systemd services are unnecessary complexity for two strategies that already share a tick loop, and the failure that triggered this sprint was caused exactly by per-strategy service references.

**D2.** Investigate and remove the source of `ict-trader-bak` and `ict-trader-example`. Likely candidates: a state file (`/home/ubuntu/...`), a hardcoded list in `src/bot/telegram_query_bot.py` or a sibling, an old systemd drop-in, or a stale config snapshot. Patch the source. Add a regression test that asserts the start-services command can only target services whose names are present in the current registry.

**D3.** Update the Telegram bot's start-services command (or whatever issued the "✅/❌" output the PM saw) to fail loudly when a configured service has no corresponding systemd unit, instead of silently skipping. Better failure mode: refuse to start until configs and units agree.

---

## Phase E — Live mode hardening (small PRs)

**E1.** Remove `DRY_RUN` and `dry_run` branching from the in-scope production path: `src/runtime/pipeline.py`, `src/runtime/orders.py`, `src/main.py`, `src/units/strategies/{turtle_soup,vwap}.py`. Replace with a single hard assertion at startup: `ALLOW_LIVE_TRADING=true` AND `DRY_RUN` is unset/false → run live; anything else → refuse to start with a clear error. Do not silently downgrade to dry-run.

**E2.** Confirm the `/accounts` dry/live toggle from S-011 PR #1 still functions for testing/staging contexts (it can stay), but the **default state of every configured account in `accounts.yaml` is `live`**. Document the toggle's semantics clearly in `docs/claude/deployment-ops.md`.

**E3.** Add tests proving:
- `place_order` refuses when position size > `pos_size` cap, for **both** turtle_soup and vwap.
- `place_order` refuses when daily loss > `daily_usd`, for both.
- `place_order` refuses when the kill-switch flag is set.
- Startup refuses to launch when `ALLOW_LIVE_TRADING != true`.

**E4.** Confirm `runtime_logs/signal_audit.jsonl` captures every signal from both strategies with strategy-name attribution. Add a test if missing.

---

## Phase F — Verification & deployment (single PR + manual VM step)

**F1.** Run the full suite: `PYTHONPATH=. pytest tests/ -q --ignore=tests/test_main_loop.py`. Must be green.

**F2.** Run `python scripts/secret_scan.py`. Must be clean.

**F3.** Run `python scripts/repo_inventory.py` and attach output to the sprint summary.

**F4.** Produce a one-page **deployment runbook** at `docs/audit/sprint-012-deployment-runbook.md`. Steps the PM (or Colab SSH) will execute on the VM, in order, with copy-paste commands. Must include:
- Pre-flight: `git status` clean on `main`, services list before changes.
- Pull and reload systemd units.
- Restart sequence (`ict-trader-live` LAST so live trading downtime is bounded).
- Verification commands (`systemctl status`, `journalctl -u ict-trader-live -n 50`, Telegram `/strategies` should show only turtle_soup + vwap, both `enabled: true`).
- Rollback procedure if anything fails.

**F5.** Sprint summary at `docs/sprint-summaries/sprint-012-summary.md` per the standard checklist in `CLAUDE.md`. Include:
- PR list.
- Tests added.
- Files deleted.
- Phantom service mystery resolution.
- Architecture decision (single-process vs per-strategy services) with rationale.
- Lessons learned (1–3 bullets).

**F6.** Final checkpoint to `CHECKPOINT_LOG.md`. Telegram: `/sprintlet_complete S-012`.

---

## Definition of Done (NON-NEGOTIABLE checklist)

The sprint is done **only** when every box below is true. Do not declare completion early.

- [ ] `config/strategies.yaml` lists exactly `turtle_soup` and `vwap`. No others. Both `enabled: true`.
- [ ] `config/units.yaml` strategies section matches `strategies.yaml` exactly.
- [ ] `config/accounts.yaml` references only `turtle_soup` and `vwap`. Risk caps non-zero.
- [ ] Exactly **one** strategy directory exists in the repo. The other two are gone.
- [ ] Exactly **one** strategy registry exists in the repo.
- [ ] Every systemd `.service` file in `deploy/` corresponds to a service that actually needs to run; every service the configs/code reference exists in `deploy/`. Bidirectional.
- [ ] `ict-trader-bak` and `ict-trader-example` no longer appear anywhere. The source has been identified, removed, and a regression test prevents recurrence.
- [ ] No `DRY_RUN=true` code path is reachable in production for turtle_soup or vwap. Startup hard-fails if `ALLOW_LIVE_TRADING` is not true.
- [ ] Risk caps fire for both strategies, proven by tests.
- [ ] `pytest tests/ -q` is green. `secret_scan.py` is clean.
- [ ] Deployment runbook exists and has been followed on the VM (PM or Claude via Colab SSH).
- [ ] `systemctl status ict-trader-live` is `active` and last 50 journal lines show signals being generated by **both** turtle_soup and vwap (or, if turtle_soup is intentionally held in dry-run pending the gates in Guardrail #4, the deferral is documented in the sprint summary with an explicit go-live criterion).
- [ ] Telegram `/strategies` command returns turtle_soup + vwap, both live, both with non-zero recent signals.
- [ ] Live trader uptime preserved across the full sprint window — no unplanned outages.

---

## Decision request — items the PM may need to weigh in on mid-sprint

Claude: pause and ask via Telegram (`/sprintlet_status decision needed: <topic>`) before acting on any of these. Do not block on items not in this list — those are yours to decide.

1. **Single-process vs per-strategy services** — strong recommendation is single-process (see C4/D1). PM confirms direction before D1 ships.
2. **Turtle Soup go-live readiness** — if turtle_soup unit tests + risk-cap tests pass cleanly, PM approves promoting it from dry-run to live in this sprint. Otherwise it ships in a held-dry-run state with a documented gate for next sprint.
3. **Killzone, ICT, breakout_confirmation deletion** — if any of these were merged in S-008+ as deliberate production strategies (not just scaffolding), PM confirms deletion. Default per this prompt: delete.
4. **`/accounts` dry/live toggle** — keep as a per-account override (recommended) or remove entirely.

---

## Files Claude is permitted to modify

- `config/*.yaml`
- `src/units/strategies/**`
- `src/runtime/**`
- `src/strategy_registry.py`
- `src/strategies_manager.py` (delete)
- `src/main.py` (light edits only — startup guards, no logic refactor)
- `src/bot/telegram_query_bot.py` (start-services command + regression test only)
- `deploy/*.service`, `deploy/*.timer`
- `tests/**`
- `docs/audit/**`, `docs/sprint-summaries/sprint-012-summary.md`, `docs/claude/deployment-ops.md`
- `run_trader.sh`, `check_bots.sh` (edit or delete)
- `strategies/**` (delete legacy duplicates only)
- Top-level loose scripts (`test_order.py`, `test_order_safe.py`, `test_bybit_connection.py`) only if the audit identifies them as duplicates of canonical test files.

## Files OFF LIMITS

- `config/master-secrets.template.yaml` (semantic changes).
- `.env.example` (unless adding the live-mode hard guard variable).
- Anything under `ml/`, `notebooks/`, `data/`.
- `ROADMAP.md` until the sprint summary PR.

---

## Pacing reminder

This is an all-night, slow sprint. Prefer correctness over throughput. After every two merged PRs, re-read this prompt and the audit doc, recheck Definition of Done, and continue. If you hit a blocker that needs PM input and it's outside the four decision-request items above, stop and post `/sprintlet_status blocked: <reason>`.

End of prompt.
