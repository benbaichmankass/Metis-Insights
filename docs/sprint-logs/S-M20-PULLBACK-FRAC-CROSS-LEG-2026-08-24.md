# Sprint Log: S-M20-PULLBACK-FRAC-CROSS-LEG-2026-08-24

## Date Range
- Start: 2026-08-24 ~15:50Z
- End: 2026-08-24 ~19:40Z

## Objective
- Primary goal: continue the M20 bracket-expectations + candle-feed workstream —
  (1) re-adjudicate the Dukascopy probe with the fixed matcher, (2) obtain the
  no-target book at the live stop, (3) build and run the `pullback_frac`
  cross-leg test at operator-approved full scope.
- Secondary goals: write up the Tier-3 `_TP_SENTINEL_CAP_PCT` venue-scope
  question (operator-requested mid-session).

## Tier
- **Tier 1** throughout.
- Justification: research tooling, workflows and documents only. No `src/`
  runtime change, no `config/` change, no order path, no VM action, no model
  promotion, no leg demotion. The one Tier-3 item produced is a **proposal
  document**, not a change.

## Starting Context
- Active roadmap items: M20 (exit/bracket work), M39 (opened concurrently by
  the `/system-review` session).
- Prior sprint reference: the predecessor session on
  `claude/bracket-expectations-exit-ctjaiq` (PRs #10198–#10226).
- Concurrent session: `/system-review` (#10223, #10232, #10234). `ROADMAP.md`
  and the three review backlogs were **yielded to it** for the whole working
  period; it posted DONE and its PRs merged, after which those files were
  picked back up at session end (this log + the backlog entries below).

## Repo State Checked
- `main` at start: `dd5955d` (confirmed #10226 had landed, as the handoff asked).
- `main` at end: `4c04075`.
- Coordination board #6927 read to a **proven tail** (short page of 2 at
  `perPage=10`) before the first substantive call, per the board's own rule
  that a full page is not proof of the end.

## Files and Systems Inspected
- `scripts/research/{bracket_reachability_audit,e35_bracket_geometry_sweep,
  e35_corpus_extract,e35_shard_plan,pullback_frac_cross_leg_scope,
  m20_fleet_exit_sweep,target_reachability_report}.py`
- `docs/research/e35-bracket-corpus.jsonl` (2204 rows), `scripts/backtest_pullback.py`
- `config/strategies.yaml`, `config/accounts.yaml` (read-only)
- `.github/workflows/{dukascopy-coverage-probe,e35-bracket-sweep}.yml`
- Dukascopy instrument catalogue (1388 entries) via probe run `32748059443`

## Work Completed
- **#10227** — `bracket_reachability_audit` gains a third baseline rung
  (`baseline_basis`: `row` / `base_block` / `absent`) + the Dukascopy
  adjudication doc.
- **#10230** — the `pullback_frac` cross-leg sweep driver + workflow, and
  `docs/design/tp-sentinel-cap-venue-scope-PROPOSAL.md`.
- **#10231** — the sweep's verdict now reaches the job log (`tee -a`).
- **#10233** — proxy-spelling fix; `resolve_data` tuple unpack corrected.
- **#10235** — `docs/research/pullback-frac-cross-leg-2026-08-24.md`, the result.

## Validation Performed
- `bracket_reachability_audit --selftest`: **22 → 33 pass**, and on the real
  corpus exit 0 with **zero implication violations**.
- `pullback_frac_cross_leg_sweep --selftest`: **47 pass** (39 then 47).
- Sweep run **`32767426410`**: 21/21 jobs green, **19 of 19 legs reported**.
- `run_guards.py` exit code captured **directly** on every push (never through
  a pipeline — the predecessor's recorded `| tail && git push` mistake).
- Diff-scoped guards (`diagnostic-provenance`, `api-tier-policy`,
  `test-schema-fidelity`) run against a **real `git diff origin/main`**, after
  a local green proved not to cover them.

## Documentation Updated
- `docs/research/dukascopy-coverage-adjudication-2026-08-24.md` (new)
- `docs/research/pullback-frac-cross-leg-2026-08-24.md` (new)
- `docs/design/tp-sentinel-cap-venue-scope-PROPOSAL.md` (new)
- `docs/research/bracket-target-reachability-2026-08-24.md` §7/§8 corrected
- `docs/research/RESEARCH-CAPABILITY-INDEX.md`, `docs/github-actions-workflows.md`
- `docs/claude/health-review-backlog.json` — 4 items filed (at session end,
  after the `/system-review` yield lifted)

## Contradictions or Drift Found
1. **`atr_stop_mult: 2.5` "absent from the joint grid"** — already corrected by
   the predecessor; its replacement proposal ("~one run per leg") is **also
   withdrawn**, because the book was already in the corpus. Corrected in the
   source doc and the capability index.
2. **`e35_shard_plan.py` cannot plan on a fresh CI checkout** — measured
   `0 job(s)`, exit 1; `e35-bracket-sweep.yml` has **never run**. Filed.
3. **`_TP_SENTINEL_CAP_PCT` duplicated in five files.** Filed.

## Risks and Follow-Ups
- The `pullback_frac` result is **not** a per-leg tuning result: one
  full-history run per cell, **no walk-forward, no IS/OOS**. A per-leg change
  is Tier-3 and needs evidence this sweep does not produce.
- 2 of 19 legs ride a **proxy** series (`MGC→GC_F`, `MHG→HG_F`), flagged per leg.
- The Tier-3 cap-scope proposal sits with the operator; its load-bearing
  unknown (does Breakout impose a ~10% limit at all?) is unresolved by design.

## Deferred Items
- `e35_shard_plan` fix — drafted, deliberately not bundled (another sweep's tool).
- `_TP_SENTINEL_CAP_PCT` consolidation — not bundled with the geometry proposal.
- A Dukascopy **span** probe (existence ≠ span, and span is the actual question).

## Next Recommended Sprint
Either (a) the `e35_shard_plan` fix + a first real `e35-bracket-sweep` run, or
(b) a Dukascopy span probe to decide the 1h-shortfall lane. Both are small and
unblock larger work.

## Wrap-Up Check
- ⚠️ **Three of my own defects were found only by RUNNING the tool**, and all
  three were silent: a verdict written where its only consumer cannot read it;
  two legs leaving the population on **green** jobs; and a local guard green
  that covered an empty population. Recorded because each renders identically
  to success.
- ⚠️ **A probe of mine returned zero and was wrong** (read a per-strategy
  `accounts:` key that is empty on every leg — routing is account-side). It
  would have retired the cap-scope item as a non-issue.
- **Nothing applied.** `config/strategies.yaml` untouched, no model promoted,
  no leg demoted, no order path modified, PR #10174 / the deploy / the
  `ib_paper` positions untouched.
