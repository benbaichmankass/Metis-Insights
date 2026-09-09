# The strategy-review packet grades nothing, on eight consecutive days — and the window/floor pair may be the wrong lever

> **Doc status:** `live` · category `evidence` · last verified `2026-09-09` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)

**Unit:** `WO-20260901-PHASE-F` (C3, decision preparation) · session `session_01DUBXxiaZ3PMERcAfcJce7P` · 2026-09-09.
**Nothing here was enacted.** No `config/`, no `src/`, no order path, no window change, no floor change. This
document exists to put ONE decision in front of the operator with its arithmetic attached.

---

## 1 · Two clauses of the open row are already met, and the row still says they are not

`OI-20260901-REVIEW-PACKET-CANNOT-PROPOSE-AN-ACTION-AND-ITS-EVIDENCE-BLOCK-IS-UNEXERCISED` names three
clears-when clauses. **Two are met.** The row is stale in the direction that invites duplicate work: a session
reading it would rebuild a cron that fires and a publication that is exercised.

### Clause (1) — a committed `INDEX.json` from a run whose event is `schedule`

**MET.** Population: the complete run history of `.github/workflows/strategy-review-packets.yml`
(`total_count: 54`, 40 returned by `actions_list`, covering #15 → #54, 2026-09-01T22:35Z → 2026-09-08T15:40Z).

| event | runs | conclusions |
|---|---:|---|
| `schedule` | **7** | 3 `success`, 4 `failure` |
| `issues` | 33 | all `skipped` (label-gated no-ops) |

Three scheduled runs succeeded **and their indexes are on `main` carrying that run's own stamp** — read from
the run history and the committed file, never from the cron expression:

| run | event | run created | committed `generated_at` |
|---|---|---|---|
| #34 `33955614332` | `schedule` | 2026-09-05T08:34:03Z | `2026-09-05T08:34:41.995130+00:00` |
| #35 `34023105345` | `schedule` | 2026-09-06T08:53:20Z | `2026-09-06T08:53:36.892229+00:00` |
| #48 `34107468880` | `schedule` | 2026-09-07T09:41:46Z | `2026-09-07T09:42:09.211126+00:00` |

⚠️ **The cron is `40 4 * * *` and every run fired 08:34–09:42Z — roughly 4–5 hours late, consistently.** That is
the same lateness `probes.yml` showed (~4h50m). It does not affect this clause; it is recorded so nobody reads
the declared minute as the firing minute.

### Clause (2) — the index carries `min_closed_for_action` and `evidence.floor_state` reads other than `unknown`

**MET, since 2026-09-02.** ⚠️ **First, a correction that will otherwise cost the next session its search:
`evidence.floor_state` is NOT a field in `INDEX.json`.** It is computed by the route,
`src/web/api/routers/strategy_review.py::_evidence_block`, from the index's `min_closed_for_action` plus each
row's `below_evidence_floor`. Looking for it inside the committed file and not finding it would read as *the
publication never shipped*.

Running that exact function over every committed index:

| index | `min_closed_for_action` | computed `floor_state` | below / gradeable |
|---|---:|---|---|
| 2026-09-01 | *absent* | `unknown` | — (index predates the field — the route working) |
| 2026-09-02 | 20 | **`none_gradeable`** | 52 / 0 |
| 2026-09-03 | 20 | **`none_gradeable`** | 52 / 0 |
| 2026-09-04 | 20 | **`none_gradeable`** | 52 / 0 |
| 2026-09-05 | 20 | **`none_gradeable`** | 52 / 0 |
| 2026-09-06 | 20 | **`none_gradeable`** | 52 / 0 |
| 2026-09-07 | 20 | **`none_gradeable`** | 52 / 0 |
| 2026-09-08 † | 20 | **`none_gradeable`** | 52 / 0 |

† stranded on a branch — see §4. Included because it was *generated*; excluded from any claim about `main`.

**So `none_gradeable` has been emitted by seven real runs.** The row's *"has ONLY EVER read `unknown`"* is false.

### Clause (3) — an operator decision on the window/floor pair

**NOT met. It is the only thing left, and §3 is the packet for it.**

---

## 2 · The finding, over eight days rather than one

Population: all eight committed daily indexes, 2026-09-01 → 2026-09-08, **all 52 enabled strategies**, window
**7 days**, floor `MIN_CLOSED_FOR_ACTION = 20`.

**`graded: 52 · actionable: 0 · by_action {"hold": 52}` on 8 of 8.**

| date | fleet closes in window | max `n_closed` on any leg | legs at zero |
|---|---:|---:|---:|
| 09-02 | 61 | 9 | 32 |
| 09-03 | 58 | 9 | 30 |
| 09-04 | 66 | 10 | 29 |
| 09-05 | 67 | 9 | 30 |
| 09-06 | 66 | 9 | 30 |
| 09-07 | 62 | 9 | 32 |
| 09-08 † | 63 | 10 | 32 |

**No leg has reached 11 closes in a 7-day window on any day measured.** The floor is 20. Grading all 52 legs
needs ~1040 in-window closes against ~62 observed — a **~17× shortfall**, stable across every day. This is not
a quiet week; it is the fleet's steady state.

⚠️ **The row's headline figure has moved and should not be re-quoted.** It cites *"13 losing legs carrying
−35,446"* from the 09-01 window. On 09-07 the same measure reads **16 losing legs, −13,147.94**; on 09-08,
**16 legs, −11,213.99**. The *direction* is unchanged — real money is being lost on legs the surface cannot
act on — but the magnitude is a rolling 7-day window, not a standing total.

---

## 3 · Would waiting help? The generator already answers this, and the answer is mostly no

⚠️ **I did not build this. `strategy_review_packet.py` already publishes a per-leg `evidence_horizon` block and
an `evidence_horizon_summary`,** and it is more careful than anything I would have written — each of the 24 legs (of 52) with zero
observed closes in the window gets **no** projected rate — only an optimistic lower bound at a 95% confidence
level, with the basis spelled out (*"Zero observed is not a rate of zero"*). ⚠️ That 95% is a CONFIDENCE
LEVEL, not a measured share of anything. Identical on 09-07 and 09-08:

| horizon class | legs | what it means |
|---|---:|---|
| `gradeable_now` | **0** | — |
| `reachable` | 20 | a finite horizon exists |
| `unbounded_no_closes` | 24 | **zero closes ⇒ no rate measured ⇒ no finite horizon projectable** |
| `structurally_ungradeable` | 8 | `shadow_execution_no_fills` — cannot close **by declaration** |

- `days_to_grade_median_reachable_point`: **70.0**
- `days_to_grade_all_reachable_point`: **140.0**

**Three consequences the decision rests on:**

1. **No window under ~70 days grades even the median *reachable* leg.** A 14- or 30-day widening changes
   `actionable: 0` into `actionable: 0`.
2. **32 of 52 legs (24 + 8) do not become gradeable at ANY window.** For the 8 shadow legs that is correct and
   permanent — `shadow` means never filling, so no window can ever produce a close: `avax_pullback_2h`,
   `eth_pullback_prop_2h`, `fade_breakout_4h`, `fvg_range_15m`, `htf_pullback_trend_2h`, `mgc_trend_1h`,
   `slv_trend_1h`, `turtle_soup`.
3. **At 70–140 days the evidence stops being about the current system.** That window spans the e35 bracket
   geometry deploy (2026-08-30), hedge-mode arming (2026-08-30), and the cash-settlement gate arming
   (2026-08-31). A KILL resting on it would grade a leg partly on geometry it no longer runs.

### ⚠️ The observation that reframes the question

At 52 legs and ~62 closes a week the **median leg closes ~1.2 trades per week**. The floor is not obviously
too high and the window is not obviously too short — **the fleet may simply be spread too thin to generate
decision-grade evidence about itself.** On that reading the lever is not the statistic at all: it is the
*number of legs*, which is Phase G / E3's sunset pass, and `SUNSET-DISPOSITIONS` already names **10 retirement
candidates over 52 legs**. Fewer legs carrying the same flow is the only change that raises per-leg `n`
without weakening the evidence bar. **I am not proposing a retirement — that is Tier-3 and G's — but the
operator should not be offered only window-and-floor when a third lever exists.**

---

## 4 · A live defect: the committed cadence has a hole, and it is the LANDING half

**4 of 7 scheduled runs concluded `failure`** — #22 (09-02), #30 (09-03), #32 (09-04), #49 (09-08). **Every one
failed at `commit-to-main`'s 30-minute merge wait, not in the generator.** Three landed anyway after the
window closed, so their red is cosmetic and their indexes are on `main`.

**#49 did not land.** [PR #11361](https://github.com/benbaichmankass/Metis-Insights/pull/11361) has been open
since `2026-09-08T09:05:03Z` with `updated_at` unchanged at `09:05:23Z` — **22 hours** — base `a0ec22ce`, and
**`comms/strategy_reviews/2026-09-08/` is absent from `main`.** The run log is explicit:

> `commit-to-main: … PR #11361 did not merge within 30m (stale-branch refresh: not_needed_up_to_date; git
> confirms the commit is not on main). The rows are written on
> automation/strategy-reviews-34207987310-1 and are NOT on main.`

⚠️ **I checked the tempting inference before reporting it, and it was wrong.** The PR changes **3** files,
which under the selection rule (*index always, two files per actionable row*) reads as the first actionable
strategy ever produced. It is not. The two extras are `.github/pr-landing/` and
`.github/pr-automerge-requests/` declarations; the stranded index reads `actionable: 0`,
`by_action {"hold": 52}` like every other day. **No verdict is being lost — only the denominator for one day.**

**Not fixed here.** Merging another lane's automation PR is a merge-queue act and this is a worker session;
filed as `BL-20260909-…` and raised on the board.

---

## 5 · What this unit did NOT do, stated rather than implied

- **Did not widen the window.** §3 shows the only window that grades anything is 70–140 days, which buys a
  verdict by making the evidence span a different system. That is the decision's to make, loudly, not mine.
- **Did not lower the floor.** A KILL at n≤10 is the low-n hazard `MIN_CLOSED_FOR_ACTION` exists to prevent.
- **Did not write a changelog entry or touch `config/strategies.yaml`.** Tier-3, and the operator's.
- **Did not clear the open row.** Clause (3) is unmet; I corrected the two stale clauses and left it open.
- **Did not merge PR #11361.**
