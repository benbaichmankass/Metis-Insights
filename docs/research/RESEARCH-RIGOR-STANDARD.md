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

## Backtest history first — a "wait for forward accrual" gate is a phantom unless the data genuinely can't be reconstructed

**The default for any discovery, validation, or calibration question is: run it
on reconstructed history NOW.** Do not gate a research decision on "wait N
weeks/days for the live producer to accrue rows" unless you have positively
established that the data cannot be reconstructed from history. A forward-soak
framing that could have been a one-shot backfill/backtest is a **phantom gate** —
it burns real weeks on a decision that was answerable in minutes, and it is the
single most expensive recurring anti-pattern in this program (M30, M28-P4, the
allocator, the exit levers all hit it).

**The classification test — before you ever write "waiting for data to
accrue", answer this:**

> *Can the decision-time state this study needs be reconstructed as-of each
> historical date from data I already have (or can fetch history for) —
> point-in-time, no look-ahead?*

- **YES → it's a phantom gate. Backfill/backtest it now.** Reconstruct the
  point-in-time series (a `*_backfill.py` that walks dated history), or run the
  question through the backtest engine, and get the decision this session. Log
  the result (edge or null) per "Honest negatives are recorded".
- **NO → it's a genuine forward soak, and only for the specific reason that
  makes it irreducible.** The legitimate reasons are narrow: (a) the row is a
  *record of a live event that only exists once it happens* (an actual live
  fill, a real broker-truth reconciliation, a live A/B outcome under the real
  execution path); (b) the feature is a *live-only artifact* with no offline
  analogue (true live latency, real slippage on real orders); (c) reconstructing
  it would itself inject look-ahead you can't strip. "The producer writes one
  row per tick and only started last week" is **not** a genuine reason if the
  same rows can be recomputed from candles/config/FRED/exchange history.

**When a genuine forward soak IS required, still say why in the same breath** —
name the irreducible reason (a/b/c above), and check whether a *shadow/annotate*
soak on reconstructed history can answer the design question while the forward
soak accrues the live-outcome confirmation in parallel. Never let a genuine
live-confirmation soak block a decision a backtest could already make.

**Worked examples (both were phantom gates, both corrected):**

- **M30 discovery** — the conditional-edge/importance study was framed as
  starved by the ~376-row live journal. It wasn't data-gated: the backtest
  engine (`build_backtest_panel.py`) replays the same decision-time features +
  native excursion outcomes over large-N history, so the C2 analyzer ran on 282
  `ict_scalp` trades in one session (Study 7, powered NULL) instead of waiting
  months for the journal to fill.
- **M28-P4 value gate** — recorded as "waiting ~weeks for the FRED producer to
  accrue point-in-time snapshots." The `valuation_snapshot_backfill.py` backfill
  (the value analogue of `backfill-shadow-predictions`) reconstructs FRED's full
  dated history in one shot; the committed
  `comms/macro/valuation_snapshots_backfill.jsonl` (21 yr, 10,125 rows) ran the
  gate in minutes → clean NULL (`M28-P4-value-gate-run-2026-07-27.md`), the
  honest baseline the conditioners must beat — all on history, no wait.

The reusable move both share: **a `*_backfill.py` that reconstructs the
decision-time series from full history, committed as a `.jsonl`, then the
existing scorer/analyzer run over it unchanged.** Reach for that before you ever
reach for "let it soak."

## The measured population must match the live-traded population

A verdict is decision-grade only if the trades measured are the trades that actually happen.
Enumerate every live gate on the subject — config levers **and** every `regime_policy.yaml`
cell, 1-D *and* 2-D — and confirm the harness models each, or name what it omits.
`fidelity: faithful` is load-bearing; never author or retire a live gate off an
`approximate` row.

Two 2026-07-30 instances, hours apart, both of which reported success:

- `side_filter` was not forwarded to the harnesses, so two live **short-only** strategies
  were measured on both legs. It did not fail closed — the row degraded to `approximate`,
  which is common enough to read as noise.
- The regime harness has **no vol axis at all**, so a 1-D cell re-audit pools every vol
  state — including states a 2-D `trend_vol` cell already gates live. A proposal that passed
  every stability gate was **withheld** for this reason
  (`BL-20260730-2D-VOL-CELLS-UNAUDITABLE`).

## Sample size, and where the edge actually sits

- **Treat a cell under ~20 trades as unmeasured, and n=0 as *cosmetic*** — zero trades is not
  evidence a gate works. Widening `squeeze_breakout_4h` from 730d (n=28) to 2900d (n=135)
  **reversed both** flagged cells *in opposite directions*: long −2.24 (n=3) → +11.60 (n=29);
  short +1.15 (n=6) → −3.40 (n=26).
- **Always report the ex-max-fold figure alongside the pooled one.**
  `htf_pullback_trend_2h` short pooled **+7.89R** but was **−3.70R ex-fold-3** — the sign
  flipped, so the edge was a single-period artifact. `squeeze_breakout_4h` short pooled
  −3.40R and held at **−0.77R ex-fold-3** — the sign survived. A pooled number that inverts
  without its biggest fold is not a finding.

## Restrictive and permissive changes need different evidence

Turning a leg **off** is restrictive and can be justified *affirmatively* (a stable drag).
Turning one **on** is permissive, and *"we cannot justify keeping it off"* is **not**
*"we have evidence it makes money."* Don't flatten a two-sided result into a single change —
that smuggles the weak half in on the strong half's evidence. Propose the halves separately,
with their evidential strength stated.

## Verify offline before spending a runner

If a check can run against already-committed data, run it locally first. A local join test
caught a wrong modeled release lag (off by 2 days) before a runner was spent — and would have
caught it even had the run come back green, because a green run with a partial join looks like
thin data rather than a systematic miss.

Corollary: know what the sandbox **cannot** answer. Most data hosts are firewalled here, so
"is this series id real?" is not settleable locally — and guessing one into config is how two
EIA `dnav` codes ended up in a FRED-series config and 404'd for a producer's entire life. Use
a **runner-side probe that reports and writes nothing**, and make it distinguish *wrong id*
from *right id, no data* — those need different fixes. A probe must never try candidates and
adopt whichever resolves.

## An impossibility claim gets MORE scepticism than a success claim

Before writing that something *cannot be measured / is not replayable / needs new tooling*,
check [`RESEARCH-CAPABILITY-INDEX.md`](./RESEARCH-CAPABILITY-INDEX.md) and grep
`scripts/research/`. Say which tool you checked. A wrong "measured: OK" wastes a decision; a
wrong "this cannot be measured" **closes off the work**, because nobody re-checks a dead end
and the claim propagates as settled fact. A code comment or constant name asserting
impossibility is scoped to its own module until proven otherwise — a `_UNREPLAYABLE` set
meaning "this harness doesn't model it" was read as "the system cannot do it", and six live
gates were reported permanently un-auditable while the tool to measure them already existed.

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
