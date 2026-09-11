# How many operating-layer surfaces require CALENDAR TIME to reach an EDGE verdict?

> **Doc status:** `live` · category `research` · MI-218 ·
> `WO-20260909-COUNT-THE-SURFACES-THAT-REQUIRE-CALENDAR-TIME-TO-PROVE-EDGE` ·
> measured 2026-09-09 · **Tier-1 measurement only — no gate, threshold or config was changed.**

## The answer, in one line

**Four confirmed surfaces, plus one conditional — and the count is a `lower_bound`, not a
`measured` total**, because the instrument that produced most of it is structurally blind to
the single largest finding in it. One of the four (M7) was already known and served as the
positive control, so **three are new.**

| | surface | gates | decides | accrual | status |
|---|---|---|---|---|---|
| **S1** | `scripts/ml/strategy_review_packet.py::decide` | 7 branches | KILL / DEMOTE / TUNE / forced HOLD | `n_closed` in a live window; `shadow_soak_days < 14` | **known** — the control; MI-215 / #11529 |
| **S2** | `ml/promotion/gates.py` — the **default** `GateThresholds` profile | 2 required gates | model **promotion** | `require_shadow_soak=True` @ `shadow_soak_days=7.0`; `min_trades=200` **live** trades | **NEW** |
| **S3** | `scripts/research/backtest_fidelity_calibrate.py::agreement` | 2 branches | whether a leg's **backtest counts as trusted OOS edge evidence** | `n_live < MIN_LIVE_N = 30` | **NEW** |
| **S4** | `.claude/skills/exit-refinement/SKILL.md` § P7 | prose rubric | exit-lever **demotion** | "the lever/head soaks LIVE … track the realized `future_r_delta` record" | **NEW** |
| **C1** | `scripts/research/e2_feature_information.py::score_panel` | 2 branches | feature-information verdict (`unmeasured`) | `min_trades=30` / `min_rows=200` — **live only if handed a journal panel** | **conditional** |

**Is this a fix or a redesign?** A **fix** — three new surfaces out of 8,464 functions is not a
systemic pattern. But it is not a small fix either, because the three are not peripheral: they are
the **daily strategy-review gate, the ML promotion gate, and the calibrator that decides whether a
backtest may be believed at all.** Nearly every edge decision the system makes passes through one
of them. The pattern is not *widespread*; it is *load-bearing*.

---

## The population, stated

**Code half** — walked `scripts/`, `src/`, `ml/`; excluded `tests/` (a test asserting a gate's
shape is not itself a gate), `__pycache__`, `.venv`, `node_modules`.

- **1,058** Python files parsed · **8,464** functions inspected · **0** parse failures.

**Prose / config half** — `docs/claude/OPEN-ITEMS.json` (66 rows, `clears_when` + `summary`), the
four review backlogs (`snoozed_until` + `clears_when`), and all **32** `.claude/skills/*/SKILL.md`.

**Probe:** [`scripts/ops/calendar_edge_census.py`](../../scripts/ops/calendar_edge_census.py).
Re-run with `python3 scripts/ops/calendar_edge_census.py [--json]`.

✅ **Re-measured against MERGED check D.** The first run was made against check D as it
stood on the MI-215 branch (`914f915e`), while #11529 was still open. #11529 has since
landed on `main` as **`d535343b6`**, and the census was re-run against it: `detector_state:
available`, control **found**, Pass A **7**, Pass B **14**, population **1,058 / 8,464**,
0 parse failures — **identical**. So every number here is measured against the detector
that is actually in the tree, not against a branch that might have changed before merging.
The suite's check-D-present control, which was `SKIPPED` while #11529 was open, now RUNS
(13 passed, 0 skipped) and confirms check D still misses the name-target verdict.

---

## The classifier, stated

Two questions per surface. **Only the first makes something a finding.**

1. **EDGE or MECHANICS?** *Edge* = expectancy, win rate, PnL, promote/demote/kill, OOS
   generalisation. *Mechanics* = did the order reach the venue, does the live execution match the
   simulator, did the alarm fire, did the deploy take. Per
   `docs/CLAUDE-RULES-CANONICAL.md` § "Promotion evidence — offline edge, live mechanics", a
   mechanics gate waiting on 1–2 executions is **the doctrine working**.
2. **Does the gating quantity require calendar time?** The decisive sub-question, and the one that
   separates a real finding from a false positive: **is the population LIVE (grows only with
   wall-clock) or OFFLINE (history already exists)?** A `min_trades=30` floor on a *backtest panel*
   is a statistical power floor — satisfied by deepening history, i.e. by **computing harder**. The
   identical floor on a *live* window is satisfied only by **waiting**. Same constant, opposite
   verdicts. This distinction eliminated **11 of the 14** automated candidates.

Clause 3 of the canonical rule is the bar: *"No gate may require calendar-time accrual to prove
edge."*

---

## The positive control — established BEFORE any count was read

**The probe must rediscover the known M7 instance without being told the file or the function
name.** `--census` returns exit code **2** and prints **no count at all** if it does not. A probe
that misses M7 is broken, and a silent probe is indistinguishable from a clean result — which is
exactly what `check_soak_doctrine.py` did for the days the M7 gate was contradicted unseen.

```
POSITIVE CONTROL — the probe must rediscover the known M7 instance
  target : scripts/ml/strategy_review_packet.py::decide
  found  : True
```

The refusal path is not decorative: run against a tree where check D has not landed, the census
prints `detector_state: absent`, `found: None`, `CONTROL NOT ESTABLISHED — NO COUNT IS REPORTED`,
and exits 2. `pass_a` / `pass_b` are **`None`, never `0`** in that state — *we could not look* is
not *we looked and found nothing*. Both behaviours are pinned by
[`tests/test_calendar_edge_census.py`](../../tests/test_calendar_edge_census.py).

---

## Why this reuses MI-215's detector instead of adding a second one

MI-215 built the right instrument for the code half. Check D resolves local aliases to a fixpoint
(so `n = headline.n_closed` makes `n` banned too) and flags any `if` whose **test** reads a
live-accrual name and whose **body** assigns a verdict. A keyword matcher would have missed six of
its seven branches. The census therefore **imports `find_accrual_gated_verdicts` and points it at
more surfaces**, in three passes:

### Pass A — check D's reach, unchanged, over every function: **7 hits, 1 surface**

Check D's own declared hole is its hand-maintained `GATE_SURFACES` map — *"a gate absent from this
map is UNREACHED"*. It ships with **one entry**. Pass A hands the same algorithm **every function
name in every in-scope file**, turning an allowlist into a census. It rediscovered all seven M7
branches and — across 8,464 functions — **found nothing else.**

That negative is real, and it is a genuinely reassuring result: check D's *algorithm* has no
untracked blind spot of its own shape. Its narrowness was entirely in its `GATE_SURFACES` map.

### Pass B — the same algorithm with widened constants and a widened predicate: **14 additional**

Check D's constants are deliberately narrow. Pass B varies them by **patching, never forking**, so
the alias fixpoint — the hard and valuable part — stays check D's, and every Pass-B hit is an
argument for widening a constant in check D rather than a competing count.

Three holes were widened. **The third was found by control, not by reading**, and matters most:

> `_assigns_verdict` matches only an `ast.Attribute` target (`decision.action = ...`). A gate that
> writes a plain local (`verdict = "hold"`), a dict slot (`row["action"] = ...`), or simply
> `return "hold"` assigns a verdict just as surely and is **invisible to check D**.

Verified with a synthetic control before the widening was written, and now pinned as a test: the
*identical* accrual-gated branch is FOUND with an attribute target and MISSED with a name target.
That test is written so that if check D is ever widened, it fails and Pass B's rationale is
revisited rather than quietly carried forward.

Pass B's own mechanism was control-tested before its result was believed, because
**`Pass B = 0` was the first number it produced** and an unverified zero is the failure mode this
whole census is about. The control passed; the zero was real; the widened predicate then took it
to 14.

### Pass C — prose and config: **34 candidates**

Check D parses Python. It **cannot reach `OPEN-ITEMS.json`, a backlog row, or a `SKILL.md` at all**,
and no amount of pointing will change that. This is the one place a different instrument is
genuinely required. It is a declared-term language classifier (`ACCRUAL_LANG` × `EDGE_LANG` ×
`MECHANICS_LANG`), and its four states are kept apart — `edge` / `mechanics` / **`both`** /
**`unclassified`** — because a row the classifier cannot separate is a row a human must read.

---

## ⚠️ The most important finding is about the instrument, not the count

**S2 was found by reading `ml/promotion/gates.py`, not by any automated pass — and neither Pass A
nor Pass B can ever find it.**

```python
shadow_soak_days: float = 7.0
require_shadow_soak: bool = True          # ml/promotion/gates.py:62
min_trades: int = 200                     # live trades
...
def _gate_shadow_soak(entry, th) -> GateResult:
    ok = days >= th.shadow_soak_days
    return GateResult(..., required=th.require_shadow_soak)
```

There is no `if` whose body assigns a verdict. The gate is a **data record** — its blocking status
is a constructor argument. An AST detector that looks for accrual-gated *control flow* is
structurally blind to a gate declared as a *field*, and this is the shape of the highest-leverage
instance in the whole census.

**Consequence for the number above: the count's honest verdict is `lower_bound`, and I cannot bound
how incomplete it is.** Reporting it as `measured` would be precisely the unprovenanced-diagnostic
error this repo has a guard for. What would make it `measured` is a check-D extension that grades
**declared gate records** (`required=`, `min_*`, `*_days` fields on a thresholds object) alongside
branches. Filed, not built — that is a change to a Tier-3 promotion surface.

---

## The four confirmed surfaces

### S1 — `strategy_review_packet.py::decide` (the control; already tracked)

Seven branches; `elif n < MIN_CLOSED_FOR_ACTION: decision.action = "hold"` at :1034 and a literal
`shadow_soak_days < 14` promotion override at :1149. Withholding a verdict for want of accrual **is**
the prohibited gate — this is the 52/52 `hold`. Owned by MI-215 / #11529; **not re-filed here.**

### S2 — the default `GateThresholds` profile *(NEW, highest leverage)*

`ml/promotion/gates.py` selects a profile per head: `regime_classifier_thresholds()` if
`is_regime_classifier(entry)`, else a bare `GateThresholds()` (`gates.py:228`). The regime profile
sets `require_shadow_soak=False` — the WS-1 de-soak, operator-approved 2026-07-26 — and
`min_trades=5`.

**Every other head lands on the default, where both are required:**

- `require_shadow_soak = True` at `shadow_soak_days = 7.0` — a literal "N days at stage", which
  clause 3 names verbatim as *"a policy artifact, not evidence"*;
- `min_trades = 200` **live** trades. At the ~1.2 closes/week the operator cites for a real leg,
  that is **≈3.2 years** before a trade-outcome head can be promoted.

Canon states this rule is *"mechanically guarded: `tests/ml/test_gates.py` asserts that no required
gate **under the regime-classifier profile** is a calendar-time edge gate."* That is true and it is
**scoped to one profile**. The default profile — which every non-regime head uses — is not covered
by that assertion. **This is the same fact pattern as M7 one level up: the rule is binding, the
guard is real, and the guard does not reach the surface.**

⚠️ Changing either value is **Tier-3** (it decides what a promotion rests on). Filed for the
operator, not touched.

### S3 — `backtest_fidelity_calibrate.py::agreement` *(NEW; the sharpest instance)*

`MIN_LIVE_N = 30`; below it the verdict is `insufficient-live` and, in the script's own words,
*"backtest is a lead, not a result."*

This surface is worth reading twice, because its docstring states the problem it was built to solve:

> *"we have never MEASURED whether our backtests are right, so we fell back to 'only trust real live
> trades' — **which caps every decision at reality's clock**."*

and the implementation then makes **30 live trades a precondition for a leg's offline evidence being
admissible**. At ~1.2 closes/week that is **~25 weeks**. The doctrine is not merely contradicted
here; it is inverted one level up — the offline edge proof is itself gated on live accrual, so the
thing built to escape reality's clock is reachable only by waiting on it.

Stated fairly: measuring backtest↔live fidelity **does** require live trades, and the abstention is
honest rather than a silent pass. The finding is not that a floor exists — it is that a **fidelity
(mechanics) measurement is used as the admissibility gate on an EDGE claim**, so a leg with a clean
purged-WF-CV `oos_edge` and 29 live trades has evidence the system will not look at. Whether the
right answer is a lower floor, a per-leg floor, or admitting offline evidence with a stated
fidelity caveat is a design decision, not this session's call.

### S4 — `exit-refinement` § P7 *(NEW; prose)*

> *"**P7 — online soak + first-decision check.** The lever/head soaks LIVE. The next
> `/health-review` MUST verify the mechanics of the first real lever-driven exit;
> `/ml-review`/`/performance-review` track the realized `future_r_delta` record.
> Demotion = delete the YAML lines."*

The **first half is exemplary** — "verify the mechanics of the first real lever-driven exit" is the
doctrine stated correctly, and is why this skill is a partial rather than a clean finding. The
second half attaches **demotion** to a *realized live R record*, which is an edge verdict on live
accrual with no stated floor and no offline counterpart. Cheap prose fix; not taken (filing is the
act of not taking it).

### C1 — `e2_feature_information.py::score_panel` *(conditional — deliberately not folded either way)*

`min_trades=30` / `min_rows=200` gate a `verdict = "unmeasured"`. These are **statistical power
floors**, and the comment defending them is correct: *"An underpowered null is not a negative."*

Whether they require calendar time depends **entirely on which panel is handed in**. The intrabar
exit panel stamps `"source": "backtest"` and `_COHORT = "backtest"` — offline, satisfied by
computing. A journal-sourced panel is live, satisfied only by waiting. **`score_panel` does not read
the panel manifest's `source` stamp**, so the floor cannot tell a reader which of the two it is
enforcing. Classified `conditional`, not `edge` and not `mechanics`: collapsing it either way would
assert a fact nobody established. The cheap improvement — have the report state the population it
gated — is filed.

---

## What was checked and found CLEAN (the denominator, so a null is readable)

- **11 of 14 Pass-B hits are true negatives**, and they are the evidence the classifier discriminates
  rather than flagging every `MIN_*`:
  - `ml2_bracket_train_eval.py::evaluate` (`min_n`) — the docstring establishes it reads *"the
    historical candle store"*; a train/eval power floor. **Offline.**
  - `adx_entry_distribution.py::run_leg` (`min_trades`) — measures ADX at *"historical entries"*.
    **Offline.**
  - `dukascopy_span_probe.py`, `diag.py::get_bybit_wallet_truth` — data-**availability** states
    (`STATE_BARS`, `STATE_NOT_PULLED`), not verdicts about edge.
  - `e2_feature_information.py:815` (`harness_valid`) and `ml2_bracket_train_eval.py:250/252/254` —
    alias false positives; the verdicts are about calibration on offline data.
  - **`src/runtime/operator_owed.py::grade_item`** — a calendar gate (`snoozed_until > now`) on a
    **governance** verdict, and a **positive precedent worth copying**: it refuses to honour a date
    unless a real `snooze_trigger` accompanies it, commenting *"a date alone is a mute button."*
    That is exactly the discipline S2 lacks.
- **`config/prop_rulesets/breakout.yaml :: funded_soak_days: 30`** — a 30-day calendar soak, but an
  **externally imposed prop-firm rule**, not our evidence doctrine. Excluded deliberately, with the
  reason stated rather than silently dropped.
- **`.claude/skills/backtesting/SKILL.md`** — *"Treat any cell under ~20 trades as unmeasured"* plus
  *"Before accepting a small n, establish what BOUNDS it."* An **offline** n over a 730d/2900d
  history. Correct as written; a Pass-C false positive and a good illustration of the classifier's
  central distinction.
- **`OI-20260906-ICT-SCALP-5M-DEMOTED…`** and **`OI-20260905-MI-128-SWEEP-WINDOW-REKEYED…`** — both
  matched on accrual + edge vocabulary and both are **correctly framed mechanics** on reading
  ("did the demote take effect", with an explicit positive-control denominator; "does the sweep
  window capture a >14-day hold"). Clean.

---

## Stated limits of this measurement

1. **The count is a `lower_bound`.** S2's shape — a gate carried as a data field — is invisible to
   the automated passes, and I cannot bound how many more of that shape exist. This is the
   load-bearing caveat and it is why the headline says `lower_bound`, not `measured`.
2. **Pass C's precision is low and its recall is unmeasured.** It is a co-occurrence classifier: 19
   `edge`/`both` candidates yielded **1** confirmed finding (S4). Its accrual terms require explicit
   phrasing — *"at least 20 graded rows"* matches, a bare *"20 graded rows"* does not — so prose
   findings it missed are entirely possible.
3. **I adjudicated the 19 `edge`/`both` prose candidates by reading. The 11 `mechanics` and 4
   `unclassified` rows were not individually read**, on the classifier's word. Any of the 4
   `unclassified` could be a finding.
4. **YAML/JSON configuration thresholds are only partly covered** — reached opportunistically by
   grep (which is how `funded_soak_days` surfaced), not by a systematic pass.
5. **One surface per adjudication.** Where a surface carries several branches of one gate (S1's 7,
   S3's 2) they are counted as one surface and the branch count reported beside it.

## Filed, not taken

Per MI-218's scope, everything above is **measurement**. Nothing was changed. Filed to the
health-review backlog for a session that owns the surface:

- extend check D to grade **declared gate records**, not only branches (limit 1 — the instrument);
- S2, the default-profile `require_shadow_soak` + `min_trades=200` (Tier-3 → operator);
- S3, `MIN_LIVE_N` as the admissibility gate on offline edge evidence;
- S4, the `exit-refinement` P7 prose;
- C1, `score_panel` stating which population it gated.
