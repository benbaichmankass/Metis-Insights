# Sprint Log: S-PROVENANCE-EXITLEAK-ROOTCAUSE-2026-07-30

## Date Range
- Start: 2026-07-30 ~13:15 UTC
- End: 2026-07-30 ~15:05 UTC

## Objective
- Primary goal: root-cause the Bybit scalp exit leak (7d, 37 closed / −$6,358.37;
  `reconciler_filled` n=28 dominant). Deliverable was explicitly NOT a fix — a correct
  causal account plus a triaged split of fixable-now vs not-yet-understood.
- Escalated mid-session by the operator, in two steps: (1) explain why repeated thorough
  audits keep missing this bug class, and fix it **structurally, enforced by machinery,
  in a way that accounts for how future Claude sessions will use it**; (2) improve the
  exit-price reconstruction beyond a bar close, and pursue the IBKR side (historical +
  going forward).

## Tier
- Tier 1 throughout (docs, CI guard, diagnostics, research tooling, read-only analysis).
- **No Tier-2 or Tier-3 change was made or merged.** The one Tier-2 remedy (the
  `_sweep_local_pnl_for_unpriced` pricing path + INV-2 `unmeasured` state) is written as
  an exact diff and left unmerged pending operator approval.

## Starting Context
- Prior sprint: `S-BYBIT-COVERAGE-DEPLOY-VERIFY-2026-07-30`, which closed with "the scalp
  exit leak has no established cause" (`BL-20260730-EXITCAPTURE-DEEPDIVE-WRONG-TPSL-PREMISE`).
- Ruled out before this session and not re-derived: `BYBIT_TPSL_MODE=partial` is live and
  predates today; `bybit_2` brackets audited clean; `side_filter: short` deployed-but-unexercised.

## Repo State Checked
- `main` at start `f8ef69f`/`8ecfcff`; ended merged with `0285b1e1`.
- **The session clone was SHALLOW (50 commits)** — the exact trap
  `BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE` describes. Unshallowed to 2961 commits
  before any history read, so the mandated Tier-2/3 `git log -p` check was valid.
- This session's MCP scope resolved to `metis-insights`, not `ict-trading-bot` — the
  denial on the old name is a scope mismatch, exactly as CLAUDE.md warns.

## Files and Systems Inspected
- `src/runtime/order_monitor.py` (`_close_trade_from_order_status` :5473, `_classify_broker_exit`
  :5237, `_sweep_local_pnl_for_unpriced` :7233), `src/units/accounts/clients.py`
  (`account_closed_pnl_for_trade` :758, the `is_demo` early-return :884),
  `src/runtime/local_pnl.py::last_mark_price`, `src/units/strategies/ict_scalp.py::monitor` :817,
  `scripts/backtest_ict_scalp.py::_walk_forward_exit`, `src/web/api/_clean_trades.py`,
  `src/web/api/routers/performance.py` (the `rCoverage` precedent), `scripts/check_db_integrity.py`
  (INV-2), `config/accounts.yaml`.
- Live VM read-only via diag relay (`ib_state`, `journalctl`) and Tier-1 system-actions
  (`monitor-miss-analysis` ×2). Trainer VM via `trainer-vm-diag` (7 read-only SQL/analysis runs).

## Work Completed
- **Root cause established and evidenced end-to-end** (below).
- **`src/runtime/provenance.py`** — the canonical, typed provenance vocabulary
  (`measured` / `estimated` / `fabricated` / `unverified`), `coverage()`,
  `require_measured()`, `UNTRUSTED_BUCKETS`. `is_measured()` is strictly binary.
- **`scripts/check_provenance_consumers.py`** — CI guard failing when a declared provenance
  key gains a writer but no consumer. Shaped after `canonical-db-resolver` / `env-gate-guard`.
- **`scripts/ops/monitor_miss_analysis.py`** — provenance-gated; refuses to classify
  mark-substituted exit prices and always prints the honest denominator + coverage.
- **Two exit-reconstruction validators** (`scripts/research/exit_reconstruction_validator{,_v2}.py`),
  both indexed in `docs/research/RESEARCH-CAPABILITY-INDEX.md`.
- **7 backlog entries** filed; PR #8039 (draft, CI 21/21 green).

## Validation Performed
- **The causal chain** — `clients.py:884 if is_demo: return None` (#4503, 2026-06-25, a
  correct fix for demo closed-pnl mis-mapping) short-circuits the lookup for every demo
  account (`bybit_1`, `bybit_portfolio` both `demo: true`) → `_close_trade_from_order_status`
  always takes the fallback branch (`exit_price` NULL, `exit_reason` pinned `reconciler_filled`)
  → `_classify_broker_exit` (2026-06-23) is downstream of a price the code deliberately
  refuses to fetch, so `sl`/`tp` is **structurally unreachable on demo**; the two changes
  landed two days apart → 6h later (`_LOCAL_PNL_BROKER_DEFER_MS`)
  `_sweep_local_pnl_for_unpriced:7381` substitutes `last_mark_price()` and books pnl from it.
- **Matched-pair proof** (`bybit_portfolio` mirrors `bybit_2`, same strategy/symbol/bracket/minute):
  trade 4180 real, exit 64100.0 vs sl 64110.32 = **−$4.00**; mirror 4181 = **−$2,589.78**.
  Trade 3909 real, labelled `sl`, **−$3.26**; mirror 3910 = **−$1,434.99**. ~650×.
- **Anchor #4218** caught mid-flight — closed 07:40:36, still inside the 6h defer,
  `exit_price`/`pnl` both NULL. 38 of 39 sibling rows had already been overwritten to
  `local_markprice`.
- **In dollars** — real-money 7d scalp PnL is **−$1.91**; the ~−$6,297 is demo artifact
  (`bybit_1` −$2,272.10 + `bybit_portfolio` −$4,024.78).
- **Control arm** — `monitor-miss-analysis` on real-money `bybit_2` returned `SL_hit`
  mean_R **−1.008** (exits land on the stop, as designed); the same code path on demo
  returned `beyond_SL` −3.94R / `beyond_TP` +6.31R, impossible for a bracket exit.
- **Provenance blast radius** (trainer #8040) — lifetime `local_markprice` 226 rows
  **+$247,683.78**; 247 rows with no provenance key at all; paper shows +$247,657 fabricated
  vs −$14,969 measured. Fabricated share **0.0% (May) → 30.5% (Jun) → 64.9% (Jul)**.
  Real money largely clean (187 measured / 12 fabricated).
- **Write-only sweep** — `exit_price_source` written in 12 files, branched on in ONE (for an
  unrelated value), **zero** references in the whole `ml/` tree. 5 of 9 quality signals are
  write-only (`exit_price_source`, `pnl_source`, `exit_reason_source`, `close_exec_type`,
  `unrealizedPnlSource`); only `reconcile_status`, `cost_source`, `backfill_kind` have consumers.
- **Exit reconstruction, measured not asserted** (trainer #8042/#8046/#8049) — candidate
  estimator (bracket walk + time-consistency filter): n=48, **median 1.33 bps, p90 16.05,
  46/48 within 50 bps**, discarding only 4 rows. Bybit candle coverage 436/436 (100%);
  IBKR 0/12.
- **Liquidations ruled out** — every recorded `close_exec_type` is `Trade`; no `BustTrade`/`AdlTrade`.
- **Tick starvation ruled out** — monitor observed live running the scalp legs with
  `verdict=None`, `errors=0`; IB client 497 `breaker_open` but `likely_wedged:false`.
  `ict_scalp.monitor()` has **no close path** at all, so a starved monitor could not change
  an ict_scalp exit reason regardless.

## Documentation Updated
- `docs/claude/health-review-backlog.json` — 7 entries (below). Merge conflict with a
  concurrent session resolved as a **union**: 319 items, no id dropped from either side.
- `docs/research/RESEARCH-CAPABILITY-INDEX.md` — both validators indexed (53/53 routed).
- `CLAUDE.md` — provenance module pointer (discoverability; see Contradictions).
- This sprint log; ROADMAP Items-Under-Consideration row.

## Contradictions or Drift Found
- `canonical-doc-coherence` passes all four checks; no doc-vs-doc or precedence drift found.
- **The structural finding is itself a drift class**: a capability that exists and cannot
  be routed to. `provenance.py` was at risk of becoming the next instance, so it is
  explicitly pointed at from `CLAUDE.md` and the validators are in the research index —
  the `guard` CI failure this session (`check_research_index`) made the point for me:
  *"a session cannot route to a tool it cannot find."*
- **Two of my own hypotheses were disproven by my own measurements and are recorded as
  wrong, not quietly dropped**: (1) v2's decision-time-bracket + break-even-replay
  "fixes" made the estimator WORSE (median 2.50 vs 1.49 bps, p90 140.8 vs 38.4) and were
  not even a clean A/B (294 of 436 rows lack a package bracket); (2) the trade-4076
  "journal stop is post-ratchet" reading was wrong — arm B used the package bracket and
  produced the identical value, so 4076 is an **inverted bracket** (stop below entry on a
  short), a different bug.

## Risks and Follow-Ups
- `BL-20260730-PROVENANCE-RECORDED-THEN-IGNORED` (**critical**, tier 2) — the class.
- `BL-20260730-DEMO-MARKPRICE-FABRICATES-PAPER-PNL` (high, tier 2) — the instance.
- `BL-20260730-WRITE-ONLY-SIGNAL-SWEEP` (high, tier 1) — 5 of 9 signals write-only;
  `close_exec_type` (liquidation vs normal fill) and `fc_present` (the M19 soak's honest
  coverage denominator) are both unread.
- `BL-20260730-MONITOR-MISS-ANALYSIS-VACUOUS-ON-DEMO` (high, tier 1) — fixed this session.
- `BL-20260730-SCALP-LONG-HOLDS-UNEXPLAINED` (medium) — genuinely open and currently
  unmeasurable (e.g. #3929, 4.3 days on a 15m scalp against a ~0.1% bracket).
- `BL-20260730-EXITCAPTURE-DEEPDIVE-WRONG-IN-A-SECOND-WAY` (medium).
- **Operator-gated, NOT applied:** the Tier-2 `_sweep_local_pnl_for_unpriced` change +
  INV-2 `unmeasured` state (INV-2 turned out to live in `scripts/check_db_integrity.py`, a
  *reporting* check, so that half is Tier-1 — only the sweep is Tier-2).
- **IBKR is the largest unfixed block** — `ib_paper` holds **+$240,569 of the +$247,683**.
  No `IBClient.fills()` / `reqExecutions`; `interactive_brokers` absent from
  `BROKER_PNL_READER_EXCHANGES = {"bybit"}`, though `exchange_fills` + the Alpaca mapper
  exist as the template. Separately `ib_connector.get_ohlcv` takes no `since`, so historical
  IB bars are unreachable while `pull_mes_ibkr_history.sh` already chunks ~80
  `reqHistoricalData` calls.
- **Do not tune any strategy, exit, or promotion gate on paper PnL** until the
  measured/fabricated split is surfaced.
- Two incidental integrity findings: **67% of measured rows have no `order_packages.sl/tp`**,
  and **inverted brackets exist**.
- **CI never attaches at PR creation** — checks only fired after a push (`synchronize`).
  A PR opened via the GitHub MCP and never pushed to sits at zero checks, which renders
  identically to "nothing wrong".
- The trainer's repo-root `trade_journal.db` is **10 days stale** (Jul 20) next to the live
  `data/` copy — independently filed by a concurrent session as
  `BL-20260730-TRAINER-JOURNAL-PULL-STALE`.

## Deferred Items
- The 4 remaining write-only keys need consumers before `provenance-consumer-guard` can be
  wired blocking. It is deliberately NOT baseline-waivered — a permanent waiver would be
  the band-aid the operator ruled out.
- `/performance` `pnlCoverage` (Tier-1, generalizing the existing `rCoverage` pattern).
- Historical relabel pass (operator chose **relabel only, never re-price**).
- The bracket/netting end-to-end proof: `bybit_1` BNBUSDT still shows **5 journal rows
  against one 9.72 exchange position** (journal claims 13.43), 3 without tracked legs, and
  four of the five display a stop that is not at the venue.

## Next Recommended Sprint
Build the IB executions reader (makes `ib_paper` rows MEASURED rather than estimated) and
land the Tier-2 pricing change + INV-2 `unmeasured` state. Then wire
`provenance-consumer-guard` blocking once the 4 keys have consumers.

## Wrap-Up Check
- [x] Every number here is a receipt from trainer-diag #8035/#8036/#8038/#8040/#8042/#8046/#8049,
      system-actions #8033/#8034, diag #8037, or a read of code at the merged SHA.
- [x] No Tier-2/Tier-3 change made or merged; the one Tier-2 remedy is a written diff awaiting approval.
- [x] Shallow clone detected and unshallowed BEFORE any history read.
- [x] Two of my own hypotheses disproven by measurement and recorded as wrong.
- [x] Coordination board START + DONE posted; backlog conflict resolved as a union, nothing dropped.
- [x] Errors made and disclosed in-session: an `issue_write update` that would have overwritten
      board #6927's body (denied on scope, then done correctly as a comment); twice damaged
      `monitor_miss_analysis.py` mid-refactor (deleted `_f`/`_classify`, then the `sys.path`
      bootstrap the new import depends on) — both caught by testing, not reading; a background
      poller built on anonymous `curl` that the proxy 403s, so it would have reported
      `TIMEOUT_NO_RESULT` regardless of the real outcome — the same false-negative class this
      sprint is about, in my own tooling; and two stale-cache validator runs whose numbers were
      from the wrong script version until a SHA-pinned fetch + assertion was added.
