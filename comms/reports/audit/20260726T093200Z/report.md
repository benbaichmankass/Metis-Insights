# Full-system audit report

- Generated: 2026-07-26T09:32:00+00:00
- Window: 2026-07-09 → 2026-07-26T09:32:00+00:00
- Roll-up grade: caution

Full-system audit (all 3 repos + both VMs + canonical store, 8 workstreams). The recent infra push — M28 macro/value, M27 15m scalp legs, M26 conflict taxonomy, M0a/M0b layer guard — is COMPLIANT and fit for purpose: zero Prime-Directive / Tier-3 / isolation violations, 19/19 CI guards + ruff green, layer-guard genuinely enforcing, consumer wiring has no breaks, live VM on latest main, canonical store single-source-clean. Older surfaces mostly up to date; Tier-1 doc-drift + diag-observability + workflow-liveness fixes landed in draft PR #7635. Roll-up is CAUTION (not healthy) only because 3 live operational issues were surfaced for the operator — none a money-loss risk.


## Operator priorities
-. ict-mes-ibkr-pull.service is in a failed state on the live trader — The MES deep-history pull (keeps the trainer MES base fresh, BL-20260626) last-ran failed; its timer is active but the run errors. Was invisible to diag until this audit added it to _CANONICAL_UNITS. Needs journalctl -u ict-mes-ibkr-pull to diagnose. BL-20260726-MES-IBKR-PULL-SERVICE-FAILED.
-. 352 Claude-pings stranded in the pre-migration repo-path inbox — A ping writer resolves via a repo-relative path while the drainer reads the canonical DATA_DIR path, so 352 pings sit undelivered in /home/ubuntu/ict-trading-bot/runtime_logs/pending_claude_pings. Relates to open #6874. BL-20260726-CLAUDE-PING-INBOX-SPLIT-BRAIN.
-. Recurring bybit_2 smoke-test place_order NoneType failures — S-017 plumbing smoke can't resolve a client on the smoke path (real trader trades bybit_2 fine). Harness defect, no live-trade impact. BL-20260726-SMOKE-BYBIT2-PLACE-ORDER-NONETYPE.
-. PROPOSAL: promote layer-guard to a required branch-protection check — M0a/M0b layering guard enforces its 6 contracts and blocks CI on its own job, but it is NOT in REQUIRED_CONTEXTS (9 checks), so a PR can merge past a broken-layering advisory. Operator call (could newly-block merges). BL-20260726-LAYER-GUARD-NOT-REQUIRED.

## Monitoring (soaking / awaiting decision)
- `BL-20260724-TRAINER-GIT-DIVERGED` [ml · awaiting-decision] Trainer worktree stale at cdbaa718 (pre-vol-flag harness) — trainer re-pulls main per-run for backtests, but the persistent worktree lags. Known; verify next trainer cycle re-syncs. (next: next trainer cycle)
- `M28/M29 macro sleeve` [performance · soaking] sleeve.execution: shadow (no order path — P5 executor unbuilt). Observe-only soak accruing; graduation is Tier-3 + P4-backtest-gated. (next: P4 gate)
- `alpaca_live re-arm` [performance · awaiting-decision] Shelved to dry_run 2026-07-15; re-arm gated on the /performance-review portfolio-vs-bybit benchmark. (next: perf-review)

_report_id AUDIT-20260726_