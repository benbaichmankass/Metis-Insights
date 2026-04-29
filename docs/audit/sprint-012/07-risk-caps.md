# § 7 — Risk-cap enforcement audit

Trace from `config/accounts.yaml` → `RiskManager` → `place_order`, then test
gap analysis.

## 7.1 Cap definitions (config)

`config/accounts.yaml`:

| Account | max_dd_pct | daily_usd | pos_size |
|---|---|---|---|
| `bybit_1` | 0.05 | 100 | 500 |
| `bybit_2` | 0.05 | 100 | 500 |
| `prop_breakout_1` | 0.02 | 50 | 200 |

## 7.2 Code path

1. **Load:** `src/units/accounts/__init__.py::load_accounts(path)` parses
   the YAML and constructs `TradingAccount` + `RiskManager` per account.
2. **Construct:** `src/units/accounts/risk.py:116-158` —
   `RiskManager.__init__` reads `max_dd_pct`, `daily_usd`, `pos_size` and
   stores them as `self.max_dd_pct`, `self.max_daily_loss_usd`,
   `self.max_pos_size_usd`.
3. **Approve:** `src/units/accounts/account.py:59-93` —
   `TradingAccount.place_order(order, dry_run=None)` calls
   `self.risk_manager.approve(order)` at line 82. On `False` it raises
   `RiskBreach`.
4. **Checks:** `src/units/accounts/risk.py:123-137` — in `approve()`:
   - **daily loss:** `self.daily_pnl < -self.max_daily_loss_usd` →
     reject (line 130).
   - **position size:** `order.meta['estimated_value'] >
     self.max_pos_size_usd` → reject (lines 133-135).
   - **drawdown:** **NOT IMPLEMENTED.** `self.max_dd_pct` is loaded but
     no branch reads it.

## 7.3 The `max_dd_pct` gap

`max_dd_pct` is stored on the RiskManager but never consulted. The config
documents and operators set this value expecting it to fire. Today it
doesn't.

**PR E3a (in § 9):** add a drawdown check in `RiskManager.approve()`. The
implementation is small but requires deciding the drawdown reference: is
it (a) intra-day equity drop from the daily high, (b) running max-equity
drop since account inception, or (c) running max-equity drop since
last reset? The prompt does not specify. **Default:** (a) intra-day. Flag
as PM decision request item #6 — see § 8.

## 7.4 Test coverage today

| Test file | Coverage | Notes |
|---|---|---|
| `tests/test_per_strategy_risk.py:76+` | Per-strategy caps from S-005 (older API) | Different layer; not the account-level `RiskManager.approve()`. |
| `tests/test_s010_accounts.py` | Account loading, risk-config presence | **Does not call `.approve()` with a rejecting order.** |
| `tests/test_accounts_integration.py` (S-010 PR #138) | Integration smoke | Does not exercise the rejection path. |

**Gaps formalised:**

1. No test calls `RiskManager.approve()` with `daily_pnl <
   -max_daily_loss_usd` and asserts it returns `False` / `RiskBreach`.
2. No test calls `RiskManager.approve()` with `order.meta['estimated_value']
   > max_pos_size_usd` and asserts rejection.
3. `max_dd_pct` enforcement is unimplemented (§ 7.3); no test exists
   because there's nothing to test.
4. No multi-strategy test verifying the same account caps fire equally for
   `vwap` and (future) `turtle_soup` orders. PR E3 closes this.

## 7.5 Required PR E3 test list (DoD § "Risk caps fire")

1. `place_order` with size > pos_size → `RiskBreach` (vwap order).
2. `place_order` with size > pos_size → `RiskBreach` (turtle_soup order).
3. `place_order` after daily_pnl ≤ −daily_usd → `RiskBreach` (both
   strategies).
4. `place_order` while kill-switch flag set → `RiskBreach`.
5. (Added by PR E3a once max_dd_pct semantics are confirmed)
   `place_order` after intra-day equity drawdown ≥ max_dd_pct →
   `RiskBreach`.

## 7.6 Account ID reconciliation note

`config/accounts.yaml` accounts (`bybit_1`, `bybit_2`, `prop_breakout_1`)
are disjoint from `config/units.yaml::accounts` (`live`). Two unrelated
account spaces. PR B3 reconciles this — see § 8 PM-decision item 3 — by
either:
- (a) renaming the `units.yaml` `live` entry to one of the
  `accounts.yaml` IDs and adding a `strategies: [turtle_soup, vwap]`
  field there, or
- (b) collapsing `units.yaml::accounts` into `accounts.yaml` and updating
  consumers.

Recommendation: **(b)** — single source of truth for account config.
`units.yaml::accounts` becomes a runtime dispatch table only.
