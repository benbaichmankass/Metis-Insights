# Full-System Audit — 2026-07-09

> **Program doc (the shared brain).** Per `.claude/skills/full-system-audit/SKILL.md`
> this is the multi-session audit's findings doc + per-file coverage map. Every
> session reads it on start and appends to it. Consistency **and** liveness axes;
> rules-first. Branch: `claude/full-system-audit-rmdf0t` (all three repos).
>
> **Predecessor:** `docs/audits/full-system-audit-2026-06-28.md` (M17). This is a
> fresh periodic pass requested by the operator 2026-07-09.

## Phase 0 — RULES audit (DONE, gate cleared)

**Method:** read the canonical corpus highest-precedence first
(`CLAUDE-RULES-CANONICAL` → `ARCHITECTURE-CANONICAL` → `ROADMAP` → latest sprint
log → both `CLAUDE.md`), ran `scripts/ci/check_canonical_doc_coherence.py` (all 4
checks PASS), and fanned out three parallel contradiction-hunt agents (rules-doc,
architecture change-log, roadmap cross-doc). Verified concrete claims against
config/code on disk.

**Verdict:** the top-precedence **operating-rules doc is internally consistent**;
where spot-checked the **system is compliant** (e.g. the auto-flip mode dead code
is genuinely deleted — Prime Directive holds). The real Phase-0 finding is
**material drift in the #2/#3 yardsticks** (`ARCHITECTURE-CANONICAL`, `ROADMAP`)
vs reality, plus two rule-wording ambiguities.

### Rule-level items — settled with operator 2026-07-09

| ID | Item | Decision | Status |
|---|---|---|---|
| R1 | Order path (`orders.py`, `execute.py`) + live-VM service units classified Tier-2 in canonical examples but Tier-3 in VM-authority-split | **Tier-3** (stricter, merge-gate sets the tier); struck from Tier-2 examples, added to Tier-3 examples | ✅ FIXED (this branch) |
| R2 | Prime Directive `*_ENABLED` rule stated as absolute but has carve-outs + a CI guard that rejects the suffix | **Narrow the wording**: forbidden = default-off `*_ENABLED` on a *required* capability; NEWS_VETO_ENABLED / M5_CONSUMER_ENABLED grandfathered; `*_MODE` is the sanctioned shape | ✅ FIXED (this branch) |
| R3 | Tier-1 "commit to `main`" vs the PR/merge-protocol + branch-protection | Clarified: "commit to main" = no operator-approval gate, still via PR | ✅ FIXED (this branch) |
| R4 | "Why no new mechanical guardrails" reads broadly vs the CI guards the same doc mandates | Scoped to the Tier-3 approval discipline; structure/wiring guards explicitly sanctioned | ✅ FIXED (this branch) |
| R5 | `ict-heartbeat` (retired 2026-07-08) still named in Tier-2 service-unit examples | Removed in the R1 edit | ✅ FIXED (this branch) |

### Material canonical-doc drift (yardstick stale) — feeds S-AUDIT-A

Verified against config/code on disk 2026-07-09:

| ID | Doc claim | Reality | Sev |
|---|---|---|---|
| D1 | ARCH: "Real-money Alpaca remains gated" | `alpaca_live`: `mode=live`, `real_money`, ~16–20 strategies routed | ⚠️ live-money |
| D2 | ARCH Step 2: `squeeze_breakout_4h` `execution: shadow` | config: `squeeze_breakout_4h` `execution: live` | ⚠️ live-gate |
| D3 | ARCH: "12 strategies registered (verified 2026-06-10)" + its own ~16-item enumeration | **48** in `config/strategies.yaml` | high |
| D4 | ARCH line ~104-105 & ~871: `_DRY_RUN_OVERRIDES`/`set_account_dry_run` "deletion never landed" | **Deleted** (docstring-only + regression test asserts absence); doc self-contradicts across 5 spots | high |
| D5 | ARCH Step 6: "IBKR offline pending new-user approval, MES not executing" (2026-05-24) | MES/MGC/MHG live; `ib_paper` also trades SPY/QQQ/IWM/TLT | med |
| D6 | ARCH Step 3: bybit_1/bybit_2 "mirrors, same roster" | bybit_2 winners-only (9) vs bybit_1 (20) | med |
| D7 | ROADMAP milestone table titled "M0..M15" | holds M17/M18/M19 rows; **M16 has no row**; `/api/bot/roadmap` parser keys on the literal heading | med (load-bearing) |
| D8 | ROADMAP "Active milestone queue (next 3)" lists M12-S1/M13-S1 as upcoming | both DONE; real active = M15/M17/M18/M19 | med |
| D9 | ROADMAP M15 row understates `alpaca_live` (SPLG/IAUM real-money + normalized caps) | header + S-PROXY ledger + config already carry it | low |
| D10 | ARCH: breaker line-nums (1048-1068 vs 1669-1689); 0.25 tick vs equity penny; repo-map omits IB connector + prop executor; ROADMAP WS5-B-PART-2 "next" though DONE; vwap/fade changelog gaps | assorted stale references | low tail |
| ENV1 | Session `DIAG_BASE_URL=http://158.178.210.252:8001` (terminated x86 micro) | live trader is `141.145.193.91`; direct diag broken → use issue relay. Env-config, not repo — note only | info |

## Phase 1 — Workstream plan

| WS | Scope | Mode | Status |
|---|---|---|---|
| **S-AUDIT-A** | Consistency / canonical-doc drift: fix D1–D10 in ARCHITECTURE-CANONICAL + ROADMAP; run `workplan-vs-architecture`. Add the M16 row + retitle the milestone table + add this audit's ROADMAP entry. | lead + 1 agent | pending |
| **S-AUDIT-B** | Liveness / zombie hunt (bot): integration inventory (brokers `EXCHANGE_MAP`, services/timers, workflows, env-gates, transports) → 3 probes each → LIVE/keep/ZOMBIE. | agent → lead PR | pending |
| **S-AUDIT-C** | Consumer wiring & display correctness (dashboard + android): every consumed endpoint exists + shapes match; null handling; 48-strategy/9-account reality renders; real/paper/prop isolation. | agent → lead PR | pending |
| **S-AUDIT-D** | Data audit (canonical store): `trade_journal.db` + `trainer_store.db` integrity, orphans/`reconcile_status`, real/paper/prop isolation, single-source-of-truth. Via diag relay. | lead (relay) | pending |
| **S-AUDIT-E** | Per-line code sweep (`src/`): fan out over directory slices for dead code / correctness / drift. Coverage map below. | agents → lead | pending |
| **S-AUDIT-F** | VM audit (live + trainer + gateway): services/timers state, `.env` inventory, running SHA vs main, disk, `/opt` symlink. Via issue relay (direct diag broken — ENV1). | lead (relay) | pending |
| **S-AUDIT-G** | Backlog drawdown: health (202) + performance (66) + ml (66) — triage, close resolved, action tractable. | agent → lead | pending |
| **S-AUDIT-H** | Stale PR/issue closeout + governance: open PRs, stale issues, session-board hygiene. | lead | pending |

## Per-file coverage map (append as read — "every line" is verifiable, not asserted)

Format: `path — reader — verdict`. Blank = not yet reached.

### Canonical docs (Phase 0)
- `docs/CLAUDE-RULES-CANONICAL.md` — lead — READ FULL, edited (R1–R5)
- `docs/ARCHITECTURE-CANONICAL.md` — lead + agent — READ FULL, drift D1–D10 logged (fixes pending S-AUDIT-A)
- `ROADMAP.md` — agent — READ (header + tables), drift D7–D10 logged
- `CLAUDE.md` (bot root) — lead — READ FULL
- `config/strategies.yaml` — lead — counted (48 cells)
- `config/accounts.yaml` — lead — counted (9 accounts)

_(subsequent sessions append their coverage here)_

## Honesty / coverage gaps so far
- VM/data state NOT yet pulled (direct diag broken per ENV1; issue relay pending in S-AUDIT-D/F).
- `src/` per-line sweep NOT started (S-AUDIT-E).
- Dashboard + Android repos NOT yet read (S-AUDIT-C).
- D2 (`squeeze_breakout_4h` live vs doc-shadow) needs a `git log -p` premise check before the doc is "fixed" — field-beats-comment says config wins, but confirm the live gate is intended, not an accidental flip.
