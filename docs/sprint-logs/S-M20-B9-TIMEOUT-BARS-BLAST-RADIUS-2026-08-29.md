# Sprint Log: S-M20-B9-TIMEOUT-BARS-BLAST-RADIUS-2026-08-29

## Date Range
- Start: 2026-08-29T17:26Z
- End: 2026-08-29T18:25Z

## Objective
Answer **Lane B / B9** of [`../claude/WORKPLAN-2026-08-29.md`](../claude/WORKPLAN-2026-08-29.md):
the trend/pullback backtest harnesses force-close every trade at
`entry_i + timeout_bars` (default 200) and no live code path does. The row asked two
things — **how big is the blast radius** (explicitly "needs a measurement, not an
assumption") and **should the order path carry a time stop or should the harness stop
modelling one**. Until answered, the coverage matrix's `passed_unshipped` column
conflates "awaiting approval" with "impossible to ship", which is a
measurement-integrity problem ranked above the remaining lever work.

## Tier
**Tier 1.** No `src/`, no `config/`, no order path, no VM action, no live behaviour
change. The Tier-3 half — whether live should gain a bar-count exit — is written up as
a scoped proposal and left **open for the operator**, not taken.

## Starting Context
B4 (#10419, `91de68b9`) had shipped validated bracket geometry to **8 live legs, real
money**, that morning. The B9 row estimated *"4–6 `passed_unshipped` cells are
UNSHIPPABLE BY CONSTRUCTION"* and named six legs, flagging the blast radius as
unestablished. Lane A was calendar-blocked until Monday's US open.

## Repo State Checked
- `origin/main` at `96d3f8a`; branch `claude/alpaca-trade-management-rg4jn3`.
- Board #6927 tail read to its **actual end** — `perPage=15` at page 108 returned
  **13 items, a short page**, which is the proof. 1,618 comments, no open merge claim.
- `/system-review`'s PR #10414 open as a draft; it rewrote all three review backlogs,
  so **this sprint filed nothing to them** and handed its one row over on the board.

## Files and Systems Inspected
- `scripts/backtest_trend.py`, `scripts/backtest_pullback.py`, `scripts/backtest_squeeze.py` (exit loops + arg parsers)
- `src/units/strategies/{trend_donchian,htf_pullback_trend_2h,squeeze_breakout_4h,fvg_range_15m,fade_breakout_4h}.py`
- `config/strategies.yaml` — the 4 `timeout_bars` declarations and the 14 B4 annotations
- `docs/research/e35-bracket-corpus.jsonl` (8,211 rows) + `e35-bracket-gate-corpus.jsonl` (133 gate rows)
- `docs/research/exit-refinement-coverage.json`
- `scripts/check_harness_lever_coupling.py`, `scripts/research/regime_debt_matrix.py`, `scripts/ci/check_matrix_config_agreement.py`, `scripts/research/m20_fleet_exit_sweep.py`

## Work Completed
1. **Confirmed the code half by reading the field, not the prose.** `--timeout-bars`
   defaults: trend **200** (`:982`), pullback **200** (`:961`), squeeze **48** (`:545`).
   `grep -rn timeout_bars src/` → read only by `fvg_range_15m.py` and
   `fade_breakout_4h.py`, each from its own `_DEFAULTS`; **no generic reader**, so
   live's effective timeout is **infinite**.
2. **Measured the blast radius** using the sweep grid's own control — base arm vs
   `to400` at identical geometry. New reproducible tool
   `scripts/research/timeout_binding_audit.py` (with `--self-test`).
3. **Classified all 51 winning cells** (`wf_pass`/`path_b_wf_pass`, 08-26 corpus +
   08-20 gate corpus) by timeout dependence and by their leg's contamination status.
4. **Reconciled the coverage matrix** — 8 stale `bracket_geometry` cells → `shipped`;
   2 → `blocked:no_live_bar_count_exit`; `timeout_binding` notes on the affected
   cells; the defect added to `known_caveats.conditions_verdicts`.
5. **Corrected a false premise** in `check_harness_lever_coupling.py`, and recorded
   why the obvious fix is the wrong direction.

## Validation Performed
- `timeout_binding_audit.py --self-test` — 4 cases, incl. that all-identical grades
  `no_power`, **never** `clean`.
- The script's fleet output **reproduces the ad-hoc analysis exactly** (41 legs,
  1,588 pairs, 439 binding, 23 clean / 18 contaminated) — an independent re-derivation.
- **Arithmetic cross-check on the money-bearing half:** all 8 B4 cells re-derived from
  the corpus by the B4 selection rule matched the inline `config/strategies.yaml`
  annotation **on all 8** (`tp2_sm1.5`, `tp3_sm2` ×2, `tp1.5_sm3`, `tp6_sm1.5`,
  `tp4_sm2`, `sm2`, `sm1.5`).
- Matrix edit checked for loss, not eyeballed: **0 keys lost**, 52 rows → 52, every
  new `ref` contains its old text verbatim, status counts moved exactly
  `shipped 22→30`, `passed_unshipped 22→12`, `blocked* +2`.
- `scripts/ci/run_guards.py` — all selected guards pass. Two failed on the first run
  and both were fixed, not waived: `artifact-validity-guard` (the new script was
  unindexed → row added to `RESEARCH-CAPABILITY-INDEX.md`) and `operator-owed-guard`
  (pytest absent from this container → installed; 54 passed).
- `check_harness_lever_coupling.py`, `check_matrix_config_agreement.py`,
  `check_matrix_corpus_agreement.py`, `check_artifact_caveats.py` all green after.

## Documentation Updated
- **NEW** `docs/research/timeout-bars-harness-vs-live-2026-08-29.md` — the measurement.
- **NEW** `scripts/research/timeout_binding_audit.py` + its `RESEARCH-CAPABILITY-INDEX.md` row.
- `docs/research/exit-refinement-coverage.json` — statuses, notes, caveat, `updated_at`.
- `docs/claude/WORKPLAN-2026-08-29.md` — B9 answered; **B10** and **B11** opened; the
  roll-up table re-counted 22/20 → **12/20** with both movements explained; recommended
  order re-ordered.
- `scripts/check_harness_lever_coupling.py` — corrected premise + a do-not-"fix"-this warning.

## Contradictions or Drift Found
1. **The sweep's stated premise is refuted by the corpus it produced.**
   `e35_bracket_geometry_sweep.py:117-120` says the default is *"far outside the
   binding region"*, citing *"5 of 284 on the E0 leg"* — one leg generalised to the
   fleet. Measured: it binds on **27.6% of pairs and 18 of 41 legs**. The census was
   taken over `exit_reason`, which is the exit loop's **default** label and therefore
   conflates a bar-count exit with running off the end of the data — a collapsed state.
2. **The coverage matrix was stale by one Tier-3 shipment** and no guard caught it
   (→ B10).
3. **`check_harness_lever_coupling.py` states a false premise** about which harnesses
   model `timeout_bars` (→ B11).
4. **Two config keys have no reader.** `mgc_pullback_1d` and `mhg_pullback_1d` declare
   `timeout_bars: 200`; the pullback unit reads nothing. Decorative, and a reader would
   reasonably infer a production backstop that does not exist.
5. **`squeeze_breakout_4h` is a third affected family** the B9 row's trend+pullback
   scope never covered. It is `execution: live`, its harness default of 48 **binds**,
   and its only validated winner (`to24`) is undeliverable.

## Risks and Follow-Ups
- **The Tier-3 question is left OPEN, deliberately.** Recommendation: the harness
  should stop modelling the exit; live should not gain one *on this evidence*. But 4
  legs are CLEAN **and** blocked (`mes_trend_long_1d`, `tlt_pullback_1d`,
  `gld_pullback_1h`, `eth_pullback_2h`) — their base arm *was* live-parity and a
  shorter hold still beat it (`gld_pullback_1h` `tp6_sm1.5_to24` walks forward 6/6).
  That is a real case for a time stop and belongs in its own scoped proposal.
- ⚠️ **Do not raise the harness default and re-grade in one step.** Re-running the 18
  contaminated legs re-measures live configuration, several on `shipped` cells.
- **B10** — build the value-agreement detector for `bracket_geometry`.

## Deferred Items
- **The measurement covers only the e35 corpus.** Trend/pullback verdicts from the
  `m20_fleet` sweeps, `trail_geometry`, `stale_stop` and the rest ran under the same
  default and are **not measured here**. The workplan's "how many non-e35 verdicts"
  question is still open for those; what is settled is that the answer is not "none".
- `ict_scalp_sol_15m` and the shadow-fleet batch are **absent from the e35 corpus**
  and so ungraded on this axis — relevant to B6.
- Nothing filed to the three review backlogs: `/system-review` (#10414) is the single
  writer this pass; the one row found was handed over on board #6927.

## Next Recommended Sprint
**P1/P2** (the promotion gate), then **B6 split** — with B9's new precondition that a
candidate leg's timeout status is checked before shipping its cell.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched (`docs/TRADE-PIPELINE.md` N/A — read-only research + docs).
- [x] Roadmap status was checked — M20's row is unchanged by this sprint: no lever
      shipped or withdrawn, and the two `blocked` cells were never shippable, so the
      milestone's done-condition denominator does not move.
- [x] Contradictions were recorded (5, above).
- [x] Remaining unknowns were stated clearly (Deferred Items) — chiefly that the
      non-e35 verdict population is unmeasured, and that `to400` is a proxy for
      infinity rather than infinity itself.

## Honest limitation of this sprint's own work
My **first** pass at the headline coerced `inert_equals_base`'s `net_total_r: null`
to `0.0`, comparing a real number against a manufactured one. It reported
**450/1,599** and invented one spurious finding on each of the 8 legs carrying such a
row. The 11 nulls are now excluded **and counted**, the corrected figure is
**439/1,588**, and the script's self-test pins the null-handling so the same mistake
cannot return silently. Recorded because the failure is the one this repo's rules name
as hardest to catch: my own tooling deceiving me in the direction I expected.
