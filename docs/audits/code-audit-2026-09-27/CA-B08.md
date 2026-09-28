# CA-B08 — code audit of leftover scripts (Wave B)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> Lane CA-B08 of the 2026-09-27 code audit · session `session_016jKXsmN6pj5g9CeJ89Y6iE` · audited `origin/main` at `816dc482` (2026-09-27 ~14:35Z) · read-only analysis. Nothing in code, config or live state was changed by this lane.

**The question:** does the code under this lane's scope do what its docstrings, tests and the repo's docs say it does, and what in it is dead or points somewhere broken?

**Scope covered (5 parallel sub-lanes, one question each, per `.claude/skills/manager/SKILL.md`'s spawn model):**

1. Top-level `scripts/*.sh` (22 files: `backup_logs.sh`, `bootstrap_diag_relay.sh`, `check_data_dir.sh`, `collect_health_snapshot.sh`, `deploy_diag.sh`, `deploy_pull_restart.sh`, `install-hooks.sh`, `install_ib_gateway_docker.sh`, `install_systemd_units.sh`, `list_stale_branches.sh`, `migrate_journal_db.sh`, `migrate_to_data_dir.sh`, `oci_attach_volume.sh`, `oci_create_volume.sh`, `oci_vm_ssh.sh`, `oci_volume_status.sh`, `run_backtest.sh`, `run_pipeline_health_test.sh`, `run_smoke_once.sh`, `setup.sh`, `verify_storage_setup.sh`, `vm_bootstrap.sh`).
2. Top-level `scripts/check_*.py` CI guards (26 files, everything registered or checked against `scripts/ci/run_guards.py`'s `GUARDS` registry).
3. Remaining top-level `scripts/*.py` (47 files: `backtest_*`, `pull_*`, `init_db.py`, `db_integrity_alert.py`, and the rest of the utility scripts).
4. `scripts/macro/**` (37 files — the ROADMAP_MACRO family).
5. The small "leftover sprint"-named subdirectories: `scripts/{analysis,micro,options,llm,reports,session_handoff,sprint015,sprint047,git-hooks}/**` (19 files).

**Total: 151 files read** (74 top-level scripts + 96 counted with `__init__.py`/`.sh` included per lane rosters above, plus 56 across the 6 subdirectories — see each lane's confirmed file list below; the checklist's "74" top-level count is stale, as CLAUDE.md itself warns roster/file counts always are — re-run, never quote).

**Answer: 20 open findings**: 0 critical, **8 high**, 4 medium, 8 low. All 7 *newly filed* high findings are pipelined as `PI-20260927-EJ89Y6IE-0001` … `-0007`. The 8th high finding (`deploy-pull-restart-no-rollback-corroborated`) independently re-derives a finding the sibling lane **CA-A10** already filed (`PI-20260927-R1DJ7SUQ-0003`) — not re-filed, to avoid a duplicate pipeline record. The machine-readable version is [`CA-B08.findings.jsonl`](CA-B08.findings.jsonl), one object per line.

**No reproduction-script directory** (unlike CA-B01's `CA-B01-repros/`) — every finding in this lane was verified with a direct command (grep, a local dry-run of the script itself, `bash -n`, or a synthetic-diff test against a CI guard) rather than a planted-data harness; the exact command is inline in each finding's `evidence` field.

---

## High findings

| id | claim (short) | pipeline |
|---|---|---|
| `ci-guard-impossibility-claims-scans-zero-of-three-backlogs` | `check_impossibility_claims.py` silently scans **0 of its 3 declared backlog registers** (archived 2026-09-21, never re-pointed) and reports a false "clean" pass — the identical defect class already found and fixed on the sibling `claim-basis-guard`. | `PI-20260927-EJ89Y6IE-0001` |
| `ci-guard-env-gate-blind-to-core-and-main` | `check_env_gate_in_diff.py`'s protected-path list omits `src/core/` and `src/main.py` — the two files CLAUDE.md itself names as the coordinator's gate fold-point and the pairs-tick entrypoint. A synthetic diff adding a new `*_ENABLED` flag to either passes the guard silently; the same line in an in-scope file correctly trips it. | `PI-20260927-EJ89Y6IE-0002` |
| `ci-guard-canonical-config-loader-blind-to-module-level` | `check_canonical_config_loaders.py` only AST-walks function bodies for hand-rolled `accounts.yaml` parsers; a module-level (top-of-script) parser — a common shape for this repo's one-off ops tooling — is invisible, reproducing the exact "10th private parser" bug class it exists to prevent. | `PI-20260927-EJ89Y6IE-0003` |
| `run-backtest-sh-stub-vs-architecture-doc` | `docs/ARCHITECTURE-CANONICAL.md:460` names `scripts/run_backtest.sh` as the backtest dispatch mechanism; the file is a 2-line `echo "Backtest script coming soon"` stub. The real, current harnesses (documented in `.claude/skills/backtesting/SKILL.md`) are a different set of Python scripts entirely. | `PI-20260927-EJ89Y6IE-0004` |
| `pull-alpaca-fills-collapses-api-failure-as-empty` | `scripts/pull_alpaca_fills.py` collapses any Alpaca API failure into the same empty-page return as a genuine no-fills window — the exact bug class already fixed on its Bybit/IB siblings. A real outage on the real-money `alpaca_live`/`alpaca_portfolio` accounts reports as a clean `candidates=0 inserted=0` run. | `PI-20260927-EJ89Y6IE-0005` |
| `print-runtime-profile-stale-signature-crashes` | `scripts/print_runtime_profile.py` calls `build_settings_from_env(os.environ)`/`validate_startup(settings)`, but both now take zero arguments — guaranteed `TypeError` on every run. Documented and known since sprint-012 (whose "fix" was deleting the covering test, not the script); README.md still names it 3× as the live-trading pre-flight check. | `PI-20260927-EJ89Y6IE-0006` |
| `spot-margin-smoke-broken-since-cutover` | `scripts/sprint047/spot_margin_smoke.py` hard-requires `bybit_2.market_type == "spot-margin"`; `config/accounts.yaml` has carried `market_type: linear` since the 2026-05-10 cutover (its own in-file comment says so). The script has aborted on every run for 4.5+ months; `docs/runbooks/spot-margin.md` §6 still points operators at it uncorrected. | `PI-20260927-EJ89Y6IE-0007` |
| `deploy-pull-restart-no-rollback-corroborated` | `scripts/deploy_pull_restart.sh` (the live, ~5-min-cadence deploy path) has no rollback-on-failure and no CI-status gate before pulling `origin/main` onto the live-money VM. **Already filed by sibling lane CA-A10** (`PI-20260927-R1DJ7SUQ-0003`); independently re-derived here, not re-filed. | see CA-A10 |

## Medium (4) and low (8)

See the jsonl for full records. Themes:

- **Doc/skill drift on binding docs** (all Tier-1 doc fixes): `.claude/skills/backtesting/SKILL.md`'s stale "no fee model" exception for `backtest_ict_scalp.py` (it now has a full net-of-cost model); `scripts/reports/render_system_report.py`'s docstring and the live `.claude/skills/system-report/SKILL.md` alias both still point at the archived `.claude/skills/system-review/SKILL.md`.
- **Orphaned safety tooling**: `scripts/validate_registry_vm.py` is correct and tested but has zero production callers — nothing runs it against the real `config/strategies.yaml` before a live deploy, despite its own docstring saying it should.
- **A second unproven CI guard**: `check_training_population.py` has no self-test (unlike its structural twin `check_strategy_coverage.py`); its failure path is currently correct (verified this session by manual monkeypatch) but CI has never exercised it directly.
- **Cosmetic script bugs, none load-bearing**: a `local rc=$?` bash pitfall in `run_smoke_once.sh` (misleading per-step exit-code logging only; pass/fail accounting is unaffected), a silent per-trade `except: pass` around hold-time in `backtest_pairs.py` (understates a secondary stat, never `net_r`), a stale caller-workflow name in `run_pipeline_health_test.sh`'s header, stray verification lines in `vm_bootstrap.sh` checking for artifacts it deliberately never installs, and a possibly-stale `ict-telegram-bot` "no drop-in by design" carve-out in `verify_storage_setup.sh` that may now conflict with `install_systemd_units.sh`'s current behavior (needs a live-VM read to settle — flagged, not resolved, since this is a read-only local audit).
- **Test-coverage gaps, correctly scoped otherwise**: `scripts/options/probe_alpaca_options.py` (read-only Phase-0 diagnostic, zero tests); `scripts/micro/microstructure_probe.py`'s claimed research-ledger landing was not found anywhere in `docs/research/` (may simply mean the S0 question is still open).

## Dead / broken-doc-reference scripts

Two distinct categories, per the task brief:

**A. Dead (referenced by nothing — no workflow, unit, skill, doc, or other script):**

| script | evidence |
|---|---|
| `scripts/backup_logs.sh` | `grep -rn "scripts/backup_logs.sh" --include=*.yml --include=*.yaml --include=*.md --include=*.py --include=*.sh .` → only its own header comment. **Also broken**: `REPO_DIR="/home/ubuntu/YOUR_REPO"` is a literal unfilled template placeholder — `cd $REPO_DIR` under `set -e` would abort immediately on any real VM. If ever fixed and run, it does an unreviewed `git push origin main`. |
| `scripts/list_stale_branches.sh` | Same grep pattern → only self. Read-only (lists stale branches, never deletes), so dead-but-harmless. |
| `scripts/setup.sh` | Only cited once, as a line-number reference in a docs audit — never as a "run this" instruction, and no workflow/systemd unit invokes it. Describes a fully retired bootstrap architecture: its cron `@reboot` target `start.sh` does not exist anywhere in the repo (`find . -iname start.sh` → zero results); the current live model is systemd + `render_env_from_master.py`'s multi-account env rendering, not a hand-typed `~/.bot_env`. Also uses non-silent `read -p` for API secrets (echoed to the terminal), already independently flagged by sibling lane CA-A10. |

**B. Referenced by docs that no longer work (the finding, not merely the reference, is what's new — each has its own row above):**

| script / doc | what's broken |
|---|---|
| `scripts/run_backtest.sh` | `docs/ARCHITECTURE-CANONICAL.md:460` names it as the backtest dispatcher; it's a no-op stub. |
| `scripts/sprint047/spot_margin_smoke.py` | `docs/runbooks/spot-margin.md` §6 names it as the testnet rehearsal; broken against current config since 2026-05-10. |
| `scripts/print_runtime_profile.py` | `README.md` (3×) names it as the pre-flight check; guaranteed `TypeError` since at least sprint-012. |
| `scripts/reports/render_system_report.py` / `.claude/skills/system-report/SKILL.md` | The skill still points at the archived `system-review/SKILL.md`; the module survives only as an unrelated library import elsewhere. |

**Confirmed NOT dead despite "leftover sprint" naming** (the audit's own naming-prior, tested and found not to hold): every file under `scripts/sprint015/` (all have live, executing tests under `tests/sprint015/` that genuinely exercise the code, including calling into current `src/units/strategies/vwap.py` — confirmed not broken), `scripts/session_handoff/**` (production/CI-wired: `close_session.py`/`validate_handoff.py` are the documented producer/validator for the `continue-work` GitHub Actions workflow), `scripts/llm/**` (CI-exercised by `.github/workflows/llm-delegate.yml` and a dedicated test), `scripts/reports/backlog_counts.py` (imported by two other ops scripts specifically to avoid re-deriving its predicate), `scripts/analysis/classify_paper_records.py` (actively invoked by the live `/performance-review` skill), and `scripts/git-hooks/pre-commit` (the canonical hook source `install-hooks.sh` symlinks from — absent in `.git/hooks/` only because nobody ran the installer in this checkout, which is expected for an opt-in hook on a fresh clone). Note: `docs/audits/full-pipeline-structural-audit-2026-05-17.md` recommends deleting both `scripts/sprint015/` and `scripts/sprint047/` — that recommendation is **stale/wrong for sprint015** (test-covered, confirmed live) but happens to be **directionally right for sprint047** (confirmed broken above), for a reason that audit didn't cite (the 2026-05-10 cutover) and couldn't have verified at the time.

**`scripts/macro/**` (37 files): zero dead scripts.** Every file is referenced by at least a GitHub Actions workflow, a sibling script's import, `ROADMAP_MACRO.md`, or a `docs/research/*.md` design note. One near-miss worth recording for future audits of this directory: a narrow `grep` pattern for `scripts/macro/macro_sources.py` alone returns nothing — the file is only ever imported via this repo's `from macro_sources import ...` sibling-import idiom (confirmed live via `construction_sweep.py:350` and `valuation_snapshot_backfill.py:56`, both wired to scheduled workflows). A dead-script grep in this codebase must also check the bare `from <module> import <name>` form, not just the `scripts/<path>` and `scripts.<path>` forms.

## Coverage caveats, stated plainly

- This lane did not execute anything against a real VM, live database, or network — all checks were static reads, `bash -n`/`py_compile` syntax checks, grep-based reference checks, local dry-runs of read-only commands, and synthetic-diff functional tests of the CI guards. Two low findings (`verify-storage-setup-stale-telegram-bot-carveout` and the `ict-db-integrity.timer` enablement question raised in passing by the top-level-`.py` sub-lane) are explicitly **not settled** — they need a live-VM diag read this lane's scope didn't include.
- `check_impossibility_claims.py`'s self-test (`guard_selftests.py impossibility-claim`) passes independently of the finding above, because it plants its own fixture and never exercises the real-repo glob resolution (`_tracked_files()`) that the finding is about — a green self-test is not evidence against this finding.
- The five VM-watchdog scripts `check_heartbeat.py`, `check_db_integrity.py`, `check_web_api.py`, `check_data_dir.sh`, and `check_ib_gateway.py` are correctly **not** part of the PR-gating "78 guards" population (they're systemd-timer-invoked live-VM watchdogs/reporters, confirmed via `grep -rln` across `.github/workflows/*.yml` and `scripts/ci/run_guards.py` returning zero matches) — not dead, just a different category than CLAUDE.md's "78 guards" claim; worth a note if anyone is counting toward that figure.
- Every file in scope was read in full or (for the largest backtest harnesses) read completely for control flow plus every function whose name is load-bearing — no file was skipped.
