# R5: the standing soak-book grading rule (registered before the run)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Rule id:** `RULE-R5-SOAK-BOOK-GRADE-v1` · **Registered:** 2026-09-26, in the
same commit as the first measurement (this is the FIRST run of this rule; the
rule doc precedes the record within the commit, the same order R3 documents
for itself). **Checklist row:** R5. **Tier:** 1. This rule is an observability
grading pass. It changes no roster, no config, no execution field and no
evidence record. Any `tweak`/`kill` disposition it produces is a PROPOSAL,
filed to `docs/claude/work/pipeline/` — acting on one is Tier-3 and stays the
operator's.

## The question

Per Stage-1 soak leg — every `(account, strategy)` on `bybit_1` and
`alpaca_paper` — are its MECHANICS flowing (intents reach an order/ticket) and
is its COST FIDELITY consistent with what its Stage-0 evidence record
assumed? **Not expectancy** — root `CLAUDE.md` § "The promotion ladder": *"a
live or paper book never establishes edge. It checks mechanics and cost."*
Stage 1 is not asked here whether it is profitable, only whether it is
working and whether its cost model is honest.

## The population

Every `(account_id, strategy)` pair on `config/accounts.yaml`'s `bybit_1` and
`alpaca_paper` rosters, **counted live at run time** by
`scripts/ops/soak_book_grade.py::enumerate_soak_legs`, never quoted from a
prior count (the roster moves — see root `CLAUDE.md`'s own warning about
`accounts.yaml::symbols` counts drifting between edits).

## The measured quantities, both reused rather than re-derived

- **Mechanics** — `scripts/ops/leg_flow_report.build_report()` /
  `leg_flow_detector.assess_leg()` (checklist row E18): counts actionable
  `{strategy}_eval` intents against orders/tickets received in the
  measurement window (default 168h). States: `unreadable` / `no_intents` /
  `starved` / `flowing`, never collapsed into each other.
- **Cost fidelity** — `scripts/research/r3_cost_fidelity.py::grade()` /
  `run()` (checklist row R3, `RULE-R3-GATE1-COST-FIDELITY-v1`): realized
  round-trip slippage in bps against the leg's `comms/strategy_evidence/`
  record, graded per (leg, account) cell. This rule calls R3's own `grade()`
  for a cell R3 did not measure (an empty sample), rather than inventing a
  second floor/CI/threshold implementation.

## The verdicts (dispositions)

| disposition | condition |
|---|---|
| `insufficient-data` | mechanics `unreadable`, or the cost-fidelity diag pull failed this run, or mechanics `no_intents` (nothing to grade yet — NOT evidence of health either), or cost fidelity `insufficient_n`/`inconclusive`. Names the n still needed (mechanics) or the sample still needed (cost). |
| `kill` | mechanics `starved`: intents are produced and **none** reach an order or ticket. A soak leg exists to prove the pipeline works end to end; a starved leg tests nothing regardless of what its cost fidelity reads. |
| `tweak` | mechanics `flowing` but cost fidelity `divergent` (realized cost exceeds the modelled assumption + tolerance) or `no_record` (no Stage-0 evidence record exists to grade against). Names the concrete hypothesis: re-price the modelled default, or author the missing record. |
| `healthy` | mechanics `flowing` and cost fidelity `consistent`. |

A leg this run could not measure is `insufficient-data`, never folded into
`healthy` — the same non-collapse rule `leg_flow_detector` and
`research_result.py` already enforce (`docs/CLAUDE-RULES-CANONICAL.md` §
"Collapsed states").

## What this rule does NOT do

- It does not grade expectancy, win rate, or R multiples — see "NOT
  expectancy" above. A leg graded `healthy` here has said nothing about
  whether it is a good strategy.
- It does not apply any `tweak`/`kill` disposition to a live config, roster,
  or evidence record. Filing is `scripts/ops/pipeline.py intent=new`;
  application is a separate, Tier-3, operator-approved act.
- It does not replace R3's own standing cost-fidelity measurement — this rule
  CALLS R3's grading function per leg; R3's own doc and corpus remain the
  authority on the venue-level and cross-account cost-fidelity picture.

## Cadence and landing

Weekly, via `.github/workflows/soak-book-grade-weekly.yml`
(`workflow_dispatch` + cron), landing a full per-leg record under
`comms/research/soak_book_grade/<run-date>.json` and a pointer/verdict record
under `research/results/_unattributed/<run_id>.jsonl` (E5's result contract;
`research_unit: null` because this is a standing checklist-row pass, not a
`research/queue/` dispatched unit — see `research_result.py`'s own docstring:
*"a manually/schedule-fired run still measured something over some
population"*).

## Re-run

```
python3 scripts/ops/soak_book_grade.py --window-hours 168 \
    --out comms/research/soak_book_grade/<date>.json
```
