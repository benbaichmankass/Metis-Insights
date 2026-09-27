# RQ-20260927-003 — exit-head-for-prop-legs feasibility look: a fixed bug, and still no verdict

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Lane: dispatched task, branch `claude/w6-prop-o2-exit-head`. Tier-1 —
> offline research plus a driver bug fix. **No config touched.** Wiring
> `exit_head_model` onto either prop leg remains Tier-3 and out of scope
> here, same as the unit itself declares.

## 0. What this answers, and what it does not

[`research/queue/RQ-20260927-003.yaml`](../../../research/queue/RQ-20260927-003.yaml)
asks a SCOPING question (not a promotion question, per its own
`why_not_inferential`): would scoring `trend_donchian_sol_prop` /
`trend_donchian_eth_prop`'s own trades (config-exact geometry: `trail_mult
3.5`, `tp_r 6.0`) against the already-published `exit-head-donchian-1h-v1`
artifact (no retrain) show enough signal to justify a real per-leg retrain +
walk-forward, the way `RQ-20260927-001`/`-002` did for the flagship BTCUSDT
leg?

**This session does not answer that question.** It found and fixed a real
bug that had already invalidated the two prior attempts, and then hit a
second, separate problem (raw compute cost on the shared trainer) that a
correct driver still could not clear inside a practical dispatch window.
Both are documented below because leaving either unstated would misrepresent
what actually happened as "still just slow," when one of the two causes is
now fixed and confirmed, and the other is a new, filed, open question.

**recovered_R and swap_saved were NOT measured for either leg this session.**
Any number attached to this unit from before this session (there are none —
both prior attempts produced `producer_failed`) or invented for it now would
be fabricated. Read the "recommendation" section (§4) for what to do next.

## 1. Session 1's `producer_failed` — landed, and its stated cause was WRONG

The unit's own `result:` field (before this session touched it) already
recorded a first `producer_failed`: both legs dispatched in one sequential
`workflow_dispatch` SSH session, cancelled at exactly the workflow's
`timeout-minutes: 60`. That record's `root_cause candidate` was explicitly
marked **NOT confirmed**: *"a cold candle fetch is the leading (unconfirmed)
suspect."*

It was the wrong candidate. The real cause (§2) is a code bug, not a data
warm-up cost — and had session 1 re-dispatched a single leg without fixing
it (its own stated scoped follow-up), it would have hit the identical wall
for the identical reason, just with less wall-clock wasted per attempt.

## 2. The confirmed bug: the driver never applied the resolver's own resample flag

`scripts/research/exit_head_prop_swap_feasibility.py` resolves candle data
via `backtest_data_source.resolve_or_refuse(symbol, timeframe, None)`.
Neither `SOLUSDT` nor `ETHUSDT` has a native `1h` CSV on the trainer — only
`*_5m.csv` / `*_15m.csv` — confirmed by a direct diag read
(`ls data/*SOL*1h* data/*ETH*1h*` returns nothing; `data/SOLUSDT_5m.csv` /
`data/ETHUSDT_5m.csv` exist). Per `m20_fleet_exit_sweep.py::resolve_data`'s
finest-grain-first rule, the resolver falls back to the `5m` file and sets
`res.resample = "1h"` — this is the resolver working exactly as designed,
and it is a **documented, deliberate** behaviour of the shared
`backtest_data_source` module.

The bug: the driver read `res.resample` onto the `Resolution` object and
then **never checked it**:

```python
df = harness._load_candles(res.path)   # <- runs straight into run_backtest,
                                        #    no resample step at all
```

Every other caller of this resolver applies the flag — the sibling pattern
is `scripts/ml/exit_head_replay.py::_load_candles(path, resample)`, which
this driver's own imports sit one line away from. So both prior dispatches
of this unit silently ran the leg's `1h`-geometry parameters (`donchian`
window, `atr_stop_mult`, `trail_mult 3.5`, `timeout_bars=200`) against RAW
5-MINUTE bars: roughly **12x** the intended bar count for the same wall-clock
span, and a materially different instrument than
`config/strategies.yaml` declares for either leg (the same defect class as
`BL-20260813-HARNESS-SYMBOL-IS-A-LABEL-DATA-DEFAULTS-TO-BTC` — a declared
input silently not being what actually ran).

**Fixed** by calling `harness._resample(df, res.resample)` when the flag is
set (`backtest_trend.py::_resample`, the same resampler
`exit_head_replay.py` already uses).

**Regression-tested**:
[`tests/test_exit_head_prop_swap_feasibility_resample.py`](../../../tests/test_exit_head_prop_swap_feasibility_resample.py)
constructs a small synthetic (non-degenerate — `candle_variance`/MI-157
refuses a flat series) 5m frame, monkeypatches the resolver and the loaded
heads so no live trainer data or artifact is needed, and asserts the
dataframe reaching `run_backtest` is the resampled ~24-bar frame, not the
raw 288-bar one. **Verified both ways**: fails against the pre-fix code
(reads 288 raw bars) and passes against the fix (reads ~24-25 resampled
bars).

## 3. The corrected driver still did not finish — a second, separate problem

Re-running the CORRECTED driver on ETH alone (SOL was never attempted this
session — see below):

1. **Synchronous dispatch** (`timeout 3300` inside a `trainer-vm-diag`
   issue): killed at the internal 55-minute mark (`RC=124`), i.e. it still
   would have hit the workflow's own 60-minute job cap had the wrapper not
   fired first.
2. **Detached dispatch** (`nohup` + sentinel file, per
   `docs/claude/trainer-vm-mode.md` §10's own guidance for jobs that may run
   longer than an hour): still running after **68+ minutes**, confirmed via
   `ps` to be genuinely progressing rather than hung (`ELAPSED 4160s`,
   `TIME 01:07:20` CPU-seconds, `RSS 279MB`, `STAT R`, ~97% CPU at every
   sample taken).

For scale: `RQ-20260927-002`'s BTCUSDT run — a 3-fold embargoed
walk-forward that trains three separate LightGBM boosters and replays all
three — completed in **~20 minutes** on the same box. One no-retrain
backtest+replay pass on ETH, at the correct bar count, taking more than 3x
that long is disproportionate on its face.

**Not isolated this session**: whether the dominant cost is (a) CPU
contention — a recurring `drift_retrain` heavy job held the trainer's
shared single core at 13:02, 14:02 and 15:03 UTC during this session's own
polling window, and by `src/utils/trainer_heavy_lock.py`'s own design a
light per-strategy backtest is deliberately **not** queued behind that lock
(it runs direct, so it competes rather than waits), or (b) a genuine
algorithmic cost specific to this leg's own trade population (`trail_mult
3.5` / `tp_r 6.0` is a wide trail relative to the non-prop `5.0`/`50.0`
siblings, but produces long average holds either way, and both
`run_backtest`'s per-bar loop and `replay_trade`'s per-trade forward
bar-scan pay per bar held), or both. Filed as
[`PI-20260927-F4CD9293-0001`](../../claude/work/pipeline/) rather than
guessed, with a stated `rerun`: a timed run on an otherwise-idle box settles
which.

**SOL was not attempted this session.** ETH alone already demonstrated the
corrected driver cannot complete inside a practical dispatch window on this
box; dispatching a second leg behind it would only have spent more trainer
CPU and session budget for the same open question.

## 4. Reconciling with the coverage matrix, and the RQ-001/002 comparison

[`docs/research/exit-refinement-coverage.json`](../../research/exit-refinement-coverage.json)
already records, from each leg's own dedicated from-scratch E1 round
(2026-08-14, live-parity TP): **SOL** `honest_negative` (`auc=0.5635`,
`beats_actual=15/23`, `n_oos=298`) and **ETH** `passed_unshipped`
(`auc=0.6138`, `beats_actual=20/24`, `n_oos=902`, stranded because that
round trained a head and never published a matching artifact). This unit's
whole premise (per its own `why_this_matters`) was to re-measure both legs
against the artifact that DOES exist and ship publicly
(`exit-head-donchian-1h-v1`) as the coverage row's own stated unblock path
(ii) for ETH's stranded verdict. **Nothing here reconciles further with
those rows** — this session produced no new measurement to set beside them,
only a fixed tool and a confirmed reason the measurement still doesn't
exist.

**Compared with how `RQ-20260927-001`/`-002` handled the flagship BTCUSDT
leg**: those units retrained (`-001` single split, `-002` 3-fold embargoed
walk-forward) and both completed and landed real verdicts —
`-001` marginal PASS (+1.3127R, n=119, single split), reversed by `-002`'s
FAIL (pooled −14.6597R, n=1041, only 1 of 3 folds positive). BTCUSDT's data
is warm on this trainer from repeated prior use across this whole family;
SOLUSDT/ETHUSDT's is not (no native 1h file at all, and — per §3 — genuinely
much slower to process even at the correct grain). The methodological
irony: this unit is CHEAPER than `-001`/`-002` by design (no retrain, one
backtest + one replay pass per leg) and still could not complete, while the
more expensive multi-fold retrain did, in a fraction of the time. That gap
is itself evidence the bottleneck is leg/data-specific (§3), not proportional
to computational complexity.

## 5. Recommendation to the manager

**retrain-worth-it: UNDECIDABLE for both legs from this session's
evidence.** The blocking question is no longer "is there signal" — it is
"can a single-leg run of the CORRECTED driver complete at all in a bounded
window," and that has not yet been answered for either leg (ETH: unknown,
still running past 68 minutes when polling stopped; SOL: not attempted).

**The one number that would decide the NEXT step**: how long the corrected
driver takes on ETH when dispatched on an otherwise-idle trainer (no
concurrent `drift_retrain`), timed end-to-end. If that comes back under
~20–30 minutes, §3's contention hypothesis is confirmed and both legs can be
re-dispatched normally; if it is still multins-of-hours slow on an idle box,
the driver's backtest/replay loop needs profiling (and likely vectorizing)
before a third dispatch is worth trying.

Do not re-dispatch a third attempt shaped like the second (short synchronous
timeout or a short-lived detached poll) — either give a detached run several
contention-free hours and poll infrequently, or fix the performance
question first. `PI-20260927-F4CD9293-0001` carries the rerun instruction.
