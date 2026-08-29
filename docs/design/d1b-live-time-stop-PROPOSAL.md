# D1b — should the live trader gain a bar-count exit?

**Status: PROPOSAL. Tier-3. Nothing here is implemented and nothing is declared.**
Written 2026-08-29 as item **N5** of `docs/claude/WORKPLAN-NIGHT-2026-08-29.md`,
following the B9 measurement in
[`timeout-bars-harness-vs-live-2026-08-29.md`](../research/timeout-bars-harness-vs-live-2026-08-29.md).

---

## 1. The question

B9 established that the backtest harnesses force-close a trade at
`min(entry_i + timeout_bars, n-1)` and that **no live trend / pullback / squeeze unit
implements a bar-count exit at all**. The harness default therefore BINDS on
**439 of 1,588 graded base-vs-`to400` geometry pairs (27.6%) and on 18 of 41 legs** —
those legs' recorded verdicts describe a strategy production does not run.

D1 fixed the *measurement* (the e35 base arm is pinned to `NO_BAR_COUNT_EXIT`;
harness defaults untouched). D1b is the separate, open question it leaves behind:

> On the legs where the harness was **not** lying — where base already equalled
> `to400` — a shorter hold still won. Is production missing a revision lever?

## 2. ⚠️ The population is smaller than the workplan claimed — corrected here

`WORKPLAN-NIGHT-2026-08-29.md` § N5 names four legs as *"CLEAN and blocked"*:
`mes_trend_long_1d`, `tlt_pullback_1d`, `gld_pullback_1h`, `eth_pullback_2h`, and asserts
that for each *"their base arm was live-parity, and a shorter hold still beat it at the
gate."* **Measured against the corpus, that is true of two of the four** — and of the
other two, one is an improvement case rather than a blocked one and one is **unreadable**
because its matrix cell is stale. The corrected reading is materially weaker than the one
the plan proposed, and the plan's own source (the coverage matrix) is part of why.

| leg | clean? | best gate-passing **timeout-free** cell | best gate-passing cell **with** a timeout | verdict |
|---|---|---|---|---|
| `mes_trend_long_1d` | ✅ clean | **NONE** | `tp1_sm2_to24` · net 8.2412 · wf **4/6** | **strictly blocked** |
| `tlt_pullback_1d` | ✅ clean | **NONE** | `tp2_sm1.5_to24` · net 6.1001 · wf **5/6** | **strictly blocked** |
| `gld_pullback_1h` | ✅ clean | `sm1.5` · net 96.9868 · wf **5/6** | `tp6_sm1.5_to24` · net 115.4005 · wf **6/6** | **NOT blocked** — an improvement case |
| `eth_pullback_2h` | ✅ clean | **NONE** | **NONE** | **unreadable — its matrix cell is STALE, see § 2.1** |

- **`eth_pullback_2h` has ZERO gate-passing cells in the CURRENT corpus** — 7
  `is_oos_fail`, nothing else. But that is **not** grounds for calling the plan wrong on
  this leg, and an earlier draft of this document did exactly that. See § 2.1.
- **`gld_pullback_1h` is not blocked**: three timeout-free cells pass
  (`sm1.5` wf 5/6, `sm2` wf 4/6, `tp4` wf 4/6). A time stop would buy
  **+18.41 net_total_r (+19.0%)** and one extra walk-forward fold — real, but it is an
  *increment on top of a shippable alternative*, not a blocked leg.

### 2.1 ⚠️ `eth_pullback_2h`'s matrix cell is STALE — and I nearly reported that as the leg's verdict

The plan's four-leg population was read from `exit-refinement-coverage.json`. That row's
`bracket_geometry` ref cites the **2026-08-20** run and declares a winner —
`tp2_sm3.5_to48`, path A, **wf 6/6 effective** — plus five further gate-passing cells, of
which **two are timeout-free** (`sm3.5` wf 5/6, `sm3` wf 5/6). On that record the plan's
framing is entirely reasonable.

**The corpus rows for this leg are from a later run** —
`collected/e35-bracket-eth_pullback_2h/2026-08-24/report.json` — and they do not
reproduce it:

| cell | 2026-08-20 (what the matrix ref still says) | 2026-08-24 (what the corpus holds) |
|---|---|---|
| `tp2_sm3.5_to48` | declared winner, path A, **wf 6/6** | not carried into the gate (`gate_verdict: null`), net 18.6132 |
| `sm3.5` | gate-passing, wf 5/6 | **`is_oos_fail`** |
| `sm3` | gate-passing, wf 5/6 | **`is_oos_fail`** |

**This is not timeout contamination.** `eth_pullback_2h` is CLEAN on the timeout axis
(39/39 cells identical to their `_to400` twins), so the 08-20 → 08-24 flip is a genuine
run-to-run instability on the same leg, not an artifact D1 has since fixed.

So the correct verdict is **"unreadable until the staleness is resolved"**, not *"not a
time-stop case"*. Stating it the second way — which this document did in its first
draft — would have converted a stale record into a confident negative finding about the
leg, which is the same error in the opposite direction from the one § 2 corrects.

**Scope of the staleness, measured.** 30 matrix cells still cite the 2026-08-20 run.
Eleven have no corpus rows at all (`pending` / `blocked:no_free_lane_candle_feed`) and are
not a finding — the ref names a run that did not measure them, and their status says so.
**Nineteen carry corpus rows from later runs (08-23 / 08-24) while the ref still cites
08-20.** Two of those nineteen assert `passed_unshipped` on a declared winner the newer
rows do not reproduce: `eth_pullback_2h` and `eth_pullback_prop_2h`.

**This is evidence for an already-open row, not a new finding.**
`BL-20260820-BRACKET-GEOMETRY-COLUMN-HAS-NO-AGREEMENT-CHECK` records that the
`bracket_geometry` column is `CORPUS_EXEMPT`, so **nothing re-derives its statuses from
the artifacts they came from** — and its own words are *"visible is not checked"*. The 19
superseded refs measured above are that gap producing exactly the drift the row predicts.
⚠️ **The `matrix-bracket-values` guard added tonight (PR #10437) does NOT close it**: that
guard checks a `shipped` cell's declared VALUES against `config/strategies.yaml`, whereas
this row closes only when a guard re-derives each cell's STATUS from
`docs/research/e35-bracket-gate-corpus.jsonl`. Two different axes; the second is still
uncovered.

⚠️ **The reverse-direction discrepancies are deliberately NOT read here.** Nine legs are
recorded `honest_negative` / `blocked:*` while their corpus rows now carry gate-passing
cells (`trend_donchian_sol_4h` has 7). **Seven of those nine are timeout-CONTAMINATED and
the N2 re-sweep is re-measuring them right now**, so their passing cells may be artifacts
of the very contamination B9 identified. Reading them before N2 lands would be precisely
the error B9 exists to prevent. Filed for the post-N2 pass, not concluded tonight.

**So the case that production is missing a revision lever rests on n = 2 legs**
(3 if `gld_pullback_1h`'s increment is counted; `eth_pullback_2h` is not countable either
way until § 2.1 is resolved). That is a small denominator and it is
the single most important thing about this proposal. Both blocked legs land on the
**same** grid point, `to24`, which is mildly corroborating and is also what you would
expect if 24 is simply the shortest hold the grid offers.

## 3. Soundness of the evidence

The corpus (`docs/research/e35-bracket-corpus.jsonl`, 8,211 rows / 41 legs) was built
**before** D1 pinned the base arm, so its base rows are contaminated *in general*. It is
nonetheless sound for these four legs, and this was checked rather than assumed: on a
clean leg every timeout-free cell must equal its `_to400` twin, and it does —
**39 of 39 cells identical on all four legs, zero differing, zero ungradeable.**

The N2 re-sweep at `a986ac3` re-measures all 41 legs against the pinned base arm. For
these four the numbers above must come back **unchanged**; that is exactly the CLEAN
control N7 runs first, and a change on a clean leg would falsify this section rather than
update it.

## 4. Incidental finding — `gld_pullback_1h` has an unshipped timeout-free winner

`gld_pullback_1h` carries `atr_stop_mult: 2.5` (the fleet default) today, while `sm1.5`
passes at `path_b_wf_pass`, wf 5/6, net 96.99. **#10419 did not touch it** — the eight
legs it declared are `mgc_pullback_1d`, `spy_trend_long_1d`, `qqq_trend_long_1d`,
`iwm_trend_long_1d`, `slv_trend_1h`, `tlt_pullback_1h`, `uso_trend_1h`,
`scha_trend_long_1d`, and none of the four legs here is among them.

**The matrix already tracks this** — `gld_pullback_1h`'s `bracket_geometry` cell reads
`passed_unshipped`, which is exactly right and is not a discovery of this document. What
is worth stating is the *direction*: this is a **B4 follow-up, not a D1b argument**, and
it cuts against the time stop, because the cheapest available improvement on this leg
needs no new mechanism at all. Not proposed for action here.

## 5. What a live bar-count exit would have to look like

Stated so the cost is visible before anyone argues the benefit. This is the shape a
proposal would need, **not** a design that has been reviewed.

1. **It is a NEW exit path on the live order path — Tier-3.** Every existing exit is
   price-triggered (stop, target, trailing amend) and rests at the venue. A bar-count
   exit rests nowhere: it is the bot deciding to flatten because time passed, so it can
   only fire while the trader is running and the leg is being evaluated.
2. **It therefore inherits the exit loop's liveness as a correctness dependency.**
   `EXIT_EVAL_MAX_INTERVAL_SECONDS` is 60 s and the measured worst interval is 45.0 s
   (n = 991 intervals / 10 processes / 8.3 h, `over_requirement` 0/991). A stop that
   exists only in the process is unarmed whenever that loop is not running — which is the
   precise gap `exit_loop_health`'s `never_ran` state exists to make visible.
3. **"Bars" must be resolved against the leg's own timeframe, from closed bars only.**
   `to24` on a 1d leg is ~5 trading weeks; on `gld_pullback_1h` it is ~24 hours. A
   wall-clock implementation would silently mean different things per leg.
4. **It must be declarable and observably inert when undeclared.** The natural key is
   `timeout_bars`, and D3 has just deleted two dead copies of exactly that key — so
   re-introducing it without a reader would recreate the defect this arc removed. Any
   revival must ship reader and declare together.
5. **It must ANNOTATE before it acts.** The repo's own precedent for a new exit influence
   is a soak first (`exit_lever_soak`, `target_extension_soak`), and § 4 of
   `exit-mechanism-construction-PROCESS.md` requires the thesis be re-evaluated rather
   than assumed. An annotate mode writing *"the time stop would have fired here"* against
   live trades is the minimum before any leg declares one.
6. **Its states must not collapse.** At minimum: `fired` · `not_reached` ·
   `undeclared` · `unknown_bar_count` (**we could not resolve how many bars have
   elapsed — which is NOT "not reached"**).

## 6. What would falsify this proposal

Any one of these should stop it:

- **The N2 re-sweep changes the four legs' numbers.** They are clean; a change means the
  clean/contaminated split is wrong and § 2 must be rebuilt before anything is argued.
- **`gld_pullback_1h` shipping `sm1.5` captures most of the available gain.** If the
  timeout-free declare lands and performs, the case narrows to n = 2.
- **§ 2.1 resolves against the time stop.** If a re-measurement restores
  `eth_pullback_2h`'s timeout-free passes (`sm3.5`, `sm3`), that leg becomes an increment
  case like `gld_pullback_1h` rather than a blocked one.
- **The two blocked legs' `to24` winners do not survive a re-sweep at the pinned base.**
  Both are `path_b_wf_pass` at wf 4/6 and 5/6 — 4/6 is not a strong fold record.
- **A cheaper existing lever reaches the same trades.** The target-extension lever is
  already built and has never produced an informative row (measured 2026-08-29: 1000/1000
  rows sentinel; 0 of the 10 real-target legs have written one). If a trade that a time
  stop would have cut is one an *existing* mechanism would have handled, build nothing.
- **n = 2 is judged too thin.** This is the honest default reading, and it is a legitimate
  place to stop.

## 7. Recommendation

**Do not build a live bar-count exit on this evidence.** Two legs, both at the grid's
shortest hold, with 4/6 and 5/6 fold records, against a new live-order-path mechanism
whose correctness depends on process liveness, is not a trade worth making yet.

The two cheap next steps, in order, are: (a) let N2/N7 confirm the four legs are stable
under the pinned base arm, and (b) treat `gld_pullback_1h`'s unshipped `sm1.5` as a B4
follow-up. Revisit D1b only if the blocked set grows materially after the re-sweep.

---

*Population note: every figure above is from `docs/research/e35-bracket-corpus.jsonl` as
of commit `6497148` (8,211 rows, 41 legs, 199 cells per leg for these four). Gate-passing
means `gate_verdict ∈ {wf_pass, path_b_wf_pass}`. The exit-interval figures are from
`/api/diag/log_file?name=exit_interval_soak`, n = 991, 2026-08-25.*
