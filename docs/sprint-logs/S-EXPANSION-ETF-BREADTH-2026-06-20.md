# Sprint Log: S-EXPANSION-ETF-BREADTH-2026-06-20

## Date Range
- Start: 2026-06-20
- End: 2026-06-20 (wrap-up PR `claude/session-wrap-followups`; continues the
  expansion-backtesting arc)

## Objective
- Primary goal: expand the tradeable surface via the existing robustness engine —
  research new symbols + non-ICT strategy families, then wire the validated
  ETF-breadth daily cells + the intraday (1h) ETF sleeve onto `alpaca_paper`
  (paper-only), recording the honest negatives.
- Secondary goals (this wrap-up PR): three follow-up fixes surfaced by the
  research — (A) the `/performance` asset_class reporting bucket fix
  (SLV/USO→commodity, TLT/IEF→bond, new `bond` token); (B) an
  `account_compat_matrix.py --ledger` path so aliased ETF cells can be scored
  from a harness emit; (C) repo cleanup of raw trainer logs; plus backlog
  logging + this doc sweep.

## Tier
- Tier 1 / Tier 2.
- Justification: research tooling + reporting metadata (asset_class is a
  reporting-only resolver, never the order path) + config instrument metadata +
  docs. No `config/strategies.yaml` / `config/accounts.yaml` / coordinator /
  risk / execute / orders change in THIS wrap-up PR. The cell-wiring PRs (#4048 /
  #4067 / #4069) are paper-only (`alpaca_paper`); real-money promotion of any
  ETF cell remains Tier-3 + `account_compat_matrix`-gated.

## Starting Context
- Active roadmap items: M7/M8 strategy-expansion program (continues
  S-CROSS-ASSET-DIVERSIFY / S-RECOMB-SWEEP / S-DIVERSIFY-BANK).
- Prior sprint reference: S-DTP-EXITPLAN-2026-06-17 (top of the ledger before
  this row).
- Known risks at start: the intraday ETF cells were wired ahead of soak — no
  live paper fill observed yet; ETF asset_class overrides in
  `config/instruments.yaml` wrongly pinned several ETFs `equity`, mis-bucketing
  the `/performance` perAssetClass breakdown.

## Repo State Checked
- Branch or commit reviewed: `origin/main` @ `7348c3a` (contains the research +
  cell-wiring PRs #4043 / #4048 / #4067 / #4069). Worked on
  `claude/session-wrap-followups` cut from it.
- Deployment state reviewed: per the research doc + ROADMAP, #4048 (daily cells)
  and #4067 (intraday pilot) are deployed + verified live; #4069 (rollout-2b)
  deployed.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`
  (instruction hierarchy, tiers, two execution gates), ROADMAP.md,
  `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`; skills `sprint-format`,
  `doc-freshness`.

## Files and Systems Inspected
- Code files inspected: `src/web/api/_asset_class.py`,
  `scripts/prop/account_compat_matrix.py`, `src/prop/montecarlo.py`
  (`ledger_to_r_sequence` + `_get`/`_exit_key`), `scripts/backtest_system.py`
  (`ROSTER`), `tests/test_perf_asset_class_close_basis.py`.
- Config files inspected: `config/instruments.yaml` (ETF entries + header).
- Docs inspected: `docs/research/expansion-backtesting-research-2026-06-20.md`
  (§0c carry, §0d ETF-breadth, §0e intraday + pairs), ROADMAP Historical Sprint
  Ledger, `docs/claude/performance-review-backlog.json`.
- Services/timers inspected: none mutated (no live-VM action this PR).
- GitHub Actions workflows inspected: none.

## Work Completed
- **(A) asset_class reporting fix** — `src/web/api/_asset_class.py`: added the
  `BOND` token (`CLASS_ORDER` = crypto, index, commodity, bond, equity, fx,
  unknown), a `_BOND_ROOTS` set (TLT/IEF/AGG/BND/LQD/HYG/SHY/TLH/IEI/SHV/BNDX/
  TIP), a bond-root check in `_infer()` before the alpaca→equity fallback, and
  docstring update. `config/instruments.yaml`: SLV/USO `equity`→`commodity`,
  TLT/IEF `equity`→`bond` (stale "no bond class token" comments replaced),
  header token list updated. New test `tests/test_asset_class_etf.py`.
- **(B) `account_compat_matrix.py --ledger`** — new `--ledger PATH` option +
  `synth_ledger_from_emit()` helper. When given, SKIPS the `bt.ROSTER` check AND
  the `bt._load_candles` + `bt.run_system_backtest` engine run; loads the harness
  emit JSONL and synthesizes a closed-trade ledger that round-trips EXACTLY
  through `montecarlo.ledger_to_r_sequence` (sets `pnl = net_r * balance_before *
  base_risk_pct/100` over a compounding balance, populating the `pnl` + `exit_ts`
  keys the reader actually reads). `--strategy` no longer required when
  `--ledger` is given (label defaults to the ledger stem). The ROSTER engine path
  is byte-for-byte unchanged when `--ledger` is absent. New test
  `tests/test_account_compat_matrix_ledger.py`.
- **(C) cleanup** — `git rm automation/results/*.txt` (47 raw trainer-log files);
  `automation/results/.gitkeep` preserved. `automation/jobs/` not touched (it does
  not exist). `automation/pr-results/` + `automation/session_handoff/` untouched.
- **(D) backlog** — appended PB-20260620-001 (verify the 6 intraday ETF cells
  produce live paper fills next US RTH), PB-20260620-002 (funding-carry dormant/
  regime-dependent — revisit when funding elevates), PB-20260620-003
  (cross-sectional sleeve un-built — deprioritized after pairs rejected) to
  `docs/claude/performance-review-backlog.json`.
- **(E) doc sweep** — ROADMAP `S-EXPANSION-ETF-BREADTH` row + "Last Updated"
  banner; this sprint log; doc-freshness pass.

## Validation Performed
- Tests run (this sandbox, `python -m pytest`):
  - `tests/test_asset_class_etf.py` — **5 passed**.
  - `tests/test_account_compat_matrix_ledger.py` — **2 passed** (the round-trip
    is exact: recovered `r_multiple` == input `net_r` for all 60 rows).
  - Total **7 passed in 0.29s**.
- `ruff check src/web/api/_asset_class.py scripts/prop/account_compat_matrix.py`
  + both new test files — **All checks passed**.
- `python -c "import yaml; yaml.safe_load(open('config/instruments.yaml'))"` — OK.
- `python -c "import json; json.load(open('docs/claude/performance-review-backlog.json'))"`
  — OK (41 items, updated_at 2026-06-20).
- Sanity print: `{GLD: commodity, SLV: commodity, USO: commodity, TLT: bond,
  IEF: bond, SPY: equity, QQQ: equity, IWM: equity, BTCUSDT: crypto}` — exactly
  as specified.
- Gaps not yet verified:
  - `tests/test_perf_asset_class_close_basis.py` could NOT be collected in this
    sandbox — it imports `src.web.api.routers.performance`, which imports
    `fastapi`, not installed here. This is a pre-existing environment limitation,
    NOT a regression from these changes (my new tests deliberately avoid the
    FastAPI import). It should pass in CI where fastapi is present; its
    `_asset_class` assertions (GLD=commodity, SPY=equity, …) are untouched by
    this change.
  - **Fix (A) needs a `ict-web-api` restart to take effect** — asset_class is
    read by the `/performance` reporting path (and the resolver lru-caches the
    instruments table per-process). Tier-2 deploy step, operator-gated on the
    live VM.
  - The intraday ETF cells producing live paper fills is UNVERIFIED (next US
    RTH; tracked by PB-20260620-001).

## Documentation Updated
- Rules doc updates: none needed.
- Architecture doc updates: none (no pipeline-stage / contract change; the
  `bond` token is an additive reporting bucket).
- Trade pipeline doc updates: none (no pipeline stage changed).
- Roadmap updates: new `S-EXPANSION-ETF-BREADTH` row + banner.
- GitHub Actions doc updates: none.
- Subsystem doc updates: the research doc
  `docs/research/expansion-backtesting-research-2026-06-20.md` already shipped in
  #4043.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **Dangling references from the cleanup (recorded, not blocking):** several
  ROADMAP Historical-Sprint-Ledger rows + the research doc cite raw logs under
  `automation/results/*.txt` (e.g. `cross-asset-robust.txt`,
  `direction2-collect2.txt`, `xa-eth-*.txt`) that this PR `git rm`s per Task C.
  Those are breadcrumb pointers to trainer-run logs, not load-bearing artifacts
  (the conclusions live in the research/sprint docs). Removing the raw logs from
  git is the intended cleanup; the citations remain as historical record of where
  the run output lived. Noting for the operator rather than rewriting historical
  rows.
- No canonical doc-vs-doc or doc-vs-reality contradiction found across
  CLAUDE-RULES-CANONICAL / ARCHITECTURE-CANONICAL / ROADMAP / CLAUDE.md for the
  changes made (two execution gates, permission tiers, instruction hierarchy, VM
  topology, removed-gate set, 3-stage ML ladder all consistent).

## Risks and Follow-Ups
- Remaining technical risks: asset_class fix is inert until the web-api restarts.
- Remaining product decisions (Tier 3): real-money promotion of any ETF cell
  (account_compat-gated); funding-carry cell (PB-20260620-002); cross-sectional
  sleeve (PB-20260620-003).
- Blockers: none.

## Deferred Items
- Intraday-cell live-fill verification (PB-20260620-001).
- A futures/daily extension of `account_compat_matrix` beyond the BTC ROSTER
  engine — the `--ledger` path is the stop-gap that lets aliased cells be scored
  now.

## Next Recommended Sprint
- Suggested next sprint: next US-RTH /performance-review to drain
  PB-20260620-001 (intraday ETF cell live-fill check) + grade the ETF book.
- Why next: the intraday sleeve is wired-but-unverified; first operational read
  is the highest-value follow-up.
- Required verification before starting: a live RTH window with alpaca_paper ETF
  activity; pull the journal + /performance for the ETF cells.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage changed; `docs/TRADE-PIPELINE.md` not applicable.
- [x] Roadmap status was checked + updated.
- [x] Contradictions were recorded (dangling automation/results citations).
- [x] Remaining unknowns were stated clearly (web-api restart; intraday live
      fills; the fastapi-dep test-collection gap in this sandbox).
