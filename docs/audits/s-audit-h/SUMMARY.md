# S-AUDIT-H — remaining-codebase sweep (M17 full-system audit, 2026-06-28)

Third wave of the M17 audit, completing line-by-line coverage of the
production code S-AUDIT-F/G did not reach: the strategy/signal-generation
logic, the rest of `src/runtime`, the rest of `src/core`/`src/units`/`src/utils`/`src/web`,
the backtest engines + non-ops scripts, and **full passes of the dashboard and
android repos**. Six disjoint read-only slices (H1–H6).

**Headline: the live trading core, the signal builders, and the backtest
engines are all behaviourally clean.** No bug can place / mis-size / mis-close a
real-money order; the backtest engines are leakage-clean and fill-conservative,
so Tier-3 go-live evidence is trustworthy; both consumer apps are contract-faithful
(correct fields, rigorous null→"—", strict real/paper/prop separation). Findings
are one real-money WIRING GAP (operator decision), a handful of DB-path /
research-tooling fixes, and doc-drift.

## The one item that needs an operator decision

**`slv_pullback_1d` + `gdx_pullback_1d` are declared `enabled: true` / `execution: live`
and routed to `alpaca_live` (real money) + `alpaca_paper` + the options account,
but have NO signal builder** — they emit zero signals (inert). The MES
`MULTI_SYMBOL_ENABLED` "looks-live-but-stranded" class. No money at risk (they
generate nothing). **Tier-3 decision: wire the two builders (one-line delegations
mirroring the wired `gld_pullback_1d`) + per-account backtest-compat, OR remove
the config blocks.** (S-AUDIT-H1 F1.)

## Fixed in PRs

| Finding | Where | Tier | PR |
|---|---|---|---|
| H4 H-2: `scripts/init_db.py` hardcoded CWD/src-relative `src/bot/trade_journal.db` (stray-journal #1308 class; operator-run-once) | scripts | 1 | this PR — routed through `trade_journal_db_path()` |
| H4 H-3: `scripts/daily_heartbeat.py` + `.env.example` non-canonical `TRADE_JOURNAL_DB` (`data/trades.db` wrong name; bare-basename example) | scripts/config | 1 | this PR — self-contained stdlib chain (env→$DATA_DIR→repo-root) + guard allowlist; `.env.example` de-footgunned |
| Both above invisible to the CI guard (it only scanned src/ml + scripts/ops) | CI guard | 1 | this PR — `_PY_SCAN_DIRS` widened to all of `scripts/` (guard passes) |

## Deferred to a follow-on Tier-1 cleanup PR (doc-drift + dead-code)

**Bot:**
- H1 F2: `src/core/signals.py` (`ICTSignalsAnalyzer`) — dead code, no live caller (feeds the retired KillZoneScalperBot). Remove (+ its tests).
- H3 F3: `processor.get_today_pnl` / `get_open_positions_count` — zombie helpers, no caller, AND blend real+paper. Remove (+ tests).
- H1 F3: allocator/`StrategyInterface`/`SignalPackage` scaffolding — live-inert (CENTRALIZED_ALLOCATOR default-off); intentional, flag-not-remove.
- H3 F5: `core/account_profile.py::from_dict` defaults `mode` to `dry_run` vs canonical `live` (latent footgun) — align to `live`.
- Doc-drift (field-beats-comment): H1 F4 (`strategies.yaml` "enabled permissive" — actually default-off), H1 F5 (stale roster-sync comment), H2 F1–F4 (`advisory_sizing`/`market_hours`/`_closed_flat_wiring`/`pipeline_result` stale docstrings — the last points the operator at the removed `/accounts` command), H4 H-1 (M5 `/test <strategy>` runs the legacy FVG engine for every strategy — document honestly / disclose in docstring; M5 is env-gated default-off research tooling).
- H3 F1/F2: `processor.get_recent_signals` reader/writer path split + `runtime_status._read_strategy_names` enabled-filter — both latent (unused path / all strategies set `enabled` explicitly).

**Operator-gated (Tier-3 file — comment only, but touches a gated config):**
- H2 F5: `config/accounts.yaml` lines 23-27 mode-mutation comment is stale (`set_account_dry_run()` deleted; `/accounts` command removed #1933; "ONLY toggle" contradicted by `account_state.yaml`'s dry-only override). Fix needs operator approval to touch accounts.yaml.

**Dashboard (separate repo PR):** H5 — 4 doc-drift (`CLAUDE.md` false "FCM device-registration write" claim — that's Android; "~3800 lines" → 6470; stale README tabs table; stale build marker) + 1 low fragile `a and b or c` ternary.

**Android (separate repo PR):** H6 — drop the vestigial decommissioned-IP (`158.178.210.252`) allowlist entry; add `"—"` guards for the "0.0%" win-rate render on the legacy Status screen + Trades header (null→"—" canon).

## Backlog (lower-value, recorded for review pickup)
M-1 `fit_confidence_calibrators` in-sample metric (use a purged holdout); `backtest_orb` net-vs-gross win-rate basis inconsistency; `backtest_squeeze` exit_reason label + missing n==0 keys; `strategy_review_packet.pull_decisions` real+paper blend (advisory surface); `render_system_report` negative-money formatting.

## Verdict
Live trading core + signal builders + backtest engines + both consumer apps:
**clean.** No money-correctness bug anywhere in the swept surface. The audit's
substantive output is the one real-money wiring gap (operator decision) + the
DB-path discipline fixes in this PR; everything else is Tier-1 cleanup.
