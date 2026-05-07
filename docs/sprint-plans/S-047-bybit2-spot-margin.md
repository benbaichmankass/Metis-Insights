# Sprint S-047 — bybit_2 Spot Margin enablement (VWAP true longs + shorts)

> **Status:** DRAFT — awaiting operator approval of plan structure before code work begins.
> **Tier:** 3 (touches strategy, sizing, risk caps, live order routing). All PRs ship as draft.
> **Triggered by:** session 2026-05-07 audit (operator directive: "the wallet holds USDT and opens long/short BTC spot positions — fix all the wiring to comply").

---

## 1. Goal

Enable Bybit V5 Spot Margin Trading on `bybit_2` so VWAP can take both long
and short BTCUSDT positions against the wallet's USDT collateral, and update
the RiskManager + dispatch + monitor + reconciler so every layer treats
Spot-Margin sells as borrowed-coin shorts (not "sell BTC you happen to hold").
The operator's explicit ask is that the RiskManager's sizing decision must
factor in margin requirements, borrow fees (interest accrual), and the
liquidation buffer — not just risk-per-trade × balance.

When this sprint ends, a sell-side VWAP signal on `bybit_2` opens a true
short via `category=spot, isLeverage=1`, the trade journal records it
correctly, the monitor closes it on TP / SL / VWAP cross, the reconciler
recognizes spot-margin shorts as exchange-side positions, and the daily
borrow-fee accrual is reflected in the per-account PnL and risk gates.

---

## 2. Dependencies

- **Operator action: none required by the sprint.** The system operates
  margin-agnostic. Whenever the operator wants `bybit_2` to actually trade
  margin, they click Enable Spot Margin in the Bybit web UI on their own
  schedule. The sprint does not block on that click and does not verify
  it. Until the click happens, every `isLeverage=1` order returns retCode
  110007 ("MARGIN_TRADING_NOT_ENABLED") at the exchange and is logged via
  the existing `report_api_failure` path — same handling as any other
  exchange retCode. After the click, orders flow through. No notebook,
  no parameter capture, no PR-thread comment.
- **Risk-manager parameter defaults:** T2 ships sensible default values
  in the risk-rule configuration surface (e.g. `max_borrow_btc`,
  `borrow_fee_apr_pct`, `liquidation_buffer_pct`). The operator can edit
  the config file directly any time; if the live exchange cap is lower
  than our default, exchange-side validation enforces — same exchange-as-
  source-of-truth model as everywhere else.
- **#441 + #446 deployed:** confirmed live as of 2026-05-07 07:52 UTC
  (boot_audit log on the VM). The new sizer's direction-aware balance
  fetch is the foundation we extend in T2.
- **`MONITOR_RECONCILE_ENABLED=true`:** confirmed in `.env` per the
  S-047 trigger session diagnostic (cell 2 part 1).
- **No open positions on bybit_2:** confirmed in the same diagnostic
  (parts 2 + 3 + 4 returned empty / zero). Margin enablement is safe
  to perform now without colliding with live exposure.

---

## 3. Deliverables

| # | Deliverable | PR title | Risk |
|---|---|---|---|
| ~~D1~~ | ~~Operator notebook to enable Spot Margin on `bybit_2`~~ — **DELETED** in PR #455. The system operates margin-agnostic; no operator-run notebook is needed to verify or capture exchange-side state. | — | — |
| D2 | `config/accounts.yaml`: declare `bybit_2` as a spot-margin account in the existing routing schema (no new `is_leverage` flag — the account's identity carries the routing, the same way `market_type: spot` already does). Spot-margin **risk-manager parameters** (`max_borrow_btc`, `borrow_fee_apr_pct`, `liquidation_buffer_pct`) land in the risk-rule configuration surface (`src/units/accounts/risk.py` / `config/risk.yaml` per the existing risk-rule shape) with **sensible hardcoded defaults**, **not** as per-account gating flags or operator-captured values. Compliance with `docs/claude/workplan.md` § "Live / dry-run rule": the dispatcher's `live | dry_run` switch remains the only canonical execution gate. | `feat(accounts): declare bybit_2 spot-margin in routing config` | strategy / model |
| D3 | `RiskManager.position_size()` upgrade: on `bybit_2`, size from USDT collateral for *both* directions; sizing returns 0 (= no trade, same shape as the existing `min_balance_usd` and daily-loss-budget refusals — these are risk-manager rules, not new gates) when the configured liquidation buffer or borrow-fee budget would be violated. New regression tests cover: spot-long (no borrow), spot-short (BTC borrow), liquidation buffer triggers zero-size, fee-budget triggers zero-size, daily-loss-budget interaction. | `feat(risk): spot-margin sizing — collateral, liquidation, borrow fees` | strategy / model |
| D4 | `execute.py`: for `bybit_2` always pass `isLeverage=1` to Bybit V5 spot `place_order` (it is a routing decision based on the account's identity, not a refusal — non-margin accounts never reach this branch because their routing differs). The existing spot-sell pre-flight is unchanged for non-margin accounts; on `bybit_2` the risk manager (D3) owns sizing decisions, so no per-account refusal lives at execute-time. New retCode handling: 110007 (margin not enabled) and 110095 (insufficient borrow available) are **logged** as exchange errors via the existing `report_api_failure` path — they are not new pre-flight gates. | `feat(exec): route spot-margin orders via isLeverage=1` | deploy / live |
| D5 | `coordinator.multi_account_execute`: for `bybit_2`, the direction-aware balance fetch returns USDT collateral for both directions (matching the risk-manager's collateral semantics in D3). Looked up from the account's routing config in accounts.yaml; non-margin spot accounts retain the existing per-direction balance behavior. | `feat(coordinator): direction-aware balance for spot-margin accounts` | deploy / live |
| D6 | `vwap.py::monitor()`: full close logic — TP-cross close, SL-cross close, VWAP-cross close, time-decay close. Deletes the break-even-only stub. Documented in the strategy header. Tier 3. | `feat(vwap): close on TP/SL/VWAP-cross instead of only break-even-SL` | strategy / model |
| D7 | `order_monitor.py` reconciler: spot-margin accounts query Bybit's borrow-position endpoint instead of the perp position endpoint. Spot-non-margin behavior unchanged (stays a no-op there since spot has no native positions). | `feat(monitor): spot-margin borrow-position reconciler` | deploy / live |
| D8 | Bug-log entry for the BUG-046/049/048 family root cause (spot has no exchange-side close, strategy monitor doesn't close, reconciler doesn't see spot positions); explicit cross-link to S-047 as the structural fix. Runbook `docs/runbooks/spot-margin.md`. | `docs(bug-log + runbook): spot-margin remediation cross-references` | docs-only |

Each deliverable maps to one PR. PRs land in the order T1..T7 below.

---

## 4. Checkpoints

| # | Checkpoint title | What completes by then | Risk class | Wall-clock | Unblocks |
|---|---|---|---|---|---|
| ~~T0~~ | ~~Operator notebook + Bybit margin enabled on `bybit_2`~~ | **DELETED** in PR #455. The system operates margin-agnostic — no operator step is part of this sprint's deliverables. The operator clicks Enable Spot Margin in the Bybit web UI on their own schedule; the sprint does not block on or verify that click. | — | — | — |
| **T1** | accounts.yaml routing for spot-margin | D2 merged. `bybit_2` is declared as a spot-margin account in the existing routing schema; the risk-manager spot-margin parameters live in the risk-rule configuration surface (operator-confirmed defaults). Loader tests pass; legacy non-margin accounts unchanged. **No new account-level toggle that can refuse trades** (per workplan § "Live / dry-run rule"). | strategy / model | 2h | T2, T3 |
| **T2** | RiskManager spot-margin sizing | D3 merged. New unit tests prove: short sizing uses USDT collateral, liquidation-buffer violation produces zero-size sizing (same shape as the existing daily-loss-budget refusal — risk-manager rules, not new gates), borrow-fee budget reduces sizable amount, daily-loss-budget rule still wins on conflict. | strategy / model | 4h | T3, T4 |
| **T3** | execute.py + coordinator wiring | D4 + D5 merged together (one diff is incoherent without the other — wiring on both sides of the boundary). Smoke test against Bybit testnet: sells round-trip with isLeverage=1 and produce the expected borrow line in the wallet. | deploy / live | 4h | T4, T5 |
| **T4** | VWAP monitor close logic | D6 merged. Replaces the break-even-only stub. Unit tests cover all four close paths and the no-action path. Operator review required (Tier 3). | strategy / model | 3h | T5 |
| **T5** | Reconciler spot-margin awareness | D7 merged. Reconciler distinguishes (spot-margin / spot-cash / linear / inverse) and queries the right endpoint for each. Spot-cash and linear behavior preserved. | deploy / live | 3h | T6 |
| **T6** | End-to-end live smoke + runbook | D8 merged. Live smoke test: `bybit_2` opens a small (0.0005 BTC) short via VWAP, lets it cycle through monitor → close, verifies journal + reconciler agree. Runbook documents borrow-fee accrual visibility and how to manually flatten a stuck borrow position. | docs-only after smoke succeeds | 2h | — |
| **T7** | Sprint close | Updates to `milestone-state.md`, `bug-log.md` (BUG-046/049/048 cross-link to S-047 closure), `CHECKPOINT_LOG.md` handoff, ROADMAP.md if relevant. | docs-only | 30min | — |

**Total wall-clock estimate:** ~20 Claude hours across 7 sessions + ≤30 min operator action.
The sprint is split into 7 sessions intentionally; one session per checkpoint matches the
"one task per session" rule in `docs/claude/operating-protocol.md` § 2.2.

---

### 4b. Unit boundary declaration

| Unit | Role in this sprint |
|---|---|
| `src/units/strategies/` (vwap.py) | **owns** — T4 rewrites monitor() close logic |
| `src/units/accounts/` (risk.py, execute.py, clients.py) | **owns** — T2 risk math, T3 exec wiring |
| `src/data_layer/` / `src/units/db/` | **reads** only — T2 + T6 unit tests use fixtures |
| `src/ui/` | **untouched** |
| `src/runtime/` (order_monitor.py, pipeline.py) | **owns** — T5 reconciler; pipeline untouched |
| `src/bot/` | **untouched** |
| `src/core/coordinator.py` | **owns** — T3 direction-aware balance update |
| `config/accounts.yaml` | **owns** — T1 schema extension |

No new cross-unit imports. The Coordinator stays the one translator between strategies and accounts.

---

## 5. Risk class & merge model

Every PR in this sprint is **Tier 2 or Tier 3** per `docs/claude/operating-protocol.md` § 4. Specifically:

- ~~**D1**~~ — DELETED (PR #455). No operator notebook in this sprint.
- **D8** — Tier 1 (docs / runbook). Self-merge after CI green.
- **D2, D3, D6** — Tier 3 (strategy parameters / sizing formulas / strategy logic). Draft PR + ping-PR + explicit "merge" reply required.
- **D4, D5, D7** — Tier 2 (live order routing, runtime orchestration). Draft PR + ping-PR + Merge/Hold buttons.

No mixed-risk PRs. D4 and D5 are split into two PRs even though they land
together because their risk class is the same and the diffs review more
cleanly separately.

### 5b. Compliance with the one-canonical-gate rule

`docs/claude/workplan.md` § "Live / dry-run rule" is unambiguous:

> *"The dispatcher maintains the **only canonical** live / dry-run switch in the system."*

That is the **single** gate that may refuse to send a trade. No
deliverable in this sprint may add a new refuse-to-trade branch
outside that gate. Specifically:

- **No `is_leverage` boolean** in `accounts.yaml`. `bybit_2` is
  declared as a spot-margin account by its routing identity (D2);
  the routing is not a gate, because non-margin accounts simply
  follow a different code path — the dispatcher does not refuse
  anything based on a flag.
- **No `if not is_leverage: refuse`** anywhere in `execute.py` or
  the coordinator. D4 routes `isLeverage=1` for `bybit_2` without
  pre-flight refusal.
- **All sizing-related refusals live inside `RiskManager.position_size()`** (D3).
  When the risk manager returns a zero size for a signal, that is
  the existing risk-manager refusal mechanism (same shape as the
  existing `min_balance_usd` and daily-loss-budget rules) — it
  does not introduce a new gate, because the risk manager is
  upstream of the dispatcher in the trade-pipeline graph and is
  the canonical place rules live.
- **Exchange-side errors are logged, not gated.** Bybit retCode
  110007 / 110095 hit the existing `report_api_failure` path; they
  do not become new pre-flight checks.
- **No operator-run notebook to verify or capture exchange-side
  state.** The system operates exchange-agnostic. Risk-manager
  parameters ship with sensible hardcoded defaults that the operator
  can edit in config; nothing in the sprint workflow requires the
  operator to extract a value from a notebook and paste it into a PR.
  This avoids the "workflow gate" anti-pattern: even a read-only
  diagnostic that conditions a downstream PR on its output is a
  refuse-to-progress gate at the workflow layer (operator-protocol
  § 4.4 catches this).

When the operator has not yet flipped Bybit's web-UI Spot Margin
toggle, every `isLeverage=1` order returns retCode 110007 server-side.
That is the **exchange** refusing — our pipeline did not refuse.
The operator flips the toggle whenever convenient; **our code does
not detect, verify, or wait for** that flip.

---

## 6. Success criteria

- ✅ `pytest tests/units/accounts/test_risk_spot_margin.py` returns 0 with ≥ 8 cases (long, short, liquidation cap, fee buffer, daily-loss conflict, min_borrow, max_borrow, edge: balance < min_balance_usd).
- ✅ `pytest tests/units/strategies/test_vwap_monitor_close.py` returns 0 with cases for TP-cross, SL-cross, VWAP-cross, time-decay, no-action.
- ✅ Bybit testnet smoke (T3): script `scripts/sprint047/spot_margin_smoke.py` opens a sell with `isLeverage=1`, verifies the borrow line appears in `get_wallet_balance`, closes it, verifies the borrow line clears.
- ✅ Live smoke (T6): a 0.0005 BTC short on bybit_2 mainnet completes one full open→monitor→close cycle. Trade journal and reconciler agree at the end (no orphans).
- ✅ Operator confirms via Telegram `/last5` and the dashboard that the cycle PnL matches expectation (entry − exit − borrow_fee − exchange_fees).
- ❌ Failed experiments do not get their own PRs — they live in the per-checkpoint summary.

---

## 7. Hard guardrails

Inherited from `CLAUDE.md`:

- No silent flips of any per-account `mode` field. The autonomous live-trading rule stands.
- No edits to `src/runtime/orders.py`, `src/runtime/notify.py`, `src/runtime/risk_counters.py`, `src/runtime/signal_writer.py`, `src/runtime/validation.py` — out of scope for this sprint. The risk math goes in `src/units/accounts/risk.py` (correct unit per architecture rules).
- No secrets in the repo. The new `BYBIT_*_2` env vars are unchanged.

Sprint-specific:

- `bybit_1` and `prop_velotrade_1` MUST be unchanged in observable behavior. Every PR includes a regression assertion that non-margin accounts route exactly as before (no isLeverage parameter sent, no borrow checks, no spot-margin reconciler call).
- `turtle_soup` strategy MUST be unaffected — it doesn't run on `bybit_2` and the `account.strategies` filter already enforces that. T4 rewrites VWAP's monitor only; turtle_soup's monitor stays as-is.
- **One canonical gate.** The dispatcher's `live | dry_run` switch is the only refuse-to-trade gate in the system (`docs/claude/workplan.md` § "Live / dry-run rule"). No deliverable in this sprint may add a new refuse-to-trade branch outside the risk manager. See § 5b for the specifics that follow from this rule.
- Spot Margin liquidation buffer is a **risk-manager parameter** that lives in the risk-rule configuration surface (default 30 % until the operator tunes it). When the buffer would be violated, T2's `RiskManager.position_size()` returns zero size — same shape as the existing `min_balance_usd` and daily-loss-budget refusals. This is the risk manager's existing refusal mechanism; it is not a new gate.

---

## 8. Hand-off

The session that picks up **T1** (the new starting checkpoint — T0 was deleted) reads:

1. This file (S-047 plan).
2. `docs/claude/checkpoints/CHECKPOINT_LOG.md` last entry.
3. `docs/claude/operating-protocol.md` § 4 (merge tiers) and § 4.4 (the pre-ship compliance check).
4. The today-#441 / today-#446 PRs to internalize the direction-aware balance fetch foundation (see notes in `coordinator.py::multi_account_execute` and `execute.py::_fetch_spot_coin_balances`).

Margin-agnostic operation: the operator clicks Enable Spot Margin in the
Bybit web UI on their own schedule. The sprint does **not** block on or
verify that click. Until it happens, every `isLeverage=1` order returns
retCode 110007 server-side and is logged via the existing
`report_api_failure` path — same handling as any other exchange retCode.
After it happens, orders flow through. No notebook, no parameter
capture, no PR thread.

---

## Cross-references

- Trigger session: this branch's diagnostic notebook
  `notebooks/operator/debug_vwap_bybit2.ipynb` (PR #450) and the audit
  pasted into the operator chat 2026-05-07.
- BUG family this sprint structurally closes: BUG-045 (silent dry-run
  masking), BUG-046 (stacked open packages — gate added), BUG-048
  (8h orphan), BUG-049 (gate over-broad — linked_only fix), and the
  unnumbered today-2026-05-07 recurrence on the spot-sell path
  (#441 + #446 patched the symptom; this sprint fixes the model).
- Workplan rule cited: `docs/claude/workplan.md` § "Decision and merge authority" — Tier 3 strategy/sizing changes require explicit operator approval.
