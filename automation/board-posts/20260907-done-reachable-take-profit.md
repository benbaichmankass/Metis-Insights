✅ **DONE** — MI-156, the reachable take-profit Tier-3 proposal. **PR #11225, open READY, all 4 checks green** (`guards` · `pytest-run` · `pytest-collect` · `repo-inventory`).

**`config/strategies.yaml` was NOT edited** — `git diff origin/main -- config/ src/` returns 0 files. The repair ships as a fenced diff. Landing declared `tier: 3` / `landing: hold` / `hold_reason: tier_2_3_needs_approval`; **no `pr-automerge-requests` file**, so it cannot self-land.

**What I established**
- The two sub-populations resolve *differently*, and are kept apart throughout: **15 explicit sentinels → 13 explicit `none` + 2 `unexamined` + 0 finite targets**; **10 with no target key → 1 finite target + 9 explicit `none`**.
- **None of the 10 no-target legs has ever carried a `tp_r`** — across all **110** revisions of `config/strategies.yaml` (2026-04-29 → 2026-09-06, full history), with four positive controls on which the probe finds `tp_r` *and* catches value changes (`ada` 50.0→4, `xrp` 50.0→3.0). So absence there is **never declared**, not *intended and lost*.
- **Reachability is measured, not assumed.** Live `cap_r` per leg (`/api/diag/position_telemetry`, 171 rows, 2026-09-07) shows the low-vol equity/ETF legs at `cap_r` **8–16** — where a finite `tp_r` genuinely binds — against the crypto 4h legs at **2.1–4.0**, where almost any finite `tp_r` a sweep would produce is *still* clamped. The census's 0.02 reference materially understates the first group.

**The single sharpest finding**
> The e3.5 sweep pipeline **already shipped a take-profit to every leg for which it produced a gate-passing, live-expressible cell** — nine legs, each carrying its winning cell id inline in config — and left **exactly one** behind: `gld_pullback_1h` (cell `tp4`). The other 24 have no shippable cell at all, because every TP-bearing passing cell on them is entangled with a bar-count timeout no live unit implements. **Their unreachability is an absence of evidence, not an oversight** — which is what makes 24 of 25 a declaration problem rather than a number problem.

**What I could NOT establish**
- **That any proposed level is where momentum runs out.** MI-155 grades 44 of 44 legs `insufficient_n`, so nothing here comes from the MFE-quantile route. The one number (`gld_pullback_1h` `tp_r 4.0`) is **reachable and NOT calibrated**, and the memo says so on the row, in the config comment, and in the decision request.
- `splg_trend_long_1d`, `tqqq_trend_long_1d`, `squeeze_breakout_4h` returned **zero** telemetry rows — no `cap_r` measurement at all, recorded as *NOT MEASURED*.

**Flagged rather than routed around**
- The dispatched work object `WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE.yaml` **did not exist** (positive control: 72 other `WO-*.yaml` present). Created on this PR following MI-148's precedent in this same workstream, with its `done_condition` labelled **transcribed from the dispatch prose**, not recovered. Filed as a RECURRENCE: `BL-20260907-DISPATCH-NAMES-A-WORK-OBJECT-THAT-WAS-NEVER-CREATED`.
- The object carries **three answerable `decision_requests`** with matching `operator_decision` edges, so none is an `unanswerableOperatorEdge`: `DEC-20260907-TP-VALUE` · `-TP-VOCAB` · `-TP-UNEXAMINED`.
- The proposed `tp_intent` key **has no reader**, which by `provenance-consumer-guard`'s own standard is a defect — the memo asks for the Tier-1 reader in the same cycle, or for the key not to land.

**Scope released.** No VM action taken, no account mode flipped, no exit head armed.
