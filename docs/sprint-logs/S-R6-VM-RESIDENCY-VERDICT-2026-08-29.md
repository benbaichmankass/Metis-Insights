# Sprint Log: S-R6-VM-RESIDENCY-VERDICT-2026-08-29

## Date Range
2026-08-28 23:20Z → 2026-08-29 00:40Z (single session)

## Objective
Answer **M40 / R6 — decide the trainer VM's fate, on evidence.** R6 was gated on R3
*holding*; R3 began holding at `76d14af5` (#10390), so the residency question became
answerable. The architecture doc's own answer was labelled **INFERRED** and explicitly
*"a hypothesis to test after R3, not a decision to take now"* — the objective was to
test it, not to execute it.

## Tier
**Tier 1.** Docs, registers, and read-only measurement. No `src/`, no `config/`, no
live order path, no VM mutation. Two read-only trainer diag pulls and one unauthenticated
`/api/bot/logs` read.

## Starting Context
Handed off at `main` = `76d14af5`, OPEN-ITEMS at 8 rows with **no loud row** for the
first time in the lane's existence. The handoff proposed R6 first on the grounds that
its gate had just opened. Standing operator decisions carried in and untouched: the
research-queue dispatcher **stays dry**, and a runner-trained model joining the live
shadow fleet is **Tier-2** and has not happened.

## Repo State Checked
- `git log`/`status` — clean tree on `claude/metis-insights-handoff-08-28-59rk7c`, at `76d14af5`.
- Coordination board #6927 read to a **proven tail** (`perPage=10, page=160` → short page
  of 4; a full page would have proven nothing). Newest event: the prior session's
  `🔓 RELEASE` — slot free, no open `🔒` or VM-LANE claim. `▶️ START` posted before the
  first substantive action.
- Trainer confirmed at `76d14af5` — same head as the repo, so nothing measured was stale code.

## Files and Systems Inspected
- **Trainer VM (read-only, 2 pulls):** #10391 unit-file states, timers, per-service
  `ExecStart`, disk, registry, offload inbox, cron, drop-ins; #10392 microstructure data
  freshness, capture service status/journal, drift-retrain journal, disk breakdown,
  dataset version pins, forecast artifacts.
- **Repo:** `.github/workflows/*` (which genuinely SSH to the trainer — 14 of a naive 19),
  `ml/offload-inbox/btc-regime-5m-lgbm-flow-v1/*/manifest.json`, `scripts/ops/run_forecast_producer.sh`,
  `src/runtime/forecast_live.py`, `src/runtime/trainer_reachability_alert.py`,
  `scripts/check_diag_unit_allowlist.py`.
- **Live API:** `/api/bot/logs?level=error&limit=1000`.

## Work Completed
1. **R6 answered, and the inferred hypothesis REFUTED.** Verdict:
   [`docs/research/R6-VM-RESIDENCY-VERDICT-2026-08-28.md`](../research/R6-VM-RESIDENCY-VERDICT-2026-08-28.md).
   **`ict-orderflow-capture.service`** genuinely requires 24/7 residency — a continuous
   2 s L2 order-book capture, `active (running)` **44 days**, verified *writing* (newest
   row `2026-08-28T23:50:00Z` vs same-command `date -u` `23:58:09`; `n_snapshots` 127–128
   per 5 m bar). Its consuming manifest states the constraint itself: *"the capture is
   **FORWARD-ONLY (no L2 history)**"*.
2. **Named the trap in R3's own result.** The model R3 registered as its proof —
   `btc-regime-5m-lgbm-flow-v1` — **is the order-flow model**, trained on columns only
   that capture produces. **R3 proved COMPUTE is portable; it did not prove ACQUISITION
   is.** Both halves are true and point opposite ways.
3. **Root-caused why § 3 missed it** — the transferable half. That table was built with
   `du`; the capture's whole output is **5.7 MB = 0.02 % of the 28 G tree**, i.e. *below
   the resolution of the instrument used*. It answered *"what DATA is pinned"* under a
   heading promising *"what is pinned to the VM"* — the repo's own **UNPROVENANCED
   DIAGNOSTIC OUTPUT class A** substitution, one level up from code.
4. **Found a live-serving role the disk inventory never counted:** `ict-trainer-forecast`
   (15 min) → `ict-trainer-publish` (2 min rsync) → the live trader's per-bar regime
   scorer reads `fc_*`. Fail-permissive, so it degrades rather than breaks.
5. **Corrected both governing docs** rather than leaving the refuted inference standing:
   `RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md` § 3 (scope correction, original kept as
   record), § R6 (measured table replacing the inference), § 5; and `ROADMAP.md`'s M40 row.
6. **Reported on the due monitoring item** (`OI-20260826-MHG-…`) with a real observation
   — see Validation.
7. **Filed two operational findings** and **added one `pending_decision`** OPEN-ITEMS row.

## Validation Performed
- **Capture liveness measured, not assumed:** `systemctl status` + a data-freshness read
  in the *same* command as `date -u`, so the comparison needed no clock assumption.
- **Zero-result probes carried a positive control.** The error-feed read ran controls
  first (`naked` 153, `target_naked` 129) so a zero on the probe term would have meant
  something. The feed returned **398 rows against a 1000 cap — short, therefore not
  truncated**, and its span was stated.
- **Board tail proven by a short page**, never by a full one.
- `open-items-guard` OK (9 items) and `session-brief --check` OK after each register edit.
- Backlog appends via `backlog_append.py` only; diffs verified as **pure insertions**
  (26 lines) with no reformat.

## Documentation Updated
- **NEW** `docs/research/R6-VM-RESIDENCY-VERDICT-2026-08-28.md`
- `docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md` — § 3, § R6, § 5
- `ROADMAP.md` — M40 R6 status
- `docs/claude/OPEN-ITEMS.json` — +1 row (9), two observations recorded
- `docs/claude/health-review-backlog.json` — +2 rows (997)
- `CLAUDE.md` — SESSION-BRIEF re-rendered

## Contradictions or Drift Found
- **The architecture doc's § 3 heading over-claimed its own table** ("almost nothing" is
  pinned). The table is right about what it measured; the heading promised more.
  Corrected in place, original preserved.
- **ROADMAP said R6 was "BLOCKED" and, later in the same cell, "UNBLOCKED"** — both
  superseded within a day. Now carries the measured answer.
- **`ict-drift-retrain` exiting `11` is NOT a fault** — it is `RETRAIN_PLAN_ONLY`
  (`dispatch_count=10 cli_exit=11 plan_only=1`). Recorded because the exit code invites
  the opposite reading. Side observation: ~47 min/day of the single core computes a plan
  it never executes, and 10 manifests are reported due hourly with nobody acting.

## Risks and Follow-Ups
- **`BL-20260829-ORDERFLOW-CAPTURE-IS-IRREPLACEABLE-AND-UNMONITORED`** (high, Tier-2) —
  the one irreproducible stream has **no monitor**: zero `orderflow` references in any
  alerting code, absent from the diag unit allowlist, and `trainer_reachability_alert`
  reads the *publish* timer's mirror mtime, which advances normally with the capture
  dead. Worse, the process catches poll errors and continues (`RequestTimeout` lines
  while `ActiveState=active`), so **`active (running)` is not evidence of capturing**.
- **`BL-20260829-TRAINER-DISK-92-PCT-THREATENS-THE-UNBACKFILLABLE-CAPTURE`** (high, Tier-2) — 42 G/45 G, 3.9 G free, largest
  trees all research byproduct. A full disk stalls the capture, and because nothing
  monitors it, **silently**. Deliberately claims *no growth rate* — two coarse readings
  are not a trend.
- **`OI-20260829-ORDERFLOW-CAPTURE-HOME-UNDECIDED`** (`pending_decision`, `loud`) — the
  guard against a future session reading "R6 unblocked" and retiring the box.

## Deferred Items
- **Lane B and Lane C** (the handoff's items 2 and 3) — untouched. Lane C's own before/after
  count re-measures at **2 of 40** workflows referencing `assert_rows_landed` today.
- **`OI-20260826-STRAY-OCA-SWEEP-SHIPPED-BUT-UNARMED`** — surfaced as due mid-session;
  **not re-checked, so deliberately not stamped.** Stamping it without looking is the
  failure the cadence exists to prevent.
- **Half (b) of the MHG row** (cancel-ib-order against the real gateway) — not exercised.
- The **`bybit_1`/ETHUSDT over-cover escalation** (167 % → 809 %) is **owned by
  `BL-20260730-BYBIT1-XRP-LEG-OVERACCUM-WORSENING`** (`kept_open`, remediation unbuilt).
  Deliberately **not re-filed** — that would be the duplicate the append guard exists to stop.

## Next Recommended Sprint
**Not more R6 measurement** — R6 is answered and what remains is an operator decision
(where the capture lives). The two filed findings compound and are the higher-value
follow-up: the capture is unmonitored *and* the disk that carries it is at 92 %, so the
default path is that R6's question gets answered accidentally by a silent stall rather
than deliberately. The freshness stamp proposed in the backlog row needs no new
transport — `publish_trainer_mirror.sh` already rsyncs `trainer_status.json` every 2 min
into the file `trainer_reachability_alert` already reads. After that: **Lane C**, then
**Lane B**.

## Wrap-Up Check
- [x] Board `▶️ START` posted before first substantive change; tail proven by a short page
- [x] Every number carries its population and its denominator
- [x] Zero-results carry positive controls
- [x] Registers edited only through `backlog_append`; diffs verified as pure insertions
- [x] `open-items-guard` + `session-brief --check` green
- [x] Refuted a hypothesis I could have confirmed cheaply, and said so plainly
- [x] Nothing armed, nothing retired, no VM state mutated


---

# Continuation — overnight autonomous run (operator-directed)

**Trigger:** operator, *"keep moving with the workplan overnight… You can make a tested and
evidence-based decision about the VM if you need to in order to continue the work. Otherwise, hold
Tier-3 decisions for me in the morning."* Then, mid-run: *"let's also try to get alpaca real money
ready to flip to live — we seem to be going in circles a bit there."*

## The VM authorisation — not exercised, and why

The grant was **conditional**: *"if you need to in order to continue the work."* **The condition was
not met.** Lanes B and C are entirely independent of the trainer's fate, so no VM decision was
required to continue, and retiring the box is irreversible and would end the 85.6-day forward-only
capture. **Held for the operator.**

## Work completed (continuation)

1. **`alpaca_live` go-live — unblocked to ONE decision.**
   [`ALPACA-LIVE-GOLIVE-STATUS-2026-08-29.md`](../research/ALPACA-LIVE-GOLIVE-STATUS-2026-08-29.md).
   The circling has a mechanical cause: STEP 1's decision input (`capacity.multiplier`) was readable
   from 2026-08-25 and **nobody read it for four days**. Read: **`1` → CASH**. That **voids STEP 1 as
   written** and **couples STEP 1 to STEP 2** (you cannot short in a cash account, so STEP 2 is an
   account conversion — and that conversion is what makes STEP 1's instrument correct again).
2. **The mirror does not mirror the risk**, and it kills the proposed ceiling basis.
   `risk_pct` 0.05 live vs 0.02/0.015 mirrors; and from `_size_unbounded`,
   `exposure_multiple = risk_pct × entry / risk_distance` — **equity cancels, so it is linear in
   `risk_pct`**. The mirrors' 1.84–2.01× was measured at 0.02, so at 0.05 the same signals demand
   ≈4.6–5.0× and a 2.0 ceiling would clamp nearly everything. Filed.
3. **Lane B measured** — [`lane-p-compat-verdict-diff-2026-08-29.md`](../research/lane-p-compat-verdict-diff-2026-08-29.md).
   3 of 11 verdicts move on a positive book, 1 of 11 on a negative one, **all conservative**; nothing
   moved `skip → ROUTE`. **No revert conversation.**
4. **Lane C triaged** — [`evidence-workflow-landing-triage-2026-08-29.md`](../research/evidence-workflow-landing-triage-2026-08-29.md).
   (a) 10 / (b) 7 / (c) 0-proposed. **No assertions wired**, per the row's own "NOT sufficient" clause
   and the 2026-08-27 operator decision that R1 precedes R2.
5. **R6 doc corrected** — the capture window was "not established" and now is: 85.6 days at 98.2 %.

## Validation (continuation)

- **The 08-27 "no numpy" blocker was re-tested, not inherited.** It was a missing *dependency*, and
  `requirements-test.txt` supplies numpy/pandas/scipy/sklearn — so Lane B did **not** need the
  dispatcher armed. **The dispatcher stays dry.**
- **Lane B's A/B varies only the code**: worktree at `f2ea9e44^`, ledger byte-identical (sha256
  pinned), `accounts.yaml` copied into the before-tree, real balances seeded with DDL **lifted
  verbatim** from `database.py:868` and read back through the repo's **own** reader, seed fixed.
  **Positive control run first** — the Lane P files differ and `standard_account_size.py` is absent
  in the before-tree, so the A/B is not measuring nothing. **Two ledgers** bracket the outcome space.
- **A stale-checkout trap was caught by a positive control**: an early Lane C probe returned zero
  hits from local `main` at `beb1547` (stale), not `76d14af`. The control (`ls` the file that should
  exist) exposed it. Without it I would have filed a false absence.
- **Two guards caught me and both corrections improved the work** — `impossibility-claim-guard`
  (my "cannot be measured" was too strong; the *demanded* multiple **is** computable, and is a
  better basis than the mirrors) and `stated-population-guard` (a percentage with no denominator).

## Contradictions or drift found (continuation)

- **`BL-20260821-ALPACA-LIVE-REFUSES-EVERY-ORDER-127-OF-127` describes a state that no longer holds** —
  it says `mode:live`; the account is `dry_run` on both the live diag and in config. Its own
  disposition was taken and the row never updated. A live source of the circling.
- **`BL-20260827`'s inventory was stale in its own headline case** — `trainer-offload-train` now
  lands, one day after filing. A hand-typed denominator is a snapshot.
- **`BL-20260824`'s own text says "three steps" in its title and "four steps" in its criteria.**

## Deferred (continuation)

- **The true GLD verdict set** — Lane B's ledger is synthetic by construction (it isolates the
  account-side change). `gld-compat-matrix.yml` still wants dispatching; its emit half needs Yahoo.
- **Lane P's drawdown-type half** — moved no verdict on either ledger; recorded **unmeasured**, not
  no-effect. A book with a deep early drawdown is the probe.
- **`OI-20260826-STRAY-OCA-SWEEP-SHIPPED-BUT-UNARMED`** — came due mid-session, **not re-checked, so
  deliberately not stamped**.
- **(c) DEAD classification** — no run-history evidence gathered.

## Wrap-Up Check (continuation)

- [x] The conditional VM authorisation was read as conditional, and the condition was not met
- [x] Every Tier-2/Tier-3 action held for the morning; nothing armed, funded, flipped or routed
- [x] Both standing operator decisions honoured (dispatcher dry; offloaded model at `candidate`)
- [x] An owned finding (`bybit_1` over-cover 167 % → 809 %) **not re-filed** — a `kept_open` row owns it
- [x] Guards green on every commit; registers edited only through the tool
