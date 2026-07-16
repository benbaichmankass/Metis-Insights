# Sprint Log: S-PAPER-PORTFOLIO

## Date Range
- Start: 2026-07-16
- End: 2026-07-16 (config phase; UI + deploy-activation ongoing)

## Objective
- Primary goal: add two PAPER accounts that mirror the *actual live-traded
  portfolio* (bybit_2 real money; alpaca_live real money) on paper money, so the
  real portfolio's performance + the risk-management plumbing can be evaluated
  WITHOUT the small-account trading constraints of the real books and WITHOUT the
  noise of the full soak roster.
- Secondary goals: (a) give consumers a way to distinguish the live-portfolio
  mirror from the data-only soak books so the app "Paper" view can scope to the
  portfolio (soak on the Accounts page only); (b) propagate the new credentials
  and activate on the live VM.

## Tier
- Tier 3 (touches `config/accounts.yaml`).
- Justification: adds accounts + routing to `config/accounts.yaml` — a Tier-3
  file per § Permission Tiers / § VM authority split hard limits. Operator
  approved the merge in chat (2026-07-16), plus the two roster decisions
  (drop Alpaca affordability proxies; leave soak books unthinned).

## Starting Context
- Active roadmap items: M15 (multi-broker paper venues) / M-livetrade
  (live-trade management + real/paper separation contract).
- Prior sprint reference: S-PROXY-EQUITIES-ALPACA-LIVE-2026-07-07 (the
  affordability-proxy rationale being diverged from here); S-NOTIF-STREAMLINE
  (the real/paper KPI-separation contract this rides on).
- Known risks at start: getting the account-key wiring right (Bybit demo secret
  auto-derivation; Alpaca dedicated login); not stranding the accounts with a
  false "account down" alert if creds aren't synced before the config deploys.

## Repo State Checked
- Branch or commit reviewed: `claude/paper-portfolio-accounts-31p76k` off `main`
  @ `bb3b093`.
- Deployment state reviewed: live trader `ict-bot-arm` (141.145.193.91); the two
  paper-portfolio accounts activate after `sync-vm-secrets` (creds → `.env`) then
  `pull-and-deploy` (new `accounts.yaml`) — order matters so the accounts are
  configured before they're loaded.
- Canonical docs reviewed: `docs/CLAUDE-RULES-CANONICAL.md` (full), root
  `CLAUDE.md`, `docs/ARCHITECTURE-CANONICAL.md` (change log + persistence).
  Skills invoked: `session-coordination`, `new-broker`, `db-wiring`,
  `credentials-and-vm-mutations`, `doc-freshness`.

## Files and Systems Inspected
- Code files inspected: `src/units/accounts/clients.py`
  (`resolve_credentials`/`_derive_secret_env`/`bybit_client_for`/`alpaca_client_for`),
  `src/web/api/routers/bot_config.py` (`_ACCOUNT_PUBLIC_FIELDS`,
  `_public_account`).
- Config files inspected: `config/accounts.yaml`,
  `config/master-secrets.template.yaml`, `config/strategies.yaml`
  (execution gates of the mirrored rosters).
- Deployment files inspected: `.github/workflows/sync-vm-secrets.yml`,
  `scripts/ops/sync_vm_secrets.sh` (confirmed the secret-name list is
  workflow-driven, so no script change is needed).
- Docs inspected: `docs/ARCHITECTURE-CANONICAL.md`, `ROADMAP.md`,
  `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.
- Services or timers inspected: n/a (config + docs change).
- GitHub Actions workflows inspected: `sync-vm-secrets.yml`, `arch-doc-guard.yml`,
  `account-class-guard.yml`, `strategy-risk-guard.yml`.

## Work Completed
- `config/accounts.yaml`: added `bybit_portfolio` (Bybit demo, `account_class:
  paper`, `paper_role: portfolio`, `api_key_env: BYBIT_API_KEY_3`, linear/3×;
  mirrors bybit_2's roster + symbols) and `alpaca_portfolio` (Alpaca paper host,
  dedicated login `ALPACA_API_KEY_PAPER_PORTFOLIO`/`_SECRET_KEY_PAPER_PORTFOLIO`,
  `paper_role: portfolio`; mirrors alpaca_live's roster + symbols MINUS the
  affordability proxies `splg_trend_long_1d`/`iaum_pullback_1d` + SPLG/IAUM).
- New optional per-account field `paper_role: portfolio | soak` surfaced on
  `/api/bot/config` (added to `_ACCOUNT_PUBLIC_FIELDS`) — the hook consumers use
  to scope the "Paper" view to the portfolio and keep soak books on the Accounts
  page only.
- `.github/workflows/sync-vm-secrets.yml`: added the 4 new secret names to
  `OPTIONAL_SECRETS` + the two step env-mappings.
- `config/master-secrets.template.yaml`: placeholder blocks for both accounts
  (satisfies the per-account drift guard).
- `docs/ARCHITECTURE-CANONICAL.md`: change-log row; `ROADMAP.md`: ledger row;
  this sprint log.
- `tests/test_paper_portfolio_accounts.py`: enforces the ROSTER-SYNC invariant
  (bybit_portfolio == bybit_2 exactly; alpaca_portfolio == alpaca_live minus the
  two proxies) + paper/paper_role/key-wiring + `paper_role` in the config
  allowlist.

## Validation Performed
- Tests run: `scripts/check_account_class.py --list` → clean (11 accounts). The
  new `tests/test_paper_portfolio_accounts.py` assertions verified by hand
  (pytest not installed in the sandbox) — all mirror-invariant + wiring checks
  pass. `_derive_secret_env('BYBIT_API_KEY_3')` → `BYBIT_API_SECRET_3`.
- Dry-runs or staging checks: `load_accounts_dict` parses both accounts with the
  expected fields; the render-env drift-guard logic reproduced locally → all
  accounts covered.
- Manual code verification: confirmed no new tick-loop symbols (all already in
  the union) and no `strategies.yaml` change (mirrored rosters are all
  `execution: live`; `ict_scalp_5m` is globally `shadow` and shadow-logs on
  bybit_2 too). Confirmed the sync script reads its name list from the workflow
  env (no per-secret list to update).
- Gaps not yet verified: live-VM activation (secrets sync + deploy + a real
  `/api/bot/config` read showing `paper_role`, and the first paper trades landing
  in `trade_journal.db`) — the Ship-Autonomously post-deploy verification, done
  after merge.

## Documentation Updated
- Rules doc updates: none needed.
- Architecture doc updates: change-log row (S-PAPER-PORTFOLIO).
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): none — no pipeline-stage
  change (new accounts ride the existing coordinator/executor/journal path).
- Roadmap updates: Historical Sprint Ledger row.
- GitHub Actions doc updates: none (sync-vm-secrets edit is self-documenting).
- Subsystem doc updates: inline `config/accounts.yaml` account comments.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- Minor: the `new-broker` skill step 7 says to add secret names "to
  `scripts/ops/sync_vm_secrets.sh`" as well, but that script is now generic — it
  reads `SYNC_REQUIRED`/`SYNC_OPTIONAL` from the workflow env and has no
  hardcoded per-secret list, so only the workflow needs editing. Field beats
  comment: the script IS generic. Logged as a doc-drift note (not blocking).

## Risks and Follow-Ups
- Remaining technical risks: if `sync-vm-secrets` is not run BEFORE
  `pull-and-deploy`, the new accounts load without creds → `account_open_positions`
  returns None → a false `account_down` latched alert until secrets land. Mitigated
  by sequencing secrets-first.
- Remaining product decisions (Tier 3): none open (operator approved merge +
  both roster decisions).
- Blockers: none.

## Deferred Items
- Dashboard "Paper" = portfolio; soak on Accounts page only (ict-trader-dashboard).
- Android "Paper" = portfolio; soak on Accounts screen only (ict-trader-android).
  Both are driven off `paper_role` from `/api/bot/config` (data-driven, no
  hardcoded ids), so they do not depend on the roster contents.

## Next Recommended Sprint
- Suggested next sprint: the dashboard + Android "Paper"-view scoping, then verify
  the paper-portfolio books are trading live (first fills in the journal).
- Why next: closes the operator's UI directive and confirms end-to-end activation.
- Required verification before starting: this PR merged + deployed;
  `/api/bot/config` returns `paper_role: portfolio` on both accounts.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. (N/A — no pipeline-stage change.)
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
