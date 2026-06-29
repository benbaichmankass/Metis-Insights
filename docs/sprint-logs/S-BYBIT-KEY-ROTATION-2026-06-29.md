# Sprint Log: S-BYBIT-KEY-ROTATION-2026-06-29

## Date Range
- Start: 2026-06-29
- End: 2026-06-29

## Objective
- Primary goal: Rotate `bybit_2` (real money) to a new **main-account** Bybit API key
  (different-account consolidation: bybit_1 paper + bybit_2 live both on the one main account),
  after first closing its open positions — without orphaning money-at-risk.
- Secondary goals: (1) build the missing capability to flatten a Bybit position remotely
  (`flatten-bybit-position` system-action); (2) run a delegated optimization investigation
  (intent conflict-resolution, position sizing, prop dynamic exits) and capture the proposals.

## Tier
- Mixed. New ops tooling = Tier-1 (the `flatten-bybit-position` PR). The position closes +
  key rotation = **Tier-2** order-path/credential VM mutations, **operator-directed**. The three
  research proposals = Tier-1 docs that *propose* Tier-3 changes (no live changes made).
- Justification: per `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers, key rotation + closing
  real-money positions are operator-gated; the operator explicitly directed both this session.

## Starting Context
- Active roadmap items: none specific; operator-initiated key consolidation.
- Prior sprint reference: n/a (ops task).
- Known risks at start: an open Bybit perp position cannot be transferred between accounts;
  rotating while positions are open on the OLD account would blind the bot to them. Prior Bybit
  ErrCode 10010 "Unmatched IP" history (`vm-bybit-diag.yml`).

## Repo State Checked
- Branch/commit: worked on `claude/bybit-api-keys-setup-mamcnu`; merged to `main` as **de9020a**
  (squash of PR #4985); branch then reset to `origin/main` for the docs commit (**a32f566**).
- Deployment state: live VM `ict-bot-arm` (141.145.193.91) confirmed at HEAD de9020a via
  `pull-and-deploy` (issue #4986) — ict-git-sync had already auto-pulled the merge.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md` (via the
  `credentials-and-vm-mutations`, `delegate-work`, `sprint-format` skills).

## Files and Systems Inspected
- Code: `src/units/accounts/clients.py` (`bybit_client_for`), `src/units/accounts/execute.py`
  (`close_open_position` Bybit branch), `src/units/ui/data_loaders.py` (`account_open_positions`),
  `scripts/ops/flatten_ib_position.py` (mirrored), `src/runtime/intents.py`,
  `src/core/coordinator.py`, `src/units/accounts/risk.py` (read for the research units).
- Config: `config/accounts.yaml` (bybit_1/bybit_2 key envs + the two open positions' strategies).
- Deploy/CI: `.github/workflows/system-actions.yml` (+ `notify_run.sh`, allowlist test),
  `rotate-account-keys.yml`, `vm-bybit-diag.yml`, `vm-diag-snapshot.yml`.
- Services/timers: `ict-trader-live.service`, `ict-web-api.service` (both bounced by the rotation).

## Work Completed
1. **`flatten-bybit-position` system-action** (PR #4985, squash-merged de9020a; 15/15 CI green):
   `scripts/ops/flatten_bybit_position.py` (+ `_action.sh` wrapper) — reduce-only, qty-clamped,
   dry-run default; wired into `system-actions.yml` (allowlist + Tier-2 + validation + SCRIPT map
   + env forwarding), `notify_run.sh`, `docs/claude/system-actions.md`; 9 new tests in
   `tests/test_flatten_bybit_position.py` + `EXPECTED_ACTIONS`/`TIER_2_ACTIONS` updates.
2. **bybit_2 key rotation, executed + verified:**
   - Closed BTCUSDT long 0.001 (issue #4988, reduce-only SELL, orderId 9be4cde6) and ETHUSDT
     short 0.01 (#4989, reduce-only BUY, orderId a6ecac6e) on the OLD key/account.
   - Confirmed flat: `/api/diag/exchange_positions?account_id=bybit_2` → `count:0` (#4990).
   - Rotated `BYBIT_API_KEY_2`/`SECRET_2` in the VM `.env` (#4991; backup
     `.env.bak.20260629T115653`); `ict-trader-live` + `ict-web-api` restarted (both active).
   - Verified new key (#4992, `vm-bybit-diag`): `.env` + running trader (pid restarted 11:56:53)
     + web-api all on `arue…Kc`; wallet-balance **retCode=0 retMsg=OK** in a fresh client AND
     inside the trader's exact environ; egress IP **141.145.193.91** (v4, no v6) — matches the
     key whitelist; **no 10010**.
3. **Optimization investigation** (3 background research agents, delegated): proposals committed
   to `docs/research/` (commit a32f566) — `pnl-optimal-conflict-resolution-DESIGN.md`,
   `position-sizing-confidence-DESIGN.md`, `prop-dynamic-exits-faster-banking-DESIGN.md`.

## Validation Performed
- CI: PR #4985 — 15/15 checks green (ruff, pytest-run, all guards). Local: `tests/test_flatten_bybit_position.py`
  9 passed; `tests/ops/test_system_actions_workflow.py` 244 passed; `py_compile`/`bash -n`/YAML parse clean.
- Live: dry-run (#4987) read the exact BTCUSDT long 0.001 before the apply; both closes
  broker-confirmed flat by the tool's post-close re-read; final exchange-side flat-check #4990 = count 0;
  post-rotation auth verified retCode=0 with matching egress IP (#4992).
- **Gaps not yet verified:** the three research proposals are design-only — none of their Tier-3
  changes were implemented or backtested. No new real-money trade has yet been placed on the new
  `bybit_2` key (auth verified; first live order will confirm the order path end-to-end).

## Documentation Updated
- `docs/claude/system-actions.md` — `flatten-bybit-position` row + allowlist entry (in PR #4985).
- `docs/research/` — three new design proposals (a32f566).
- This sprint log. ROADMAP.md: ops task; no milestone row required (note here for the record).

## Contradictions or Drift Found
- The session's cloud `DIAG_BASE_URL` env still pointed at the **retired** micro IP
  `158.178.210.252` (terminated 2026-06-16); the live VM is 141.145.193.91. Worked around by
  overriding the host. Worth correcting the env wherever it's set (logged below).

## Risks and Follow-Ups
- **Stale key in auxiliary bots (follow-up):** `rotate_account_keys.sh` bounces only
  `ict-trader-live` + `ict-web-api`. The Telegram bots `src.bot.claude_bridge` (pid 1779684) and
  `src.bot.telegram_query_bot` (pid 2189036) still hold the OLD `BYBIT_API_KEY_2` (`lDRH…CX`) in
  memory — read-only status bots, not the order path, but they'd report stale/old-account balances
  on a bybit_2 query until restarted. Recommend restarting those services (operator decision pending).
- **Tier-3 proposals await operator approval + backtests** before any implementation: uniform-1.5%
  sizing + RiskManager confidence modulation (decided in principle; bybit_2 5× / alpaca_live 2.5×
  effective raise needs the maxDD walk-forward gate); `FLIP_POLICY=selective`; prop dynamic exits.
- The old Bybit account/key should be revoked by the operator once the new account is confirmed
  trading (credential hand-off — operator action).

## Deferred Items
- Implementation of the three research proposals (separate Tier-3 draft PRs, backtest-gated).
- Restart of the aux Telegram bots to retire the old key from their memory.

## Next Recommended Sprint
- Implement Unit B (per-strategy risk removal + uniform 1.5% + confidence sizing) as a Tier-3
  draft PR with the §5 backtests — it's the most concrete and the operator has set the direction.
  Required verification: walk-forward that bybit_2 at the 5× effective raise stays inside the 5%
  daily/intraday DD caps; prop EV/survival regression for breakout_1.

## Wrap-Up Check
- [x] Code inspected directly (file:line citations in the research docs + the flatten PR).
- [x] Canonical docs reviewed (rules via skills).
- [x] TRADE-PIPELINE: no pipeline *stage* changed (a new ops flatten action; sizing/flip changes
  are proposals only) — no update required.
- [x] ROADMAP checked — ops task; recorded here.
- [x] Contradictions recorded (stale `DIAG_BASE_URL` env).
- [x] Unknowns stated (proposals unimplemented/unbacktested; first live order on the new key pending;
  aux-bot restart pending).
