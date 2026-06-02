---
name: new-broker
description: Wire a new broker (futures, FX, crypto, prop firm) into the bot's execution path. Use when the operator says "add Tradovate", "integrate <broker>", "wire up a new exchange", or anything that adds a new entry to `src/units/accounts/integrator.py::EXCHANGE_MAP`. Covers credentials handoff (via the `credentials-and-vm-mutations` rule), the package + factory + integrator + executor wiring, `accounts.yaml` entry, and verification. NOT for tuning an existing broker's params and NOT for adding a strategy (that's `new-strategy`).
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
   names from the broker's config dataclass (e.g.
   `TradovateConfig.load()` reads `TRADOVATE_USERNAME`,
   `TRADOVATE_PASSWORD`, `TRADOVATE_APP_ID`, …).
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
   `<Broker>Adapter` facade. Mirror `src/units/accounts/tradovate/`.
2. **Factory** in `src/units/accounts/clients.py::<broker>_client_for(account)`
   — returns the adapter or `None` when creds are missing. Reads from
   `os.environ`; does its own cred validation before constructing the
   adapter. Mirrors `velotrade_client_for` / `tradovate_client_for`.
3. **Integrator entry** in `src/units/accounts/integrator.py` —
   `<Broker>API` class + `EXCHANGE_MAP["<broker>"]` registration.
4. **Executor branch** in `src/units/accounts/execute.py::_submit_order`
   for `exchange == "<broker>"` — same missing-client → ping contract
   as the IB / Tradovate branches; translate the bot's order shape to
   the broker's `OrderRequest`; translate broker errors to the
   `retCode`-style envelope the coordinator's diagnostic-ping wrapper
   formats.
5. **Accounts entry** in `config/accounts.yaml` — ships INERT with
   multiple independent gates:
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
7. **Credential-propagation workflow** — if no existing workflow
   (`rotate-account-keys`, `system-actions`, etc.) covers the broker's
   secrets, **first** open a Tier-1 PR adding
   `.github/workflows/provision-<broker>-creds.yml` (mirror
   `rotate-account-keys.yml`'s `SendEnv` pattern; values never reach
   logs). This is your prerequisite for the operator's secret-add to
   actually mean anything.
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
- Open the Tier-3 strategy-assignment PR (a strategy moves from
  another broker to this one, or a new shadow assignment is added);
  draft, wait for explicit operator approval, merge.
- Promote `mode: dry_run` → `mode: live` via the `set-account-mode`
  operator action only after the smoke test passes and the operator
  approves.

## Composes with

- `credentials-and-vm-mutations` — the deeper rule layer this skill
  applies. Read it before writing the operator section.
- `vm-ops` — for VM-side mutation mechanics.
- `git-actions` — for the workflow dispatch issue-body format.
- `diag-data` — for smoke-test and post-state verification reads.
- `new-strategy` — when strategy-assignment is the next step after
  wiring is complete.
- `doc-freshness` — at end of session.
