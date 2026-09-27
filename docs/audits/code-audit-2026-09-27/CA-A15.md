# CA-A15 — Code audit: remaining small src/ modules

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> lane CA-A15 of the 2026-09-27 code audit · session `session_014SZHZwFb7DiRbfRLBXqdGK` · reports to manager (row `CA`, `docs/claude/work/MANAGER-CHECKLIST.json`) · audited HEAD `043825797` (origin/main at 2026-09-27T13:00Z)

Lane of the 2026-09-27 operator-adopted whole-repo code audit (row `CA`,
`docs/claude/work/MANAGER-CHECKLIST.json`, dispatched via PR #13156). Method
and finding schema per `docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md` §3/§5.

**Scope:** `src/{analysis,backtest,core,data_layer,exchange,news,pipeline,
research,sysdyn,utils,config}/**`, `src/units/{db,dashboards,trading_school,
ui}/**`, `src/strategy_registry.py`. `Coordinator.multi_account_execute`
(`src/core/coordinator.py:865-2870`) belongs to lane CA-A02 and was
deliberately not reviewed here.

This lane split the scope across three parallel sub-passes (general-purpose
agents, each with a fixed file list) and then independently re-verified every
critical/high finding against the live code before writing it up — per the
audit's own Rule 1 ("never grade a claim using the same system that produced
it").

## Coverage

**Read in full** (every file, confirmed against `wc -l` totals):
`src/core/{account_profile,allocator,instrument_class,instrument_profile,
order_contract,portfolio_state,profile_loader,signal_contract,
strategy_interface}.py`; `src/core/coordinator.py` lines 1-865 and 2870-3886
(the in-scope portion); `src/pipeline/**`; `src/data_layer/**`;
`src/config/**`; `src/strategy_registry.py`; `src/backtest/backtester.py`;
`src/backtest/run_backtest.py`; `src/exchange/**`; `src/news/**`;
`src/sysdyn/**`; `src/research/**`; `src/units/db/**`; `src/units/
dashboards/**`; `src/units/trading_school/**`; `src/units/ui/**`;
`src/utils/**`; `src/analysis/**`.

**Read partly:** `src/backtest/run_backtest_vwap.py` — ~350 of 1607 lines
read line-by-line (header, constants, cost-policy wiring, `_simulate_trade`,
`main()`'s cost-arg handling); the remaining ~1250 lines (CLI arg parsing,
sweep-grid iteration, reporting/plotting) were grepped exhaustively for
slippage/funding/qty/risk-money terms but not read line-by-line. Nothing
found in the read portion depends on the unread portion; flagged separately
as a test-coverage gap (this file has zero direct unit tests) rather than
asserted clean.

**Not settled:** whether `src/core/coordinator.py`'s `multi_account_execute`
and `src/units/accounts/execute.py`'s other close-call-sites (the other ~9
of the 10 named in `BL-20260825-CLOSE-SIDE-LEG-CANCEL-IS-WIRED-AT-1-OF-10-
CALL-SITES`) share the leg-forwarding bug found in `/closeall` — only the one
call site inside this lane's scope (`processor.py::close_open_positions`)
was audited; the other 9 sites are outside CA-A15's file list.

**Tests run** (not just read): `tests/test_s031_pr4_closeall_helper.py`
(16/16), `tests/test_canonical_db_resolver.py` (23/23),
`tests/test_s031_pr1_status_helpers_in_ui.py` +
`tests/test_execute_journal_rejections.py` (23/23 combined),
`tests/test_s4_allocator_wiring.py` (78/78 in its sprint group),
`tests/test_s7_multi_account_execute_typed.py` (15/15),
`tests/test_silent_except_sweep.py`'s positions test,
`tests/test_backtester.py` (21/21), `tests/test_runtime_risk_injection.py`
(11/11). All green — none of these bugs are caught by CI today; that is the
point of most of the findings below.

**Environment notes:** `ccxt` is not installed in this sandbox (network
verification of the Bybit v5 category-mismatch claim was not possible;
corroborated instead from this repo's own code — a second module,
`src/units/accounts/clients.py`, independently works around the exact same
fact). `pandas`/`numpy`/`pytest` were not preinstalled and were installed
mid-session to get real pass/fail evidence rather than false-positive
"skipped" results.

## Findings by severity

- **Critical: 1**
- **High: 4**
- **Medium: 5**
- **Low: 1**

Full detail with evidence/expected/actual/population/detector for all 11 in
[`CA-A15.findings.jsonl`](CA-A15.findings.jsonl).

### Critical

- **`AUD-20260927-CA-A15-closeall-leg-id-not-forwarded`** — the `/closeall`
  operator command never forwards a closed trade's `sl_order_id`/
  `tp_order_id` to `close_open_position()`: the SELECT doesn't fetch those
  columns, and a `hasattr(row, "get")` check on a `sqlite3.Row` (which has no
  `.get()` method) always evaluates `False`, so the ternary always resolves
  to `None` regardless. On a Bybit partial-TP/SL account this reproduces the
  exact stray-leg incident the code's own adjacent comment names
  (`BL-20260721-BYBIT2-XRP-TPSL-LEGCAP`). The existing regression test for
  this exact bug class passes today because its fixture never seeds a
  non-null leg id. Filed: `PI-20260927-RLBXQDGK-0002`.

### High

- **`AUD-20260927-CA-A15-bybit-positions-spot-category`** —
  `BybitConnector.get_positions()` hardcodes `category="spot"` even for
  linear-perpetual accounts, so it silently returns `[]` instead of real
  positions; this feeds `CURRENT_OPEN_POSITIONS`, which gates
  `MAX_OPEN_POSITIONS`. Currently dormant only because `MAX_OPEN_POSITIONS`
  is unset anywhere in config/env — the method itself is unconditionally
  broken. Filed: `PI-20260927-RLBXQDGK-0001`.
- **`AUD-20260927-CA-A15-passthrough-allocator-flat-risk`** —
  `PassthroughAllocator` sizes every strategy at a flat, stale 0.5% risk
  (the per-strategy lookup it reads was removed from `strategies.yaml` on
  2026-06-29 and always misses), versus the real account-level `risk_pct`
  (default 1%) times a confidence scalar that `risk.py` applies and
  `PassthroughAllocator` does not. Currently dormant behind
  `CENTRALIZED_ALLOCATOR` (default false, provisioned nowhere) but **not**
  shadow-only when armed — it dispatches through the same real order path.
  A Tier-3 sizing question, not a lane self-fix. Filed:
  `PI-20260927-RLBXQDGK-0004`.
- **`AUD-20260927-CA-A15-run-backtest-summarize-fabricated-zeros`** —
  `run_backtest.py::summarize()` hardcodes `max_drawdown`,
  `max_drawdown_pct`, `sharpe_ratio`, `total_pnl_pct` to `0.0` even when
  real trades are present; these values flow into the `backtest_results` ML
  dataset family indistinguishable from a genuine zero. Zero test coverage
  on this module at all. Filed: `PI-20260927-RLBXQDGK-0003`.
- **`AUD-20260927-CA-A15-db-loaders-stray-trade-journal-fallback`** —
  `src/units/ui/data_loaders.py` prefers whichever of [canonical DB path,
  legacy repo-root path] exists on disk first, so an absent canonical file
  next to a stray repo-root `trade_journal.db` silently reads the wrong
  store — contradicting the module's own comment that the legacy candidate
  was already dropped, and invisible to the `canonical-db-resolver` CI guard
  (whose own test suite explicitly allowlists the code shape involved).
  Reachable from the live web API and the deployed Telegram bot. Filed:
  `PI-20260927-RLBXQDGK-0005`.

### Medium

- `AUD-20260927-CA-A15-backtest-harness-slippage-funding-defaults` —
  answers the audit's explicit question by naming each of the 3 in-scope
  harnesses and its real default (not one uniform "0.0"): `backtester.py`/
  `run_backtest.py` model nonzero slippage but have **no funding term at
  all** (structurally absent, not a 0.0 default); `run_backtest_vwap.py`
  defaults `0.0`/`0.0` only for a direct/programmatic caller — its actual
  CLI entrypoint (what production tooling uses) resolves nonzero
  venue-aware costs by default. `CLAUDE.md`'s blanket "harnesses default
  slippage and funding to 0.0" claim is imprecise; proposed a docs fix, not
  filed to the pipeline (docs blast radius).
- `AUD-20260927-CA-A15-allocator-stale-parity-docstrings` — two docstrings
  and two design docs still claim `PassthroughAllocator` mirrors `risk.py`
  exactly; false since 2026-06-29. Folded into the allocator finding's
  pipeline record rather than filed separately.
- `AUD-20260927-CA-A15-s7-dispatch-test-cannot-fail` —
  `tests/test_s7_multi_account_execute_typed.py` never imports
  `src.runtime.pipeline`; it hand-reimplements the dispatch block under test
  and asserts a mock against its own configured input. Verified no active
  divergence exists today (compared both implementations line-by-line), but
  the test structurally cannot catch a future one.
- `AUD-20260927-CA-A15-round-qty-float-truncation` —
  `InstrumentProfile.round_qty()` truncates via `int(qty / qty_step)`
  instead of rounding, silently under-sizing by one whole step on inputs
  like `0.29`/`0.01` due to float imprecision. Zero callers found anywhere
  in `src/`/`scripts/` — dead code today, flagged so a future caller doesn't
  inherit it silently.
- `AUD-20260927-CA-A15-bybit-connector-print-not-logged` — 4 of 5
  `BybitConnector` methods (including `place_market_order`, the live
  order-placement path) report via bare `print()` instead of `logger`,
  unlike every other exchange connector and unlike this file's own
  `get_positions()`.

### Low

- `AUD-20260927-CA-A15-collapsed-zero-pnl-helpers` — two `processor.py`
  helpers collapse "no trades today" and "DB query failed" into the
  identical zero-value shape, against `CLAUDE.md`'s own stated policy.
  Currently no production caller for either function.

## What this lane found clean

`src/sysdyn/**` and `src/research/**` are genuinely pure/no-side-effect
research code with no runtime caller (several modules say so in their own
docstrings) and, where they model three/four-state results, do so with the
collapsed-state discipline the rest of the repo should match.
`src/news/**`'s veto/adjustment pipeline fails closed to neutral, never
fabricates a non-zero score on failure, and its config keys are documented
in `docs/reference/env-vars.md` (not orphaned). `oanda_connector.py`,
`alpaca_connector.py`, `ib_connector.py` all degrade to `None`/log-at-WARNING
on failure with no silent zero/empty-as-valid substitutions.
`src/config/accounts_loader.py` and `symbol_sets.py` are carefully defensive
(their file headers document the past incidents that shaped them) and no
gaps were found. `src/data_layer/*` are genuine re-export shims to
`src/units/db/*`, not duplicate logic. The canonical-DB-resolver convention
holds everywhere else checked in scope (`database.py`, `db_init.py`,
`trainer_store.py` all route through `src.utils.paths`) — the one exception
is the `data_loaders.py` finding above.

## Disposition

5 critical/high findings filed to `docs/claude/work/pipeline/`
(`PI-20260927-RLBXQDGK-0001` through `-0005`), each with `origin.ref: CA`,
a `rerun` command, and an observation-based `due_when` so a future session
can re-check whether it still applies. The 6 medium/low findings are
recorded in `CA-A15.findings.jsonl` with a `disposition` of either
`verified-non-issue` (checked, currently no live discrepancy in behavior —
e.g. the print/logger gap, the s7-test structural gap) or a note on why it
wasn't filed as a standalone pipeline row (folded into a related filed
finding, or flagged for whoever next touches genuinely dead code). None of
the 11 findings were fixed in this lane — per the audit's own separation of
finding from fixing, no Tier-2/3 change was enacted here.
