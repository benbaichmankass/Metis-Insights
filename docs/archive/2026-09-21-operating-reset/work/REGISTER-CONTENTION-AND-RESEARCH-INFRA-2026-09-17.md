# Register contention, and whether the research/testing/ML infra is fit for purpose

> **Doc status:** `unknown` · category `unknown` · last verified `2026-09-17` · registered in [`docs/DOCUMENT-INDEX.md`](../../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> ENGINEERING LANE, `session_01SzoZAiXJibakevDU9pbwxm`, under
> `WO-20260917-ENGINEERING-LANE-STRUCTURAL-FIXES-AND-RESEARCH-INFRA-FITNESS`.
> Operator, 2026-09-17: *"there are a lot of structural fixes need in many
> places, and we need to put on emphsis on making sure all the
> reasearch/testing/ml infra is fit for purpose and designed to be utlizted
> correctly and efficiaently"*.

---

## 1. The conflict census — MEASURED, with both controls stated

**MEASURED 2026-09-17.** Population: **every open PR — all 70**, both API pages,
deduped. Base `origin/main` `19e59662`. Probe `git merge-tree --write-tree` on a
clone **unshallowed first** (5,356 commits; this container arrives at 50, and
`git log` answers plausibly and wrongly on the truncated one —
`BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE`).

⚠️ **The first probe I wrote issued a FALSE CLEAN BILL and the control caught
it.** Its positive control inserted a newline at line 1 of a register — a
*cleanly-mergeable isolated hunk* — so it reported `conflicted=False` and would
have reported zero conflicts across the fleet. Replaced with a **whole-file
rewrite off an old commit**, which overlaps every later hunk by construction:
`conflicted=True, rc=1`. Negative control `main` vs `main`: clean. This is the
second consecutive session whose first conflict probe was wrong in the
reassuring direction.

| | conflicted | population |
|---|--:|--:|
| **all open PRs** | **56** | **70 (80.0%)** |
| substantive (`claude/**`) | **12** | 14 |
| **`automation/**` bots** | **44** | **56** |

**Conflicted paths, by how many PRs each blocks:**

```
 28  docs/claude/DUE.json          28  docs/claude/DUE.md
 23  docs/claude/ERROR-FEED-DIGEST.json   23  ...ERROR-FEED-DIGEST.md
  8  docs/claude/health-review-backlog.json    8  CLAUDE.md
  8  docs/claude/session-board.json            5  docs/claude/CONSTRAINT.json
  5  docs/claude/READOUT.md                    5  docs/claude/PROBES.json
```

**ZERO conflicts in `src/`, `tests/`, `scripts/` or `config/`.** All 22 distinct
conflicted paths are registers or generated artifacts.

### Two corrections to the dispatch brief, both in the direction of "worse"

1. The brief says **"9 of 14 substantive"**. It is **12 of 14** — only `#12398`
   and `#12400` are clean.
2. The brief **does not mention the automation half at all, and it is four times
   larger.** 56 of the 70 open PRs are cron-authored register refreshes;
   **23 `error-feed-digest` PRs had stacked up since 2026-09-13.**

### And the dominant blocker is NOT the one the brief names

The brief reports `health-review-backlog.json` in "5 of 7" — true of a 7-PR
sample of *substantive* PRs, and not true of the population. Over all 70 it
blocks 8, against 28 for `DUE.*` and 23 for `ERROR-FEED-DIGEST.*`.

**That difference changes the diagnosis.** `DUE.*` and `ERROR-FEED-DIGEST.*` are
**fully generated files**, committed to the repo and rewritten wholesale by their
crons. Two PRs that each regenerate the same artifact conflict **by
construction** — so a cron that opens a PR per run conflicts with every previous
unmerged run **of itself**. That is not register *contention* between authors; it
is **one deterministic renderer racing itself**, and it accounts for **51 of the
56** conflicted PRs.

---

## 2. The root cause, and why the existing remedy could not reach it

`commit-to-main` already carries a `refresh-command` input whose docstring
describes this exact failure. It could not fire here, for two independent
reasons:

1. **It lived in the merge-SUCCEEDED arm.** A merge that CONFLICTED went
   straight to `git merge --abort` → `failed_conflict` → branch stranded
   permanently. The one remedy that fits a generated file was unreachable in
   exactly the case that needed it. The only conflict the action could resolve
   was a single hardcoded path (`docs/claude/session-board.json`) whose own
   comment records that it is now **unreachable**.
2. **Almost nobody set it.** MEASURED over all **27** call sites: **1** sets
   `refresh-command`; **27** set `verify-merged` — the latter is the positive
   control that the probe can see a real input at all.

### The change

When the stale-branch merge conflicts, recompute instead of aborting — under
**three conditions, all required**, so it can never resolve a file the caller
does not own:

1. a `refresh-command` is set (the caller has **declared** it can recompute);
2. **every** conflicted path is one the caller named in `paths:` — one path
   outside that list means two runs disagree about data this action cannot
   settle, and it still aborts;
3. the recompute **succeeds**.

Main's side is taken first (`--theirs`), so nothing already on main is lost and
any declared path the command does not rewrite keeps main's content; the
recompute then runs over it. Three outcomes, never collapsed:
`refreshed_after_generated_conflict` · `failed_conflict_rederive` (*we tried and
could not*) · `failed_conflict` (*we refused*).

**Callers wired:** `error-feed-digest`, `probes`, `due-list`
(`constraint-readout` already had one).

---

## 3. THE OBSERVATION — replayed against the REAL stranded branches

Not a self-test. The inputs are the **actual unmerged heads on `origin`**.
For each, reproduce `git merge origin/main` (confirming the conflict is real),
apply the new resolution, then re-probe the result with `merge-tree`.

⚠️ **`paths:` and the recompute command are parsed from the workflow files
themselves**, not transcribed — v1 of this replay hand-copied them, mis-copied
two, and produced 11 spurious "aborts" that were my transcription error and not
the fix.

**POPULATION: 33 real stranded automation PRs — every open conflicted PR of the
four wired callers.**

| outcome | n |
|---|--:|
| **`RESOLVED_CLEAN`** (CONFLICT → CLEAN) | **30** |
| `ABORT_undeclared` (correctly refused) | 3 |

The 3 refusals are correct: `#11953`, `#11939`, `#11919` conflict on
`docs/claude/session-board.json`, which is **outside their declared paths** —
condition 2 doing its job.

### The guard, and its own mutation test

`tests/test_commit_to_main_callers.py`: a caller that lands an artifact it
**generates in-job** must declare a `refresh-command`. It reuses the file's
existing `WRITE_OUTPUTS` registry rather than a second copy.

**It immediately found a 4th caller I had not identified** — `sunset-pass`.
Exempted, because `sunset_pass.py --write` lands `comms/sunset/<UTC-date>/`, a
fresh directory per run (verified on disk: `2026-09-01`, `2026-09-14`), so two
runs write **disjoint** paths and cannot self-conflict. The exemption is
**VERIFIED, not presence-only**: a name that is not a registered write tool fails
the build. Residual named rather than hidden — two runs on the *same* UTC date
would share a path.

**6 of 6 realistic mutations caught. Two initially PASSED and are recorded
because the reasoning behind each looked sound:**

| mutation | first result | fix |
|---|---|---|
| `--theirs` → `--ours` in the recompute | **MISSED** — a bare `"--theirs" in s` was satisfied by the *unrelated* session-board block above it | pin the exact line |
| widen `SELF_DISJOINT_OUTPUTS` to silence the rule | **MISSED** — the finder still matched, so the finder's own non-vacuity test passed while the invariant it feeds had been exempted into inertness | assert the count of callers **actually subject** to the rule |

The second is the sharper one: **non-vacuity of the finder is not non-vacuity of
the rule.**

---

## 4. Is the research/testing/ML infra fit for purpose?

**VERDICT: the harnesses are fine. The last hop to a durable artifact is what is
broken.** Every item below is a mechanism that is built, tested and green in a
harness while producing nothing in production.

### 4.1 MI-287's harnesses — **THEY PASS** (this settles `DEC-20260917-E35-GATE`'s precondition)

Measured **by running all 23**, not by reading MI-287's lifecycle state — which
the decision's own note warns is indistinguishable between a session that fixed
everything and one that fixed nothing.

The population was established two independent ways that **agree exactly**: the
23 legs reading `harness_failed` at the fix's parent `41a2f5e56`, and the 23
evidence records rewritten by the fix commit `0be1980a2`. Symmetric difference
empty.

| | n |
|---|--:|
| found | 23 |
| ran | **23 of 23** |
| **PASS** | **22** |
| FAIL (real assertion failure) | **0** |
| COULD_NOT_RUN | 1 |

The one exception is `splg_trend_long_1d`: the venue serves **no rows for
SPLG**, with the SPY/GDX/ES=F control returning 252 each — an upstream data
absence, not a harness defect and not a dependency problem.

⚠️ **The exit code is NOT the verdict** — `build_strategy_evidence.py::main`
returns 0 unconditionally except for an unknown leg name, so all 23 exited 0
*including* the failure. The verdict lives in `coverage_state`.

⚠️ **The mandate's premise was refuted:** there were never 23 *broken harnesses*.
There was **one dependency floor** — `yfinance>=0.2.0` admitted a version whose
plain-`requests` User-Agent Yahoo answers with HTTP 429. Do not re-quote "23" as
a count of broken harnesses; it is a count of affected **legs**.

⚠️ **This does NOT open the e35 gate.** Passing harnesses ≠ an actionable gate:
MI-292's committed measurement records `actionable 0` and `below_evidence_floor
51 of 52`, because `min_closed_for_action` is 20 and exactly one leg clears it.

### 4.2 `replay-pregate` — dead in BOTH directions, and the second blocker is recorded nowhere

Population: **6 nightly runs, 2026-09-12 → 2026-09-17, consecutive.** **6 of 6
partial, 0 complete.** `git log origin/main -- runtime_logs/replay_pregate/` is
**empty — zero reports have ever reached main.**

1. **It cannot finish.** The poll deadline is 38 min; at the observed
   ~3.8 min/head, 22 heads needs **≈84 min**. The `timeout-minutes: 45→80` raise
   did not change this (5 of the 6 runs carry it and still stop at 10) — that
   raise sized the *job*, not the *poll*. The 10/22 wall has not moved in 15 days.
2. **⚠️ It also cannot LAND, and the open item does not carry this.** PR #12385:
   `guards` ❌ with `PASS 67 · FAIL 1`, the failure being `pr-landing-guard` R5 —
   `runtime_logs/**` is **not in `TIER1_SURFACE`**. A *complete* run would be
   refused identically. Positive control that this is not a general outage:
   `PROBES.json` lands on main same-day, because its artifact is under `docs/**`.

**New finding:** all 10–11 graded heads are `btc-*`, in sorted order. **Not one
non-BTC head has ever been graded** — an absence no consumer of `latest.json`
can see, because a partial run deliberately never writes it.

### 4.3 `probe_actions_log` — blind, 60 of 60

`docs/claude/PROBES.json` (`generated_at 2026-09-17T10:07:08Z`): 68 rows, verdicts
`pass 6 · fail 5 · could_not_run 57`. All **3 of 3** rows backed by
`probe_actions_log` read `could_not_run` / `exit_2`, each *"20 run(s) listed but
NONE had readable logs"*. Run **listing** works; only log **download** fails.

Its self-test on main **PASSES with 16 planted controls all firing** — against a
fake HTTP server, while it reads zero real logs. **PR #11925 has the fix and has
sat unmerged for 5 days**; main does not have it (measured three ways on the
file). Corroboration the PR could not gather: a real job log **was** read via the
API today (4303 bytes), so the logs exist and are readable — the fault is in the
reader's own HTTP path, not permissions or retention.

### 4.4 Harness reachability — the "47 of 51" claim still holds on its own terms, and the headline now overstates the harm

| | 2026-07-30 | 2026-09-17 |
|---|--:|--:|
| `scripts/research/*.py` | 51 | **157** |
| in no skill | 47 | **145** |
| **rate** | **92.2%** | **92.4%** |

The ratio is unchanged to within 0.2pp while the absolute count tripled.

⚠️ **But the correction matters:** `docs/research/RESEARCH-CAPABILITY-INDEX.md`
— the artifact the July audit named as missing — **was built**, is 186 KB, was
**updated today**, and names **165 of 242 (68.2%)** tools. Counting index OR
skill OR workflow, **179 of 242 (74.0%)** are reachable and **63 (26.0%)** are
not. So *"92% of research tools are unreachable"* would be **wrong today**;
routing moved into an index rather than into skills.

**Method stated:** basename substring match over `.claude/skills/**`,
`.github/workflows/**` and the index; positive control fired on 4 known-reachable
tools. It over-counts reachability, so 26% is a **floor**.

**Still true, same file, 7 weeks on:** `backtesting/SKILL.md` names **4 of 13**
`scripts/backtest_*.py` entry points while its `description` calls them *"the
standalone research harnesses"* — the exact false-completeness defect
`CLAUDE-RULES-CANONICAL.md` cites. 7 of 13 are reachable from nothing.

### 4.5 Do the harnesses actually run? — **yes**

8 of 8 representative scripts invoke correctly (5 reachable, 3 orphaned).
End-to-end: `backtest_squeeze.py` → exit 0, `trades=100 win_rate=33.0%`;
`backtest_orb.py` (orphaned) → exit 0; `backtest_xsec_momentum.py` → exit 2,
*"need >= 2 universe assets"* — **refusing correctly rather than fabricating.**
`pytest tests/ml/` → **1097 passed, 5 failed, 3 skipped**; all 5 failures were
`ModuleNotFoundError: sklearn`, and after installing it the affected directory
passed 17/17. **0 real failures.**

⚠️ **One caveat worth carrying:** `data/backtest_candles.csv`, the
`BACKTEST_DATA_PATH` default, is **5001 rows spanning 3.5 days (2022-07-23 →
2022-07-27)**. A session running a harness bare gets a 100-trade, 3.5-day result
that reads like an answer. The harnesses do print their date range, so it is
provenanced — but the default is a smoke fixture, not a research corpus.

---

## 5. What I fixed, what I left, and why

**Fixed:** the generated-artifact conflict class (§2–3) — the root cause of
**51 of 56** conflicted PRs, observed resolving 30 of 33 real stranded branches.

**Left, with reasons:**

| left | why |
|---|---|
| `replay-pregate` R5 / `runtime_logs/` | Landing machinery, a **separate concern** from this PR (*one PR = one concern*). Filed with the exact remedy. |
| `replay-pregate` 38-min poll vs ~84 min | `clears_when` (b) is explicitly an **operator decision** to re-scope; a session may not take it. |
| PR #11925 (`probe_actions_log`) | Already written and open — it needs a **merge decision**, not a re-implementation. |
| `backtesting/SKILL.md` completeness | Tier-1 and real, but a skill rewrite is its own unit, not a rider on a landing-machinery PR. |
| The 12 conflicted substantive PRs | Each needs its **author's** judgement on a register union; resolving them from here is `BL-20260814-HAND-RESOLVED-BACKLOG-MERGE-SILENTLY-REVERTED-SIX-ITEMS-INCLUDING-A-RESOLUTION`. |
| Anything Tier-2/3 | Out of lane scope. Proposed to the manager, never taken. |

**Not established, stated plainly:** whether the trainer VM is memory-pressured
*today* — that needs the `trainer-vm-diag` relay, which was not used, and no
repo-reachable surface carries trainer `free -m`. The OOM framing in that open
item's own title is already refuted in its own `detail` (both OOM greps empty);
the binding constraint is the **rate**, and that is measured.
