# Sprint Log: S-TREND-HARNESS-CONVERGENCE-2026-08-08

## Date Range
- Start: 2026-08-08
- End: 2026-08-08

## Objective
- Primary goal: Converge the two divergent `backtest_trend.py` copies onto the **live-faithful** engine, so a `trend_donchian` variant is never graded `approximate` for a WIRING reason (a lever the pipeline's harness had no flag for) rather than a capability reason.
- Secondary goals: (a) move the `exit_head_*` leg to the trainer, where the published head artifact actually lives — the operator's decision on the open question left by #8605; (b) answer whether `backtest_pullback` / `backtest_squeeze` carry the same fork.

## Tier
- Tier 1
- Justification: research tooling, tests, docs, and one new GitHub Actions workflow. No `src/`, no `config/`, no order path, no VM mutation, no DB write. The one Tier-2 file in the wider workstream (`src/runtime/exit_head_shadow.py`) landed in the **predecessor** PR #8618 under an explicit operator approval and is not touched here.

## Starting Context
- Active roadmap items: M20 (exit refinement) tooling; the faithful-backtest platform work (`docs/research/FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md` §5d–§5f).
- Prior sprint reference: PR #8605 (fidelity claim consumed by `backtest_fidelity_calibrate`), PR #8618 (measured the divergence; established the convergence DIRECTION and deliberately stopped short of the port).
- Known risks at start: the continuation prompt warned that #8617/#8618 were squash-merged, so stacking on the old branch would produce a conflicted PR whose CI silently never dispatches. Branch was restarted from `origin/main` accordingly.

## Repo State Checked
- Branch or commit reviewed: started from `origin/main` @ `1b7a717c`; merged `ee904ee6` mid-sprint; landed as `39ef121e`.
- Deployment state reviewed: none mutated. Trainer read-only via the workflow; live VM untouched.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`, `ROADMAP.md`, coordination board #6927.

## Files and Systems Inspected
- Code files inspected: `scripts/backtest_trend.py`, `scripts/research/backtest_trend.py`, `scripts/research/regime_debt_matrix.py`, `scripts/research/trend_harness_divergence.py`, `scripts/ml/exit_head_replay.py`, `src/runtime/exit_head_shadow.py`, `scripts/research/{m20_exit_sweep,m20_fleet_exit_sweep,regime_matrix,regime_tag_emitted,regime_adx_cutpoint_sweep}.py`.
- Config files inspected: `config/strategies.yaml` (`trend_donchian` — read only, not modified).
- Deployment files inspected: none.
- Docs inspected: `docs/research/FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md` §5c/§5e/§5f.
- Services or timers inspected: `ict-trainer-git-sync` (observed lag, not inspected directly).
- GitHub Actions workflows inspected: `research-exit-head-replay-trainer.yml` (new), `bootstrap-labels.yml`, `pytest-run.yml`, `scripts/ci/run_guards.py`.

## Work Completed
- **Item 1 — flag convergence (the primary goal).** All 15 research-only lever flags (`--trail-decay-*`, `--giveback-*`, `--bank-*`, `--confirm-bars`, `--skip-hours`, `--vol-skip-*-pctl`, `--vol-pctl-window`, `--trail-vol-*`) ported into `scripts/backtest_trend.py`. **Re-implemented in that engine's semantics, not copy-pasted** — a paste would have dragged the other engine's rolling-ATR trail and flip exit along with the lever, which is the defect §5f documents. Pipeline flags 28 → **43**; flags present only in the research copy 15 → **0**. Two trail tighteners compose by **minimum**, not sum; `confirm_bars` anchors the trade record to the **entry** bar, not the signal bar.
- **Item 2 — exit-head leg → trainer.** `exit_head_replayable()` asks the environment whether a servable head loads, replacing an unconditional `_UNREPLAYABLE` fold that made a LOCATION fact read as a CAPABILITY fact. New `research-exit-head-replay-trainer.yml` + `exit-head-replay-request` label. Recorded as `exit_head_replayable` / `exit_head_deferred_to_replay` — **not** as a `fidelity` upgrade (see Contradictions).
- **Item 3 — sibling-fork question answered: NONE.** `backtest_trend` was the only forked harness; `backtest_pullback` / `backtest_squeeze` / `backtest_fade` each have exactly one copy.
- **Item 4 — `m20_exit_sweep` + `m20_fleet_exit_sweep` repointed** at the live-faithful engine (no test pins their numbers).
- **Item 5 — follow-up workflow fix (PR #8643).** The first real trainer run failed on `ModuleNotFoundError: No module named 'pandas'` — the remote script used bare `python3` (system) instead of the trainer's `${REPO}/.venv`. Fixed with **no** silent fallback.

## Validation Performed
- Tests run: `tests/test_trend_harness_levers.py` (29 new) + the five pre-existing research-engine lever suites **unmodified** (59 total across the six files); `tests/research/` (39); `test_exit_head_replay`, `test_exit_head_shadow`, `test_trend_harness_stale_exit`, `test_giveback_stop_lever`, `test_strategy_tune_sweep`. CI `pytest-run`: **9,985 passed** on the final head.
- Dry-runs or staging checks: the committed instrument `scripts/research/trend_harness_divergence.py` reproduces the pre-port figures **exactly** (d20 `29 / −13.187`, d30 `22 / −9.822`) and reports 43 pipeline / 36 research / 36 shared / **0 research-only**.
- Manual code verification: measured the divergence directly rather than arguing it — matched-config comparison plus ATR-basis isolation. Confirmed `trend_donchian.order_package` freezes the entry ATR into `meta["atr"]` and `monitor()` trails off that frozen value, which is why the **pipeline** copy is the live-faithful one.
- Gaps not yet verified: **the exit-head replay has never completed a real run.** Both blockers are known (see Risks). The published head artifacts ARE confirmed present on the trainer by direct `ls` — that much is measured.

## Documentation Updated
- Rules doc updates: none needed.
- Architecture doc updates: none — no schema/boundary/pipeline-stage change.
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): **not applicable** — this sprint touched no pipeline stage (research tooling only), so the dashboard Trade Process tab needed no visual verification.
- Roadmap updates: M20 row extended with this sprint (harness convergence + the exit-head leg's location).
- GitHub Actions doc updates: new workflow is self-documenting in its header; no separate doc entry needed.
- Subsystem doc updates: `scripts/research/regime_debt_matrix.py` module docstring rewritten twice — once for the convergence, once to correct the overclaim below.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **Contradiction 1 — my own prior backlog row proposed the WRONG direction.** `BL-20260808-TREND-HARNESS-FORK-SPLITS-FIDELITY-FROM-EVIDENCE` originally suggested repointing `build_harness_cmd` at `scripts/research/backtest_trend.py`. Measurement REVERSED that: the research copy trails off a rolling current-bar ATR, the live monitor trails off the frozen entry ATR. Repointing would have made the fidelity pipeline *less* live-faithful. The row's `proposed_fix` was rewritten to record the settled direction.
- **Contradiction 2 — a design-doc impossibility claim was simply false.** §5e said the exit head "needs the model registry at inference". It does not: the head is a self-contained JSON with the booster inline, loaded from `runtime_logs/trainer_mirror/exit_head/`. The blocker is FILE DISTRIBUTION. An overstated impossibility closes off work, so this is recorded in both the module docstring and the workflow header.
- **Code/doc mismatch — self-inflicted, caught and corrected.** Two code comments and the module docstring were reworded mid-sprint to describe behaviour that the code did not have (see Deferred item 1 and the inert-conditional finding). Both were corrected in the same PR. *Field beats comment* cuts both ways: when the field changes, the comment is what has to move.

## Risks and Follow-Ups
- Remaining technical risks: **the exit-head replay is still unproven end-to-end.** Blocker (a) the venv fix (PR #8643) is not merged; blocker (b) the trainer was at `ee904ee6` and had not synced `39ef121e` — git-sync **lag**, not breakage.
- Remaining product decisions (Tier 3): `BL-20260808-TRAIL-LEVER-TUNED-ON-NON-LIVE-FAITHFUL-TRAIL` — the **live** `trend_donchian` `trail_decay_arm_r: 6.49` / `trail_decay_tight_mult: 2.5` were fitted against the research engine's rolling-ATR trail. The lever is **reductive**, so this is a tuning-basis defect, not an incident. **No config change is proposed.** The M20 params want a re-sweep on the now-converged engine before anyone leans on them.
- Blockers: none for the merged work.

## Deferred Items
- **Deferred item 1 — the engine RETIREMENT.** I over-reached into it; CI caught it; it was reverted. My consumer sweep grepped the module **PATH**, while every importer uses the **bare module name** via `sys.path` — the sweep returned clean over a population it structurally could not see. Eight importers, **five of them test files whose assertions encode the research engine's own semantics** (rolling-ATR trail, opposite-signal flip exit, post-exit cooldown) and are currently its only executable record. Tracked: `BL-20260808-RESEARCH-TREND-ENGINE-RETIREMENT-BLOCKED-BY-TEST-COUPLING`.
- **Deferred item 2 — the inert-conditional CLASS.** The `exit_head_replayable()` gate shipped **inert**: `build_harness_cmd` already lists every unflagged cfg key in `omitted`, so the caller-level fold could only ever UNION keys that were present regardless — byte-identical output on both branches. The test asserted the value *both* branches return, so it passed while proving nothing, and the trainer branch had never executed anywhere. Found by parametrizing over the predicate. Fixed in-sprint; the class is filed as `BL-20260808-INERT-CONDITIONAL-SHIPPED-AS-A-BEHAVIOUR-CHANGE` because **no existing guard covers it** — `diagnostic-provenance-guard` covers labels, `provenance-consumer-guard` covers stored keys, neither covers control flow.

## Next Recommended Sprint
- Suggested next sprint: **S-TREND-ENGINE-RETIREMENT** — migrate the 3 research scripts + 5 test files off `scripts/research/backtest_trend.py`, then retire it behind a convergence guard; and re-sweep the M20 trail levers on the converged engine to resolve the Tier-3 row.
- Why next: the fork is now a FLAG no-op but still a real second engine, and the live trail lever's tuning basis stays wrong until the re-sweep runs.
- Required verification before starting: do **not** blanket-repoint the five test imports — several assert rolling-ATR / flip-exit behaviour the live-faithful engine does not have, so a blind repoint converts a real test into a green one that checks nothing. Decide per file whether the pinned behaviour is engine-specific (port the assertion + re-derive expected numbers, stating the population) or engine-independent (repoint only).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. — **N/A: no pipeline stage touched** (research tooling only).
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.

---

## Verification note carried forward (process, not code)

**The full test suite cannot run in the Claude-Code-on-the-web sandbox** — 38 collection errors, `pyo3_runtime.PanicException`, on the `tests/test_web_api_*` files, which CI collects cleanly. This is why a green **8-file local subset** reached CI with a failure in it: the subset skipped `tests/research/`, the very directory whose behaviour the change altered.

**CI is the authority for this repo's full suite.** A local subset that skips the directory you changed behaviour in is not verification. Two related traps hit the same day, both the same shape (an unasserted denominator reading as a clean negative):

- `mcp__github__pull_request_read` `get_status` returns `total_count: 0` for this repo — that is the **legacy commit-status API**, which the repo does not use. Read `get_check_runs`.
- A `cd`-less Bash call landed in the wrong directory; every `git` command failed, yet a `wc -l | xargs` pipeline still printed `commits ahead: 0`. Failed commands producing clean-looking zeros.

And one merge-mechanics fact worth not re-deriving: a PR at `mergeable_state: "dirty"` (real merge conflict) **suppresses CI dispatch entirely**, which is indistinguishable from a slow queue if you only look at `get_check_runs` returning `total_count: 0`. Resolving the `health-review-backlog.json` conflict naively would also have silently dropped a concurrent session's row — take `main` verbatim, re-append your own, and assert your side introduced exactly the ids you expect.
