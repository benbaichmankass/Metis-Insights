# Sprint Log: S-M15-ALPACA-LIVE (alpaca_live live activation)

## Date Range
- Start: 2026-06-25
- End: 2026-06-25 (activation complete; soak begins)

## Objective

Activate `alpaca_live` — the real-money Alpaca brokerage account that had been
funded but inert behind three stacked gates (credential gate: env vars absent →
factory returns None → `configured: False`; strategy gate: `strategies: []`;
mode gate: `mode: dry_run`). Resolve all three gates and confirm the account
reaches `mode: live` with 12 ETF strategies routed.

## Tier
- PR #4548 (credential wiring fix): Tier 1 — CI/tooling fix, autonomous.
- Issue #4552 (sync-vm-secrets run): Tier 1 — read/observe.
- PR #4551 (strategy assignment): Tier 3 — `config/accounts.yaml` edit, operator
  approved in chat 2026-06-25 ("merge").
- Issue #4553 (mode flip): Tier 3 — `set-account-mode` system-action, operator
  pre-authorized as part of the overall activation approval.

## Root Cause Analysis

`alpaca_live` had been inert since the Alpaca integration shipped (2026-06-11).
Three independent gates stacked:

1. **Credential gate** (`configured: False`): `sync-vm-secrets.yml` had a
   structural bug — the two steps that inject secrets (`Report OPTIONAL secrets
   presence` + `Sync secrets to VM`) did NOT declare
   `ALPACA_API_KEY_ID_LIVE` / `ALPACA_API_SECRET_KEY_LIVE` in their `env:` blocks.
   GitHub Actions only injects a secret into a step when it is explicitly mapped
   via `${{ secrets.NAME }}` in that step's `env:`; without that declaration the
   variable is empty. The factory's `alpaca_client_for()` reads the env var at
   runtime and returns `None` if either key is empty — producing
   `configured: False` with no visible error.

2. **Strategy gate** (`strategies: []`): `alpaca_live` was declared with an empty
   strategy list. No strategies routed → no signals attempted → no orders.

3. **Mode gate** (`mode: dry_run`): Even with credentials + strategies, the mode
   gate would have silently discarded every order before placement.

## Work Completed

### PR #4548 — Fix sync-vm-secrets.yml (credential gate, Tier 1)
- Added `ALPACA_API_KEY_ID_LIVE: ${{ secrets.ALPACA_API_KEY_ID_LIVE }}` and
  `ALPACA_API_SECRET_KEY_LIVE: ${{ secrets.ALPACA_API_SECRET_KEY_LIVE }}` to
  BOTH the "Report OPTIONAL secrets presence" step and the "Sync secrets to VM"
  step in `.github/workflows/sync-vm-secrets.yml`.
- Merged to `main` SHA `6ddfdecd`.

### Issue #4552 — sync-vm-secrets run (Tier 1)
- Dispatched the `sync-vm-secrets-request`-labelled issue to propagate live
  Alpaca credentials to the VM.
- Result: exit 0; both `ALPACA_API_KEY_ID_LIVE` and `ALPACA_API_SECRET_KEY_LIVE`
  confirmed present in the live VM `.env`.

### PR #4551 — Assign 12 ETF strategies to alpaca_live (strategy gate, Tier 3)
- Updated `config/accounts.yaml` `alpaca_live.strategies` from `[]` to all
  12 paper-validated ETF strategies:
  - Daily (6): `spy_trend_long_1d`, `qqq_trend_long_1d`, `gld_pullback_1d`,
    `iwm_trend_long_1d`, `tlt_pullback_1d`, `ief_pullback_1d`
  - Intraday 1h (6): `gld_pullback_1h`, `slv_trend_1h`, `spy_pullback_1h`,
    `qqq_pullback_1h`, `tlt_pullback_1h`, `uso_trend_1h`
- Operator approved in chat; merged to `main` SHA `4de2c4a6`.
- CI: 18/18 checks passed.

### Issue #4553 — set-account-mode alpaca_live live (mode gate, Tier 3)
- Dispatched the `system-action`-labelled issue:
  `action: set-account-mode / account: alpaca_live / mode: live / reason: …`
- System-action workflow confirmed: `alpaca_live.mode` set to `live`, service
  restarted, `ict-trader-live.service` active.

## Validation Performed

- Sync workflow exit 0: live Alpaca creds verified present on VM.
- PR #4551 CI: 18/18 checks green.
- set-account-mode workflow: `mode: live` confirmed, service active.
- `alpaca_live` state at activation: `mode: live`, 12 strategies routed,
  `configured: True` (credentials present), `account_class: real_money`.
- **First live signals expected on next US market open (13:30 UTC)**; soak
  watch: confirm order-package rows accumulate for real-money alpaca_live
  (distinct from alpaca_paper's existing paper rows).

## Doc Freshness (end-of-session sweep)

- `canonical-doc-coherence.py`: 4/4 checks PASS.
- Dead IP grep: all `158.178.210.252` references are in historical/terminated
  context; the CI allow-list filter correctly excludes them.
- CLAUDE-RULES-CANONICAL.md: instruction hierarchy mirrors CLAUDE.md exactly.
- **Gap found + fixed**: ROADMAP.md M15 row updated below with today's activation.
- **Minor item logged to backlog**: `docs/security/permissions-tiers.md:76`
  still describes Tier-3 SSH identity as `158.178.210.252` (the terminated x86
  micro) rather than the current live VM `141.145.193.91` (Ampere, migrated
  2026-06-14, terminated 2026-06-16). Logged as `BL-20260625-STALE-IP-PERMS`.

## Contradictions or Drift Found

- None in the canonical set (CLAUDE.md / CLAUDE-RULES-CANONICAL.md /
  ARCHITECTURE-CANONICAL.md / ROADMAP.md hierarchy). All agree on the two
  execution gates, the removed-gate list, the 3-stage ML ladder, and the
  single-sourced VM topology.
- `docs/security/permissions-tiers.md:76` — stale VM IP (doc-vs-reality, Tier-1
  doc fix). Logged to health-review backlog `BL-20260625-STALE-IP-PERMS`.

## Risks and Follow-Ups

- Soak monitoring: confirm `alpaca_live` order-package rows accumulate and are
  tagged `account_class: real_money` (not blended with `alpaca_paper`).
- First real fills: verify broker-side bracket SL/TP attaches correctly on
  Alpaca production API (same client path as `alpaca_paper` but different env).
- Strategy rejection patterns on `alpaca_live` first day: netting guard, session
  gate, daily-loss cap — confirm none is inadvertently blocking all signals.
- Real-money position sizing: `alpaca_live` inherits the same risk parameters as
  `alpaca_paper`; confirm per-trade risk % is appropriate for live capital.
- OANDA `oanda_practice` remains paper — no change this session.
