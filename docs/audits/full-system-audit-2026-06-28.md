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
- **Conclusion:** the symptom is NOT a missing code branch. Cause is in the
  **live layer**: creds not present in the VM process env, `alpaca.balance()`
  failing at runtime, or the hourly job not running. ⏳ Probes in flight:
  issue **#4913** (`db_info` — is `balance_snapshots` populated?) and **#4914**
  (`journalctl ict-hourly-snapshot.service` — does it run, does alpaca raise?).
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
- 🔎 `oanda_practice` is fully shelved (mode dry_run, strategies [], creds unset
  since 2026-06-12) — documented-keep, not a zombie, but confirm the integration
  code isn't half-removed.
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
