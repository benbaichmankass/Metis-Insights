# `comms/strategy_evidence/runs/2026-10-07-stage0-prop/` — committed Stage-0 trade records for the prop-bar fit

**What these are.** The per-trade rows (`<leg>__trades.jsonl`) of the five
Stage-0 harness dispatches that PASSED their pre-registered rule
(RQ-20261006-009 / -017 / -018 / -021 / -031, graded 2026-10-07 by
`scripts/research/regrade_wiring_only_results.py` from the landed measurement),
with one provenance sidecar each (`<leg>__stage0.json`: unit, rule, run id and
URL, commit, artifact id, cost stack, sha256 of the rows).

**Why they are committed.** `research-harness-dispatch.yml` uploads
`trades.jsonl` as a run artifact with `retention-days: 14` and commits only the
summary record under `research/results/`. The prop-bar fit
(`scripts/research/prop_ev_grid.py`, via `research-script-run.yml`) prices a
committed per-trade record and the runner executes only
`scripts/research/*` and `scripts/backtest*.py` — it cannot fetch a corpus or
re-run the harness. Without these files the five PASS legs could not be fitted
once the artifacts expired (2026-10-20 .. 2026-10-21).

**Provenance (MEASURED).** Each `__trades.jsonl` is the verbatim artifact of
the run named in its sidecar, downloaded 2026-10-07; `sum(net_r)` over the rows
reproduces the run's `result.json` `net_total_r` to within rounding, and the row
count equals the `population.n` on the landed result row. The bytes are not
edited.

**What they are not.** Not an M7 evidence record: nothing here is
`comms/strategy_evidence/<leg>.json`, so `check_roster_promotion_evidence.py`
and `prop_ev_grid.py --leg LEG` (unpinned) do not resolve to them — a fit unit
pins the path with `--leg LEG=PATH`. Not a live or paper leg: no entry in
`config/strategies.yaml` or `config/accounts.yaml` names these legs. In-sample
over the harness's default 1095-day (crypto) / 720-day (SLV) window on default
parameters; no OOS split exists in these harnesses.

**`fade_slv_1h` cannot be priced yet.** `scripts/backtest_fade.py`'s
`--emit-trades` rows carry no `exit_time`, `entry` or `sl`, which
`prop_ev_sim.py` requires on every row. The record is kept so the PASS is not
lost; the gap is PI-20261007-71Y2H2VZ-0001 (fix the emitter, re-dispatch,
re-commit, then seed the fit).
