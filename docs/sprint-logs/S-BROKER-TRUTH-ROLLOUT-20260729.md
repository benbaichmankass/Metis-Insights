# Sprint Log: S-BROKER-TRUTH-ROLLOUT-20260729

## Date Range
2026-07-29 (single session, macro/roadmap continuation).

## Objective
Continue the Metis-Insights macro/roadmap workplan: (1) resolve the three open
prior-session macro DRAFT PRs; (2) execute assessment **rec #7 — broker-truth
cost coverage** across all accounts (the precondition M24 P3/P4 are blocked on).

## Tier
Tier-1 throughout (observability / read-paths / tooling / docs). Two Tier-2
systemd-unit behaviour changes (the fills/funding timers) shipped with explicit
operator deploy approval. No order-path, no `src/` live-trading logic, no config
gate touched.

## Starting Context
Prior session left three open DRAFT PRs (#7880 M1 event-study, #7884 macro-research
skill, #7888 FMP NO-BUILD) folded into a self-firing check-in, plus the rec-#7
broker-truth API-gap mapping as the next task. `docs/research/roadmap-toolbox-assessment-2026-07-29.md`
rec #7: "Only 3/8 accounts have broker-truth … blocks M24 net-R."

## Repo State Checked
`main` progressed across the session: `dfd0172` → `d5bf62f` (this session's merges
+ the sibling session's #7889/#7890/#7894). Coordination board #6927 checked; START/DONE
posted for each work unit; no merge-slot collision (only concurrent area was #7886
on `execute.py`/`database.py`, a different subsystem).

## Files and Systems Inspected
- `config/accounts.yaml` (Bybit/Alpaca/IB account creds + `api_key_env`/`symbols`/`alpaca_env`).
- `scripts/pull_exchange_fills.py`, `pull_exchange_funding.py`, `src/runtime/exchange_fills_puller.py`,
  `exchange_fills_store.py` (schema), `scripts/ops/pull_exchange_*_action.sh`.
- `deploy/ict-exchange-{fills,funding}-pull.{service,timer}` (found the scheduling
  ALREADY existed since 2026-07-13/07-19 — corrected an earlier wrong "no timer" claim).
- `src/units/accounts/alpaca_client.py`, `alpaca_options_exec.py::account_activities`,
  `src/units/accounts/ib_client.py`, `clients.py::ib_read_client_for`.
- `src/config/accounts_loader.py`, `scripts/check_canonical_config_loaders.py`, `ruff.toml`.

## Work Completed
**Prior-session PRs resolved:**
- **#7884** (macro-research skill) — merged. **#7888** (FMP `/stable` NO-BUILD) — merged.
- **#7880** (M1 event-study) — **closed broken**: its branch was reused after the
  calendar-spine squash-merge, so its head was already-merged content; the event-study
  code was on the sibling session's container, which re-pushed it clean as **#7889**
  (since merged). Stayed off #7889 to avoid a collision.

**Broker-truth rollout (rec #7) — 3 PRs:**
- **#7891 (merged, PR a)** — Bybit fills+funding pull made **multi-account, config-driven**
  (`live_bybit_fill_accounts` via the canonical `accounts_loader`; `--all-bybit-accounts`,
  fail-soft). Both daily services + on-demand action wrappers switched. New
  `src/runtime/exchange_accounts.py`; 10 tests.
- **#7895 (merged, PR b)** — Alpaca fills adapter (`/v2/account/activities` FILL →
  `exchange_fills` schema): `exchange_fills_alpaca.py` (pure mapper + cursor pagination,
  injectable), `live_alpaca_fill_accounts`, read-only `AlpacaClient.account_activities`,
  `pull_alpaca_fills.py --all-alpaca-accounts`, wired as a 2nd `ExecStart` on the daily
  fills timer. 15 tests. Verified the 3 Alpaca accounts have distinct creds (no double-count).
- **#7897 (docs, PR c)** — IB is the operator-gated tail: `reqExecutions` is clientId-scoped
  (a separate pull sees zero of the trader's fills), no Flex integration exists →
  `broker-truth-ib-flex-DESIGN.md` (Flex Web Service path + exact operator steps) instead of
  a blind parser. Secret slots `IB_FLEX_TOKEN`/`IB_FLEX_QUERY_ID` minted via issue #7896.

**Coverage:** 1 account actually pulled → **6/8 automated** (Bybit + Alpaca trios), 8/8 with
two operator secrets (IB Flex token + `bybit_2` UM export).

## Validation Performed
- 25 new unit tests green (`test_exchange_accounts.py` + `test_exchange_fills_alpaca.py`) +
  existing fills puller/store suites; real enumeration against live `accounts.yaml`.
- CI-equivalent ruff (`ruff check --select E4,E7,E9,F`) clean on every changed file;
  `canonical-config-loaders` + `bash -n` on wrappers pass. Full CI green on both merged PRs.
- Ruff footgun caught + fixed: local ruff 0.16 vs CI's default set — `ruff --fix` stripped
  the pullers' required `# noqa: E402`; restored + verified against CI's exact selector.

## Documentation Updated
- `docs/research/broker-truth-ib-flex-DESIGN.md` (new) — the IB Flex design + operator steps.
- `docs/research/roadmap-toolbox-assessment-2026-07-29.md` — rec #7 annotated (a/b shipped, IB gated).
- `ROADMAP.md` — M24 row noted broker-truth cost coverage WIDENED past `bybit_2` (advances the P3/P4 blocker).
- This sprint log.

## Contradictions or Drift Found
Corrected my own mid-session error (a genuine drift): initially reported "nothing runs
the fills pull on a schedule" — WRONG, the `deploy/ict-exchange-{fills,funding}-pull.timer`
units existed; the real gap was single-account, not no-timer. Surfaced the correction to
the operator. `check_canonical_doc_coherence.py`: all checks pass.

## Risks and Follow-Ups
- **IB Flex build** — gated on the operator creating a Flex token (`BL-20260729-IB-FLEX-FILLS`).
- Alpaca regulatory `FEE`/`CFEE` activities not captured (fills `fee=0` honest for commission-free equities) — a later slice.
- `bybit_2` lifetime wallet-truth is structurally UM-export-only (unchanged).

## Deferred Items
The IB Flex adapter code (fetch + XML→row mapper + 3rd timer ExecStart + `sync-vm-secrets`
wiring) — deferred to a fresh session once the operator token + a real Flex XML capture exist
(verify-before-build).

## Next Recommended Sprint
IB Flex fills adapter (once `IB_FLEX_TOKEN`/`IB_FLEX_QUERY_ID` are set).
- Required verification before starting: capture a real Flex XML fixture first.

## Wrap-Up Check
- doc-freshness run: `check_canonical_doc_coherence.py` PASS; decisions landed in ROADMAP (M24) +
  this sprint log + assessment rec #7 + health-review-backlog (`BL-20260729-IB-FLEX-FILLS`).
