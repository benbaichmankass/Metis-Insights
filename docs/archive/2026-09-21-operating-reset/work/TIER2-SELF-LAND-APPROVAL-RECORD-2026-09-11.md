# Letting an approved Tier-2 PR land — the design, and what #11738 revealed

> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> Evidence for `WO-20260911-LET-AN-APPROVED-TIER-2-PR-LAND`.
> Lane `session_01SrUaz8LmKMcSwNZEr8JupC`, 2026-09-11, against `origin/main` @ `2901ccf44`.

## The ask

Operator directive, 2026-09-11, verbatim: **"the operator does not click things"**.

`scripts/ci/check_pr_landing.py` **R4** barred a Tier-2 diff from `landing: "self"`
**whatever its approval state**. PR #11738 has been green, mergeable and
operator-approved since **2026-09-10T07:52Z** — the operator chose `r2_only` on
`DEC-20260910-EXIT-EVAL-60S-REMEDY` from a four-option popup — and unlanded ever
since. The decision was recorded; only the click was missing.

## The design, and why each alternative is weaker

R4's *reason* is sound and is not weakened: **a change and its own approval must
not be the same act.** What R4 got wrong is the **proxy** — it used *"a human
clicks Merge"* to stand for that separation. R15 replaces the proxy with the
property itself: **an approval this branch demonstrably could not have written.**

### Channel chosen: a typed record at `docs/claude/work/approvals/{slug}.json`

Weighed against the three candidates the brief named:

| candidate | why it is weaker |
|---|---|
| **GitHub review approval** | It is *a click* — the thing being removed. And it is not attributable: sessions act through a token on the operator's account, so a review carries the same identity a session can produce. |
| **The decision round-trip** (`POST /api/bot/work/decision` → `work-decision-commit.yml`) | **Strongest on provenance and it is the right end state** — a session cannot write the VM transit log it reads. But `OI-20260901-DECISION-ROUNDTRIP-...` records that **no decision has ever made the round-trip**, and it writes into `objects/*.yaml` rather than a credential path. Gating on it today admits nothing. See *Follow-up* below — it is not discarded, it is sequenced. |
| **`operator_decision` on `OPEN-PRS.json` / the work object** | The channel #11738 actually used, and it is hand-written by a session into `docs/**`, which is inside `TIER1_SURFACE` — **a session can self-land a change to it.** Used as-is it is forgeable in two acts. |

So the record gets **its own path**, and that path is the mechanism.

### What is checked (R15)

`(a)` the declaration names a record · `(b)` it lives under `APPROVAL_DIR` ·
`(c)` it **exists at the merge-base** with `main` · `(d)` it is **byte-identical
at HEAD**, and is parsed from the **merge-base blob, never the worktree** ·
`(e)` **no** path under `APPROVAL_DIR` appears in the diff at all · `(f)` it
names **this** branch · `(g)` `tier: 2` only — **Tier-3 never** · `(h)` typed
non-empty `verdict`/`channel`/`decision_id`/`work_object` (which must exist)
`/decided_on`/`decided_by`/`text` · `(i)` **`scope_paths` checked against the
diff** · `(j)` the session that landed the record must not be one that committed
on this branch, *where that is knowable*.

### How each forgery case is refused

| forgery | refused by |
|---|---|
| no approval at all | `R15(a)` |
| approval **on the PR's own branch** | `R15(e)` **and** `R12` **and** `R15(i)` — three independent rules |
| approval names a **different** branch/PR | `R15(f)` |
| diff **exceeds** the approved scope | `R15(i)` |
| **Tier-3** with a valid Tier-2-style approval | `R4` (declared) and `R15(g)` (by path, and by record tier) |
| approval record **outside** the protected directory | `R15(b)` |
| branch **edits** the record it cites | `R15(e)` |
| scope recorded **in prose only** | `R15(i)` |
| **two-PR chain** — self-land PR A writing an approval for your own PR B | `R12`, because `docs/claude/work/approvals/**` is in `LANDING_MACHINERY` |

All are planted in `--self-test` (27 planted defects, 7 positive controls) and in
`tests/test_pr_landing_tier2_approval.py` (34 tests), each asserting **the rule
that fires**, not merely that something failed.

⚠️ **The mutation checks are the point.** The on-branch forgery is caught three
ways, so a control can keep passing after its own property is deleted. Each
load-bearing predicate is therefore mutated in isolation with the overlaps
neutralised, and the suite is proved to notice
(`test_mutation_separation_predicate_is_load_bearing`,
`test_mutation_scope_comparison_is_load_bearing`).

## ⚠️ What #11738 revealed — the more valuable finding

**Its recorded approval does NOT satisfy R15, and the gap is in the RECORDING,
not in the gate.** Measured against the real diff (12 files, 11 substantive):

1. **No `scope_paths` exists.** The recorded scope is prose — *"R2 ONLY — the
   per-pass IB circuit breaker"*. `R15(i)` refuses it, correctly: a scope that
   cannot be compared to a diff grants everything.
2. **Worse — the scope is not path-expressible at all.** The operator chose
   `r2_only` and the record explicitly says R1 *"must not ride along"*. But
   **R1 and R2 both live in `src/main.py`**, so **no path set separates them**.
   A `scope_paths` covering the real diff necessarily includes `src/main.py`,
   which is exactly where the excluded remedy would go.
3. **Read adversarially, #11738 does honour `r2_only`** — established, not
   assumed: `_apply_per_account_leverage()` still precedes
   `_start_exit_loop(settings)` on both sides (697→738 at the merge-base,
   883→924 at the PR head), and **neither symbol appears on any `+`/`-` line**.
   The `src/main.py` change is R2 plumbing (`_ib_breaker_report`,
   `_ib_queue_timeout_seconds`). **But R15 cannot establish that — a human did.**
4. **No Tier-3 path is in the diff** (measured, population = the 11 substantive
   paths), so `R15(g)` is not what blocks it.

**I did not loosen the check to fit.** The honest resolution is that whoever
writes #11738's record must *read the diff, confirm it is R2-only, and enumerate
the paths they confirmed* — and that read is precisely the human read `R12`
already puts in front of the approvals directory. Simulated with such a record,
#11738 is **ADMITTED**.

⚠️ **The decision options were framed as semantic remedies and nothing at
decision time was asked to name paths.** That is the recording gap, and it is a
change to how decisions are *asked*, not to how they are checked.

## ⚠️ What this buys, stated without inflation

For a **single one-off** Tier-2 PR this is roughly **a wash** — one human read of
an approval record instead of one merge click. The win is elsewhere:

- an approval is **per-decision, not per-PR** — every later push under an
  existing record lands free, and #11738's author has already re-pushed twice;
- the artifact a human reads is a **12-line JSON carrying the operator's verbatim
  words**, not a thousand-line diff;
- the scope becomes **machine-checkable**, which nothing currently is;
- and it is the **precondition** for the zero-click path below.

## ⚠️ What R15 does NOT establish

It proves an approval was **separate from the branch it lands** — *not that the
operator originated it*. **Measured 2026-09-11:** every squash-merge on `main`
carries author `Ben <…@users.noreply.github.com>` and committer
`GitHub <noreply@github.com>` (population: the 15 most recent commits on
`origin/main`), so **commit metadata cannot distinguish a session-written record
from an operator-written one**. `channel` records a *claim*.

What stands behind that claim is **R12** — a human read the PR that created the
record — plus **R15(j)** where it is knowable. Clause (j) only ever **adds** a
refusal: `Claude-Session:` trailers cover **10 of the 15** most recent commits on
`main` (66.7%; automation commits carry none), so their absence is *we did not
look* and is never read as a pass.

## Follow-up, deliberately not built here

Wiring `work-decision-commit.yml` to write an approval record directly would make
the path **zero-click** and operator-originated. It is **not free**: that
workflow lands through `commit-to-main`, which self-lands as Tier-1 automation,
and `APPROVAL_DIR` being in `LANDING_MACHINERY` would block it under R12 —
**by construction, and correctly**: if a self-landing automation route could
write a credential, anything that can trigger that route can mint one. Resolving
that is its own decision, and it needs the decision popup to ask for
`scope_paths` at decision time (finding 2 above). Recorded, not attempted.
