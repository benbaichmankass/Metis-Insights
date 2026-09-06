# Is backtesting actually gating what goes live? — MI-147

**Date:** 2026-09-06 · **Work object:** `WO-20260906-IS-BACKTESTING-ACTUALLY-GATING-WHAT-GOES-TO`
· **Checklist item:** `MI-147-BACKTEST-SOAK-CHAIN-AUDIT` · **Tier-1, read-only audit.**

## The question, verbatim

> "we have and are correctly utilizing all of the back testing infrastructure, which is
> supposed to mean that soaking is only for mechanical verification of what we already
> have tested to be verifiably true in back testing. Right? So we also need to understand
> why that doesn't seem to be producing the results that we're expecting."

## Answer in one paragraph

**No — the premise does not hold, and it fails at the first link.** The claimed chain is
*backtest establishes an edge → soak mechanically verifies it → the leg trades live*. For
the 44 enabled live legs, the backtest evidence does not sit **before** the go-live; it
sits **after** it. **0 of 44 legs had a backtest result that was read and dispositioned
before the leg went live** (median lag 56 days, max 88). So soak was never verifying a
prior backtest result — **soak was the first measurement of these legs**, and the
backtesting that exists is largely retrospective documentation of legs already trading.
That is why soak does not behave like mechanical confirmation: it was never given
anything to confirm.

---

## THE POPULATION (stated once, used throughout)

**M = 44 legs.** Defined as: every entry under `config/strategies.yaml::strategies` with
`enabled: true` **AND** `execution: live`, with both fields' documented permissive
defaults applied (`enabled` implicitly required, `execution` defaults to `live` — the
file's own header, lines 22–37). Measured at `f2b871e` (2026-09-06) by parsing the YAML,
not by grep.

Arithmetic cross-check (per RULE ONE, counts not proofreading): 55 total entries →
52 `enabled: true`, 45 `execution: live`, **44** satisfying both; 10 `shadow`, 3 disabled.
Every one of the 44 carries exactly **one** symbol, so *strategy* and *leg* are 1:1 in
this population and M is a leg count as well as a strategy count.

⚠️ This **independently reproduces MI-146's denominator of 44**, derived here from the
config rather than inherited from that finding.

---

## (a) Does a pre-live backtest EXIST for each leg?

**MEASURED.** Method: each leg's go-live is the first commit in `origin/main`'s history
of `config/strategies.yaml` where that leg reads `enabled: true` + `execution: live`
(110 versions of the file replayed and parsed; 0 unparseable). Backtest evidence is the
first commit that introduced the leg's **name** into a backtest-flavoured file under
`docs/research/`, `docs/audits/`, `docs/audit/`, `docs/sprint-logs/`, `comms/research/`
(git pickaxe `-S`). All timestamps normalised to UTC before ordering.

| state | count | share of M=44 |
|---|---:|---:|
| **PRE-LIVE** — evidence commit strictly precedes the go-live commit | **9** | 20.5% |
| **SAME-COMMIT** — evidence and go-live shipped in the *same* commit | **6** | 13.6% |
| **POST-LIVE** — the leg's name first enters backtest evidence *after* it is already trading | **29** | 65.9% |

Of the 29 POST-LIVE legs, a **family-level** backtest (family stem + the leg's symbol,
not the leg's own name) predating go-live exists for **18**. For the remaining **11 of 44
(25.0%) no pre-live backtest evidence was found at any level** — leg-named or family:
`gdx_pullback_1d`, `ict_scalp_5m`, `scha_trend_long_1d`, `splg_trend_long_1d`,
`trend_donchian`, `trend_donchian_ada_4h`, `trend_donchian_avax_4h`, `trend_donchian_sol`,
`trend_donchian_sol_4h`, `trend_donchian_xrp_4h`, `uso_trend_1h`.

Even the 9 PRE-LIVE are thinner than they look: only 4 precede go-live by more than a day
(`trend_donchian_{eth,sol}_prop` 31.3 d, `ada_pullback_2h` 6.8 d, `squeeze_breakout_4h`
24.1 h). `eth_pullback_2h` precedes its own go-live by **48 minutes** — same working
session, not a gate that was consulted and then acted on.

⚠️ **A rejected first answer, recorded because it was wrong in the flattering direction.**
An earlier pass of this same probe returned **36 of 44 PRE-LIVE**. It was contaminated:
it tested *current* file content against each file's *first-add* date, so
`comms/claude_strategy_scores.jsonl` — an **append-only log of graded LIVE order
packages, not a backtest** — first committed 2026-05-25, matched 30 legs whose rows were
appended months later. Both the date basis and the "is this a backtest?" test were wrong.
The 9/6/29 split above uses introduction-date (pickaxe) and excludes `comms/reports/`,
`comms/schema/` and `claude_strategy_scores.jsonl` as live artifacts.

### ⚠️ What (a) does NOT establish — the third state

This is a **repo-artifact probe**. It cannot see a backtest that ran on the trainer VM
and whose output was never committed — and that is a live possibility, since
`backtest_fidelity_calibrate.py`'s own usage line says *"on the trainer, where both DBs
live"*. For the 11 legs above the honest reading is **"we could not find evidence"**,
which is a *third state* distinct from *"no backtest exists"*. It is also an upper bound
in the other direction: a document naming a leg is not proof it backtested that leg.

---

## (b) Was the result READ and DISPOSITIONED?

**MEASURED — and this is the unambiguous half.** Using the repo's own instrument rather
than a parallel one: `scripts/research/research_disposition.py --report`, the tool the
`performance-review` skill designates for *"did anyone read that sweep"*.

Over the **whole corpus (n = 370 units** across `m20` 218 / `e35` 97 / `gld_compat` 55**)**:

    dispositioned 103 · unread 11 · superseded_unread 256 · no_rows 0 · corpus_unreadable 0
    admission: accruing 5 · not_queue_dispatched 365

Restricted to **M = 44**: all 44 legs appear in the corpus and all 44 carry at least one
`dispositioned` unit — which looks like a pass until the dates are read.

> **The entire disposition corpus spans 2026-08-10 → 2026-08-31.**
> Every leg in M went live between 2026-05-15 and 2026-08-13.
>
> ### **0 of 44 legs had any dispositioned backtest unit before it went live.**
> ### Lag from go-live to first disposition: min 0 d, **median 56 d**, max 88 d.

Two supporting measurements over the same population:

- **365 of 370 units (98.6%) are `not_queue_dispatched`** — they never went through
  `research/queue`, the mechanism that is supposed to *declare the power requirement
  before the run*. The queue governed essentially nothing.
- **182 of 232 units belonging to these 44 legs (78.4%) sit below the declared power
  floor of n ≥ 49.06** (α=0.05, power=0.80, d=0.4 — the floor the `performance-review`
  skill states). Observed `n_oos` ranges **2 → 358**. Per that skill, below-floor is a
  read whose answer is *"this cannot answer the question"* — not a verdict.

⚠️ `superseded_unread` (256) is **not** counted as a failure here — the skill states most
historical units are superseded by construction and treating them as findings is the
desensitized-alarm pattern. The finding is the **timing** of the 103 dispositioned ones.

---

## (c) Do backtest exit locations and LIVE exit locations agree?

**Both MI-151 claims verified independently. Both hold. The briefing's path was wrong.**

**Path correction:** the calibrator is `scripts/research/backtest_fidelity_calibrate.py`,
**not** `scripts/ops/backtest_fidelity_calibrate.py`. (`scripts/ops/` holds 286 entries
and none is this file — positive control: the probe finds it one directory over, plus
`tests/test_backtest_fidelity_calibrate.py` and `scripts/research/backtest_fidelity_cost_ab.py`.)

### Claim 1 — "it has never been run": SUPPORTED, with a stated limit

Across the full 4,277-commit history (2026-03-22 → 2026-09-06), **no
`backtest_fidelity_*` output artifact has ever been committed.** Only the three *source*
files were ever added. `comms/research/` — the script's own documented `--out` directory
— has received exactly **one** file in the repo's life, `crypto_correlation_2026-08-18.json`,
unrelated. No workflow under `.github/` references the script.
**Positive control:** the same pickaxe *does* find file additions — it returns the
script's own add commit and the one `comms/research/` file — so the silence is absence,
not a broken probe.
⚠️ **Limit:** this proves no output ever *landed in the repo*. A run on the trainer whose
output was never committed is not excluded. Read as **"no run has left a durable record"**,
not "no process ever executed it".

### Claim 2 — "it grades the wrong quantity for exit location": CONFIRMED

Read from the code, not the docstring. Its SQL is
`SELECT pnl, notes, direction, timestamp FROM trades` — it reads **no exit price, no exit
level, and no exit timestamp-versus-target**. It derives `r_multiple` from `pnl` and
grades three axes: win-rate difference (≤0.15), a two-sample KS on the realized-R
distribution (≤0.30), and a mean-R gap (≤0.50), abstaining below `MIN_LIVE_N = 30`.

Those are **outcome-distribution** agreement. *Exit location* — where in price the exit
actually happened relative to the declared bracket — is not among them. Two books can
agree on win-rate and R-shape while exiting in entirely different places. **The named
instrument cannot answer (c) even if it ran.**

### Was it run for this audit? No — and why not

`--backtest-db datasets-out/backtest_trades.db` and `--live-db` are both absent from this
environment (no `datasets-out/`, no `trade_journal.db`), and the script is designed to run
trainer-side. Running it would in any case have graded outcome agreement, not exit
location. **Reporting that it cannot answer the question is the finding; producing a
number from it would have been the unprovenanced-diagnostic pattern this repo names.**

### What DOES answer (c) — read structurally instead

**MEASURED.** The live units clamp take-profit to `min(entry*(1+0.099), entry + tp_r*risk)`
via `TP_VENUE_CAP_PCT = 0.099` (`src/runtime/tp_venue_cap.py:64`). Against the 15 backtest
harnesses:

| harness | models the live TP cap? |
|---|---|
| `backtest_trend.py`, `backtest_pullback.py`, `backtest_squeeze.py`, `backtest_fade.py` | yes — but **`tp_cap_pct: float = 0.0`, DEFAULT OFF** |
| the other **11**, incl. `backtest_ict_scalp.py` and `src/backtest/run_backtest{,_vwap}.py` | **zero references** |

With `tp_cap_pct <= 0` the harness sets `tp_price = None` — **it has no take-profit exit
path at all.** So at its defaults the backtest runs a book with *no target* while the live
system runs one with a target clamped to 9.9% of entry. Those are not the same exit
geometry, and the disagreement is **structural and on by default**, not a fidelity score
that drifted.

This is already filed as **`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`**,
named in `backtest_pullback.py`'s own source comment. It composes exactly with MI-146's
measurement that **25 of 44 enabled live legs (56.8%) have no reachable take-profit** —
15 carrying a `tp_r >= 20` sentinel clamped by the 9.9% cap, 10 declaring none.

---

## The "why" — which explanation the evidence supports

The four candidates, graded:

| candidate | verdict |
|---|---|
| **Legs went live without the gate** | **SUPPORTED.** 29 of 44 are named in backtest evidence only *after* go-live; 11 of 44 have no pre-live evidence at any level. |
| **The gate ran but nobody read it** | **SUPPORTED, and it is the stronger half.** 0 of 44 dispositioned pre-live; median lag 56 d; 98.6% of units never queue-dispatched; 78.4% below the power floor. |
| **Backtest and live disagree about exits** | **SUPPORTED, structurally.** TP cap default-off in 4 harnesses, absent in 11; MI-146's 25/44 unreachable TP. |
| **The backtest is faithful and live execution diverges** | **NOT SUPPORTED — and not currently knowable.** Fidelity has never been measured; the one instrument for it has left no durable run and grades the wrong quantity. |

**These are not four competing explanations — they are one failure in sequence, and the
order matters.** The chain broke at link one, so links two and three were never load-bearing:

1. **The gate was not in front of the decision.** For 35 of 44 legs the backtest artifact
   is dated at or after go-live. A gate that produces its evidence after the decision is
   not a gate; it is a write-up.
2. **Nothing converted a result into a decision at the time.** 0 of 44 dispositioned
   pre-live. The disposition machinery is real and works — it was built 2026-08-10, which
   is *after every leg in M had already gone live.*
3. **So soak inherited the whole job.** Soak was never "mechanical verification of what
   backtesting proved" because backtesting had not proved anything about these legs yet.
   Soak was, and is, **the first measurement** — which is precisely why its results
   surprise: a first measurement has nothing to confirm and no prior to be consistent with.
4. **And the retrospective backtests still cannot reconcile with live**, because at their
   defaults they model a different exit geometry than the live system runs (no TP vs a
   9.9%-clamped TP), on a book where 25 of these 44 legs (56.8%, MI-146) cannot reach their target at all.

**The unflattering sentence, stated plainly:** the backtesting infrastructure is
extensive, well-built, and largely was not in the decision path for the legs currently
trading. The premise in the operator's question describes the system as designed, not the
system as run.

---

## What I could NOT establish

Stated as its own section because collapsing *we did not look* into *nothing is there* is
the failure this repo names most often.

1. **Whether backtests ran on the trainer VM without committing output.** (a) and the
   calibrator's never-run finding are both **repo-artifact** probes. A trainer-side run
   leaving no durable record is not excluded by either.
2. **Whether backtest and live exit LOCATIONS agree.** No instrument in this repo grades
   it. The named one grades outcome distribution. This is unmeasured, not measured-clean.
3. **The 6 SAME-COMMIT legs.** Ordering within a single commit is not resolvable, so
   whether the evidence informed the promotion or merely shipped alongside it is unknown.
4. **Whether pre-live *decisions* were made in conversation.** An operator approval given
   in chat leaves no repo artifact. (a)/(b) measure the durable record, which is the thing
   a later session can actually read — but it is not the same as the decision.

## Proposed follow-ups — PROPOSED, NOT APPLIED (all Tier-3 or out of this scope)

1. **Do not back-fill the 11.** Running a backtest now for a leg that has traded live for
   months does not reconstruct a pre-live gate — it produces a number contaminated by the
   outcome. The gap is the finding.
2. **Make the gate structural rather than cultural:** a leg cannot move to
   `execution: live` without a dispositioned unit whose `run_stamp` predates the config
   commit. That is checkable in CI against `research_disposition.py`'s own record — the
   ordering data used in this audit is exactly what such a check would read.
3. **Default `tp_cap_pct` to `TP_VENUE_CAP_PCT` in the 4 harnesses that support it, and
   port it to the 11 that do not** — starting with `backtest_ict_scalp.py`, which covers
   8 of the 44 legs. **Tier-3**: it changes every prior verdict those harnesses produced.
4. **Either give the calibrator an exit-location axis or stop citing it for exit
   fidelity.** Today it is named as the instrument for a question it does not measure.

**MARKS.** Counts in (a), (b), (c) are **MEASURED**, with population and locator stated
inline. The four-part "why" chain is **INFERRED** from those measurements, named above,
and is falsifiable — check it. Nothing here is a **DECIDED**; no config, gate, roster or
coverage `status` was changed by this audit.
