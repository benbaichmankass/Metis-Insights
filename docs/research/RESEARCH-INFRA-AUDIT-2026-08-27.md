# Research & testing infra — audit phase 1: the measurement layer

> **Operator directive, 2026-08-27:** *"why was it built that way, was it actually
> built wrong, and why shouldn't we try to use it? ... We've been working for weeks
> already on the infra to push active management forward and keep tripping over
> ourselves — we need a full audit of the research and testing infra to understand
> what we actually have and precisely define the gap ... assuming instead of
> verifying is laziness that costs more effort down the line."*

**Scope of THIS phase — stated so its limits are not overread.** Phase 1 covers
the **measurement layer**: the compat-matrix promotion gate, and the M20 exit
coverage matrix that active management is graded against. It does **not** yet
cover harness fidelity (fee models, config-exactness, `--emit-trades` paths), the
ML/trainer pipeline, or the candle data layer. Those are phases 2–4, scoped in
§ 5. Every number below was read from code or from a committed artifact this
session; nothing is carried from a note.

---

## 1. The headline, because it reverses the working assumption

**The research infrastructure is not the bottleneck, and it is not rotting.** It
ran. It produced **468 graded cells** (52 strategy legs × 9 lever columns) in the
M20 coverage matrix.

The bottleneck is that **the measurement has no statistical power**, and the
matrix's own vocabulary cannot say so — so an underpowered null is recorded as a
finding. Three quarters of the matrix currently reads as *"we tested this and the
lever does not help"* when the defensible reading is *"we could not tell."*

| status | cells | share |
|---|--:|--:|
| `honest_negative` | 329 | **70.3%** |
| `n/a` | 36 | 7.7% |
| `blocked:*` | 32 | 6.8% |
| `shipped` | 22 | 4.7% |
| **`passed_unshipped`** | **22** | **4.7%** |
| `pending` | 20 | 4.3% |
| `shipped_gate_failed` | 7 | 1.5% |
| **total** | **468** | 52 × 9 ✓ |

---

## 2. The power problem — measured, not asserted

The matrix records base trade counts in prose inside each cell's `ref`
(`base n IS=315 OOS=34`), not as structured fields. Extracted across every cell
that states one:

**Population: the 113 cells whose `ref` states an IS/OOS base count.**

| | value |
|---|--:|
| median OOS base | **33 trades** |
| OOS base < 50 | **107 / 113 = 94.7%** |
| OOS base < 100 | **111 / 113 = 98.2%** |
| min | 4 |
| max | 145 |

⚠️ **STATE THE POPULATION.** Only **96 of the 329** `honest_negative` cells state
a base count at all — **29%**. The other 71% record a verdict with no denominator
anywhere in the cell. That is itself a finding: the matrix's headline is computed
over cells most of which do not say what they were computed on.

**Why 33 is disqualifying for this question.** An exit lever changes the *tail* of
a return distribution — it moves a minority of trades, usually the worst or the
longest. The gate asks for an improvement in `net_R` **and** `maxDD` in both IS
and OOS. On 33 out-of-sample trades, of which the lever may fire on a handful,
the sampling error on either statistic is the same order as any realistic lever
effect.

**The gate knows the shape of this and stops one step short.** `MIN_OOS_TRADES = 25`
exists and cells below it get their own `insufficient_base` verdict — correctly
kept distinct from failure. But:

- **The floor sits 8 trades below the median of the population it grades.** It
  excludes almost nothing: 2 cells carry `insufficient_base` against a median
  base of 33.
- **It is a proxy for the wrong quantity, and the skill says so** —
  `exit-refinement/SKILL.md`: *"The floor is a PROXY for what actually matters —
  the trades the LEVER fired on, and whether the effect exceeds its own noise ...
  Per-cell fire counts are not recorded; that gap is open."* So a lever that
  modified 2 exits on a 200-trade base clears the floor and is graded on n=2.

**The errors run in BOTH directions, which is the part that matters.** The same
thin base that manufactures false negatives manufactures false positives, and
there is a recorded instance: `spy_trend_long_1d vt_hot90_t2` **passed on 3 OOS
trades** with a 6/6 walk-forward and a `ΔmaxDD` of *exactly 0.0* — a difference of
exactly zero is that cell announcing that the lever never fired.

So **neither the 329 negatives nor the 22 passes are trustworthy as they stand.**
This is not an argument that the levers work. It is an argument that the matrix
does not currently answer the question it is being read to answer.

### ⚠️ THIS IS A RECURRENCE, NOT A DISCOVERY — it was found for one lever 13 days ago

`BL-20260814-EXIT-HEAD-E1-GATE-IS-90-PERCENT-PREDICTED-BY-OOS-BOOK-SIZE-NOT-SYMBOL`
(**still `kept_open`**) established the identical structure on 2026-08-14, for the
`exit_head_ml` column alone, and stated the conclusion in as many words:

> *"A gate whose outcome is 90% predicted by book size is substantially a **POWER TEST**."* <!-- population-ok: verbatim quote from BL-20260814; its 90% is that row's own measurement over 20 of 36 resolved exit_head_ml cells (those stating an n_oos), restated in the paragraph below -->

Its measurements: a single threshold on book size classifies the pass/fail verdict
at **90.0% accuracy** (big-book 6 of 7 pass, small-book 1 of 13); PASS median 600
against FAIL median 168. And — the same second defect found here — *"20 of the 36
resolved `exit_head_ml` cells [state a denominator]. **16 are excluded for stating
none.**"*

⚠️ **DO NOT MERGE THE TWO NUMBER SETS: THEY ARE DIFFERENT UNITS.** That row's
`n_oos` (hundreds) counts **per-bar dataset rows**, which an ML exit head needs.
The median of **33** in § 2 counts **OOS trades**, which is what a hard-lever sweep
is graded on. They are not comparable and must not be quoted as one series.

What recurs is the **structure**, and that is the point: a gate whose verdict
tracks sample size, over a population most of which does not state its sample
size. It was filed for one of nine lever columns, kept open, and **never
generalised to the other eight** — so 329 negatives across the full matrix have
been accumulating under a defect that was already named, evidenced and filed.
That is the mechanism behind *"we keep tripping over ourselves"*: the finding
existed, and nothing carried it across.

### The vocabulary gap

The matrix's `legend` defines seven statuses, each with a written justification —
including `shipped_gate_failed`, which exists precisely so a live-but-lapsed lever
cannot be absorbed into either neighbour. The vocabulary is careful. It is missing
exactly one member:

> `honest_negative`: **"tested, failed the gate"**

There is no state for **"tested, and the test had no power to detect the effect."**
Those are different facts calling for opposite follow-ups — one closes a cell, the
other says *get more trades or pool the estimate* — and today they are the same
cell colour. This is the `collapsed-state` class the repo already polices
elsewhere, applied to a verdict rather than a field.

---

## 3. The harvest gap — 22 measured passes are not in production

`passed_unshipped` = *"validated, awaiting implementation/approval."* There are
**22**, and **14 of them are `bracket_geometry` from a single sweep that ran on
2026-08-26** (`e35-bracket-sweep.yml` run 32975514836) — i.e. **yesterday**.

That is not rot. That is a fresh, unharvested result. The pipeline's P0–P5 stages
ran and produced verdicts; **P6 (the Tier-3 batch → YAML declare) did not.**

⚠️ **Do not harvest these before § 2 is fixed.** Four of the 22 state a base count
and all four are under 50 OOS trades (median 41). Shipping a pass drawn from the
same underpowered population is how a cosmetic lever reaches production — the
failure mode `BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS` already records.

---

## 4. Why the compat-matrix STANDARD arm was built that way

The operator's first question, answered from the design record and the code, not
from the comment that started this.

**It was built right, then extended by reusing an adjacent tool whose semantics
did not match.**

**Original intent** — `docs/integrations/prop-accounts-architecture-DESIGN.md`
(2026-06-17), stated twice:

> *"Real / paper broker accounts → a **standard** ruleset (the account's own
> `risk_pct`, **no breach/economics**) — for which the 'compatibility test' is
> **just the ordinary net-of-fee performance backtest**."*

> *"the `standard` case = **a no-breach ruleset** with the account's risk."*

So the standard arm was designed as a no-breach performance test. Correct, and
still what the module docstring claims today.

**The extension** — the same doc, § 1, records `PB-20260618-012` (the Daily/ETF
Alpaca extension) deliberately tightening it:

> *"the ROUTE gate is **tightened beyond positive end-return**: it also requires
> `survival ≥ --min-survival` (default 0.90) AND `P(breach) ≤ --max-p-breach`
> (default 0.10) under the account's own soft limits"* — so *"a positive-but-fragile
> cell can't route onto live capital."*

**That intent is sound and worth keeping.** Fragility is a real reason to refuse a
cell.

**Where it went wrong.** To compute survival the extension reused the machinery
already sitting there — `run_montecarlo` + a `PropRuleset`. `PropRuleset.limits`
offers exactly **two** drawdown shapes (`ruleset.py:36`):

- `static` — measured off the **starting balance**
- `trailing` — measured off the **running peak**

Neither expresses what a standard account's `max_dd_pct` actually is. So
`_standard_ruleset` chose `static` — the only member that would parse — and the
account's field landed in a slot whose semantics are **terminal**.

**Verified in code, three sites:**

| site | what it does |
|---|---|
| `evaluator.py:155` | `ref = peak if dd_type == "trailing" else account_size` — static references the starting balance |
| `montecarlo.py:227` | `floor = account_size * (1.0 - static_dd_pct)` — fixed, never re-referenced |
| `montecarlo.py:291` | `# once breached, the account is dead — stop walking` |

Against what the field means in production (`src/units/accounts/risk.py:21`, and
the `accounts.yaml` header):

> *"max **INTRA-DAY** equity drawdown **from today's high**"* — resets at UTC
> midnight, and breaching it **refuses one trade**. It never disables the account.

**Three axes inverted at once:** intraday → permanent · from-today's-high →
from-starting-balance · refuses-a-trade → kills-the-account.

**Plus an independent second defect:** `account_size_usd` falls back to
`_DEFAULT_STANDARD_SIZE = $10,000` (`account_rulesets.py:43`) whenever the risk
block omits it — which is **every** standard account. `bybit_2`'s measured balance
is ~$296. `run_montecarlo` compounds `risk_pct` against that fictional notional.

### Was it built wrong?

Separate the two, because the answer differs:

- **The goal: not wrong.** Refusing a positive-but-fragile cell is right.
- **The model: wrong** — and it is a class this repo already has a name, a guard
  and a scar for. `CLAUDE.md` § *"Diagnostic provenance"* sub-class **A**:
  *"the label names quantity Q; the code called accessor `f()`; `f() ≠ Q`."* The
  canonical instance is `max(proba)` printed as `P(volatile)`.

**Why no guard caught it:** the field is called `max_dd_pct` on both sides. The
name matched; the semantics did not. `diagnostic-provenance-guard` covers
`scripts/{ml,research,ops,macro,reports}/` — `src/prop/` is outside its scope.

### The generalizable lesson

> **A type system with no member for the new concept will silently absorb it into
> the nearest existing member.** `PropRuleset` had `static` and `trailing`; the new
> concept was neither; the code picked the closer one and produced a confident
> number instead of an error.

The cheap prevention is a **third member** (or a distinct standard-account limit
type) — an enum that *cannot* represent the new rule is a compile-time question;
an enum that *approximately* can is a silent wrong answer years later.

### So: should we throw it away?

**No — and an earlier suggestion in this session that we might retire it is
withdrawn.** The survival gate is the only thing standing between a fragile cell
and live capital. What it needs is not deletion:

1. **A third drawdown shape** expressing *intraday, from today's high, resets
   daily, refuses a trade rather than killing the account.* Then `p_breach`
   becomes *"probability this cell trips the daily brake"* — genuinely useful,
   and **not terminal**, so `survival` stops being the wrong question.
2. **A real account size** — read the account's equity, or refuse to grade rather
   than substitute $10,000.
3. **Re-grade or withdraw every prior standard-column citation.** At least one
   exists: `BL-20260803-GLD-ALPACA-PORTFOLIO-SURVIVAL-SKIP` quotes
   `survival 0.871 < 0.90` as a routing finding.

Tier-3: it changes promotion verdicts.

---

## 5. The precise gap to building active management

Three items, in dependency order. **Only the third is a build; the first two are
corrections to how we decide.**

### G-A — Make the measurement able to answer the question *(prerequisite)*

- Record **per-cell lever-fire count** (how many trades the lever actually
  modified). Named as an open gap by the skill itself; it is the real denominator.
- Add an **effect-vs-noise** test to the gate — the effect must exceed its own
  sampling error, not merely have the right sign.
- Add an **`underpowered`** status, distinct from `honest_negative`, and **re-grade
  the matrix.** Expect a large share of the 329 to move.
- Make the base count a **structured field**, not prose: 71% of negatives state no
  denominator at all today.

**Until this lands, the matrix's 70.3%-negative headline (329 of 468 cells = 52
legs × 9 levers) should not be used to
argue that exits do not matter for these strategies.**

### G-B — Harvest, after G-A

22 `passed_unshipped`, 14 of them a day old. P6 (Tier-3 batch → YAML declare) is
the missing step. Re-grade them under G-A first, then batch the survivors.

### G-C — The binding physical constraint: **n**

A median of 33 OOS trades per leg is not a research-tooling problem. It is a
**trade-volume** problem, and no harness improvement fixes it. Real-money lifetime
is 408 trades across 12 strategies; paper is 645. Per-leg, per-lever, that is what
it is.

There are only four ways out, and they should be chosen deliberately rather than
drifted into:

1. **More history** — longer backtest windows per leg (bounded by data coverage).
2. **More trades per leg** — higher-frequency legs (the M27 scalp expansion is
   already this argument).
3. **Pooled estimation** — stop asking 468 independent underpowered questions.
   Estimate each lever's effect **across legs** with partial pooling, so a lever
   borrows strength from every leg it was tried on. This is the standard answer to
   many-thin-cells and it is not currently anywhere in the pipeline.
4. **Accept fewer, better-powered cells** — grade a small number of high-volume
   legs properly instead of the whole fleet thinly.

**Recommendation: (3).** It is the only one that uses the 468 cells already paid
for rather than discarding them, and it converts the matrix's biggest weakness
(many thin cells) into its input.

---

## 6. Inventory finding: 50 research scripts with no declared disposition

`scripts/ci/check_unwired_artifacts.py --dirs scripts` (run this session):
**2 `unwired` · 105 `doc_only` · 9 `skill_invoked`**. Of the 105 `doc_only`
("referenced ONLY by docs — documented, but nothing runs it"), **50 are under
`scripts/research/`**.

⚠️ **`doc_only` does NOT mean "broken" or "never ran".** Many are legitimately
one-shot studies that ran once, produced a memo, and are correctly finished. The
finding is narrower and is the check's own framing: each is *"a corpse to remove,
a capability to WIRE, or a tool that must declare `# wiring: manual-only"* — and
**none of the 50 has declared which.** A future session cannot distinguish a
retired one-shot from a live capability that quietly stopped being called, so it
re-derives. That is a direct contributor to the operator's *"keep tripping over
ourselves."*

Cheap fix: a one-line `# wiring:` declaration per file. It is not a rewrite.

---

## 7. What phase 1 did NOT check

Stated so the audit's coverage is not overread:

- **Harness fidelity** — whether the sweeps are config-exact, and whether fee /
  slippage models match live. ⚠️ There is a known live instance:
  `accounts.yaml::risk_pct: 0.015` is a **fraction** while five research/prop
  files compute `rpct / 100.0` as a **percent** — 100× apart under one flag name
  (audit F-37..F-40, 2026-08-20). Unverified this session.
- **The ML/trainer pipeline** — 95 registry models, last cycle **trained 0**,
  trainer disk at 91.2%.
- **The candle data layer** — coverage, gaps, and the MGC outage now blinding 3
  live legs.

