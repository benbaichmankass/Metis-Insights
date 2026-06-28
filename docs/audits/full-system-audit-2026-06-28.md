# Full-System Audit — 2026-06-28 (branch `claude/full-system-audit-6h5q79`)

**Status:** IN PROGRESS. This is the live coordination + findings record for the
whole-system audit across all three repos (`ict-trading-bot`,
`ict-trader-dashboard`, `ict-trader-android`). Any concurrent Claude session
working this audit MUST read this file first and update it — it is the
cross-session source of truth for what is claimed, verified, and in flight.

Operator directives for this audit (2026-06-28):
1. Read all canonical docs/rules/specs; surface every contradiction before fixing.
2. Review **every line of code** across all three repos + everything live/running
   — no assumptions, no shortcuts, nothing reported as true unless personally verified.
3. Flagship wiring bug: **Alpaca account balance not showing on dashboard or Android app.**
4. **NEW (2026-06-28):** thoroughly fix Claude *workflow governance* — sessions
   not reading rules before working/committing, wasting time on tools they lack,
   and "racing" PRs to merge (retest churn, no cross-session PR-queue coordination).
   Close the gaps so multiple simultaneous sessions proceed smoothly.

## Method (per the `full-system-audit` skill)

Two axes: **Consistency** (docs agree with each other + code) and **Liveness**
(every artifact is actually reachable/run/wanted — the zombie hunt). Plus the
operator's two added axes: **wiring/display correctness** (the Alpaca class) and
**Claude workflow governance**.

Coverage is tracked so nothing is silently skipped. A finding is only marked
VERIFIED when backed by code read directly + (where live state matters) a diag
probe — never from a subagent summary or a doc claim alone.

## Verification environment constraints (confirmed this session)

- **Direct VM egress is firewalled** from this sandbox (`diag_fetch.sh` → curl
  timeout). Live-VM state must go through the **GitHub-issue diag relay**
  (`vm-diag-snapshot`, label `vm-diag-request`, title `[diag-request] <path>`),
  which serves **only `/api/diag/*` GET paths**. Trainer VM → `trainer-vm-diag`
  relay (arbitrary bash).
- **The dashboard (Streamlit Cloud) and Android app cannot be rendered** from
  this sandbox. UI verification = reading the code + confirming the live API
  data it consumes; there are no screenshots.

## Workstreams

- **A. Consistency / doc-drift** (Pass 1) — canonical docs vs each other + code.
- **B. Liveness / zombie hunt** (Pass 2) — dead integrations/units/gates/workflows.
- **C. Wiring & display correctness** — Alpaca balance flagship + every consumer
  field render path.
- **D. Claude workflow governance (NEW)** — session preflight discipline,
  tool-capability clarity, and cross-session PR-queue coordination.

---

## Findings log

Legend: ✅ VERIFIED (code read + evidence) · 🔎 LEAD (needs verification) ·
⏳ AWAITING LIVE PROBE · 🛠 FIX PROPOSED · ✔ FIXED.

### C — Alpaca balance flagship

- ✅ **The Alpaca balance code path is correct end-to-end.** Verified by reading:
  - `src/runtime/hourly_report.py::account_snapshots()` — enumerates
    `list_accounts()`, calls `account_balance(acc)`, reads `bal["total_usdt"]`,
    writes both `balance_snapshots.json` and the `balance_snapshots` DB table
    (via `_record_balance_snapshot_to_db`) for **every** account incl. on failure
    (api_ok=False).
  - `src/units/ui/data_loaders.py::account_balance_with_diagnostic` → dispatches
    `alpaca`/`oanda` → `_m15_client_balance_diagnostic` → `alpaca_client_for` →
    `AlpacaClient.balance()` (returns equity/cash float) → `{status:ok, total_usdt}`.
  - `src/units/accounts/clients.py::alpaca_client_for` — reads per-account
    `api_key_env`/`api_secret_env` (default `ALPACA_API_KEY_ID`/`_SECRET_KEY`;
    `alpaca_live` uses `..._LIVE`). Returns `None` if creds unset.
  - `src/web/api/routers/accounts.py` — `/accounts/balances` reads the DB table
    (`get_latest_balance_snapshots`) then JSON fallback. Clean.
  - Consumers are **config-driven** (enumerate every `/api/bot/config` account):
    dashboard `streamlit_app.py::page_accounts` (line ~3169) +
    `ict-trader-android` `AccountsScreen.kt` (`buildAccountRows`). So all 3 Alpaca
    accounts render as rows IF the balances envelope carries them.
  - Scheduler exists: `deploy/ict-hourly-snapshot.{service,timer}` (hourly) →
    `scripts/send_hourly_now.py` → `build_accounts_hourly_report` → `account_snapshots()`.
  - `ALPACA_API_KEY_ID(_LIVE)` / `..._SECRET_KEY(_LIVE)` ARE in
    `.github/workflows/sync-vm-secrets.yml` REQUIRED/OPTIONAL sets.
- ✅ **ROOT CAUSE CONFIRMED (live probes #4913 + #4914, 2026-06-28).** It is
  **`alpaca_live` only** — its dedicated live keys are **rejected by
  `api.alpaca.markets`**:
  - `db_info` (#4913): `balance_snapshots` has **5,530 rows** — writer/table/
    endpoint chain is healthy.
  - hourly journal (#4914): the 08:00 accounts report shows
    `alpaca_paper: bal $99,582.38 | API OK`,
    `alpaca_options_paper: bal $99,582.38 | API OK`, and
    **`alpaca_live: API ERROR`**, with four log lines
    `alpaca balance: request is not authorized`.
  - Verified the error source: `alpaca_client.py::balance()` line 146 logs
    `env.get("retMsg")` = Alpaca's HTTP-error `message`. The client was
    **constructed** (keys present — else it'd raise `MissingCredentialsError`
    with a different message), so the keys are **present on the VM but
    unauthorized for the live host**. The code wiring (api_key_env
    `ALPACA_API_KEY_ID_LIVE`, `alpaca_env: live` → `api.alpaca.markets`) is
    correct.
  - The apps render alpaca_live as a row with no balance ("—" / API-error),
    which is *correct* behaviour for a failed read — so "balance not showing" =
    the live-key auth failure, NOT a UI bug.
- ⚠️ **Sharper implication (confirming via #4917):** `balance()` and `place()`
  share the identical auth path (`_request`, same headers/host), so if
  `/v2/account` is unauthorized, **live orders are almost certainly failing
  too** — i.e. `alpaca_live` (flipped real-money-live 2026-06-26) may not
  actually be trading. The latest bot commit (#4908, "keep monitoring
  small-ticket fills on alpaca_live until confirmed") is consistent with that
  uncertainty. ⏳ #4917 (`journal?table=trades`) will confirm whether any
  alpaca_live fills exist post-2026-06-26.
- ❌ **EARLIER CREDS CONCLUSION WAS WRONG.** The operator had rotated keys
  repeatedly and confirmed the live keys work when used directly. The real
  cause is a **wiring bug**, surfaced by the operator's "check the endpoints"
  hint + screenshots (live key `AK…` → `api.alpaca.markets`; paper key `PK…` →
  `paper-api.alpaca.markets`).
- ✅ **TRUE ROOT CAUSE (verified across all three code paths) —
  `BL-20260628-ALPACA-LIVE-HOST`:** `accounts.yaml` declares
  `alpaca_live.alpaca_env: live`, but **none** of the three account-dict
  builders plumbed `alpaca_env` through, so `alpaca_client_for` fell back to
  `os.environ.get("ALPACA_ENV","paper")` = the **paper** host and sent the live
  `AK…` key to `paper-api.alpaca.markets` → `"request is not authorized"`:
  - `src/units/accounts/__init__.py::load_accounts` → `TradingAccount(...)`
    never passed `alpaca_env` (and `account.py` had no such field) → order
    ENTRY path (`coordinator.py:1199`) + close path
    (`order_monitor.py:1221` `getattr(acc,"alpaca_env")` → None).
  - `src/core/coordinator.py:1091` `account_cfg` dict omitted `alpaca_env`.
  - `src/units/ui/data_loaders.py::_load_yaml_accounts` passthrough tuple
    omitted `alpaca_env` → balance/positions READ path.
  - **Consequence:** `alpaca_live` (flipped real-money-live 2026-06-26) was
    **fully inert** — every order 401'd AND balance unreadable. Rotating keys
    never had a chance (wrong host, not bad key). Confirms the 3-day chase.
- ✅ **FIX (this branch) — plumb `alpaca_env`/`base_url`/`oanda_env` through all
  loaders:** `account.py` (new optional fields), `accounts/__init__.py`
  (`load_accounts` passes them), `coordinator.py` (`account_cfg` forwards them),
  `data_loaders.py` (read-path passthrough). Regression test
  `tests/test_alpaca_live_host_routing.py`. Verified: `alpaca_live` now resolves
  `base_url = https://api.alpaca.markets`; paper accounts stay on the paper host.
- 🔎➡️🛠 **FOLLOW-UP: a FOURTH loader was found post-deploy (verified on the live
  VM, 09:07 trader journal `alpaca positions: request is not authorized`).**
  #4916 fixed the three loaders feeding the balance + order-entry + close paths,
  but the **positions reconciler** builds its own account dict via
  `order_monitor.py::_load_account_cfgs_for_reconcile` →
  `accounts_loader.load_accounts_dict`, and that builder also omitted
  `alpaca_env` → `account_open_positions`'s alpaca branch kept dialling the paper
  host for `alpaca_live`. Fix staged (add `alpaca_env`/`base_url`/`oanda_env` to
  that dict) + regression test extended. **Held from merge** pending the other
  session's merge-queue clearing (operator directive 2026-06-28). This is the
  value of post-deploy live verification: the merged fix looked complete from
  the code but the journal proved a 4th path remained.
- ⚠️ **TIER-3 — gated on operator merge.** This change makes the real-money
  `alpaca_live` account actually trade live (orders will reach the live host and
  fill) for the first time. Draft PR #4916; **must NOT be merged/deployed without
  explicit operator approval** (live promotion of a real-money account). No
  credential change required.
- ⚠️ **Note on the hourly unit:** `ict-hourly-snapshot.service` runs
  `/usr/bin/python3` (system interpreter, not the trader venv) with
  `EnvironmentFile=-/home/ubuntu/ict-trading-bot/.env`. Verify (a) `.env` carries
  the Alpaca keys and (b) system python3 can import the balance path. (To verify
  via probes above.)

### A — Consistency / doc-drift (LEADS, to verify)

- 🔎 Bot serves endpoints **absent from CLAUDE.md's "Dashboard REST API" table**:
  `/api/bot/devices/*`, `/api/bot/pnl/exchange`, `/api/bot/positions/net`,
  `/api/bot/strategy/attribution`, and several `/api/diag/*` (`db_info`,
  `exchange_positions`, `shadow_stats`, `version`). Confirm each exists, then
  decide document-vs-remove. (Source: cross-repo endpoint inventory; needs
  direct router read.)
- 🔎 API contract consumer-side is otherwise clean: every dashboard + Android
  `/api/...` call maps to a real bot endpoint (0 orphan calls reported; verify
  spot-checks).

### B — Liveness / zombie hunt (LEADS, to verify)

- 🔎 Units in `deploy/` but **NOT in diag `_CANONICAL_UNITS`**
  (`src/web/api/routers/diag.py`): `ict-ib-gateway-reset.timer`,
  `ict-shadow-log-rotate.{service,timer}`, `ict-devnull-guard.{service,timer}`,
  `ict-smoke-once.service`, `claude-vm-runner@.service`. For each: is it a real
  active unit (→ add to `_CANONICAL_UNITS` for diag coverage) or dead (→ remove)?
  `ict-ib-gateway-reset` is documented as a real timer in CLAUDE.md → likely a
  diag-coverage gap, not a corpse. **Probe needed:** live `/api/diag/services`
  + the gateway VM.
- ✅ **VERIFIED zombie — `ict-bot.service` in diag `_CANONICAL_UNITS`**
  (`src/web/api/routers/diag.py:74`). No `deploy/ict-bot.service` exists; the
  live trader is `ict-trader-live.service` (also in the list, confirmed active
  in the 09:07 journal). `ict-bot.service` is the retired pre-rename trader
  unit — a dead entry that makes `/api/diag/services` perpetually report a
  not-found unit. **Fix:** remove it from `_CANONICAL_UNITS`. Tier-1, batch into
  a separate audit-cleanup PR (NOT the Tier-3 Alpaca branch).
- ✅ **VERIFIED diag coverage gaps** — `scripts/install_systemd_units.sh` globs
  `deploy/*.service|*.timer` (line 73), so all deploy units are installed, but
  `_CANONICAL_UNITS` omits these real recurring timers: `ict-shadow-log-rotate.
  {service,timer}`, `ict-devnull-guard.{service,timer}` (trader VM), and
  `ict-ib-gateway-reset.{service,timer}` (gateway VM — partly expected since
  diag/services runs systemctl on the trader VM). `ict-smoke-once` /
  `ict-env-check` (one-shots) + `claude-vm-runner@` (template) are correctly
  excluded. **Fix:** add the trader-VM timers to `_CANONICAL_UNITS` (same Tier-1
  cleanup PR); confirm gateway-VM units belong in a gateway-scoped probe.
- 🔎 `oanda_practice` is fully shelved (mode dry_run, strategies [], creds unset
  since 2026-06-12) — documented-keep, not a zombie, but confirm the integration
  code isn't half-removed.
- ✅ **Brokers — all LIVE, no zombie.** `EXCHANGE_MAP` = {bybit, breakout, oanda,
  alpaca}; `accounts.yaml` routes bybit(2), alpaca(3), interactive_brokers(2),
  breakout(1), oanda(1). Every routed exchange has ≥1 account. **Tradovate fully
  purged** (0 refs in src/ + config/) — the prior corpse stayed dead.
- ✅ **VERIFIED vestigial routing path (zombie candidate, operator disposition).**
  `EXCHANGE_MAP` + `integrator.route_order` + `TradingAccount.place_order` are a
  legacy router superseded by `execute_pkg` (the live path, per-exchange branches
  in `src/units/accounts/execute.py`). Evidence: (a) the `EXCHANGE_MAP` stub
  classes RAISE `NotImplementedError` (`integrator.py:41` BybitAPI); (b)
  `EXCHANGE_MAP` omits `interactive_brokers` yet IB trades live — because IB goes
  through `execute_pkg`, not this map; (c) `coordinator.py:1082` documents that
  `account.place_order` was REMOVED from the live path (it raised
  NotImplementedError — the VWAP "0 fills" bug); (d) the only `.place_order(`
  live calls are on exchange CLIENTS, not `TradingAccount`. Kept alive ONLY by
  tests (`test_s010_accounts.py`). Per the disposition-flip rule this needs a
  live consumer or a written keep-justification; it has neither. **Disposition:
  operator call** — remove the vestigial path (+ its tests) OR document why it's
  kept. Non-trivial (touches account.py/integrator.py); NOT auto-removed.
- 🔎 Env-gate inventory from the subagent leaned on CLAUDE.md for many entries —
  **must be re-derived from actual `os.environ` call sites** before any are
  trusted or flagged.

### D — Claude workflow governance (NEW — design pending)

Operator-reported failure modes + candidate fixes (to be designed, not yet built):
1. **Sessions don't read rules/files before working or committing.** Existing
   controls: SessionStart hook (`.claude/settings.json`), binding skill-first
   rule, "read every file you'll change in full." Gap = enforcement. Candidate:
   a pre-commit / pre-PR checklist gate + tighter, shorter canonical preflight.
2. **Wasting time on tools the session lacks.** The "PM-side session
   capabilities" section documents this but is buried. Candidate: surface a hard
   capability preflight; make `before-asking-the-operator` /
   `credentials-and-vm-mutations` triggers louder.
3. **PR-merge racing / no cross-session queue.** No coordination mechanism
   exists. Candidate: a lightweight repo-side **PR queue/lock** protocol (a
   claimed coordination file or a label-based single-writer "merge train") +
   require-branch-up-to-date-before-merge + this audit doc as the live board.

---

## Coverage map (files personally read in full — append as you go)

- `config/accounts.yaml` (full) ✅
- `src/runtime/hourly_report.py` (full) ✅
- `src/web/api/routers/accounts.py` (full) ✅
- `src/units/ui/data_loaders.py` (balance section ~633–950) ✅ (rest pending)
- `src/units/accounts/clients.py` (factories ~84–168) ✅ (rest pending)
- `deploy/ict-hourly-snapshot.{service,timer}` ✅
