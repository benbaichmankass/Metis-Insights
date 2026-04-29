# Sprint S-012 — Production Wiring Audit (Phase A)

> **Status:** Phase A complete. No code changes in this PR.
> **Author:** Claude Code (autonomous), 2026-04-29.
> **Scope:** Evidence-based audit of architectural drift across strategies,
> registries, configs, services, entrypoints, dry-run flags, and risk caps.
> Followed by PM-decision items and an ordered PR sequence for Phases B–F.

This top-level doc is an **index + executive summary**. The detailed evidence
for each audit section lives under `docs/audit/sprint-012/` so individual
sections can be read, updated, and cited independently in follow-up PRs.

## Index

| § | File | What's in it |
|---|---|---|
| 1 | [01-strategy-inventory.md](sprint-012/01-strategy-inventory.md) | Every strategy file across `strategies/`, `src/units/strategies/`, `src/runtime/strategies/`, with imports, config refs, and tests. |
| 2 | [02-registry-inventory.md](sprint-012/02-registry-inventory.md) | All places strategies are registered/enumerated. Canonical vs duplicate. |
| 3 | [03-service-config-mapping.md](sprint-012/03-service-config-mapping.md) | Cross-table of strategy × `strategies.yaml` × `units.yaml` × `.service` file. |
| 4 | [04-phantom-services.md](sprint-012/04-phantom-services.md) | Investigation of `ict-trader-bak` / `ict-trader-example`. |
| 5 | [05-entrypoints.md](sprint-012/05-entrypoints.md) | Canonical entrypoint vs every stale alternative. |
| 6 | [06-dry-run-surface.md](sprint-012/06-dry-run-surface.md) | Every file/branch reading `DRY_RUN`, `ALLOW_LIVE_TRADING`, `paper`, `simulate`. |
| 7 | [07-risk-caps.md](sprint-012/07-risk-caps.md) | Trace from `accounts.yaml` → `RiskManager` → `place_order`, plus test gaps. |
| 8 | [08-pm-decisions.md](sprint-012/08-pm-decisions.md) | Items requiring PM judgement before Phase B/C/D ships. |
| 9 | [09-pr-sequence.md](sprint-012/09-pr-sequence.md) | Ordered PR list for Phases B–F with acceptance criteria. |

## Executive summary

The repo has accumulated four sprints of feature work (S-008 → S-011) on top
of a foundation that was never fully wired. Concrete drift:

1. **Three strategy roots.** Legacy `strategies/` (where the only copy of
   Turtle Soup lives), modern `src/units/strategies/`, and a runtime
   signal-builder dir at `src/runtime/strategies/`. Turtle Soup is **not
   referenced by any production config**.
2. **Two registries.** `src/strategy_registry.py` (YAML-driven, canonical
   since S-007) and `src/strategies_manager.py` (in-memory dict, orphan,
   only consulted by the legacy breakout model loader).
3. **Service-to-config mismatch.** `config/strategies.yaml` and
   `config/units.yaml` declare per-strategy services
   (`ict-trader-vwap`, `ict-trader-ict`, `ict-trader-breakout`) but the only
   `.service` file in `deploy/` is `ict-trader-live.service`. The runtime
   never actually launches per-strategy units — `src/main.py` runs a single
   pipeline. The per-strategy service names are **aspirational metadata**.
4. **Phantom services not in the repo.** `ict-trader-bak` and
   `ict-trader-example` do **not** appear anywhere in the current repo or
   git history. The Telegram failure the PM saw must originate from VM-side
   state (a stale `enabled` symlink, a manual `systemctl start` from shell
   history, or an out-of-repo wrapper). See § 4 — needs PM input to
   investigate the VM directly.
5. **Stale shell scripts.** `run_trader.sh` and `check_bots.sh` reference
   `src.core.automated_trading_loop` — an orphan module that the live
   systemd unit never invokes (live unit calls `python -m src.main`).
6. **Dry-run surface is largely correctly gated.** The startup interlock at
   `src/runtime/validation.py:118-132` is the right pattern (refuse to
   start if `DRY_RUN=false` without `ALLOW_LIVE_TRADING=true`). Most
   `DRY_RUN` reads are at the order-execution layer — appropriate. The
   prompt's instruction to "remove DRY_RUN" should be interpreted as
   "remove silent downgrades and ensure the hard interlock is the only
   path", not "delete every flag read".
7. **Risk caps partially enforced.** `pos_size` and `daily_usd` are checked
   in `RiskManager.approve()`. **`max_dd_pct` is defined in config but
   never read or checked.** No tests exercise the account-level rejection
   path.

## Architecture decision (recommended)

**Single-process, multi-strategy.** The live unit `ict-trader-live.service`
already runs `python -m src.main` and dispatches per-strategy via
`Coordinator.strategy_order_pkg()`. The per-strategy `service:` fields in
the YAMLs are documentation noise that misled the Telegram start-services
flow. **Drop the `service:` field from `strategies.yaml`/`units.yaml`** and
remove every code path that maps a strategy to a systemd unit. Single
service, single process. Confirmed in § 9 PR D2.

This is **decision-request item #1** from the sprint prompt; we will pause
for PM confirmation before D-phase ships.

## Strategy roster after sprint

Per PM intent: **`turtle_soup` and `vwap` only**. Everything else is removed
unless flagged in § 8 for PM keep/delete confirmation.

## Definition-of-Done coverage

This audit produces no DoD checkboxes directly — all DoD items are satisfied
by Phases B–F. The PR sequence in § 9 is structured so every DoD checkbox
has at least one PR that closes it.

## Self-approval bookkeeping

No deletions are performed in this PR. The deletion list is enumerated
inside § 9 (PR sequence) so the next Claude session executes them with
explicit grep-no-imports proofs at PR time.
