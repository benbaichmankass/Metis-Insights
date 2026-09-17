# MI-278 U27 — R6's residue is zero, so it can stop being diff-scoped; and R1 stops prescribing a 65-file write

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Row:** `BL-20260912-DOCUMENT-INDEX-GUARD-R1-PRESCRIBES-A-65-FILE-WRITE-TO-FIX-A-ONE-ROW-REGISTRATION-THE-SAME-TRAP-ITS-OWN-DOCSTRING-ALREADY-NAMES-FOR-THE-DATE`

---

## 0. ⚠️ This unit corrects the same claim of mine twice, and the second correction reverses the first

I published three readings of the 65-file `document_index.py --write` diff that rode PR #12122. Two were wrong.

| # | where | claim | verdict |
|---|---|---|---|
| 1 | #12122 body | *"the index was behind"* | partly right, no mechanism |
| 2 | #12122 comment | *"the churn was **avoidable** — hand-insert a stub"* | **right about the mechanics, wrong about the value** |
| 3 | here | **it drained R6's entire residue**, which is the precondition R6's own comment names for un-scoping it | measured below |

Reading 2 called a residue drain "avoidable churn" **because I did not know R6 existed.** It had merged four hours before the run — `daaa083ee` (MI-280, #12018) at 2026-09-12T20:31Z. The correction is not that the diff was small; it is that its value was the opposite of what I said.

## 1. The measurement

Every committed row's status, against a fresh `build_rows()`:

| ref | drifted rows | of |
|---|---|---|
| `b209768c2` (before #12122) | **46** | 1072 |
| `origin/main` (after) | **0** | 1073 |

**Positive control:** the same probe finds 46 at the earlier ref, so the zero is a reading and not a broken query. Re-checked after three further PRs merged (`77aa19915`): still **0**.

R6's own comment states the blocker in its own words:

> *"There are 47 standing disagreements on `main` today; an unscoped rule would fail every PR in the repo from the moment it merged, which is not a guard, it is an outage."*

That is gone.

## 2. What ships

**(a) R6 gains an UNGATED step.** `check_document_index.py --all` reports every drifted row, not only those the diff touches, registered in `run_guards.py` beside the diff-scoped one. This is the `diagnostic-provenance-guard` / `api-tier-policy-guard` pattern named in `CLAUDE.md`, applied on the same evidence shape — an ungated backstop added *only once the residue reads zero*.

⚠️ **The diff-scoped step could not have caught these 46 and that is not a criticism of it.** A row drifts when `status_for` or `ACTIVE_DOCS` changes — i.e. from a commit touching neither the row nor its document. All 46 accumulated exactly that way, invisibly, while the guard printed `OK`.

⚠️ **Under `--all` an uncomputable row set is a FINDING; under the scoped rule it is not.** That asymmetry is asserted by a control. An ungated assertion that reports nothing when its own input could not be built reads byte-identically to a clean tree — sub-class C.

⚠️ **The cost is stated, not hidden:** a future change to `status_for` or `ACTIVE_DOCS` now reds every PR until someone runs `--write`. That is the accepted trade in both precedents, and it is the right direction — the alternative is what just happened. **Rollback is deleting one step.**

**(b) R1 names the one row.** Its remedy quoted a command whose diff was two orders of magnitude larger than the finding. It now quotes **the generator's own stub** for the unregistered path — so pasting it cannot drift from what `--write` would produce, and R6 checks that — while **still naming `--write`**, with its blast radius stated, for the several-documents case that the one-line remedy does not serve.

⚠️ This is the trap `document_index.py::carry_last_verified` already names in its own docstring (*"the guard prescribed it, which made it a trap rather than a footgun"*). That fix carried the **date**; the **status** surface kept moving. **The earlier fix was correct and incomplete.**

## 3. ⚠️ The cost side, and my own probe under-counted it

The same `--write` **erased the hand-written basis on 35 index rows**. I first reported **19** — my grep required a two-part basis and silently dropped the 16 rows carrying three or four parts.

Corrected: **35 rows at `b209768c2`, 0 at `origin/main`** — **24 `live`**, 2 `historical`, 9 `unknown`. So **26 rows lost a non-`unknown` status**, not 19.

**And the bases are richer than a status word**, which the first report also missed. They encode the verification property a session established: `harness-shown-able-to-fail`, `corrects-its-own-authors-published-number`, `headline-is-a-refusal`, `positive-control-on-the-zero`, `refutes-its-own-hypothesis`, `additivity-proven-not-asserted`.

**A regex returning a number is not a measurement until it is shown to find every positive.** I ran one that looked conclusive, in a unit about provenance, and published it twice.

## 4. ⚠️ Nothing is restored into `STATUS_EXPLICIT`, deliberately

The obvious move is to carry the 26 determinations back. **It is refused**, on the table's own docstring:

> *"Bulk-adding entries here to drain the `unknown` column would reproduce the defect. One row per document actually read, or leave it `unknown`."*

Sixteen of the 35 are MI-278 memos I wrote, so I could vouch for those — but adding sixteen at once is indistinguishable from the bulk add the docstring forbids, and I cannot vouch for the other nineteen at all. **`unknown` is the register's declared correct value for a determination it cannot derive**, and the bases were never a supported surface. The strings are **recoverable from git history** — recovered, to write this. Anyone who can vouch for their own row can add it, one at a time, with a basis naming what they established.

## 5. Verification

`--self-test` → **30 planted rule cases**, 0 failures. Five defects planted, all five caught:

| planted defect | control that fired |
|---|---|
| `--all` silently ignores an uncomputable row set | `R6 --all FAILS on an uncomputable row set` |
| `--all` still applies the diff scope | both `--all` firing cases |
| `--all` leaks into the scoped rule | 7 controls, incl. `the SCOPED rule must stay silent` |
| R1 drops `--write` from the stub remedy | `R1 must still name --write` |
| R1 emits a stub the generator did not produce | `with no stub R1 must fall back to the command` |

The third plant firing **seven** controls is the useful one: it shows the scoped rule's behaviour is pinned independently, so `--all` cannot be widened into it by accident.

---

## Standing

`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-…` is `loud: true` and unchanged: **17 days, −$38,851.81, onset 2026-08-27**. Nothing here addresses it.
