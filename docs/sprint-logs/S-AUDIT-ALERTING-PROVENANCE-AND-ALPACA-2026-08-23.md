# S-AUDIT-ALERTING-PROVENANCE-AND-ALPACA-2026-08-23

## Date Range
2026-08-23, afternoon segment of the full-system-audit session (`main` at `405a633e`
at start, `78d0bdd0` at wrap). Continues the morning's
[`S-TIER3-DISPOSITIONS-2026-08-23`](./S-TIER3-DISPOSITIONS-2026-08-23.md).

## Objective
Drain the review backlog while the exit-mechanism verification waited on the COMEX
reopen, and answer the operator's live question: **is `alpaca_live` ready for the
`dry_run → live` flip once funded?**

## Tier
**Tier-1 throughout.** Every code change is on an alerting, read, or diagnostic path
— no order path, no strategy, no risk cap, no account mode. `config/strategies.yaml`
and `config/accounts.yaml` were **read only**. The Alpaca work produced **no code
change at all**: it is measurement plus backlog rows, because the decision it feeds
is Tier-3 and belongs to the operator.

## Starting Context
The morning closed three Tier-3 dispositions. The operator's standing instruction was
*"keep working through the backlog while we wait"*, then *"you can merge what's ready…
I'm going to fund alpaca in the next few days, are we ready to go live considering the
limitations we discussed?"*

## Repo State Checked
`origin/main` fetched at each step; deploy state read from `/api/diag/version` (web-api)
and `/api/diag/status` (trader) — **different processes, different shas**, never inferred
from one another.

## Files and Systems Inspected
- `src/runtime/order_monitor.py` — the target-naked alert cooldown
- `src/web/api/routers/diag.py` — `_LOG_FILES` allowlist + `/api/diag/version`
- `src/web/runtime_status.py::_resolve_git_sha` · `scripts/deploy_pull_restart.sh`
- `src/prop/prop_reconcile.py` — the daily-loss cushion
- `config/accounts.yaml::alpaca_live` + `alpaca_portfolio` (read only)
- Live surfaces: `/api/bot/logs`, `/api/bot/prop/status`, `/api/bot/order-packages`,
  `/api/bot/positions`, `/api/diag/broker_account_status`, `/api/diag/log_file`

## Work Completed

### Four defects found by measurement and fixed

| PR | Defect | The measurement that found it |
|---|---|---|
| [#10193](https://github.com/benbaichmankass/Metis-Insights/pull/10193) `c42be441` | The `ib_target_naked` CRITICAL cooldown was a module global on `time.monotonic()` — **per-process**, while the condition it rate-limits is broker state that outlives any process. Every trader restart re-armed it. | **202 of 376 ERROR+ rows = 53.7% of the entire CRITICAL/ERROR feed** over 6.5 days, for two `ib_paper` positions in an already-filed state. Declared ceiling 4/symbol/day; **delivered 31**. Mechanism confirmed directly, not inferred: 9 process starts on 08-23 vs exactly 9 MES pages (n = 1 day). Now durable in `runtime_logs/target_naked_alert_state.json`; an **unreadable latch alerts rather than suppresses**. |
| [#10194](https://github.com/benbaichmankass/Metis-Insights/pull/10194) `fced7279` | Three durable alert latches had **no read surface**, so a permanently-broken latch is indistinguishable from a working one. `CLAUDE.md`'s `log_file?name=` row was stale by 8 names. | 21 documented vs 26 in code. Now 29 = 29, pinned by a test in **both** directions. |
| [#10196](https://github.com/benbaichmankass/Metis-Insights/pull/10196) `78fb95cb` | `/api/diag/version` shelled `git rev-parse` at **request** time, so it reported the **DISK** sha. `deploy_pull_restart.sh` compared it against its own `rev-parse` over the same tree — **`X == X`**, a check that could not fail, and would have passed during the 2026-05-09 stale-code incident it cites. | Proven live: version returned `fced7279` while the *same process* returned **HTTP 400** for a `log_file` name present in `fced7279` (control: an older name returned 200). Now `git_sha` binds once at import; `restart_pending` is **`null`, never `false`**, when either sha is unknown. |
| [#10199](https://github.com/benbaichmankass/Metis-Insights/pull/10199) `240b9f4f` | `prop_reconcile` summed `(realized_today or 0.0) + (unrealized or 0.0)`, so a **missing** `realized_today` became a **zero loss** and published a full cushion. | The full **$142.92** daily-loss cushion was rendered over **−$218.79** of recorded losses, on a snapshot graded `ok`. Both terms are now required; `day_pnl_state` ∈ `measured` / `realized_unreported` / `unrealized_unreported` / `unreported`. |

### Alpaca live-readiness — four blockers, **none of them funding**

Recorded in [#10201](https://github.com/benbaichmankass/Metis-Insights/pull/10201),
[#10202](https://github.com/benbaichmankass/Metis-Insights/pull/10202),
[#10203](https://github.com/benbaichmankass/Metis-Insights/pull/10203). No code changed.

1. **`shorting_enabled: False` while 157 of 262 packages (60.0%) are short**; 4 legs are
   100% short. Cross-checked from a second surface: 7 of 16 open positions are genuinely
   short with entry prices.
2. **7 of 15 legs exceed 100% of account notional at every funding level.** Sizing is
   `entry ÷ per_share_risk × risk_pct` — **scale-invariant**, so more money does not fix
   it. Upgraded from projection to live observation: `alpaca_portfolio` holds **$199,697**
   of notional (TLT short 1,042 sh = $87,632), `alpaca_paper` **$168,166** — 8×–80× equity
   at the proposed funding. **No `max_gross_exposure_pct` is declared on the account.**
3. **The paper record is net-negative except `uso_trend_1h`** — 10 of 11 legs negative,
   pnlCoverage 0.35–0.55.
4. **Both detectors that would catch a silent failure skip this account** — read from the
   process environ, not inferred from `.env`. `silent_refusal_alert` is *exactly* the
   detector for the shorting-rejection signature.

Funding arithmetic (secondary, and the only part funding governs): **$2,500** admits all
15 priced legs; **$1,000** admits 14 of 15.

## Validation Performed
- `python3 scripts/ci/run_guards.py` — **exit code checked directly**, not the tail
  (a `| tail -N` swallowed the rc twice earlier in the day)
- New tests: `test_target_naked_cooldown_is_durable.py` (5) ·
  `test_diag_log_file_allowlist_coherence.py` (3) ·
  `test_diag_version_reports_running_sha.py` (5) ·
  `test_prop_daily_cushion_not_fabricated.py` (6)
- Two tests in `tests/test_web_api_diag.py` **encoded the old contract** and were
  rewritten to the new one *without weakening* — the replacements assert strictly more.
- The prop suite initially passed **vacuously** (the fixture passed an account as a dict,
  `_ruleset_for` raised `unhashable type: 'dict'`, every limit resolved to `None`). The
  fixture now asserts the ruleset loaded, and the suite was **falsified** against pre-fix
  code (4 of 6 fail).
- The sandbox `web_api` collection failure (`pyo3_runtime.PanicException` from the system
  `cryptography`) was **fixed rather than excused**:
  `python3 -m venv --system-site-packages /tmp/vdiag && /tmp/vdiag/bin/pip install
  --upgrade --ignore-installed cryptography pyjwt email-validator`. "web_api tests can't
  collect here" is no longer a valid reason to skip a local run.

### Fixes verifying themselves against live data
- `/api/diag/version` now returns `{"git_sha":"240b9f4f","git_sha_on_disk":"78d0bdd0",
  "restart_pending":true}` — before #10196 that endpoint would have reported `78d0bdd0`
  and claimed currency.
- `/api/bot/prop/status` now returns `day_pnl_state: "realized_unreported"` with
  `distance_to_daily_loss_usd: null`, replacing the false $142.92 cushion.
  `distance_to_dd_floor` still resolves ($64.00), so the fix did not blind the panel.

## Documentation Updated
- `CLAUDE.md` — four edits: the `IB_BROKER_NAKED_CHECK_SECONDS` row (durable cooldown),
  the `log_file?name={…}` list (21 → 29 names), the `/api/diag/version` row (new fields
  plus the record that the deploy assertion was vacuous).
- `docs/claude/health-review-backlog.json` — **48 rows** dated `20260823`, of which 8 are
  `resolved` by this session's PRs.
- This log.

## Contradictions or Drift Found
`scripts/ci/check_canonical_doc_coherence.py` passes (rc = 0, all checks). The
`log_file` allowlist and its `CLAUDE.md` row now agree at **29 = 29**, enforced by test.
No doc-vs-doc or precedence violation found.

## Risks and Follow-Ups
- ⚠️ **Two fixes are DEPLOYED BUT UNEXERCISED and must not be reported as working.**
  The target-naked cooldown went live *hours after* the condition ended
  (`BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART`), and #10174's IB transmit
  fix has seen no newly-born bracket — both of today's targets were attached
  retroactively via `attach-ib-target`, a **different code path**.
- The **Tier-3 `alpaca_live` decision is open**: shorting enabled, or long-only. It
  determines both the routing diff and the exposure cap; neither is drafted until the
  operator answers.
- `BL-20260823-ALERT-LATCHES-WITHOUT-A-READ-SURFACE` stays **open** —
  `liveness_watchdog_state.json` (the trader's restart budget) is still unreadable from
  outside.
- The prop-journal gap **08-20 → 08-22** is recording new fills again but was never
  backfilled.

## Deferred Items
- The **exit-geometry rebuild** ("a bracket must carry an expectation at entry") is the
  operator's stated focus and is deliberately deferred to a **fresh session** — it is
  larger than this one's remaining budget.
- `sol_pullback_2h` / `eth_pullback_2h` re-measure after the morning's disposition.

## Next Recommended Sprint
The exit-geometry rebuild, opened fresh. [PR #10198](https://github.com/benbaichmankass/Metis-Insights/pull/10198)
(a concurrent session: donchian bracket sweep, **77/77 gated cells fail on drawdown, not
on returns**) is direct input to it and should be read first.

## Wrap-Up Check
- [x] Every material finding filed with severity + tier + resolution criteria
- [x] `doc-freshness` run; canonical coherence checker green
- [x] Deployed-but-unexercised fixes flagged as such, not reported as working
- [x] No Tier-3 change made or implied
