---
name: new-broker
description: Wire a new broker (futures, FX, crypto, prop firm) into the bot's execution path. Use when the operator says "integrate <broker>", "wire up a new exchange", or anything that adds a new entry to `src/units/accounts/integrator.py::EXCHANGE_MAP`. Covers credentials handoff (via the `credentials-and-vm-mutations` rule), the package + factory + integrator + executor wiring, `accounts.yaml` entry, and verification. NOT for tuning an existing broker's params and NOT for adding a strategy (that's `new-strategy`).
---

# /new-broker — wire a new broker into the execution path

Every broker integration on this bot follows the same architecture
regardless of how the broker's auth happens to be shaped (one API key,
seven env vars, an OAuth flow, a desktop Gateway login). The auth
material varies; the contract doesn't.

**First, invoke `credentials-and-vm-mutations`.** That skill owns the
operator-side rule. This skill is the broker-specific application —
it says nothing the contract doesn't already imply about who does
what, only about which files Claude touches.

## The operator surface (derive these from `credentials-and-vm-mutations`, not from a precedent runbook)

The operator does exactly three things, no more, no less:

1. **Originate at the third party** — sign up at the broker, create an
   API app, capture cid/secret/keys, choose any device-id strings.
2. **Add the values to GitHub Actions secrets** with the exact env-var
   names the broker's config dataclass reads from the environment
   (e.g. `<BROKER>_API_KEY`, `<BROKER>_API_SECRET`, …).
3. **Ping you** when the secrets are in Actions.

Everything else — propagating values to the VM, listing accounts on
the broker, running smoke tests, patching the account-id back into
YAML, promoting modes — is yours, via workflows. Re-read
`credentials-and-vm-mutations` if you're about to write an operator
step that doesn't fit one of those three lines.

## The Claude-side wiring (touch points)

In one PR or a small chain, in roughly this order. The list is the set
of *places* a new broker has to land, not a rigid sequence — combine,
split, or reorder as the broker's specifics warrant. What's invariant
is that EVERY new broker touches each of these.

1. **Self-contained package** under `src/units/accounts/<broker>/` —
   env-driven config (demo by default; live is a config flip, no code
   change), auth, REST / WS clients with retry + reconnect, internal
   domain models, services (account / market-data / order / position),
   risk manager, recorder, event bus, broker-agnostic
   `<Broker>Adapter` facade — a self-contained module under
   `src/units/accounts/<broker>/`.
2. **Factory** in `src/units/accounts/clients.py::<broker>_client_for(account)`
   — returns the adapter or `None` when creds are missing. Reads from
   `os.environ`; does its own cred validation before constructing the
   adapter. Mirrors `oanda_client_for` / `alpaca_client_for`.
2b. **PnL source declaration** (same file, `clients.py`). Every account
   resolves realised PnL the same way — *prefer broker truth, fall back to
   local compute* — and which path applies is a **declared property of the
   integration, not a per-account flag or a hardcoded special-case**. Decide:
   does this broker expose an authoritative closed-PnL reader the bot will
   consume?
   - **No (the common case — futures/FX/most paper venues):** do nothing.
     The integration is **local by default**: its closed/orphaned trades get
     fee-blind-but-correct PnL from `order_monitor._sweep_local_pnl_for_unpriced`
     → `src.runtime.local_pnl` (entry/exit/qty × `contract_value_usd` from
     `config/instruments.yaml` — so **add the broker's instruments there with
     the right `contract_value_usd`**, or PnL will be off by the multiplier).
   - **Yes (broker has a closed-pnl endpoint, e.g. Bybit):** extend
     `account_closed_pnl_for_trade` to read it AND add the exchange string to
     `BROKER_PNL_READER_EXCHANGES`. That account's PnL is then recovered
     fee-accurate by `_sweep_pending_pnl_from_bybit`; the local sweep only
     backstops rows older than the broker's retention window.
   Default-local means a forgotten declaration never strands a trade at
   `$0.00` (Prime Directive). Add a `tests/test_<broker>_wiring.py` assertion
   for the expected `exchange_has_broker_pnl_reader("<broker>")` value.
3. **Integrator entry** in `src/units/accounts/integrator.py` —
   `<Broker>API` class + `EXCHANGE_MAP["<broker>"]` registration.
4. **Executor branch** in `src/units/accounts/execute.py::_submit_order`
   for `exchange == "<broker>"` — same missing-client → ping contract
   as the IB / Bybit branches; translate the bot's order shape to
   the broker's `OrderRequest`; translate broker errors to the
   `retCode`-style envelope the coordinator's diagnostic-ping wrapper
   formats.
5. **Accounts entry** in `config/accounts.yaml` — ships INERT with
   multiple independent gates:
   - `account_class: paper | real_money` — **REQUIRED** on every account
     (the paper/real funding category; CI-guarded by
     `scripts/check_account_class.py`). A new broker is almost always
     `paper` first (practice/demo venue). Do NOT use `demo: true` to
     mean "paper" — `demo` is the Bybit-only transport flag.
   - `mode: dry_run` with inline `# dry-run-guard: allow — <reason>`
     marker (CI gate).
   - `strategies: []` (coordinator's per-account filter blocks every
     signal).
   - Broker-specific account-id field set to `0` / unset (executor
     refuses on zero).
   - Symbol list declared but inert via the gates above.
   Each gate is independent; flipping one alone never wakes the account.
6. **Master-template placeholder** in
   `config/master-secrets.template.yaml` — add a
   `<broker>.accounts.<account_id>` block so the per-account drift
   guard (`tests/test_render_env_from_master.py`) passes. Use
   `no_secret: true` when the broker's creds are read directly from
   `os.environ` and not rendered through the per-account loop.
7. **Credential propagation** — add the broker's env-var names to the
   `REQUIRED_SECRETS`/`OPTIONAL_SECRETS` lists in the canonical
   `.github/workflows/sync-vm-secrets.yml` workflow (and to the
   placeholder list in `.github/workflows/init-actions-secrets.yml` so
   the operator gets pre-created empty slots to paste into). **The
   secret list lives ONLY in those workflows** — `scripts/ops/sync_vm_secrets.sh`
   is generic (it mirrors whatever the workflow passes it and carries no
   per-broker list to edit), so do NOT try to add names there
   (BL-20260716-NEWBROKER-SYNC-SCRIPT-DOC). Use `OPTIONAL_SECRETS` for
   any new broker so the workflow tolerates the operator-not-yet-provisioned
   state instead of failing. Do NOT add a per-broker provisioning
   workflow — `sync-vm-secrets.yml` is the single workflow that owns
   Actions → VM `.env` mirroring; broker-specific workflows are an
   anti-pattern that proliferates files and drifts.
8. **Tests** under `tests/test_<broker>_wiring.py` — `EXCHANGE_MAP`
   registration, factory cred handling, `_submit_order` edge cases
   (missing client, wrong type, zero account-id, dry-run path),
   `accounts.yaml` load. Plus the package's own unit tests under
   `tests/unit/<broker>/`.
9. **Runbook** at `docs/runbooks/<broker>-integration.md` — derives
   the operator-step section from `credentials-and-vm-mutations`, not
   from another runbook. Three operator steps + a ping; everything
   else is described as Claude-side action.
10. **Architecture + roadmap touch** —
    `docs/ARCHITECTURE-CANONICAL.md` (Step 6 broker-execution
    paragraph lists the new broker), `ROADMAP.md` (one-line entry
    under the active milestone queue if it deserves visibility).

## After the operator pings you

Once the operator has done their three steps, you drive these
autonomously:

- Dispatch the credential-propagation workflow (mechanics in the
  `git-actions` and `vm-ops` skills).
- Pull account-discovery info via the broker's `list_accounts` CLI
  through the diag relay.
- Open a tiny Tier-1 PR patching the discovered account id into
  `accounts.yaml`.
- Run the broker's smoke test on the VM via the diag relay; report
  results.
- **Verify PnL resolves end-to-end** once the account has a closed trade:
  pull the journal row via the diag relay and confirm `pnl` is non-NULL
  (broker-truth for a declared-broker integration; local-compute with
  `notes.pnl_source="local_compute"` otherwise). A closed trade stuck at
  `pnl NULL` / `$0.00` means the PnL source wasn't declared (step 2b) or the
  instrument's `contract_value_usd` is missing.
- Open the Tier-3 strategy-assignment PR (a strategy moves from
  another broker to this one, or a new shadow assignment is added);
  draft, wait for explicit operator approval, merge.
- Promote `mode: dry_run` → `mode: live` via the `set-account-mode`
  operator action only after the smoke test passes and the operator
  approves.

## Composes with

- `before-asking-the-operator` — the broader runner-not-operator rule;
  invoke any time you find yourself about to write an operator-side
  instruction outside the three legitimate categories.
- `credentials-and-vm-mutations` — the credentials-specific application
  of that rule. Read it before writing the operator section.
- `vm-ops` — for VM-side mutation mechanics.
- `git-actions` — for the workflow dispatch issue-body format.
- `diag-data` — for smoke-test and post-state verification reads.
- `new-strategy` — when strategy-assignment is the next step after
  wiring is complete.
- `doc-freshness` — at end of session.
