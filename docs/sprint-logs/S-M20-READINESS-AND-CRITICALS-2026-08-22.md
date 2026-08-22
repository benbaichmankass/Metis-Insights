# S-M20-READINESS-AND-CRITICALS-2026-08-22

## Date Range
2026-08-22 (second unit of the `s4-pairs-control` session, continuing directly from
`S-PAIRS-CONTROL-EXIT-RESIDUE-2026-08-22`; main at `4987e726` at the start of this unit).

## Objective
The operator's direction for this unit was explicit: *"our goal is prepare everything so
that we can continue with M20 where we left off, but we need to make sure all the infra is
working correctly, it looks like we still have quite a backlog, which if I'm not mistaken
is basically what the current work plan is."*

Three things follow from that sentence, and they were worked as three:
1. **Measure what M20 actually needs** — a done-condition read, not a status re-quote.
2. **Verify the infra**, rather than assert it.
3. **Test the assertion "the backlog is basically the workplan"** — it is a factual claim
   and it is checkable.

Then, on the operator's three answers to the questions that measurement raised: ship item
**1.8** (both halves, Tier-2 approved) and work the **8 critical rows the workplan does not
cite**.

## Tier
Tier-1 throughout, with **one Tier-2 change shipped under explicit operator approval**:
item 1.8's exit-label re-derivation (a money-DB writeback). No Tier-3 gate touched, no
`execution:` change, no account mode, no model promoted, no live-VM mutation. The
historical relabel tool is **staged and not run** — `--apply` is required to write and it
has never been pointed at the live journal.

## Starting Context
`docs/claude/WORKPLAN-2026-08-21.md` is the queue. Phase 0, items 1.0–1.7 and T.2–T.4 are
closed by the four prior sessions of the day. Sole session; merge slot free. The
operator-approved Sunday 2026-08-23 22:30Z routine
(`trig_014S3NAzMKy2Ac2AM2GgyRE5`, MES target attach + MGC flatten) is standing and must not
be duplicated or pre-empted.

## Repo State Checked
`main` at `4987e726` at the start of the unit, `62908780` at the end (6 PRs: #10149–#10154).
The live web-api served `4987e726` for the infra reads in § 5 of the readiness doc, so those
readings are against deployed code.

## Files and Systems Inspected
- `docs/research/exit-refinement-coverage.json` — the M20 done-condition matrix (52 legs × 9 lever columns)
- `docs/design/m31-p5-telemetry-reading-lever-PROPOSAL.md` § 5 — the P5 preconditions
- `scripts/ops/fetch_backtest_candles.py`, `scripts/research/m27/fetch_yfinance_5m.py`, `requirements-backtest.txt`
- `../ict-trader-dashboard/streamlit_app.py::_yf_ticker` — the non-crypto symbol map, in the sibling repo
- `src/runtime/order_monitor.py` — `_sweep_pending_pnl_from_bybit`, `_close_trade_from_order_status`, `_check_broker_naked_ib_positions`, `_check_broker_naked_bybit_positions`
- `scripts/ops/attach_ib_target.py`; `src/units/accounts/clients.py::account_open_positions`
- `docs/claude/health-review-backlog.json` (794 rows at start), `performance-review-backlog.json` (105)
- Live: `/api/diag/version`, `/api/diag/services`, `/api/diag/log_file?name=exit_loop_health`, `/api/diag/ib_open_orders`, `/api/bot/notifications`, `/api/bot/ml/status`, trainer-diag relay

## Work Completed

**1. M20 readiness measured — 85.3% resolved, and the blocker is infrastructure, not
research.** `docs/research/m20-readiness-2026-08-22.md`. Over the full 468-cell matrix:
399 resolved (68.2% `honest_negative`, 7.7% `n/a`, 4.7% `shipped`, 3.2% `passed_unshipped`,
1.5% `shipped_gate_failed`), **69 remaining**. The remainder decomposes into three kinds
that must not be scheduled together — **25 INFRA** (buildable now), **20 WORK** (ordinary
pipeline runs), **15 WAITING** (soak depth, nothing accelerates it), 9 other.

**The single biggest blocker is one missing artifact: a non-crypto candle feed.**
`bracket_geometry :: blocked:no_free_lane_candle_feed` is **25 cells = 36% of everything
remaining and 51% of everything blocked**. ⚠️ **This is not "nobody wrote the fetcher
yet"** — the free lane is `data.binance.vision`, a **crypto** archive, and
`fetch_backtest_candles.py` offers exactly two sources, both crypto. All 25 blocked legs are
non-crypto with **zero** crypto among them (IBKR futures ×5, US equities/ETFs ×18, XAUUSD).
It is a source-coverage fact.

**The same feed unblocks M31 P5's precondition 3b**, whose walk-forward needs per-leg
candles at the live timeframes — so one artifact closes two threads that the roadmap
currently lists apart.

**2. The infra verified rather than asserted** (§ 5, measured 18:1xZ). Trader `active` on
current `main` with a 24 s heartbeat; `ict-web-api`/`caddy`/`ict-telegram-bot`/
`ict-claude-bridge` all `active`; exit loop `fresh` / `requirement_state: within` at
`max_interval_ms` 33.96 s; trainer mirror age 66 s; trainer disk 89.1% used.
⚠️ **My own first pass was wrong and is recorded as wrong**: I flagged 18 `ict-*.service`
units as faults. They are **oneshot units whose `.timer` is active** — `inactive` is the
correct steady state. Likewise `ict-ib-gateway-watchdog` is `inactive` **by design**
(`install_systemd_units.sh:381` restricts it to the gateway VM by role marker).
⚠️ **And the one green reading that must not be over-read:** `requirement_state: within` at
`intervals_measured: 36` means *no process has lived long enough to draw the tail* — the
58.9 s observation that motivated the requirement came from an **n=694** process.

**3. A compound alert condition nobody owns.** Both live alerts are `ib_target_naked` on
`ib_paper` (MGC 95.0, MES 15.0) and are the exact conditions the Sunday routine will fix —
expected. But **MGC is simultaneously `ib_target_naked` AND `monitor_blindness`**
(`candles_unavailable`, 3 ticks), and **each alert's stated backstop is the other's missing
half**: target-naked says the stop still holds, monitor-blind says *"Broker SL/TP backstop
(if any) still holds"*. On this position the take-profit does not exist and the monitor is
not running. Filed `BL-20260822-MGC-TARGET-NAKED-AND-MONITOR-BLIND-TOGETHER`. `ib_paper` is
paper money — not money-at-risk, but it **is** an M20 evidence source.

**4. "Is the backlog the workplan?" — measured, and NO.** The workplan cites **28** backlog
ids, 21 open, against **401** open + kept_open rows. **380 (94.8%) of the open set is not in
the workplan**, including **8 critical** and **85 high**. That is by design — the 2026-08-21
rewrite was a deliberate triage — but it means *"clear the workplan"* and *"the infra is
correct"* are different statements. This measurement is what produced the operator's next-unit
decision.

**5. Item 1.8 SHIPPED, both halves** (#10151, Tier-2, operator-approved). Forward fix:
`_sweep_pending_pnl_from_bybit` now re-derives the exit label at the moment the price
arrives. **Three guards, each load-bearing** — `is_reduce_leg` derived exactly as at the
other call site (the SELECT reads `setup_type`, which it did not before; omitting it would
mislabel reduces as `sl`/`tp`, the precise failure the exclusion exists to prevent); only a
row still carrying the **generic** reason is relabelled (the sweep selects on `pnl IS NULL`
and can catch a row another path closed with a real reason); and the classification is
wrapped so a label failure **can never lose the price write**. The summary counter starts at
`"reclassified": 0` with the note that *"we looked and none qualified"* must not read as
*"we did not look"*.

Historical half: `scripts/ops/reclassify_frozen_exit_reasons.py` — **annotate-first**,
`--apply` required to write, `--provenance` scoping **the WRITE and never the measurement**,
importing `_classify_broker_exit` rather than re-deriving it, recording `exit_reason_prior`
so it is reversible. ⚠️ **Staged, not run.** The operator's own steer sets the order: the 91
broker-truth rows are a much stronger case than the 90 resting on estimated prices, so
`--provenance broker_truth` first, read the annotate JSONL, widen only if warranted.

**6. `attach-ib-target`'s verification made three-state** (#10152), taken **first** among the
criticals because the Sunday routine runs that action with `--apply`. `still_absent` could
not tell a **filled** target from one that was **never placed** — the two demand opposite
follow-ups. Now `target_resting`(0) / `target_filled`(0) / `absent_unexplained`(1) /
`could_not_look`(3), with the position read (`account_open_positions`: `None` = could-not-read,
`[]` = flat) as an **independent** signal rather than an inference from the order list.

**7. The over-cover detector ported to IB** (#10154), closing the code half of
`BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS`. Detect-only, graded by OCA group:
**ERROR** when the excess spans disjoint groups (the naked-short sequence — `ocaType=1`
cancels *within* a group, so disjoint groups do not cancel each other) and **WARNING** within
one (self-limiting). It reads `stop_qty`, not `covered_qty` — the one-sided-coverage lesson
from `BL-20260816-COVERAGE-IS-ONE-SIDED`. `_IB_OVERCOVER_FACTOR` is a **literal** equal to its
Bybit sibling, deliberately not an env var: an env var here would ship with no read surface
(`BL-20260813-ENV-VARS-SHIP-WITHOUT-A-READ-SURFACE`).

**8. The two IB protection rows re-graded against a live read** (#10153), not against their
own text. The over-cover **instance is cleared** — MES now holds ONE stop (338, qty 15.0 =
position 15.0); order 375 is gone, so the 30-against-15 the row describes is not the current
state. Severity **critical → high**, narrowly: no live over-cover, so no present naked-short
exposure; the *detection* gap was unchanged at grading time (and is now closed by #10154).
Meanwhile `BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG` is
**CONFIRMED LIVE with its figure re-derived independently**: the surviving MES stop is the
**stray** — declared `7533.69642857` vs resting `7516.5` = 17.196429 pts × 15 × multiplier 5
= **$1,289.73**, reproducing #10081 from the venue read and the journal row rather than
inheriting the number. **The over-coverage is gone; the quantity is right and the level is
wrong.** Fix is Tier-2 (a protective cancel + re-attach on a live position) and was left
untouched.

## Validation Performed
- **Item 1.8 forward fix:** 11 new unit tests. **10 of 11 fail pre-fix / 11 pass post-fix.**
  ⚠️ The one that passes pre-fix is the *failure-guard* test, which passes trivially because
  before the fix there is no classifier to fail — stated rather than counted as evidence.
  5 further tests cover the historical tool end-to-end against a real sqlite journal.
  99 related tests pass. `provenance-consumer-guard`, `collapsed-state-guard`,
  `canonical-db-resolver` all clean.
- **Falsification method:** every pre-fix run checks out `origin/main`'s copy of the file and
  **asserts the fix is genuinely absent** before running. This is the standing method after an
  earlier `git stash push` on an already-committed file produced a no-op that exited 0 — so the
  "pre-fix" run had tested the fixed code.
- **`attach-ib-target`:** 8 tests over the four verify states.
- **Over-cover detector:** 9 tests, including a **detect-only** test that strips string
  literals and comments via `tokenize` before asserting no re-arm call is reachable (a raw-text
  match tripped on the detector's own log message, which names `place_protective`), plus a guard
  test proving the stripper strips.
- **Live IB read** (`/api/diag/ib_open_orders`): the over-cover instance cleared, the $1,289.73
  divergence reproduced, and the Sunday run predicted to land `target_resting`, **not**
  `target_filled` — see the correction below.
- ⚠️ **`run_guards.py` SKIPPED `ruff-lint`** (diff-scoped, changes uncommitted). `ruff check .`
  was run directly rather than quoting a pass the guard runner did not make. Ruff stays pinned
  `>=0.15.0,<0.16` — unpinned, 0.16's widened defaults report 12,089 repo-wide errors and bury
  the real ones.

## Documentation Updated
- **New:** `docs/research/m20-readiness-2026-08-22.md` (the assessment + the ordered path).
- `docs/claude/WORKPLAN-2026-08-21.md` — item 1.8 row; the M20-readiness section; the 18:3xZ
  operator-decisions block; the criticals working log; **and a correction to item 2.1**, whose
  blocker text was stale **in the blocking direction** (see below).
- `docs/claude/health-review-backlog.json` 794 → 796 (two new rows; six updated, incl. the
  over-cover re-grade critical → high).
- `docs/claude/performance-review-backlog.json` 105 → 106
  (`PB-20260822-AVAX-SCALP-SIZED-OFF-MARGIN-NOT-RISK`).
- `ROADMAP.md` — this ledger row + `Last Updated`; an `m20-readiness-2026-08-22` pointer on the
  M20 milestone row.

## Contradictions or Drift Found
- **Workplan item 2.1 was stale in the dangerous direction** — it recorded a trainer blocker
  that no longer holds. Left unfixed it would have had the next session plan M20 around an
  unavailable trainer. Corrected.
- **`src/runtime/provenance.py`'s docstring step 1 was stale** (the demo branch was narrowed to
  resolve from the fills store on 2026-07-30) — fixed in #10149, which is also where an
  **overclaim of mine was withdrawn**: I had called the frozen-label chain *"a mechanism nobody
  had named"* when that docstring already described its shape. Withdrawn across five surfaces.
- **My own service-state reading was wrong** (18 oneshot units flagged as faults) and is
  recorded as wrong in the readiness doc rather than quietly corrected.

## Risks and Follow-Ups
- ⚠️ **CORRECTION to PR #10152's own body, recorded so it is not re-quoted.** I wrote that the
  Sunday run *"supplies this either way — a fill proves the path this row exists for."* **At the
  current price it will not.** MES declared TP **8390.59025** against a last close of **7687.5**
  (CME shut, so that is Friday's close and cannot move before the reopen) — the TP is **703.09
  points / 9.1% above market**, so the attach will be a genuine **resting limit**. The Sunday run
  exercises `target_resting`; `target_filled` stays **unproven in production**.
- **Three live positive controls are OWED**: item 1.8's forward fix (needs a real late-price
  close), the IB over-cover detector (unit-verified only — it has never fired on a real
  over-cover, because there is none live), and `attach-ib-target`'s `target_filled` branch.
- **`reclassify_frozen_exit_reasons.py` is staged, not run.**
- **5 criticals remain open**, in this order: `BL-20260818-IB-BROKER-PNL-READER-HAS-NO-CALLER`
  (Tier-2, next — ⚠️ **composes with item 1.8**, which just changed how a late-arriving price is
  labelled; read that first) · `BL-20260818-MIRROR-LEGS-DIVERGENT-TRAILED-STOPS` (Tier-2,
  starting with a Tier-1 "establish why") · `BL-20260818-ICT-SCALP-HAS-NO-TAKE-PROFIT-CLOSE-PATH`
  (Tier-3, step 1 is a Tier-1 measure) · `BL-20260818-MOST-OPEN-TRADES-HAVE-NO-DECISION-DRIVEN-EXIT`
  and `BL-20260818-EVERY-CRYPTO-PULLBACK-LEG-IS-OOS-UNPROFITABLE` (both Tier-3 **dispositions**,
  not fixes — they need an operator call, not a PR).

## Deferred Items
- The **yfinance free-lane candle feed** — decided (operator chose yfinance) and **sequenced
  after the criticals**. Needs `1d` support added to the m27 puller (21 of the 25 legs are
  `1d`/`1h`) and the `_yf_ticker` map ported from the dashboard repo. ⚠️ **Prove it on a GitHub
  runner** — Yahoo returns HTTP 429 from this sandbox, and *"it works locally"* is the shape of
  failure this repo keeps closing. Carry the caveat that `ES=F`/`GC=F`/`HG=F` are **continuous
  proxies**, not the exact MES/MGC contracts.
- **Criterion 4** (the price axis on the *enforcing* protection path), the stuck-cascade sweep,
  the three cascade gaps, #10081's apply-mode repair, the Alpaca read surface, and the MES stop
  divergence — all left untouched per the standing constraint.

## Next Recommended Sprint
Work `BL-20260818-IB-BROKER-PNL-READER-HAS-NO-CALLER` (Tier-2) after re-reading item 1.8, then
the two remaining Tier-2 criticals; put the three Tier-3 rows to the operator as **dispositions**
rather than carrying them as tasks. Then build the yfinance feed and prove it on a runner. Do
**not** fold the 15 `WAITING` cells into a sprint that reports them as progress.

## Wrap-Up Check
Board `▶️ START` posted before the first change; `✅ DONE` at close. The scheduled Sunday
2026-08-23 22:30Z session (`trig_014S3NAzMKy2Ac2AM2GgyRE5`) was **not** duplicated or
pre-empted — it had not fired at session end. `/doc-freshness` run at close; its step 5 caught
that this unit had no sprint log and no ROADMAP ledger row, which is what this file and that row
close. **That is the fourth consecutive session in which step 5 has caught a missing surface** —
the pattern itself is worth the operator's attention.
