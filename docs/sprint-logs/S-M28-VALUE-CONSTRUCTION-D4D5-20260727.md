# Sprint Log: S-M28-VALUE-CONSTRUCTION-D4D5-20260727

## Date Range
2026-07-27 (single session; research-driver, continuation of S-M28-VALUE-CONSTRUCTION-P4).

## Objective
Extend `scripts/macro/value_construction_sweep.py` (ledger entry 13) with the queued
next-iteration construction cells and grade each through the **UNCHANGED** P4
net-of-cost lifecycle gate:
- **D4 composite** — `change ⊕ detrend`, equal-weight then IC-weighted.
- **D5 horizon sweep** — grade the `change` cell across hold horizons to find where its
  edge survives cost.
- **D2 regime-conditioning** — condition the `change` cell on a price-derived vol/trend
  regime.

Decision rule (entry 13's hand-off): if D4/D5/D2-regime stay sub-threshold
(`edge_vs_baseline > 0` net-of-cost, the naive-all-long bar), value is
**cross-gate-conclusively exhausted** → record it and recommend the pivot
(higher-freq microstructure / operator-gated dataset).

## Tier
Tier-1 throughout — pure observe-only research tooling + tests + docs. No order path,
no live influence (P5 expression / P6 `c_macro` remain Tier-3, out of scope).

## Starting Context
Fresh branch `claude/m28-value-thesis-sweep-evapa5` off latest `main` (b827590). Prior
PR #7766 (entry 13) established: D1 `change`/`detrend` + D2 `turning` **beat the level
baseline and flip conviction calibration positive**, but **nothing beats naive-all-long
net-of-cost** — the value edge lives in the shift, not the level, but sub-threshold.

## Repo State Checked
Read `docs/CLAUDE-RULES-CANONICAL.md` + root `CLAUDE.md` (in full), the
`session-coordination` skill, `RESEARCH-RIGOR-STANDARD.md` (§ backtest-history-first),
`M28-signal-research-methodology.md` (D1–D5 backlog), `M28-P4-value-gate-run` doc, the
ledger (entries 1–15 + M30–M34 sections), and the full P4 toolkit
(`value_construction_sweep.py`, `signal_constructions.py`, `thesis_backtest_run.py`,
`src/units/strategies/macro_thesis/{thesis_backtest,thesis_replay,thesis_tick}.py`,
`.github/workflows/m28-value-grade.yml`).

## Work Completed
- **`scripts/macro/value_construction_sweep.py`** — added, all graded through the
  UNCHANGED P4 gate (`form_tick_theses` reads `cheap_score` regardless of the metric tag):
  - **D4 composite** (`emit_composite_construction`): blends the `change` + `detrend`
    legs' oriented cheap_scores into one conviction on their intersecting
    `(symbol, base-driver, date)` keys. `composite_eq` (equal-weight, pure) and
    `composite_ic` (IC-weighted: each leg weighted by its own standalone
    `calibration_rank`, clamped ≥0 — deliberately **in-sample-optimistic**, so a null is
    conclusive; caveated in the code + here). A key is emitted only when EVERY leg reports
    it (a real composite, never a single-leg passthrough).
  - **D5 horizon sweep** (`render_horizon_table` + `--horizon-sweep`, default
    `7,14,30,60,90,180`): grade the `change` cell at each hold horizon (each baseline
    horizon-matched inside `run_thesis_backtest`) to locate the horizon where
    `edge_vs_baseline` turns positive net-of-cost, if any.
  - **D2 regime-conditioning**: condition the `change` cell on a **price-derived** regime
    gate — `change_x_calm_vol` (trailing realized-vol ≤ its trailing median = calm) and
    `change_x_uptrend` (price momentum > 0). Both gates are trailing/past-only (PIT-safe).
  - **Bug fix (`_asof_gate`)**: the price-gated cells (incl. the pre-existing
    `level_x_price_turning`) had scored **n=0** because value-snapshot `as_of` dates are
    weekly **Saturdays** (FRED weekly series), which are never trading days — so the
    exact-date gate match (`condition_snapshots`' `gate.get(as_of)`) found nothing and
    neutralized every row. `_asof_gate` resolves a trading-day gate onto each Saturday
    `as_of` **as-of-or-prior** (PIT-safe, never a future bar), so every price-gated
    conditioner now actually scores.
- **`tests/test_m28_value_construction_sweep.py`** — +2 tests: the composite blend
  (equal- and weighted, incl. the mean identity + all-weight-on-one-leg identity) and the
  Saturday-`as_of` as-of-or-prior gate resolution. 7 tests total, green.

## Validation Performed
- `ruff check scripts/macro/value_construction_sweep.py tests/test_m28_value_construction_sweep.py` — clean.
- `pytest tests/test_m28_value_construction_sweep.py -q` — **7 passed**.
- End-to-end grade **smoke-test with synthetic candles** (real fetch is proxy-blocked here
  — see Blockers): D4 `composite_eq`/`composite_ic`, the D5 horizon table, and the
  D2-regime cells (`change_x_calm_vol` n≈295, `change_x_uptrend` n≈396) all produce real
  n (no longer n=0) and the tables render. **The synthetic numbers are meaningless** — they
  only prove the pipeline runs end-to-end; the decision-grade numbers require the real ETF
  candles the hosted runner fetches.

## Blockers (honest, load-bearing — this session could NOT obtain the grade)
Two hard capability walls prevented running the decision-grade P4 grade in this session:

1. **This session's GitHub integration is read-only for issues/PRs.** `get_me` and all
   reads succeed, but **every write returns `403 Resource not accessible by integration`**
   — reproduced on `add_issue_comment` (the #6927 board START/CLAIM), `create_pull_request`
   (this PR), and it will equally block opening the `m28-value-grade-now` grade issue.
   `git push` works (separate credential), so the **code is pushed** to
   `origin/claude/m28-value-thesis-sweep-evapa5` (commit `2e3ac95`), but the PR could not
   be opened and the grade could not be dispatched from here. (Distinct from the documented
   transient MCP "token expired" drop — this is a consistent permission-scope 403, not a
   blip; a retry did not clear it.)
2. **Candle fetch is proxy-blocked from this session**, so a local grade is also impossible:
   the agent proxy answers `403 Forbidden` to CONNECT for `stooq.com` and
   `query1.finance.yahoo.com` (allowlist is pypi/npm/anthropic/github only), and `yfinance`
   isn't installed. The grade needs ~21yr of SPY/TLT/IEF/GLD/SLV daily closes, which only
   the hosted US-IP runner (`m28-value-grade.yml`) can fetch.

Because the grade needs THIS code on `main` (the workflow checks out `ref: main`) AND the
runner to fetch candles, and neither the merge nor the grade dispatch is reachable from
this session, the **grade result was not obtained** — so per the honesty rule, the ledger
entry + ROADMAP row recording the D4/D5/D2-regime verdict are **deliberately NOT written
yet** (recording numbers not measured would be fabrication). The conditional conclusion
("value exhausted → pivot") is likewise **not** asserted, because it is contingent on a
sub-threshold grade this session did not run.

## Operator hand-off (the one genuine decision point)
The code is complete, tested, and pushed. To finish the milestone:
1. **Grant issue/PR write to this session's GitHub integration** (so a future session can
   drive it end-to-end), **or** do the two steps below manually:
2. **Open + merge the PR** from `claude/m28-value-thesis-sweep-evapa5` → `main` (Tier-1,
   ruff+pytest green). Suggested title:
   `feat(M28): value-construction D4 composite + D5 horizon sweep + D2 regime-conditioning`.
3. **Dispatch the grade**: open an issue with the label **`m28-value-grade-now`** (any
   title/body); the `m28-value-grade` runner fetches the 5-symbol candles, runs the sweep on
   the committed 21yr backfill, and posts the D4/D5/D2-regime scorecard back to the issue.
4. A follow-up docs pass then records the real numbers in
   `docs/research/M28-signal-research-ledger.md` (new entry), `ROADMAP.md` (M28 row), and
   the ledger's compounding-read section — and, **if D4/D5/D2-regime are all sub-threshold**
   (`edge_vs_baseline ≤ 0`), records **value as cross-gate-conclusively exhausted** and
   recommends the pivot (higher-freq microstructure off existing feeds / operator-gated
   dataset), per entry 13's hand-off and the ledger's 2026-07-24 Escalation section.

## Documentation Updated
This sprint log (the durable session record) + `docs/claude/session-board.json`
(`active_sessions` registration, noting the board-comment 403). Ledger + ROADMAP updates
are held for the grade result (above).

## Contradictions or Drift Found
None. One latent bug fixed in passing (the `n=0` price-gate exact-date-match, above) — the
prior sprint log (S-M28-VALUE-CONSTRUCTION-P4) had flagged `level_x_price_turning` n=0 as
"minor; price-momentum gate over-filtered"; this session root-caused it (Saturday `as_of`
vs trading-day gate) and fixed it so all price-gated cells actually score.

## Risks and Follow-Ups
- The decision-grade P4 grade is pending the operator hand-off above.
- The IC-weighted composite uses full-sample calibration_rank as the leg weight
  (in-sample-optimistic); this is intentional (a null under optimistic weighting is
  conclusive) and caveated in the code — a stricter IS/OOS-split weight fit is a possible
  refinement if the composite ever clears (it is not expected to).

## Next Recommended Sprint
`S-M28-VALUE-D4D5-GRADE-RECORD` — after the PR merges + `m28-value-grade-now` posts the
scorecard: record the D4/D5/D2-regime verdict in the ledger + ROADMAP, and — if
sub-threshold — close M28 value as cross-gate-conclusively exhausted and recommend the
microstructure/operator-gated-dataset pivot.

## Wrap-Up Check
Code + tests pushed (commit `2e3ac95`); ruff + pytest green; synthetic-candle smoke test
passed. Grade + merge blocked on integration write-403 + candle-fetch-block (documented
above with the operator hand-off). Coordination-board START/CLAIM could not be posted
(403) — registered in `session-board.json` instead. `doc-freshness` pending on the docs
follow-up (which lands with the grade numbers).
