✅ **DONE** — `MI-147-BACKTEST-SOAK-CHAIN-AUDIT` · **PR #11188 MERGED** (`dc2436df` → main, 22:14:21Z, all 5 checks green)

Session `session_01T5o3AkucfgozxdVhANWAdw`. Finding: `docs/research/mi147-backtest-soak-chain-2026-09-06.md`.

## THE POPULATION
**M = 44 legs** — every `config/strategies.yaml::strategies` entry with `enabled: true` AND `execution: live`, permissive defaults applied, parsed at `f2b871e`. Cross-check: 55 entries → 52 enabled, 45 live, **44** both. Each has exactly one symbol, so leg == strategy 1:1. **Independently reproduces MI-146's denominator of 44** from config, not inherited.

## THE THREE COUNTS
| | over M = 44 |
|---|---|
| **(a) pre-live backtest EXISTS** | **9 PRE-LIVE · 6 SAME-COMMIT · 29 POST-LIVE.** Family-level evidence covers 18 of the 29; **11 of 44 (25.0%) have none at any level.** Only 4 of the 9 lead by more than a day — `eth_pullback_2h` by **48 minutes**. |
| **(b) READ and DISPOSITIONED pre-live** | **0 of 44.** Whole disposition corpus = **2026-08-10 → 08-31** (n=370); every leg went live **2026-05-15 → 08-13**. Lag min 0 d, **median 56 d**, max 88 d. Plus 365/370 (98.6%) `not_queue_dispatched`, 182/232 units for these legs (78.4%) below the n ≥ 49.06 power floor. |
| **(c) exit locations agree** | **Unmeasured, and unmeasurable by the named instrument.** |

## THE "WHY" — one failure in sequence, not four candidates
1. **The gate was not in front of the decision** — 35/44 evidence dated at-or-after go-live. A gate producing evidence after the decision is a write-up.
2. **Nothing converted a result into a decision at the time** — 0/44. The disposition machinery works; it was built **2026-08-10, after every leg in M had already gone live.**
3. **So soak inherited the whole job** — it was the *first* measurement, with nothing to confirm. That is why it surprises.
4. **And the retrospective backtests still can't reconcile with live** — `TP_VENUE_CAP_PCT = 0.099` is referenced by only 4 of 15 harnesses, and in all 4 `tp_cap_pct` defaults to `0.0` (⇒ `tp_price = None`, **no take-profit at all**). The other 11 — incl. `backtest_ict_scalp.py` (8 of the 44 legs) — never reference it. Composes with MI-146's 25/44 unreachable TP.

**Plainly:** the backtesting infrastructure is extensive, well-built, and largely was not in the decision path for the legs currently trading.

## MI-151 CLAIMS — both verified independently, both HOLD
⚠️ **Path correction:** the calibrator is **`scripts/research/backtest_fidelity_calibrate.py`**, not `scripts/ops/`.
- **No durable run** in 4,277 commits; `comms/research/` (its own `--out` dir) has one unrelated file ever; no workflow references it. *Positive control passes.* **Limit:** proves no run *landed*; a trainer-side run is not excluded.
- **Wrong quantity:** SQL is `SELECT pnl, notes, direction, timestamp FROM trades` — no exit price/level/timestamp. Grades win-rate + KS(realized-R) + mean-R gap = *outcome distribution*. **Not run for this audit**, and running it would not have answered (c).

## COULD NOT ESTABLISH
1. Trainer-side backtests never committed — (a) and the never-run finding are **repo-artifact** probes. For the 11 legs this is **"we could not find evidence"**, a third state, not "no backtest exists".
2. Exit-location agreement — no instrument in the repo grades it.
3. The 6 SAME-COMMIT legs — intra-commit ordering unresolvable.
4. Pre-live decisions made in conversation — no repo artifact.

⚠️ **A wrong first answer, recorded:** an earlier pass returned **36/44 PRE-LIVE**, contaminated by testing *current* content against *first-add* dates — `comms/claude_strategy_scores.jsonl` (graded **live** packages, not a backtest) matched 30 legs. Corrected to introduction-date via pickaxe.

## SCOPE HONOURED
Read-only. **No** strategy config, execution gate, account roster, or `exit-refinement-coverage.json` cell `status` touched. **No backtest was run to fill a gap.** Every remedy PROPOSED only. 4 backlog rows filed via `backlog_append.py::append_row` (3 research, 1 health), each with `severity`/`tier`/`resolution_criteria`.

## TWO RELAY DEFECTS HIT AND FILED
- `add_issue_comment`, `create_pull_request` and `update_pull_request` **all 403** from this session — board posts via `board-post.yml`, PR via relay.
- **`claude-pr-automerge` races `pr-opener`** on a branch's first push: it won, opened #11188 with its **generic body**, and `pr-opener` returned `FAILED-already-exists`, discarding the authored 6,156-char body — unrecoverable in-session because the two edit paths 403. **⚠️ So #11188's body does NOT describe its own change; the finding doc does.** Filed as `BL-20260906-AUTOMERGE-RELAY-RACES-PR-OPENER-AND-DISCARDS-THE-AUTHORED-BODY`.

Also hit the documented *relay-results-commit buries CI* trap (`total_count: 0` + `mergeable_state: blocked`), then a genuine `dirty` conflict after main moved 4 commits — rebased, re-filed the dropped row, green.
