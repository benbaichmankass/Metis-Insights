# System-review evidence — 2026-09-11 (MI-272)

> **Doc status:** `live` · category `evidence` · session `session_01W56JbiKrada5AM4pGtHxmg`

**What this is, and is not.** This is the measurement record behind MI-272's
findings — populations, denominators, and the specific reads. It is **not** the
consolidated system report, which this run did **not** produce;
`docs/claude/system-review-checklist.json` records that as `not_started` rather
than passing a partial artifact off as the deliverable.

**Read-only throughout.** No order placed, modified or cancelled. No leg
cancelled on any over-covered book. The standing `alpaca_paper`/GLD wedge and
every open position were left untouched. Every Tier-2/3 finding is proposed with
its exact fix and none is enacted.

---

## 1 · The closed-flat invariant is speaking, and it is wrong

**Population:** the complete `invariant_violations.jsonl` served by
`/api/diag/log_file?name=invariant_violations` — **9 rows, 5 distinct
`trade_id`s**, 2026-09-10T14:04:12Z .. 2026-09-11T13:56:26Z, all `bybit_1`, all
`phase: alert_only`. This is the *entire* output of the mechanism since audit
F-11/F-12/F-13 repaired it on 2026-09-09.

**The denominator first,** as `OI-20260909-CLOSED-FLAT-INVARIANT` demands.
`closed_flat_coverage` is present and interpretable: 100 rows on the served page
(file 386,929 bytes, so a **tail**, not the lifetime), 2026-09-11T14:20:58Z ..
17:58:42Z. `checked: true` on 100/100 — the writer is writing, so this is *not*
the deploy defect a zero might have been. `cadence_basis` `measured` 99/100,
`bootstrap` 1/100. `could_not_look` 0. `examined` **10** total, `flat` 10,
`residual` 0, over only 2 of 100 windows. `controls_ok: true` on 100/100 —
recorded as clearing **nothing**, since `controls_scope` is
`pure_classifier_only`.

**The defect, by arithmetic:**

| trade | account | symbol | side | `position_size` | reported `exchange_qty` |
|---|---|---|---|---:|---:|
| 5639 | bybit_1 | SOLUSDT | short | 9.8 | **−250.4** |
| 5658 | bybit_1 | SOLUSDT | short | 9.3 | **−250.4** |
| 5665 | bybit_1 | SOLUSDT | short | 7.5 | **−250.4** |
| 5647 | bybit_1 | AVAXUSDT | short | 2747.1 | −14952.9 (5.4×) |
| 5677 | bybit_1 | ETHUSDT | long | 8.79 | 1.87 (*smaller*) |

Three different trade sizes cannot yield one identical per-trade residual.

**Root cause, verified in source.**
`src/runtime/closed_flat_invariant.py::_residual_from_positions` sums every
position row matching `(symbol, canonical_side)` and never references the
closing trade's quantity. The module's stated contract is symbol-scoped in
terms — *"If the exchange still shows a non-zero position on the same symbol +
side, that's a contract violation"* — so **the code matches its contract and the
contract is wrong** on a netted book, where one exchange position legitimately
holds N journal rows.

**Exit reasons of the 5:** `pairs_revert` ×2, `pairs_half_open_cleanup`,
`netting_attributed`, `intent_reduce`. The pairs sleeve runs market-neutral legs
on the same symbols as the directional legs, so **open siblings are the normal
state there**, not an edge case.

**It pages.** `_default_alerter` emits `Level.ERROR`; `outcomes.py` admits
`{ERROR, CRITICAL}` to Telegram by design. All 9 reached the operator.

**Not established:** whether any of the 9 *also* sits on a genuine unattributed
residual. Settling that needs per-trade attribution this module does not
compute — which is itself the finding.

---

## 2 · The 60-second exit-evaluation requirement

**Population:** every row on disk via `/api/bot/exit-interval/soak` — **72,611
rows, 72,148 graded intervals, 463 processes.** The first reading not subject to
CLAUDE.md's "no process lived long enough to draw the tail" caveat.

| quantity | value |
|---|---|
| in-process breaches | **1,219 / 72,148 = 1.69 %** |
| `max_interval_ms` | **120,189.1 = 2.00×** the 60 s requirement |
| `last_breach_utc` | **2026-09-11T05:00:32Z** (today) |
| `restart_gap_state` | **`breached`** |
| restart-gap breaches | **77 / 462 = 16.7 %** — one in six restarts |
| `max_gap_ms` | **210,409.2 = 3.51×** |
| restart-gap exclusions | 0 overlapping, 0 ungradeable, 0 unattributed |

**The live instrument disagrees:** the running process reports
`requirement_state: within`, `max_interval_ms` 40,412.6, ratio 0.6735 over
`intervals_measured: 172`, on a process started 16:31:15Z. A ~95-minute life
that never drew the tail — exactly the failure CLAUDE.md names when it says to
read `requirement_state` beside `intervals_measured`.

⚠️ CLAUDE.md's 2026-08-25 figure (*n=991, 0 over_requirement, max 45.0 s, 15.0 s
margin*) is a **991-row tail of a 72,611-row file** and must not be re-quoted as
current. The row says so itself.

**What this is not:** a claim that a specific trade was harmed. A breach is
exposure, not a realised loss; no close was attributed to one.

---

## 3 · Scheduled workflows — the premise was wrong

**Population:** all **27** workflow files declaring a `schedule:` block (of 141
workflow files); run history read for **27 of 27**, zero `could_not_read`.

**Nothing is `never_fired_on_schedule` and nothing has `no_runs_at_all`.** The
failure class several OPEN-ITEMS rows are written against is not the live
problem. Three others are.

**3a — `replay-pregate-nightly.yml`: zero successes, ever.**
`?status=success` → `total_count: 0`. Scheduled histogram n=77 (oldest run #100,
2026-06-27T06:53:28Z): **68 failure, 9 cancelled, 0 success.**
`git ls-tree -r origin/main -- runtime_logs/replay_pregate/` → **0 files**. Two
defects in job log 103202363431: the pre-gate cannot attribute its own failure
(`transport exit code was not captured`), then pushes partial evidence straight
at `HEAD:main` with no rebase → `! [rejected] ... (fetch first)`. The failure
*mode has changed* over time (runs 28777932869 and 31295958585 died at
`no JSON object in driver output`), so "always red" is not one stuck bug.

**3b — delivery falls off monotonically with declared frequency.**

| declared | example | decl/day | obs/day | delivery |
|---|---|---:|---:|---:|
| hourly | work-digest | 24 | 6.10 | **25 %** |
| hourly | error-feed-digest | 24 | 6.24 | **26 %** |
| 2-hourly | stale-automation-sweep | 12 | 4.49 | 37 % |
| 2-hourly | work-decision-commit | 12 | 5.76 | 48 % |
| 3-hourly | session-reaper | 8 | 4.94 | 62 % |
| 6-hourly | 5 workflows | 4 | 3.64–4.01 | 91–100 % |
| daily | 13 workflows | 1 | 1.00–1.17 | 100 % |

Cause **not established** — free-tier throttling is a hypothesis, not a finding.

**3c — the shared `commit-to-main` landing step is the largest single cause of
discarded output.** Nine workflows on one day computed their artifact and then
failed to land it. `#11785`, `#11786`, `#11789` are all `closed, merged: false`;
there are 2 open PRs in the repo and **zero on an `automation/*` branch**, so
nothing is queued. Today's due-list, constraint readout and error-feed digest
exist only as abandoned branches. Two sub-causes: generated-artifact conflict
against a moving `main`, and **GitHub API rate-limit exhaustion** — `pr-queue-watch`
34623771514 (16:44:57Z) and `trainer-capture-watch` 34623006745 (16:36:51Z) both
died on `API rate limit already exceeded for user ID 119055177`, 8 minutes apart.

⚠️ That makes a **red `pr-queue-watch` ambiguous** between "the queue is unworked"
(its deliberate exit-1 page) and "GitHub refused the receipt PR" — defeating the
purpose of reserving exit 1 for the page.

**3d — systemic lateness.** Median 4–5 h late across the dailies and 6-hourlies
(oci-inventory +5.98 h, research-queue-dispatch +5.21 h, constraint-readout
+5.06 h, purge-artifacts +4.93 h, … due-list +4.26 h). **No `clears_when` clause
may read a declared cron minute as a firing minute.** Note `board-heartbeat`
delivers 4.01/day against an 18 h staleness bar, but its worst observed gap is
**7.63 h** — inside the bar, with less margin than `7 */6` implies.

---

## 4 · ML — the 34/4 ratio, verified and interpreted

**Verified exactly, reconciling two ways with zero stage disagreements:**
`/api/diag/shadow_stats` returns 34 records (30 shadow + 4 advisory) = 32
registry rows at shadow+ − 2 registered-but-not-scoring + 4 off-registry exit
heads. Soak ages over the 34: **median 80.0 d, mean 72.5 d, max 115.6 d, 11
models ≥ 100 d.**

**Verdict: a structural ceiling, not a stalled gate.** `ml_vol_regime_for_symbol`
resolves advisory **per-SYMBOL**, so the 3 advisory registry heads are exactly
one regime head each for BTCUSDT/15m, MES/5m, SOLUSDT/15m. A second head on a
symbol *replaces* the incumbent rather than adding an opinion — there is no
fourth slot. Measured over all 46 stage transitions: 36 in 90 d, 9 into
advisory, **every promotion paired with a demotion**; last promotion 2026-08-04,
which demoted its predecessor in the same commit.

So the correct alarm is not *"why aren't we promoting"* — it is **"30 heads at
shadow, median 80 days, and the retire-or-refine lifecycle has fired twice in 60
days."**

**The mechanical blocker, and a correction.** ⚠️ The prior review's *"all
drift-dependent verdicts are ungradeable because `reference_count` is 0 by
arithmetic"* is **false today** — `reference_count == 0` for exactly 2 of 34,
both 4.5-day-old exit heads correctly `insufficient_data`; the other 32 carry
33–1,894. Drift computes.

What is actually wrong is worse: declared reference window 2026-08-05T17:59Z →
2026-09-04T17:59Z = **30.00 days**; oldest row in the file **2026-09-01T22:31Z**;
**actually covered 2.81 days = 9.4 %**. `ml/shadow/inspector.py::iter_records`
(:159) opens one literal file and never a rotated archive, while the endpoint
reports `reference_window_start: 2026-08-05` — a date for which the file holds
**zero rows** — with `reference_count: 300` reading as a healthy denominator.
Unprovenanced diagnostic output, sub-classes **B** and **C**.

The archives exist and are synced nightly (`db_pulls` → `archives: 3`), and a
grep for any consumer of `shadow_predictions.*.jsonl.gz` returns only
`sync_trainer_data.sh` itself. **This is a recurrence:** that script's line 318
documents the diagnosis verbatim (`MB-20260712-SHADOW-LOG-HISTORY`); the sync
half was fixed and the read half never was.

**It reaches the gate.** `drift_clean` is required (`ml/promotion/gates.py:747`).
Grading all 34 at the gate's own thresholds: **FAIL 24 · PASS 8 · insufficient
2** — 71 % of the fleet blocked. Of the 8 passes, **2 pass with KS = PSI =
0.0000 because they emit a constant** (`execution-quality-baseline-v0`,
`setup-quality-audit-baseline-v0`, `score_min == score_max` over 3,895
predictions each). A degenerate model aces the drift gate.

**No promotion and no demotion recommended** — acting on that statistic in
either direction is the unprovenanced-diagnostic trap.

---

## 5 · Live-money reads

**24 of 24 open journal rows match venue quantity exactly** (positive control
for every reconciliation below). All 13 Alpaca positions at **exactly 1.00×**
stop *and* target coverage. `alpaca_live` (real money) reads 0 orders / 0
positions — **flat, an empty denominator, not "covered"**.

**A dated live instance of the Alpaca over-close.** `alpaca_portfolio`/TLT held
two sibling rows on one netted symbol: trade **5414** (56 shares) closed
2026-09-08T12:54:52 `sl_cross`, pnl −11.2; **four minutes later** trade **5266**
(16 shares) closed 12:58:38 `exchange_flat_reconciled`, **pnl NULL**. Closing
one flattened the symbol and took the sibling's shares; the sibling's outcome
was not mis-attributed — it was **never measured**. Corroboration, not proof: the
order-level evidence (the DELETE call and resulting fills) was not read.

**The GLD wedge is standing.** `close_wedge_standing` carries a non-empty
`wedges`: `alpaca_paper|GLD|long`, `share_hold: cancel_accepted_ineffective`,
both orders still resting, `first_seen` 2026-09-11T09:01:52Z, `last_seen`
17:09:46Z, `pages_suppressed: 8`. ⚠️ `first_seen` is the first sighting *under
this classification*, not the age of the condition — the wedge has stood since
2026-08-27, so reading it as wedge age understates by two weeks.

**MGC:** venue 35.0 vs one open journal row at 35.0; bracket matches the journal
to the tick (SL 4305.97857 / STP 4306.0; TP 4444.53214 / LMT 4444.5). The
43-lot divergence is **not observable today** — and the resolution is
**unattributed**, so this is not a clean bill.

**`bybit_2` (REAL MONEY)** available margin reads `coin_derived` with
`is_broker_truth: false`. Correctly labelled and not collapsed — but the only
real-money crypto account is sizing against a derived figure.

---

## 6 · Two false P1s, checked before filing

1. **`restart_pending: true`** reads as its own docstring's *"2026-05-09 incident
   state"*. All 33 files between the running web-api sha and `origin/main` are
   `docs/` / `.github/` / top-level markdown — the deploy script's **designed**
   non-runtime skip, and its log says so (`Running processes already deployed`).
   Because this repo commits bookkeeping constantly, the field is ~permanently
   true while meaning *"some markdown changed"*.
2. **Four venue positions appeared to have no journal row** (`alpaca_paper`
   IEF/SPY, `alpaca_portfolio` IEF, `ib_paper` MES). They sit **below
   `/api/diag/journal`'s 1000-row id floor** (ids 4194/4195/4347/4350 vs a floor
   of 4701). A live instance of *"a count that reads 0 may mean we could not
   look"*.

Both are genuine instrument defects; neither is the outage it looks like.

---

## 7 · What was not measured, stated rather than implied

- **The consolidated system report** — not rendered. Named `not_started`.
- **Per-trade decision grading** — not run; `comms/claude_strategy_scores.jsonl`
  not written. A real gap in the mandate.
- **Performance / PnL attribution** — routed to **MI-271** by dispatch, not
  duplicated. Two lanes producing competing numbers off the same contaminated
  30 d instrument is worse than one.
- **The prop-account stall** — routed to **MI-274**, which has since landed its
  attribution (`3573b9a95`).
- **Security sweep is PARTIAL:** 858 request lines parsed, but the journal tail
  caps at 1000 lines and reached only to 2026-09-11T06:38Z (~11.4 h). The
  09-08 → 09-11T06:38 window is **unmeasured, not clean**. And the verdict rests
  on route-fingerprint plausibility: the diag token is publicly exposed
  (accepted risk), so a third party using it would present as a cloud IP with a
  valid token — indistinguishable by IP from our own runners.
- **Trainer-side gates** — 7 of the 8 promotion gates need trainer compute; no
  relay was dispatched. All trainer facts are mirror-derived (mirror 19.4 s
  fresh, reconciling exactly with the live registry on every stage).
- **`dataset_audit.jsonl`** is not on the diag allowlist (verified with
  `exit_lever_soak` as a positive control), so *which* column is dead for
  `setup-quality-lgbm-v2` is unestablished.
- **`BL-20260905-LOCAL-PNL-SWEEP-WINDOW`'s last clause could not be tested:** its
  criterion needs closed rows held > 14 days, and the 1000-row window contains
  **zero** of them. The denominator is empty, so a zero there is not evidence.
  Control: 36 closed rows carry `pnl NULL` at *some* hold length, so the probe
  finds positives on that axis.
