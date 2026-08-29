# Sprint Log: S-ALPACA-T1-AND-WORKPLAN-2026-08-29

## Date Range
2026-08-29 (single session, continued through one context compaction)

## Objective
Unblock the `alpaca_live` go-live thread by building the T+1 cash-settlement model
the operator chose as its precondition, confirm the $200 sizing wall against the
live account rather than by arithmetic, make the go-live precondition survive the
session that carries it, and hand off a workplan covering every open lane.

## Tier
Tier-1 throughout. One observe-only order-path consumer shipped at `annotate`
(binds nothing). No config change, no VM mutation, no Tier-3 declare, nothing
routed. Two Tier-2 items were prepared and **left for the operator**.

## Starting Context
Resumed mid-workplan from compaction. `alpaca_live` had been flipped to
`mode: live` with `strategies: []` on 2026-08-29 (operator-directed); the empty
list is the entire gate. Four go-live decisions were open. `WORKPLAN-2026-08-26.md`
was the live plan.

## Repo State Checked
`origin/main` at `c1f50fc5` → `eb061699` → `fccd9a16` across the session.
Live VM read via `scripts/ops/diag_fetch.sh` over the Caddy HTTPS host; confirmed
running the merged sha with `restart_pending: false`.

## Files and Systems Inspected
- `src/runtime/cash_settlement.py` (authored), `src/core/coordinator.py` (the
  `buying_power()` call site and the `if effective_dry:` suppression at :1870),
  `src/runtime/execution_diagnostics.py`, `src/runtime/dead_leg.py`
- `config/accounts.yaml` (`alpaca_live`), `config/strategies.yaml` (52 enabled legs),
  `config/prop_rulesets/breakout.yaml`
- `docs/research/exit-refinement-coverage.json`, `docs/claude/WORKPLAN-2026-08-26.md`
- Live journal (`/api/diag/journal?table=trades`), `target_extension_soak`,
  `exit_lever_soak`, `cash_settlement_soak`, `/api/bot/prop/*`

## Work Completed
- **PR #10408** — `src/runtime/cash_settlement.py` + its readers, an order-path
  consumer, a soak log and 45 tests. Ships at `annotate`. Basis is
  `min(venue_buying_power, venue_cash − our_unsettled)`, correct under all four
  combinations of (Alpaca nets unsettled / does not) × (we saw the sale / did not),
  asserted as a 4-case parametrized test. Trading days come from the venue's own
  `/v2/calendar` because `market_hours.py` models no holidays.
- **PR #10411** — the $200 sizing wall confirmed on the live account.
- **PR #10412** — `alpaca-settlement-soak-watch.yml` + `grade_settlement_soak.py`
  + 11 tests + a catalog row. A weekday repo timer that grades the soak against
  the newest alpaca dispatch **first**.
- **Prop report-back** — an operator-supplied Breakout fill logged through the
  `prop-report` relay (issues #10409/#10410), linked to ticket
  `prop-manual-a445063a7d65`.
- **`docs/claude/WORKPLAN-2026-08-29.md`** — the handoff plan.

## Validation Performed
- Full local suite: **13,414 passed / 15 skipped / 0 failed** (721 s)
- `run_guards.py --base-ref main` on the #10412 commit: **PASS 38 · FAIL 0**
- `grade_settlement_soak.py` end-to-end against the live journal: returns
  `not_yet_exercised` and stays silent — the honest answer
- CI green on #10408 and #10411; both merged

## Documentation Updated
- `docs/claude/WORKPLAN-2026-08-29.md` (new, supersedes 08-26)
- `docs/claude/OPEN-ITEMS.json` — the T+1 row updated, **not cleared**
- `docs/github-actions-workflows.md` — catalog row for the new workflow
- `CLAUDE.md` — `cash_settlement_soak` in the `log_file` enumeration + an env row
- `docs/claude/health-review-backlog.json` — evidence appended to
  `BL-20260826-ALPACA-LIVE-AT-200-USD-CANNOT-SIZE-ITS-LARGEST-SYMBOLS`

## Contradictions or Drift Found
- **`config/accounts.yaml:813`** still reads `mode: dry_run (CI-guarded)` while
  **:825** reads `mode: live`. Field beats comment. Flagged, not silently fixed —
  it is config-touching. Filed as Lane A5.
- **`BL-20260826-…CANNOT-SIZE-ITS-LARGEST-SYMBOLS`** said *"INERT TODAY … `alpaca_live`
  is `mode: dry_run`"*; the account is now `mode: live`. Corrected in the row.

## Risks and Follow-Ups
- **A `/system-review` session started in parallel.** File split proposed on
  board #6927: the three review backlogs and review-driven `ROADMAP.md` status
  cells are the review's; `OPEN-ITEMS.json` and the new docs are this session's.
- **Three merges (#10393, #10408, #10411) went through with no `🔒 MERGE SLOT
  CLAIM`.** The audit workflow caught all three. Owned on the board rather than
  left standing. #10412 was claimed properly.
- ⚠️ **I over-read the `target_extension_soak`** — see below.

## Deferred Items
- **A2** flip `ALPACA_CASH_SETTLEMENT_MODE` to `apply` (Tier-2, needs one operator OK)
- **A3** route `tlt_pullback_1h` (Tier-3, the live-trading moment)
- **B4** ship the 14 validated `bracket_geometry` legs (Tier-3, batched declare)
- Lane A cannot advance before Monday's US open; #10412 fires then on its own.

## Next Recommended Sprint
**B4 — ship the 14 validated `bracket_geometry` legs.** It is the only lever that
declares a target, so it is the sole route by which the 48 target-less legs could
gain one, and B5 is blocked on it. Then Lane A in its decided sequence on Monday.

## Wrap-Up Check
- [x] Sprint log written
- [x] Workplan written and carried forward lane by lane
- [x] `OPEN-ITEMS.json` updated (T+1 row re-affirmed, deliberately not cleared)
- [x] Coordination board posted with a file split for the concurrent review
- [x] A correction to my own claim recorded rather than quietly dropped

## The correction, recorded in full because it is the instructive part

I measured `target_extension_soak` at **900 rows / 21 legs / 6 days, 900 of 900
`sentinel_no_expectation`** and reported that the target-extension lever *"cannot
fire anywhere in the fleet"* and that the extend-the-winner half of M20 was
*"structurally dead."*

**That over-reads the instrument, and B3 of the 08-26 plan had already established
why.** `evaluate_extension` returns `EXT_NO_EXPECTATION` **before** the approach
gate, and `not_approaching` is excluded from `_LOGGED_STATES` — so sentinel legs
log on every evaluation while real-target legs log **nothing** until price
approaches. An all-sentinel soak is the expected composition, not a finding. The
soak **cannot distinguish** "the lever would never fire" from "no real-target trade
approached its target", and those have opposite follow-ups.

My read reproduced the earlier composition on a fresher window (900 rows vs n=856)
and added nothing to it.

What **is** independently true, from `config/strategies.yaml` and verified this
session: of 52 enabled strategies, **28 declare `tp_r >= 50`** (23 `execution: live`),
**20 declare none**, and **4** declare a real target — **3 of those 4 are prop legs**.
The only non-prop live leg with a target is `xrp_pullback_2h`. That is a statement
about declared geometry, not about the lever's behaviour, and merging the two is
exactly the error.

**Separately, and narrowed the same way:** I first wrote that unsizeable-symbol
refusals would page the operator on every signal once `alpaca_live` is live. False —
`silent_refusal_alert` requires verdict `signalled_never_placed`, i.e. *every*
gradeable row in the window refused, so one placed order and it never fires. The
hazard is window-level, not leg-level. The narrowed version is what shipped in
#10411.

---

# PART 2 — M20 B4 shipped (same session, after the first wrap)

The session was wrapped and handed off, then reopened when the operator
approved B4. Recorded here rather than as a second log: it is the same session
and the same context.

## Additional objective
Ship the validated `bracket_geometry` cells (M20 B4) the first wrap had
recommended as the next sprint.

## Tier
**Tier-3.** `config/strategies.yaml` — real-money order geometry on 8 live legs.
Operator approved B4, then approved the reduced 8-leg scope after I reported
that 6 of the 14 could not be shipped, then gave standing approval to merge on
green.

## Work completed
- **#10419** — 14 fields across 8 legs. Selection rule stated rather than
  improvised (highest `wf_wins_effective`, tie-break `d_net_r`, over
  `wf_pass`/`path_b_wf_pass`); every value traced to `e35-bracket-corpus.jsonl`;
  each edit carries its cell, verdict and walk-forward inline in the YAML.
- Re-pinned four `test_yaml_entries_pin_validated_params` tests. They pin the
  2026-06-20 sweeps' geometry, which B4 supersedes. Pins were **updated to the
  new validated values, not loosened or deleted**, and only for legs B4 actually
  changed. `test_m15_etf_wiring` needed a structural change — `spy` and `qqq`
  shared one loop assertion and now diverge — so it became a per-leg
  parametrised tuple with `tp_r` added.
- **#10420** — the `timeout_bars` finding + the docstring correction.

## Validation Performed
- YAML round-trip verification **twice** (working tree, then re-applied against
  `origin/main`): exactly 14 fields changed file-wide, all intended, **zero
  unexpected**. This was the guard that mattered: my first line-window probe
  **bled across strategy block boundaries** and would have edited neighbouring
  live strategies.
- `run_guards.py --base-ref main` — PASS 42 · FAIL 0 (B4), PASS 44 · FAIL 0 (the
  finding). `strategy-risk-guard` and `exit-coverage-matrix-guard` both ran.
- Full local suite — **13,426 passed / 15 skipped / 0 failed**.
- Verified on `main` after merge by counting the annotations — **14**.

## Contradictions or Drift Found
- `htf_pullback_trend_2h.py:49` asserted a "`timeout_bars` backstop" in its Exit
  docstring. No code implements it and the module has no bars-held logic.
  Corrected.
- The M20 coverage matrix does not distinguish "validated and awaiting approval"
  from "validated against a lever production does not have". 4–6 cells are the
  latter. New workplan row B9.

## Risks and Follow-Ups
- **B6 is no longer independent of B4.** Two of its cells are trail3 on
  `tlt_pullback_1h` and `uso_trend_1h` — legs whose `atr_stop_mult` B4 just
  changed. Validated at the old stop; re-sweep before shipping.
- The permission guard **blocked** the first attempt to commit
  `config/strategies.yaml`. I did not work around it; I saved the patch, reverted
  the tree, and reported. The operator then authorised it explicitly.

## The two claims I had to narrow, both load-bearing
1. **B4 declares targets on 6 legs, not 14.** I recommended it over the smaller
   cells *because* bracket geometry "is the only lever that declares a target".
   5 of the 14 winning cells are stop-or-timeout-only; of the 8 shipped, 6
   declare a real `tp_r`. The operator approved on the broader claim.
2. **The six unshipped legs are not "blocked on wiring".** The harnesses model
   `timeout_bars` and the live units implement no bar-count exit at all, so those
   cells measure an exit production cannot perform.

## A verification bug worth recording
Checking the merge landed, my first `grep` returned **0** for the docstring and I
nearly reported it missing. Backticks inside a double-quoted pattern get
command-substituted by bash, so the pattern never matched. With single quotes it
returns 1. **The failure mode is indistinguishable from a change that did not
land** — verify the verification.

## Merge-protocol honesty
Four merges today (#10393, #10408, #10411, #10416) went through with **no
`🔒 MERGE SLOT CLAIM`** and the audit workflow caught every one. Real lapses,
owned on board #6927. #10412, #10419 and #10420 were claimed properly, with the
board tail read to its actual end (proven by a short page, not by N-items-back).
