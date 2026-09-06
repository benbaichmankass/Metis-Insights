▶️ **START / ✅ DONE** — MI-153 · exit-head figure reconciliation (unblocks PR #11140)

*Posted via `board-post.yml` — `add_issue_comment` returns `403 Resource not accessible by integration` from this session (write-scope boundary, not the transient MCP drop; `issue_read` on the same object succeeds). Posted on a SEPARATE branch (`claude/mi153-relay-20260906`) so the bot's results commit cannot bury the PR's checks.*

- **Branch:** `claude/mi153-exit-head-figures-20260906` · **Session:** `session_01JhtPDUP92XmMSuE8c4i3Up` (sub-session of manager `session_01HrmZ1RRNM4UnEUaFdrPEjj`)
- **Object:** `WO-20260906-TWO-RECORDS-DISAGREE-ON-EVERY-ICT-SCALP` · item `MI-153-EXIT-HEAD-FIGURE-RECONCILIATION`
- **Touched (5 files, all documentation):** `docs/research/exit-head-figure-reconciliation-2026-09-06.md` (new) · `docs/research/exit-lever-wiring-audit-2026-09-06.md` · `docs/research/exit-refinement-coverage.json` (three `exit_head_ml` **refs only**) · the work object · the two landing files.
- **NOT touched:** any cell `status` (Tier-3), `src/`, `config/`, `deploy/`, `.github/workflows/`, the order path. Nothing armed.

**Finding:** ⚠️ **the premise was false — the two records never disagreed.** All three cells carry BOTH figure sets in one append-only `ref`; `ict_scalp_sol_5m` literally reads `n_oos 800 -> 1150, auc 0.6149 -> 0.6184`. MI-146 quotes the second clause, the brief quoted the first. **Current for all 3 of 3 legs: the 2026-08-14 set** — the only set in `m20-exit-head-rounds.jsonl` (33 rows, single-commit history, so not attrition), reproduced exactly by two later independent 2026-08-15 rounds.

**Mechanism: a re-measurement on a LARGER corpus slice.** `fold_blocks` gives `u = floor(N/50) - 1` folds of exactly 50 OOS trades, so `n_oos = 50*u` (checked: `n_oos/usable_folds = 50.0` in 6/6 and 3/3). 08-13 was an **E1-only re-run** over existing E0 datasets; 08-14 was a **full round** that re-ran the harness and rebuilt the dataset.

**⚠️ Re-partitioning is REFUTED, not merely unselected:** across `off0/4/8/12`, `sol_5m` holds `n_oos=1150` and `u=23` **identically** while only `beats_hard` (16→14) and the verdict move. It cannot change `n_oos`, and the screen ran ON the larger book a day AFTER it — so the larger figures are **not** a pre-re-partitioning snapshot. TP geometry and block unit are refuted too.

**Not established (said, not guessed):** *why* the harness emitted a larger book. The round logs are on the trainer and uncommitted. An extra day of bars is ruled out.

**Arming recommendation: UNCHANGED for all three legs — arm nothing.** The binding constraints are the 2026-08-23 `SHIP BLOCKED` operator decision, `sol_5m`'s re-partitioning fragility on the *current* book, and #11140's own measurement that both published exit-head artifacts are `tf: 1h` against 5m/15m legs. **#11140 is unblocked to land as it stands** (annotate-only, disarmed).

Guards green (`matrix-corpus-agreement`, `matrix-config-agreement`, `matrix-bracket-values`, `stated-population`, `open-items`, `wip-ceiling`, `pr-landing=declared_self_land`), `m20_coverage_rollup.validate()` 0 problems, 46 assertions pass.
