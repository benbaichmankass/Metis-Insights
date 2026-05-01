# Sprint S-023 — Accounts wiring + API failure pings

> **Sprint type:** Bug-fix sprint, autonomous Claude execution.
> **Owner:** Claude Code (autonomous, self-merging).
> **PM:** Ben.
> **Created:** 2026-05-01. **Closed:** 2026-05-01.
> **Goal:** Make `/accounts_status` work end-to-end (every account
> shows a real balance instead of "balance unavailable") and turn
> every failed exchange API call into a Telegram alert with the
> direct API response.

## Operator brief

> "the next issue is fixing the accounts wiring. the accounts status
> command is generating a response that strongly implies that the
> system is currently not successfully sending the orders to the
> accounts ... I have a feeling the issue might be related to
> labeling of the secrets in the master file used for generating
> the env. the vm should always update automatically to the last
> repo so make sure the accounts call for the correct keys ...
> anytime an API interaction on the vm is unsuccessful, I should
> get a ping telling me what API and what the failure reason was
> (at least the direct response from the API)"

## Root cause

A **three-way label drift** across the secrets pipeline that no test ever
caught:

| File | Expected env-var name |
|---|---|
| `config/accounts.yaml` (since S-012 PR B3) | `BYBIT_API_KEY_1`, `BYBIT_API_KEY_2`, `BREAKOUT_API_KEY_1` |
| `config/master-secrets.template.yaml` | (no per-account block) |
| `scripts/render_env_from_master.py` | wrote `BYBIT_API_KEY` (singular) |

Result: no matter what was in the master file, the rendered `.env.live`
never set the env vars `bybit_client_for()` reads. Every account fell
through to `client is None` → `account_balance` returned `None` →
`Coordinator.accounts_status` fabricated the generic *"balance
unavailable (missing API creds or exchange rejected the request)"*.

Plus a **second contributing bug** in the same chain:
`_load_yaml_accounts` was projecting accounts.yaml entries into a dict
that *stripped* the `api_key_env` field. Even after PR1 wires up the
env vars, this would have kept the bot from finding them.

## Outcome at a glance

| Goal | Status | Shipped in |
|---|---|---|
| Master template has per-account block keyed by account_id | shipped | PR1 |
| Render script reads accounts.yaml + emits per-account env vars | shipped | PR1 |
| Render script surfaces warnings for accounts it skips | shipped | PR1 |
| Drift guard: every accounts.yaml account has master template entry | shipped | PR1 |
| `_load_yaml_accounts` preserves `api_key_env` / `api_secret_env` | shipped | PR2 |
| `/accounts_status` shows specific missing env-var names | shipped | PR2 |
| `/accounts_status` shows Bybit retCode + retMsg verbatim on auth fail | shipped | PR2 |
| `/balance` and `/accounts_status` use the same diagnostic logic | shipped | PR2 |
| Every failed exchange API call → Telegram ping | shipped | PR3 |
| Direct API response (retCode/retMsg, exception) attached to ping | shipped | PR3 |
| Token redaction before responses go to Telegram | shipped | PR3 |
| Per-fingerprint dedup prevents flap storms | inherited from S-022 PR1 | — |

## PRs merged

| # | Title | Net LOC |
|---|---|---|
| [#243](https://github.com/the-lizardking/ict-trading-bot/pull/243) | S-023 PR1: render script writes per-account env vars from accounts.yaml | +511 |
| [#244](https://github.com/the-lizardking/ict-trading-bot/pull/244) | S-023 PR2: specific `/accounts_status` diagnostics | +646 |
| [#245](https://github.com/the-lizardking/ict-trading-bot/pull/245) | S-023 PR3: API failure pings with direct response | +587 |

**Total net change:** ~+1,750 LOC across 3 PRs (code + tests + docs), all
self-merged after green tests.

## Deliverables

| Component | Location | Tests |
|---|---|---|
| Per-account env-var rendering | `scripts/render_env_from_master.py::_per_account_pairs` | `test_render_env_from_master.py` (+11) |
| Master template per-account block | `config/master-secrets.template.yaml` (`bybit.accounts.*`, `breakout.accounts.*`) | drift guards in `test_render_env_from_master.py` |
| Credentials diagnostic | `data_loaders.credentials_check` | `test_account_diagnostics.py` (7) |
| Bybit retCode parser | `data_loaders._bybit_response_error` | `test_account_diagnostics.py` (4) |
| Structured balance check | `data_loaders.account_balance_with_diagnostic` | `test_account_diagnostics.py` (6) |
| Coordinator integration | `Coordinator.accounts_status` | `test_account_diagnostics.py` (2) + `test_s021_smoke_and_status.py` updated |
| `_load_yaml_accounts` field preservation | `data_loaders._load_yaml_accounts` | covered transitively |
| API failure reporter | `src/runtime/api_reporting.py` | `test_api_reporting.py` (21) |
| Wired call sites | `data_loaders` (3) + `units/accounts/execute.py` (1) | integration tests |

**Total new tests added:** ~50 across 2 new test files + 11 added to
existing `test_render_env_from_master.py`.

## What changed for operators

### Before

`/accounts_status` showed for every account:
```
🟢 bybit_1 (bybit / regular)
  🔌 API: ❌ balance unavailable (missing API creds or exchange rejected the request)
  💵 Daily PnL: $+0.00 / limit $100
  📦 Max pos: $500 | Open: 0
```

A failed Bybit API call was logged at warning level locally but never
surfaced. The operator had no way to tell whether the system was even
**trying** to place orders.

### After

`/accounts_status` shows the specific failure per account:
```
🟢 bybit_1 (bybit / regular)
  🔌 API: ❌ missing env vars: BYBIT_API_KEY_1, BYBIT_API_SECRET_1
        (declared in config/accounts.yaml; export them in the
         systemd unit's EnvironmentFile, then restart the trader)
```

Or, once env is set but Bybit rejects:
```
🟢 bybit_1 (bybit / regular)
  🔌 API: ❌ Bybit error retCode=10003: API key is invalid.
```

Plus, **every API failure dispatches a Telegram ping** with the direct
response:
```
[ERROR] api_call → bybit_get_wallet_balance_failed: Bybit error retCode=10003: API key is invalid.
| exchange=bybit | op=get_wallet_balance | account=bybit_1
| retCode=10003 | retMsg=API key is invalid.
```

Rate-limited by the existing PR1 contract: 1 alert per fingerprint per
5 min, hard cap 30/hour, suppressed-count appended to the next message
that gets through.

## Operator action post-merge (one-time setup)

The fix is fully in-repo — the VM's auto-pull picks it up automatically.
What's still on you:

1. **Add per-account credentials to your master secrets file** under the
   new blocks the template now expects:
   ```yaml
   bybit:
     accounts:
       bybit_1:
         api_key:    "<real key>"
         api_secret: "<real secret>"
       bybit_2:
         api_key:    "<real key>"
         api_secret: "<real secret>"
   ```
2. Re-encrypt with sops and re-render the .env.live:
   ```
   python scripts/render_env_from_master.py \
     --master ~/secure/.../master-secrets.sops.yaml \
     --age-key-file ~/.../age-keys.txt \
     --profile vwap_btcusd_live \
     --out .env.live --allow-live
   ```
   **Read the warnings** the script prints — any account it skipped is
   named.
3. Restart the trader systemd unit.
4. Run `/accounts_status` from Telegram to confirm. Every account
   should show ✅ and a real USDT balance.

## Lessons learned

1. **Three-file label drifts are invisible without an end-to-end test.**
   The existing test suite covered each layer in isolation
   (`test_render_env_from_master.py` knew about the legacy singular
   names; `test_data_loaders.py` knew about the per-account contract;
   nothing crossed the boundary). PR1 added a drift guard
   (`test_template_account_ids_match_accounts_yaml`) that asserts
   every account_id in `accounts.yaml` has a matching block in the
   master template. That class of bug can't recur silently.

2. **`_load_yaml_accounts` projecting to a fixed schema was an attractive
   trap.** It looked safer than passing the whole YAML dict downstream,
   but it silently dropped fields that downstream code needed. The fix
   was to add a small allow-list of pass-through keys
   (`api_key_env`, `api_secret_env`, `type`, `risk`). Worth a CLAUDE.md
   note: when refactoring config-loading code, prefer non-dropping
   transforms unless there's a specific privacy/security reason.

3. **Bybit returning HTTP 200 + `retCode != 0` was the silent killer.**
   The previous code only caught exceptions, so an auth failure that
   came back as `{"retCode": 10003, "retMsg": "API key is invalid"}`
   showed up as zero balance with no error. PR2's
   `_bybit_response_error` is a 5-line check that should have been
   there since day one. Pattern to apply elsewhere: any external API
   that distinguishes transport errors (HTTP 5xx) from business errors
   (HTTP 200 + business-error code) needs both checked.

4. **Dual function definitions in test files are a real bug.** Pre-
   existing in `tests/test_data_loaders.py`: two functions named
   `_bybit_account` with different signatures. Python silently kept
   the second, masking a credential-check failure mode in upstream
   tests that had been "passing" for sprints. Fixed in PR2 by renaming
   one to `_bybit_strategy_account`. Worth a lint pass at the file
   level — `pyflakes` would have caught this.

5. **Per-fingerprint dedup pays off again.** Without it, PR3 wiring
   `report_api_failure` into 4 hot-path call sites would have flooded
   the operator on the first network blip. The 5-min/30-hour cap from
   S-022 PR1 means we can be aggressive about which sites get
   instrumented — the cost of an extra ping site is bounded.

6. **Token redaction is non-trivial.** First pass only matched
   `Bearer <token>` after a colon-equals; tests caught that
   `Authorization: Bearer xyz` would leave the token visible. Second
   pass added a dedicated `_BEARER_RE`. Lesson: when shipping a
   redactor, write the test cases first — the failures shape the
   regexes, not the other way around.

## Security note: age private key exposure

While filing this work, the operator pasted an age private key into the
chat to give context on the secrets pipeline. **That key alone decrypts
every secret in `master-secrets.sops.yaml`.** Treat it as compromised:

1. Generate a new age key: `age-keygen -o new-keys.txt`.
2. Re-encrypt the master file with the new public key (`sops updatekeys`
   or decrypt + re-encrypt).
3. Delete the exposed key from the local copy and the Drive folder.
4. **Rotate any production API keys** that were already in the master
   file — anyone with chat access in the meantime could have decrypted
   them.

The repo's `secret_scan.py` is clean (no key in any commit) but the
chat archive exposure is real.

## CLAUDE.md improvements proposed for next sprint

1. **Add a config-drift guard pattern to `docs/claude/repo-map.md`.**
   When two config files reference each other (one declares names,
   the other declares values), there must be a regression test that
   walks the cross-references. PR1's
   `test_template_account_ids_match_accounts_yaml` is the model.

2. **Document the "external API returns 200 + business-error code"
   pattern in `docs/claude/debug-memory.md`.** Bybit, Binance, and
   most prop-firm APIs do this. Code that calls them must check the
   business code, not just HTTP status.

3. **Add `pyflakes` to the lint pass.** It would have flagged the
   duplicate `_bybit_account` definition in `tests/test_data_loaders.py`
   immediately. The fix is one line in `scripts/secret_scan.py` or a
   new `scripts/lint.py`.

4. **Add a section to `docs/claude/security-secrets.md` about chat
   exposure.** The age private key situation here was the second
   time in two sprints that a secret has flowed into chat. Worth an
   explicit reminder for future operators.

## Verification (post-merge, on the VM)

After steps 1-3 in "Operator action" above:

1. **`/accounts_status`** should show `✅ Balance $X.XX USDT` for every
   account.
2. **Force a failure** to verify pings: temporarily flip one
   `BYBIT_API_KEY_*` to garbage and restart. `/accounts_status` should
   show the new specific message; Telegram should fire one
   `[ERROR] api_call → bybit_get_wallet_balance_failed:
   Bybit error retCode=10003: ...` message; subsequent failures
   within 5 min should be deduped.
3. **Force a network failure**: take the VM off-network briefly. Same
   ping pattern, this time with `exception_type=ConnectionError`.
4. **Confirm clean state recovery**: restore creds + network. Next
   `/accounts_status` shows ✅ for every account; no further alerts.
