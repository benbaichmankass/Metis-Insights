# § 6 — Dry-run / live-trading flag surface

Total occurrences of `DRY_RUN` / `dry_run` in `src/`: ≈79. The footprint is
mostly correct; the prompt's instruction to "remove DRY_RUN" should be
read as **"ensure the only path from `DRY_RUN=true`/`ALLOW_LIVE_TRADING≠true`
is a hard refusal at startup, never a silent live-trade skip"** — not as
"delete every flag read".

## 6.1 Keep — startup interlock and execution gates

| File:line | Flag | Role | Verdict |
|---|---|---|---|
| `src/runtime/validation.py:118-132` | `DRY_RUN` + `ALLOW_LIVE_TRADING` | startup interlock — refuses live trading if `DRY_RUN=false` without `ALLOW_LIVE_TRADING=true`. **Fail-closed.** | **KEEP**. This is the canonical safety gate. PR E1 hardens the error message. |
| `src/main.py:127-129, 135, 168` | `DRY_RUN` env read + log line | startup boot log + mode decision. | **KEEP** but add the explicit assertion required by PR E1 ("if `ALLOW_LIVE_TRADING != true` and `DRY_RUN != true` → refuse to launch"). |
| `src/runtime/orders.py:163-177` | `DRY_RUN` | order-placement gate; returns `dry_run` status dict instead of placing real orders. | **KEEP**. This is the "what to do when dry-run is intentional" branch — required so dev/staging can exercise the path without an exchange call. |
| `src/units/accounts/execute.py:29, 80` | `_DRY_RUN` env, per-call override | per-account execution-layer gate. Honours both env and the S-011 per-account `dry_run` override. | **KEEP**. Per-account override is the S-011 PR #141 contract — confirmed wanted by PM in decision-request item #4 (default keep). |
| `src/units/strategies/_base.py:20-23` | comment only | docstring confirming strategies are pure (no dry_run flag in strategy layer). | **KEEP**. Already correct design — strategies don't branch on dry_run. |

## 6.2 Remove — orphan / stale

| File | Verdict |
|---|---|
| `src/core/automated_trading_loop.py` | Whole module is an orphan entrypoint (§ 5). Anything dry-run-related inside it goes with the file (PR C6). |

## 6.3 PM decision needed

| File:area | Issue | Decision request |
|---|---|---|
| `/accounts` Telegram command + `_DRY_RUN_OVERRIDES` dict (S-011 PR #141 — `src/units/accounts/__init__.py`, `src/units/accounts/account.py`) | The S-011 sprint added per-account dry/live toggling. Sprint S-012 prompt asks PM to confirm whether to keep this per-account override or remove it entirely. | Decision-request item #4. Recommendation: **keep**. It's the staging escape hatch for prop-account configuration. Explicit doc note in `deployment-ops.md` that the **default state for every configured account is `live`**. |

## 6.4 Tests

| Test file | Coverage | Verdict |
|---|---|---|
| Existing tests in `tests/test_runtime_validation.py` | Cover the startup interlock conceptually but the file has 23 pre-existing failures (carried since S-009). | PR E1 will add **focused** new tests at a new file (`tests/test_s012_live_mode.py`) rather than fix the legacy file. The DoD test list: |
| (new in PR E3) | `place_order` refuses oversized orders for **both** turtle_soup and vwap. | new |
| (new in PR E3) | `place_order` refuses when daily loss exceeds cap. | new |
| (new in PR E3) | `place_order` refuses when kill-switch is set. | new |
| (new in PR E3) | Startup refuses to launch when `ALLOW_LIVE_TRADING != true` and `DRY_RUN != true`. | new |

## 6.5 Post-sprint contract

After PR E1 merges, the **only** way the live trader runs orders is:

```
DRY_RUN unset (or false)  AND  ALLOW_LIVE_TRADING=true
   → live orders submitted via the exchange connector
DRY_RUN=true  AND  ALLOW_LIVE_TRADING=anything
   → no exchange call; dry_run status dict returned
ALLOW_LIVE_TRADING≠true  AND  DRY_RUN≠true
   → process refuses to start; exit non-zero with explicit error
anything else (e.g. ambiguous combos)
   → process refuses to start
```

The per-account `dry_run` override layered on top of these continues to
work exactly as in S-011 PR #141 (decision-request item #4 confirming).
