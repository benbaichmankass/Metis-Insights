# Sprint Log: S-WORKPLAN-CONTINUATION-2026-08-22

## Date Range
- Start: 2026-08-22T~07:30Z
- End: 2026-08-22T~12:10Z

## Objective
Work `docs/claude/WORKPLAN-2026-08-21.md` as a queue, starting at item **1.0**
(bound the IB fetch tail), then **T.2** (Bybit hedge-mode resolver, shipped
inert), the **trainer disk**, and the **Phase 0 retirement pass** (0.2–0.5) plus
**2.5**. Close out on the operator's mid-session redirect: *bugs and technical
blockers before research*.

## Tier
Tier-1 throughout, plus **two Tier-2 items shipped under the workplan's standing
pre-approvals** (item 1.0 on evidence; T.2 with an empty allowlist). **No Tier-3
gate was flipped.** No `execution:`, no `mode:`, no risk cap, no model promotion.

## Starting Context
The 2026-08-21 plan's item **T.1** had shipped and been live-falsified, but the
60 s exit-evaluation requirement was **still breached at the tail**
(`max_interval_ms` 84–89 s, reached within 50 passes — not a rare event). Item
1.0 was pre-approved to ship *on evidence*: a stated-population measurement plus
a live positive control, evidence first and revert if it does not fall.

## Repo State Checked
- `origin/main` at `1b05353` → session ended at **`dd0fb364`**.
- Four PRs merged: **#10129** (`9c9235f`), **#10130** (`1f06fd2`),
  **#10136** (`787418c7`), **#10140** (`dd0fb364`). Zero left open.
- Guards run on a **committed** tree before every merge (a staged-only run is
  vacuous). Local `ruff 0.15.22` (0.16.x gives 24 false hits).

## Files and Systems Inspected
- `src/units/accounts/ib_client.py`, `src/main.py::_exit_loop`,
  `src/runtime/exit_loop_health.py`, `runtime_logs/exit_interval_soak.jsonl`
- `src/units/accounts/execute.py` (4 order sites), `src/runtime/order_monitor.py`
- `scripts/ci/check_unwired_artifacts.py`, `scripts/ci/run_guards.py`
- `scripts/ops/attach_ib_target.py` vs its siblings `flatten_ib_position.py`,
  `cancel_ib_order.py`
- Trainer VM: dataset GC, pins, disk accounting (relay-only, read then act)
- Live VM via `/api/diag/{ib_state,tick_cost,venue_session,ib_open_orders,exchange_positions}`

## Work Completed

### 1.0 — the IB fetch tail. Root-caused, the row's own hypothesis REFUTED, shipped, live-controlled.
`BL-20260816-IB-QUEUE-TIMEOUT-EXCEEDS-EXIT-BUDGET` blamed a queue timeout. **That
timeout fires 0 times.** The real cost was `_probe_liveness` running on *every*
IB fetch — including the cached-handle path — where attempt 1 kept timing out and
the retry kept answering, forever. **Population: n = 75 attempt-1 timeouts over
2226 s across four disjoint live windows (01:30Z–07:40Z), 2.02/min, within-window
1.50–2.32/min.** At 6.5 s each (`IB_PROBE_TIMEOUT_S` + `IB_PROBE_RETRY_GAP_S`)
that is **488 s of blocking in 2226 s — 21.9 % of wall clock** — while the branch
that actually condemns a connection fired **zero** times. The retry did not absorb
a rare event; it converted an outright failure into a permanent per-call tax.

Fix: `IB_PROBE_CACHE_S` (default 60 s) — trust a **successful** probe on a
**cached** handle for that long. It can only ever skip a repeat of a check that
already passed: a fresh handle always probes, a failure is never cached, and
`<= 0` restores the old behaviour byte-for-byte.

Post-deploy: probe rate **0 / 1395 s** (~47 expected), `max_interval_ms`
**89.35 → 39.64 s**, breaches **1.82 % → 0 %**.

⚠️ **Stated honestly: the breach-count comparison ALONE is underpowered** at
n = 147 (expected 2.67, p = 0.069). The powered tests are passes > 30 s (0 vs
4.59 expected, **p = 0.010**) and the probe rate itself.

The derived identity `interval = max(0, 30000 − pass_prev) + pass_curr` holds to
**≤ 182 ms over n = 990**, so it is a derivation and not a fit — which is what
lets cold start be excluded rigorously (pass #1 is 37–47 s but has no prior
interval and cannot breach). **A per-pass wall-clock BUDGET was considered and
rejected:** it caps the metric by skipping legs, making the *skipped* legs'
real latency worse. The fix removes work rather than hiding it.

### T.2 — Bybit position-mode resolver. Shipped INERT.
`src/runtime/bybit_position_mode.py` + 5 call sites, **empty allowlist**, wire
payload byte-for-byte unchanged and **asserted by a test**, not by inspection.
`positionIdx` names the **book**, never the order side — closing a long sends
`side="Sell"` and belongs to `positionIdx=1` — so callers pass the *position's*
direction and the reduce-only path inverts once, at the boundary. Four states,
never collapsed; `unresolved` sends no `positionIdx` so Bybit refuses rather than
acting on a guessed book. `bybit_1`'s `account_class` was **re-read live** from
`/api/bot/config` rather than inherited. The row's claimed "second defect" (the
half-open check behind the bar dedup) was found **already fixed** — verified in
code, not assumed.

### Trainer disk — the audit reached the OPPOSITE conclusion to the plan.
**Do not unpin.** The GC is pin-bound (confirmed, not inferred: `--min-age-days 0`
returns the *identical* candidate set). The plan's "41 manifests" does not
reproduce — the tool says `manifest_pins: 41, manifests_total: 76`; **41 is the
count of distinct PINS**, and only 25 manifests pin a non-canonical version. Of
those, `v520` backs the **live BTC advisory** head, `v530` the **SOL advisory**
head, `v521` the **ETH 15m head T.4 is about**: ~2.9 GB theoretically available
there against ~2.3 GB from housekeeping that risks nothing.

Took the housekeeping: **2.7 → 5.0 GB free (95 % → 89 %)**. Nothing evidential
moved, and the job **proved** it rather than asserting it (`datasets-out`,
`ml/experiments-runs`, `m20_exit_head` unchanged; GC report byte-identical).

### Phase 0 (0.2–0.5) + 2.5 — the retirement pass.
- **0.3:** `unwired-artifact-guard` ran `--self-test` **only** — the scan never
  ran, while its comment claimed a diff-scoping it did not have.
- **0.2:** fixed two blind spots in that guard (module-form imports invisible;
  `bin/` outside the runner corpus). **151 → 129: 15 % of the reported debt was
  never real.** Filing must cost something — so must counting.
- **0.4:** already collapsed; the live error was ours (below).
- **0.5:** 62.3 s for 50 guards, 33 under 0.5 s — **nothing qualifies for
  retirement.** A negative result, recorded rather than dropped.
- **2.5:** auto-merge relay moved off its single shared trigger file onto a
  per-request directory.

### The blocker cluster — `attach-ib-target` could never have worked.
Operator approved attaching the declared targets to the two target-naked
`ib_paper` positions. The MES apply (#10139) died:

```
Error 326: Unable to connect as the client id is already in use.
circuit breaker tripped … suppressed for 120s
{"action": "place_failed"}
```

`_attach` called `ib_client_for(cfg, readonly=False)` — the account's **execution**
clientId (497). IBKR refuses a duplicate rather than evicting, so with the trader
up (always) it could not connect. **No live harm, verified not assumed:** all
three clients `connected`, `consecutive_failures: 0`, `bot_running: true`,
`tick_age_seconds: 1.2`. The refusal is what protected the trader.

⚠️ **The shape is the finding.** The **dry run never builds a client**, so it
reports `state: ready` with all four refusals passed. Only the apply can falsify
it, and that is the path nobody runs in CI.

⚠️ **Third defect on this one action, by the third session to look.** #9920 died
`exit 127` (git-sync lag); #9922 on an `ImportError` for a symbol that never
existed (found and fixed 2026-08-18, § 5 of `S-SYSREV-TRADE-MECHANICS-2026-08-18.md`);
this one sat behind both. Each fix was correct and each revealed the next. The
causal doc gap: `docs/claude/system-actions.md`'s `flatten-ib-position` row
**states** the ops-clientId rule and the `attach-ib-target` row **did not** — now
fixed.

## Validation Performed
- **1.0:** live positive control on the deployed trader with the population
  stated and the underpowered comparison flagged as underpowered.
- **T.2:** 16 tests including the byte-for-byte inert assertion and the
  close-a-long-belongs-to-the-LONG-book inversion.
- **clientId fix:** falsified against the pre-fix tree — **2 failed → 2 pass**
  (`AttributeError` on the missing helper; `ib_client_for called without an
  explicit client_id`). The test asserts **the wiring as well as the band**: a
  correct helper never passed to the factory fixes nothing.
- **0.2/0.3:** guard self-test 8/8 → **13/13**.
- Guards **32 PASS / 0 FAIL / 18 skip** on the committed tree; all three required
  CI checks verified via `get_check_runs` before every merge.
- Trainer reclaim: byte-exact reconciliation (children 26.41 GB = parent
  26.41 GB, 55 entries).

## Documentation Updated
- `CLAUDE.md` — `IB_PROBE_CACHE_S` + `BYBIT_HEDGE_MODE_SYMBOLS` rows; corrected
  the `IB_PROBE_TIMEOUT_S` row (its "absorbs a one-off cold-start miss" framing
  is **measured false** in steady state); extended `/api/diag/ib_state`.
- `docs/claude/system-actions.md` — the `attach-ib-target` row now states the ops
  clientId, with why its silence was causal.
- `docs/claude/WORKPLAN-2026-08-21.md` — status column + session log + the two
  operator-decision blocks (07:0xZ and 11:1xZ) + the scheduled broker actions.
- `docs/claude/health-review-backlog.json` — 786 → **787** rows, arithmetic and
  duplicate-id asserted on every edit.

## Contradictions or Drift Found
1. **`unwired-artifact-guard`'s comment claimed a diff-scoping it did not have**,
   and it never ran its scan. Fixed (0.3).
2. **`attach-ib-target`'s system-actions row omitted the ops-clientId rule its
   sibling states.** Fixed. This is the drift that let the defect ship.
3. **Repo-wide folklore corrected: "all four required checks" — four checks RUN,
   THREE are required.** Source: `branch-protection-sync.yml:137`,
   `REQUIRED_CONTEXTS=["pytest-collect","pytest-run","guards"]`; `repo-inventory`
   is advisory. The fact had exactly one home and was still restated wrongly by
   me all session — because it was derived from `get_check_runs` returning four
   rows instead of from the source. That is 0.4's whole subject, and no amount of
   doc-collapsing prevents it.

## Risks and Follow-Ups
- **`BL-20260822-ATTACH-IB-TARGET-USES-TRADER-CLIENTID` is fixed but NOT live-verified.**
  The Sunday 2026-08-23 22:30Z MES apply is its positive control, and it is owed.
- The scheduled run's fired session **may have no MCP tools** (the trigger tool
  warned). Its Step 0 checks and stops rather than improvising — explicitly
  forbidding `curl https://api.github.com`, which the sandbox 403s into a
  clean-looking empty result.
- **MES's resting stop is 7516.5 vs a declared 7533.696 — $1,289.73.** #10081's
  subject, deliberately untouched.
- `IB_PROBE_CACHE_S` defers detection of a **mid-life** wedge on a socket that
  still reads connected by up to 60 s. A peer-close is unaffected, and
  `IB_FETCH_TIMEOUT_S` still bounds every request.

## Deferred Items
- **T.3 `slv_trend_1h`** — operator: **investigate, keep it LIVE.** My demote
  recommendation was **not** taken; do not flip `execution:`.
- **T.4 ETH 15m** — run the gate packet, **report, promote nothing.**
- **Research 2.1–2.4** — deferred behind the blocker cluster by operator order.
- **Trainer swapfile** (8 GB using 188 MB on a 5.8 GB-RAM box) — a proposal, not
  done: the box has OOM history (`BL-20260717-TRAINER-SINGLE-MANIFEST-OOM`).

## Next Recommended Sprint
The CRITICAL exit/protection cluster, in the operator's stated order (bugs before
research): `ict_scalp` has no take-profit close path · IB protection is
price-blind · 22 of 34 open trades have no decision-driven exit · workplan item
1.1. Then T.3's investigation and T.4's packet, both report-only.

## Wrap-Up Check
- [x] Four PRs merged, zero open; merge protocol run by hand each time (board
      tail **proved** with a short page, claim posted, merged on a verified
      `get_check_runs` read, release posted).
- [x] `doc-freshness` run incl. step 5; `canonical-doc-coherence` 5/5 PASS.
- [x] Backlog arithmetic + duplicate-id asserted.
- [x] Sunday broker actions scheduled durably (`trig_014S3NAzMKy2Ac2AM2GgyRE5`).
- [x] **Phase 0's own bar met: no new guard, no new doc, no backlog row *about*
      the backlog.** The one row filed is a genuine live defect.

## Postscript — one class, four instances
Four of this session's incidents are the same failure: **an operation whose
failure is indistinguishable from its success.**

1. `pgrep -af … | head -5 && HEAVY=1` — `head` exits 0 on empty input, so the
   guard fired on **no** match and skipped `git gc` on a false positive.
2. `curl https://api.github.com … || echo '{}'` — the sandbox's 403 becomes a
   clean "no check runs visible".
3. `check_suite.completed` reading green while `pytest-run` had ten minutes left
   — **four consecutive PRs**. It is not an occasional race; it is what that
   event means.
4. **`attach-ib-target`'s dry run reporting `state: ready` on an apply path that
   could not connect** — and this one is the worst of the four, because the
   *success report itself* is the thing that cannot fail.

Two errors of my own belong in the same list, both caught and corrected in
session: I stated `.git` as the missing ~5 GB **as if measured** (it was 339 MB;
the answer is `.venv` at 5.4 GB), and my first auto-merge glob matched a README
and **armed auto-merge on my own PR** — which, in fairness, proved criterion 3's
trigger half for free.
