# `research/templates/` — pre-registered sweep families the queue refills itself from

Operator, 2026-09-28 (relayed by the manager): *"The research queue should be
running 24/7 with or without Claude, we need to make sure the queue has enough
tests to run and keep replenishing it."*

Each file here is ONE family. It fixes, **before any unit is generated**: the
question, the decision rule (id, statistic, rule text), the power block, the
cost stack, the runner and its inputs. `scripts/research/queue_replenish.py`
expands the family's `grid:` (symbol x timeframe x geometry) into concrete
`research/queue/RQ-<date>-NNN.yaml` units, dedupes against every unit that
already exists (done, queued, blocked — by `generated.key`), and keeps at least
`--target` runnable units queued, drawing families by the operator's priority
weights (`weight:`). Nothing here is a question a session invented after
seeing a result: the rule is the family's, registered once, and every unit
carries `decision_rule.registered_before_run: true` because the file that
generated it predates the run by construction.

A generated unit self-lands ONLY because `check_pr_landing.py` can re-run the
generator at the PR's merge-base and get the same bytes (`--verify`). Change a
template and the next generation is a NEW family key — the units already
generated are untouched and their rules stay the ones registered for them.

`weight:` is a share of the deficit, not a count: 30/25/20/15/10 means that of
every 20 units generated, 6 are prop-fit, 5 exit-lever, 4 bracket, 3
walk-forward, 2 macro (largest-remainder allocation, deterministic).
