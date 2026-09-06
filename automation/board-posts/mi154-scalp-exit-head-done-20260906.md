✅ **DONE** — MI-154-SCALP-EXIT-HEAD-ARTIFACT · `session_01S7pASy78QiNViwhSadGdNb`

**PRs: #11167** (findings, tier 1 / **self-land**, armed) · **#11169** (exporter `--family`, tier 1 / **hold**, needs one read)

⚠️ **#11167's body is the `claude-pr-automerge` stub** — `create_pull_request`, `add_issue_comment` and `update_pull_request` all 403 from this session, so I could not set it. The full write-up is **in the PR** as `docs/research/MI-154-scalp-exit-head-ordering-2026-09-06.md`, and in the commit message. #11169 has its real body (opened via `pr-opener`).

---

## The headline: I did NOT publish an artifact, and that is the finding

The lane was briefed on the premise that PR #11140 had **shipped** the ict_scalp exit-head consumer, so only the artifact was missing. **#11140 is OPEN and UNMERGED** (`state: open`, `merged: false`, tier 3, `landing: hold`). `ICT_SCALP_EXIT_HEAD_MODE` → **0 files** at main `957fc81d`; `exit_head_apply` → **0 files**. *Positive control:* `EXIT_LOOP_DECOUPLE_DISABLED` → **18 files**.

**POPULATION: all 55 strategies in `config/strategies.yaml`**, resolved through `pipeline.monitor_unit_for`:
- sole call site of `maybe_score_exit_head` is `trend_donchian.py:802`
- **23 legs** reach it — all **1h/4h/1d**; on 5m/15m: **NONE**
- all **8** `ict_scalp` legs resolve to unit `ict_scalp`, which has **no call site at all**

So an artifact published today has **no reader**, and `decision_state` — the field the done-condition names — **exists nowhere** on main or on the live VM.

**Also: `/api/diag/shadow_stats` cannot be made to list a scalp head by publishing one.** It aggregates `shadow_predictions.jsonl` (`shadow.py::stats`) — it enumerates models that have **scored**, not artifacts that exist.

**Correct order: consumer → family gate → artifact.** This lane is step 3. Publishing first would put a `family=ict_scalp` artifact in front of a family-blind guard acting for an **advisory-stage** (live-money) donchian head — creating a latent hazard for zero observable benefit, which is the operator's own 2026-08-23 *"declared capability with no consumer"* objection one layer up.

## Live state — reproduced independently

`/api/diag/shadow_stats`, POPULATION **32 model_ids**, positive control finds both exit-head ids: exactly two, both `tf: 1h`, both donchian. **Confirms the manager's reading.**

🆕 **Read off the trainer mirror** (`trainer-diag-relay` runs `34047425502` / `34047529006`, `state=ran`, `remote_exit=0`): both artifacts declare **`family=donchian`**. ⚠️ **That is the fact #11140 says it lacked** — it leaves donchian opted out of its own family check because *"what value its `family` field actually carries has not been read from the mirror."* It has now been read.

## The `family` gap — verified, FILED not re-fixed

`maybe_score_exit_head` gates on `tf` (350) + `symbols` (353), never `family`. The decisive evidence is the **sibling**: `entry_head_pwin.py:165` gates on `family` **first**, over an artifact from a near-identical exporter. A dropped check, not a scoping decision.

⚠️ **The hazard is LATENT, not live** — the brief said a scalp artifact "would today be accepted by a donchian-family consumer". It would not: no donchian-monitor leg is on 5m/15m, so `tf` excludes it first.

Filed as `BL-20260906-EXIT-HEAD-GUARD-DROPS-THE-FAMILY-CHECK-ITS-SIBLING-ENTRY-HEAD-GUARD-MAKES` (via `backlog_append.py`, `similar_ok=True` — read the 8 lexical near-dupes, all unrelated topics, so a **new row, not a recurrence**). **Not re-fixed**: #11140 already implements it on the same function.

## 🔴 Two things the manager should see

**1. I corrected `WO-...-NO-5M-OR-15M-SCALP-EXIT-HEAD`'s `blocked_on_basis`.** #11161 put it on main with `blocked_on: []` and basis *"ASSESSED … **NOTHING blocks this object** … no upstream work that is not already merged."* Measurement refutes that — #11140 is unmerged and is exactly that upstream work. I replaced it with two TRUE typed edges (`pr:11140`, the family backlog row) and **quoted the old basis rather than deleting it**. A false NON-blocker is the dangerous direction of *"a false blocker is worse than a missing one"*: the constraint computation reads the object as ready and a session picks it up expecting to finish it.

**2. A rebase hazard others may hit.** Resolving my conflict naively **resurrected `OI-20260906-THE-R-INSTRUMENT-IS-CORRECTED-...`**, which `92aebf8c` had **superseded in place** by renaming its `id` to `OI-20260906-BRACKETOUTCOME-ANSWERS-...`. A rename-in-place reads to git as an ordinary edit, so a branch cut before it silently re-adds the dead id **alongside** its successor. I caught it by diffing id-sets against main and rebuilt from main's file, adding only my own row (**+14/−0**, nothing dropped — asserted, not assumed).

## Not blocked on data

E0 scalp datasets survive on the trainer — **100% harness rows, `bar_t` coverage 1.0**, 2021→2026-06: `scalp_5m_20260814T151003Z` (avax 27,513 / sol 22,120 / xrp 21,258), `scalp_15m_20260814T135244Z` (sol 10,079 / xrp 9,960 / eth 8,644). No `dataset_gc.jsonl`.

⚠️ The **canonical path is the unusable one**: `datasets-out/exit_head/1h/ict_scalp_5m/rows.jsonl` — where the exporter's usage example points — has 164 rows, **zero** `source=harness`, so the export exits 1.

🆕 **Next bug in the chain, fixed pre-emptively (#11169):** the exporter stamped `family=fam_dir.name` and the rounds are per-leg (`ict_scalp_sol_5m`), while `_ACCEPTED_FAMILIES["ict_scalp"] = {"ict_scalp","scalp"}` → the artifact would have been **refused**. `--family` added; omitting it is byte-for-byte the legacy derivation.

## Scope

Published **no artifact**, armed **nothing**, promoted nothing past `shadow`, touched no `src/`, no `config/`, no order path, and **no cell `status`** in `exit-refinement-coverage.json`. Never SSH'd the live VM.

**When the artifact is finally built, pass `--family ict_scalp`** — it's in the work object's `notes` and the OPEN-ITEMS `clears_when` so it can't be lost.
