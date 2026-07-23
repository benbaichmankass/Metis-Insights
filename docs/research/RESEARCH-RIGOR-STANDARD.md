# Research Rigor Standard

Skill-adjacent reference doc (same tier as `docs/strategy-tuning.md`,
`docs/news_layer.md` — **not** elevated into
`docs/CLAUDE-RULES-CANONICAL.md`'s Document Priority hierarchy, so it does
not pull in the `canonical-doc-coherence` CI check). Binding on any research
done under [`research-driver`](../../.claude/skills/research-driver/SKILL.md)
and any domain skill born from it (Generation-Discipline Rule 1).

Consolidates the research-rigor principles that were previously
duplicated/scattered across `exit-refinement` and `backtesting`, so a new
skill can reference this doc instead of re-deriving or re-copying them.

## Walk-forward / out-of-sample discipline

No in-sample-only claims. Any parameter/lever/model verdict that ships
must pass on OUT-OF-SAMPLE data, not just the fitting window. Purged
walk-forward (time folds, embargo, purge on the trade's last bar) is the
standard where the harness supports it — see `exit-refinement`'s P4 for
the concrete shape.

## Config-exact harnesses

A sweep or backtest runs the leg's ACTUAL live YAML params (`strategies.yaml`,
`accounts.yaml`, the relevant `config/*.yaml`), never a harness default that
happens to be convenient. A result computed against parameters the live
system doesn't actually run is not evidence about the live system.

## Truncation-honest counterfactuals

No barrier re-simulation. Exit/outcome values come from the observed close
mark, never a re-simulated "what if the barrier had been X" that wasn't
actually reached. This was the T0.4 lesson (`exit-refinement` § Hard rules)
and generalizes to any counterfactual evidence read.

## Honest negatives are recorded, never silently skipped

A sweep, experiment, or research initiative that fails its gate is a
completed deliverable, not a non-event. Record it (coverage matrix cell,
backlog item, or `ROADMAP.md`/sprint-log entry per `research-driver` Step 6)
with the reason — don't drop it and don't quietly retry until something
passes.

## Real / paper / prop are never blended

Any evidence read — performance stats, PnL, win rate, drawdown — keeps the
three funding classes (real money, paper, prop) strictly separate. This is
the same "never blended" contract that governs the dashboard and the bot's
own `/performance`/`/stats` endpoints; research evidence is held to the
same standard so a finding can't be an artifact of mixing funding classes.

## In-distribution guards on any shared-monitor scorer

When multiple strategy legs share a monitor hook (e.g. the donchian
family), a scorer/head evaluates only the legs it was actually trained on
— never silently scores an out-of-family leg (the IWM incident, #6201,
`exit-refinement` § Hard rules). Applies to any shared-infrastructure
research artifact, not just exit heads.

## Closed bars only in live scorers

Live evaluation reads only fully-closed bars — never a partial/forming
bar — so live scoring matches how the offline training data was
constructed (live == train; #6207).

---

Room to extend as new domain skills get codified and want to inherit
rather than restate. Add a section here when a rigor principle recurs
across ≥2 skills instead of copying it into each.
