# Research infra + skills audit — 2026-07-30

**Operator directive (verbatim):** *"before we keep moving on the research, we need to do an
audit of the research skills and tool instructions — it's insane that we still can't run a
research session without claude misunderstanding how the work is supposed to happen. We need
to make sure that all research work runs through a clear ruleset and that claude fully
understands the infra he's using before he dives into these tasks."*

This is the audit. It is written from a specific failure in *this* session rather than in the
abstract, because the abstract version has been written before and did not prevent it.

---

## 1. The failure, precisely

I concluded that `trend_donchian`'s **six live regime cells** could *never* be re-measured,
because "an ML exit head can never be replayed offline." I then:

- filed it as a **high**-severity backlog row,
- wrote a three-option disposition in a research doc,
- recommended accepting six live gates as permanently un-auditable,
- and escalated it to the operator as a **decision**.

All of it was wrong. `scripts/research/analyze_exit_head.py` trains the take/skip exit head
**and simulates its decisions per trade** against the baseline fixed exit, under CV grouped
by `trade_id`, purged, embargoed, and uniqueness-weighted — fed by
`build_intrabar_exit_panel.py`. The capability had existed the entire time and is *more*
rigorous than the harness I was treating as the only option.

### 1a. The proximate cause

`scripts/research/regime_debt_matrix.py` carried this comment:

```python
# _UNREPLAYABLE — an ML exit head can never be replayed offline.
```

Its true scope is narrow: *`backtest_trend.py` does not model this lever.* I read a local
scope limitation as a global impossibility and promoted it straight to a research
conclusion. Corrected in place (field beats comment).

### 1b. Why nothing caught it — the measured gap

| Metric | Value |
|---|---|
| Scripts in `scripts/research/` | **51** |
| Mentioned in **any** skill | **4** |
| Mentioned in **no** skill | **47** |

Not mentioned anywhere: `analyze_exit_head`, `build_intrabar_exit_panel`,
`build_backtest_panel`, `m20_ml_exit_probe`, `regime_debt_matrix`, `regime_cell_walkforward`
— **including the two tools I used all session**.

Meanwhile `backtesting/SKILL.md` opened with:

> *"This skill maps every real backtest entry point in the repo (verified against the
> scripts on `main`)"*

So the one skill a session would consult for "how do I measure this strategy" **asserted
completeness while being ~92% incomplete**. That is not a documentation gap; it is an active
misdirection. A skill saying "partial — see the index" would have sent me looking. A skill
saying "every entry point" told me to stop.

**This is the same bug class as everything else in the session, one level up.** I spent the
day refusing to trust artifacts that reported success out of a wrong scope, then trusted a
*skill* reporting completeness out of a wrong scope and a *comment* reporting impossibility
out of a wrong scope.

---

## 2. Impossibility claims are the dangerous kind

A tool that says **"measured: OK"** when it measured nothing wastes a decision. A tool that
says **"this cannot be measured"** when it can *closes off the work entirely* — nobody
re-checks a dead end, and the false claim propagates into backlog rows and operator
decisions as settled fact. It is strictly worse, and it had none of the scepticism applied
to it.

**New binding rule** (added to `docs/CLAUDE-RULES-CANONICAL.md` § "Green is not evidence"):

> Before writing that something **cannot be measured / is not replayable / needs new
> tooling**, check `docs/research/RESEARCH-CAPABILITY-INDEX.md` and grep
> `scripts/research/`. A code comment, constant name, or skill asserting **impossibility**
> is not authoritative — it is scoped to its own module until proven otherwise. State
> *which* tool you checked.

---

## 3. What shipped in response

| Fix | What it does |
|---|---|
| **`docs/research/RESEARCH-CAPABILITY-INDEX.md`** | The missing artifact: a *"can we measure X?"* routing layer over all 51 research tools, organised by **question** rather than by module — the shape a research session actually queries |
| **`scripts/ops/check_research_index.py`** (CI) | Fails the build when a `scripts/research/` script is in neither the index nor an `EXEMPT` list that **requires a reason**. "Nobody indexed it" is not representable |
| **`backtesting/SKILL.md`** | False completeness claim removed, replaced with an explicit "this is NOT the full toolbox → see the index", plus the incident that makes the point |
| **`regime_debt_matrix.py`** | The `_UNREPLAYABLE` comment corrected; the set itself is unchanged and still right (`exit_head_*` genuinely is out of scope *for that harness*) |
| **`CLAUDE-RULES-CANONICAL.md`** | The impossibility-claim rule above |

### 3a. Two live swallowed-failure instances found while auditing

Auditing the exit-head path meant reading its runner, which surfaced the same
green-while-measuring-nothing pattern in the **research workflows**:

- **`research-exit-head-build.yml`** — `|| true` on *both* the panel build and the analyzer.
  An empty panel still rendered a `VERDICT.md`. Also **could not target a strategy at all**:
  no `roster` / `clock_tf` input, so a `backtest_system` run silently used the default
  roster. Fixed: inputs added (validated against arg injection), failures no longer
  swallowed, and a **non-empty panel assertion** gates the analyzer.
- **`research-panel-build.yml`** — five swallowed steps. Fixed with three *different*
  answers rather than one blanket rule, because the right semantics differ:
  the panel build is load-bearing → **must fail**; the multi-outcome sweep tolerates one
  outcome failing but now **fails if every** outcome failed (a sweep that produced nothing
  is not a success) and warns on partial; the per-cell step is explicitly requested → **must
  fail**.

**My own guard would not have caught any of them.** `artifact-validity-guard`'s `PRODUCER`
pattern matched only `fetch_*` / `_produce` / `_backfill` / `_snapshot` — missing the entire
research producer family (`build_*_panel.py`, `analyze_*.py`), which is exactly where
verdicts are read from. Widened; now clean across all workflows.

---

## 4. Honest assessment — will this actually prevent recurrence?

Partly, and it is worth being precise about which part.

**Mechanically enforced (will hold):** index completeness (CI), no swallowed research
producer (CI), no vacuous artifact (CI), shallow-clone truncation surfaced (SessionStart
hook). These do not depend on a session choosing to be careful.

**Documentation-only (depends on the session reading it):** the impossibility-claim rule,
the `backtesting` skill's corrected scope note. These are the same *kind* of control that
already failed today — `research-driver` and `backtesting` were both well written and I
still went wrong, because I never had reason to open the one that mattered.

So the durable protection is the **index + its CI check**, not the prose. The prose exists so
the next session knows *why* the index is authoritative.

**What is still missing** (filed, not fixed here):

- `BL-20260730-HARNESS-LEVER-MAP-COUPLING-GUARD` — no mechanical coupling between live
  strategy keys and the harness lever maps, so a new live lever can be added and the
  research tools keep silently measuring the old behaviour. This *already happened today*
  with `side_filter`.
- No skill owns the **regime-cell lifecycle** (author → re-audit → retire). It currently
  rides `research-driver`'s loose umbrella, which is how the self-erasing-queue and
  cosmetic-cell problems went unnoticed. A `regime-selectivity` skill was proposed earlier
  in this session and remains unbuilt.
- The index routes to tools but does not state each one's **fidelity limits** (which levers
  it cannot model). That is where today's error actually lived, so it is the most valuable
  follow-up: `BL-20260730-CAPABILITY-INDEX-NEEDS-FIDELITY-LIMITS`.
