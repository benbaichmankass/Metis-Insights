# Sprint Log: S-M20-COVERAGE-AND-VERIFY-2026-08-09

## Date Range
- Start: 2026-08-09
- End: 2026-08-09

## Objective
- Primary goal: discharge the operator's conditional Tier-3 approval of the
  `trend_donchian` trail-decay retune by **testing it**, and land the follow-ups
  that test exposed.
- Secondary goals: make M20's done-condition roll-up state the truth (bundled
  rows, a live-but-failing lever, a duplicate leg); codify the operator's
  "always verify" directive as a canonical rule.

## Tier
- **Tier 1** throughout. Docs, research tooling, CI-adjacent guards, one pure
  additive function in `src/runtime/execution_costs.py`.
- Justification: no `config/` write, no order path, no DB write, no VM mutation
  (the trainer was used read-only via the diag relay). The Tier-3 item in scope
  — `config/strategies.yaml::trail_decay_tight_mult` — was **NOT changed**;
  verified in `main` after every merge rather than assumed.

## Starting Context
- Continues [`S-M20-TREND-ENGINE-RETIREMENT-2026-08-09`](./S-M20-TREND-ENGINE-RETIREMENT-2026-08-09.md)
  (PR #8660), which proposed the retune and left it for the operator.
- Operator approved option A **conditional on testing it before implementing**.
- Known risk at start: the sweep that produced the proposal could not itself
  discharge the condition — it reports aggregates, not the sample beneath them.

## Repo State Checked
- Branch/commits: `main` `0d6434c` → `814f9a1`; PRs #8685, #8687, #8691, #8693.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/ARCHITECTURE-CANONICAL.md`, `ROADMAP.md`.
- Config verified by reading the field, not the prose: `trend_donchian`
  (`trail_decay_tight_mult: 2.5`), `xauusd_trend_1h` (`enabled: false`,
  `trail_mult: 4.0`), `mgc_trend_1h` (`execution: shadow`).

## Work Completed

### The retune was tested and WITHDRAWN (the headline)

New instrument `scripts/research/m20_trail_attribution.py` supplies the
denominator the sweep never reported. *POPULATION: BTCUSDT 15m→1h,
2023-01-01 → 2026-07-22, 250 trades (175 IS / 75 OOS at split 2025-07-01),
config-exact, CLI cost basis. Trainer-diag #8672 / #8676 / #8677.*

| finding | measurement |
|---|---|
| denominator | **17 of 250** trades arm the lever (6.8%); **3 of 75** OOS |
| where the lever acts | tight 2.0 is **worse by 1.8395R** — +0.2R on 14, −4.6395R on 3 |
| the +0.2R | mechanical: `(2.5−2.0)×ATR ÷ (2.5×ATR)`; the instrument reproduces the engine's arithmetic |
| the apparent edge | +0.2960R aggregate is **sequencing**: +2.1355R from 3 trades present in only one book (June 2023) |
| arm sensitivity | negative at 3.0 / 4.0 / 5.0 / 6.49; only 8.0 positive and `TOO_THIN` (n=7) |

**A live soak could not have tested this.** The arm is 6.49R; 2026's max peak-R
is 4.593 over 35 trades. `exit_lever_soak` would accrue ZERO rows and read
*clean* — a green light from a measurement that never ran.

Verdict: **hold at 2.5.** Option B (remove) is also unsupported — vs OFF the
lever is +9.53R at identical full-tape maxDD.

### The maxDD reconciliation exposed a defect in the attribution tool (#8685)

The gate number came from a date-restricted run; the first attribution pass
measured the full tape and disagreed. The restriction hypothesis was **wrong**
(both bases agree to 4dp). The cause was **cost basis**:
`scripts/backtest_trend.py` resolves symbol-specific slippage/funding into
module globals **only on the CLI path**, so an in-process `run_backtest()`
caller runs fee-only — maxDD 13.2595 vs 14.7968 on an identical 175-trade book.

**The in-process default was NOT changed.** `git log -p` showed it is deliberate
and load-bearing: #8468 keeps the confidence sweep, the ML recorder and the M30
panel bridge byte-identical. The planned "fix" would have silently changed three
named consumers. What shipped instead: `execution_costs.resolve_cost_policy`
(one definition of *unset ⇒ venue, explicit wins*, with `None` and `0.0` kept
distinct), `exit_head_replay` applying it explicitly, and a test asserting a
freshly-imported harness is *still* fee-only so a future change trips a tripwire.

### RULE ONE — always verify (#8691)

Operator directive. Four scoped verify rules already existed and none stated the
general duty, so each new shape of the mistake got its own entry. Stated once,
first, cross-referencing the four rather than duplicating them. Examples are
this session's own, all dated.

### Coverage matrix made honest (#8687, #8693)

- New status **`shipped_gate_failed`** — live, gate later failed, operator holds.
  Neither neighbour fits: `honest_negative` implies not-live (the original bug),
  `shipped` asserts a validation that no longer reproduces.
- Bundled rows **exploded to one leg each** (23 → 50 rows, 184 → 400 cells).
  Per-leg statuses assigned only from each ref's explicit wording; silent or
  ERRORED/TIMED-OUT → `pending`.
- A **duplicate leg** (`xauusd_trend_1h` in two rows with opposite content)
  resolved from config, merged per-cell.

## Validation Performed
- 8 new cost-basis tests; **149 passed** across exit-head / trend-harness /
  lever / research suites.
- Attribution instrument validated with **three controls**: `MEASURABLE` where
  the lever fires, `INERT` on an unreachable arm, `INERT` on tight-vs-itself.
- Cost-basis safety claim **asserted, not argued**: switching basis does not
  move the trades (same entry times, exit bars, outcomes, R).
- Explosion asserts row count + leg-uniqueness; 14 spot-checks vs the refs;
  rerun exits non-zero leaving the file byte-identical.
- `config/strategies.yaml` re-verified unchanged in `main` after each merge.

## Documentation Updated
- `docs/research/M20-trail-decay-resweep-2026-08-09.md` — superseding section.
- `docs/CLAUDE-RULES-CANONICAL.md` + `CLAUDE.md` — RULE ONE.
- `docs/research/exit-refinement-coverage.json` — new status + explosion.
- `docs/research/RESEARCH-CAPABILITY-INDEX.md` — 2 new tools routed.
- `ROADMAP.md` — M20 row: the Tier-3 decision, and the corrected coverage figures.

## Contradictions or Drift Found
- **`ROADMAP.md` still said the Tier-3 call was OPEN** ("Operator picks retune /
  remove / hold") after it had been decided, tested and withdrawn — zero
  mentions of the outcome in the centralized record. Found by this session's own
  `doc-freshness` pass and fixed here. This is exactly the failure the skill's
  step 5 names: a decision lands in a sprint log and a research memo while
  ROADMAP never learns.
- **A live lever read as a closed negative** (`trail_decay` `honest_negative`
  while declared live since #6273) — fixed in #8660, then found still wrong
  (`passed_unshipped`) and fixed properly in #8687.

## Risks and Follow-Ups
- `BL-20260809-COLLAPSED-STATES-NO-CANONICAL-HOME` (open) — 5 instances in 2
  days across 2 sessions; no canonical home. Operator call.
- M20 remaining: `exit_head_ml` (equities/native-futures/`ict_scalp`) and
  `exit_ladder` (8 `ict_scalp` legs, `blocked:no_harness_levers`).

## CORRECTION — this log shipped a defect it recorded as validated

**Written after the fact. The "Validation Performed" section above is true but
insufficient, and a future session should read it as a caution, not a model.**

The explosion published **"304/376 = 80.9% across 47 live legs."** Two of those
cells **did not exist**: `squeeze_breakout_4h` and `fvg_range_15m` were missing
the `vol_trail` column outright — not `pending`, absent. The roll-up did
`r.get(col)` per row × column and counted the `None` from a **missing key** as
if it were a status. Counted 400 cells; 398 existed.

**Caught by another session**, not by me or by CI — corrected to **308/376 =
81.9%** on a complete denominator, with both gaps filled by AST-checking the
harness CLIs (`squeeze` has `--trail-mult` but no `--trail-vol-above-pctl` →
`blocked:no_harness_levers`; `fvg` has no `--trail-mult` at all → `n/a`).

Three things make this the sharpest item in this log:

1. It is **the collapsed-states class** — "missing" indistinguishable from
   "needs no verdict" — in the artifact that measures M20's own done-condition.
   This session filed the backlog row naming that class, in the same PR.
2. It breaks **RULE ONE #2** (*a negative result needs a denominator*), which
   this same session had written and merged hours earlier.
3. **The roll-up's own output printed the evidence.** The status table listed
   `None 3`, then `None 2`. Those were the missing cells, on screen, in the
   author's own output. Not a check skipped — a signal displayed and read past.

Enforcement gap filed: `BL-20260810-ROLLUP-DENOMINATOR-UNASSERTED`. The
`collapsed-state-guard` added the same day polices declared three-state
contracts **in code**; this is a data artifact with a missing field and an
aggregate that buckets the absence — same class, different substrate, outside
its scope.

**A second correction, also from another session.** This session's handoff
prompt told the next one to *"budget for honest negatives"* on the `ict_scalp`
ladder sweep, citing the fleet-wide banking prior. They refused to pre-write
verdicts from it, correctly: the prior's stated mechanism is *"strategies whose
edge IS the fat right tail"*, and `ict_scalp` has none — every leg is a fixed
`tp_at_r 1.5` bracket. **A prior accepted for the wrong reason is not a prior.**
Passing it forward unchecked would have biased their sweep toward the answer
this session expected.

**And the handoff named a branch with an open, unmerged PR** — the one carrying
the defect above. `session-handoff` § "No loose ends" already forbids that
(binding, operator-directed 2026-08-04: *the foundation must be ready before
anyone is told to stand on it*). Auto-merge was armed, so the letter was met;
the branch was still not a sound base, which is what the rule is for. The
successor joined that branch **deliberately and correctly** — a fresh branch off
`main` could not have fixed an in-flight defect, because `main` did not yet have
the change.

## The reusable lesson: my own verification was the thing that failed

Four times, and never on a hard check — always a one-liner that looked
conclusive. `grep 'baseline\['` missed `baseline.get(`. A 900-char regex window
missed `enabled:`/`execution:`. A bundle scan keyed on `/` and `fleet` missed
brace notation. A `startswith()` matched an extra row.

The sharpest came *after* RULE ONE merged. CI's pinned ruff found one `E702` in
my file; I had run a controlled test comparing `ruff check .` totals between
`main` and my branch, seen `8141` both sides, and concluded "version artifact" —
then wrote that into a PR body. **Comparing totals is not comparing sets.** My
local ruff 0.16.2 does not flag `E702` at all, so its silence carried no
information, and I quoted it as evidence.

Every one of these was invisible to re-reading and caught by an assertion, an
arithmetic cross-check, or CI. That is the argument for RULE ONE #4 — and for
writing the assertion even when the change looks too small to need one.
