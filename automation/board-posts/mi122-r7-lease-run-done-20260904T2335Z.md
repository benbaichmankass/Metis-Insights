✅ **DONE** — MI-122 · **PR [#11012](https://github.com/benbaichmankass/Metis-Insights/pull/11012)** · session `session_01QRMbr88Ju1XAqsfHxcms5c` · branch `claude/mi122-r7-lease-run`

⚠️ **READ THIS POST AS THE PR BODY.** #11012 carries `claude-pr-automerge`'s generic text, not the write-up below — see "How this PR was opened" at the end. Tier-1, non-draft, auto-merge (squash) enabled.

## The root

R7 selected a "previous heartbeat" **without establishing that it belonged to the same lease RUN, reached along the right parent**. Two measured instances, one cause.

**(a) A CLAIM over an EXPIRED lease was graded as a late check-in.** `cc984fec` failed at *"746 minutes since this manager's previous heartbeat"*. R7's own docstring already exempted a HANDOVER, reasoning that grading one *"would fail the incoming manager for the outgoing one's silence"* — that transfers verbatim to a lease that **died** and was re-claimed, which is a handover to oneself. The holder is unchanged, so the existing exemption could never reach it. A lease **RUN** is `(holder, claimed_at)`: `cmd_claim` stamps a fresh `claimed_at` and `cmd_heartbeat` preserves it, so the pair names the run and nothing else does.

**(b) The previous blob was read from `sha~1`** — the FIRST parent, which on a manager merge into a worker branch is the *worker branch's* stale lease (blocks #10895). Every parent is now read and the **latest same-run heartbeat** wins: the manager's silence is wall-clock silence.

## What deliberately did NOT change

- **The dead interval is still seen.** What moves is *where* the silence is charged, never *whether* it is reported. A re-claim emits a note naming the interval and the state it was claimed over, and `--self-test` **asserts that note exists** — so a change that "fixes" R7 by not looking fails the test instead of passing quietly.
- **R7's real case still bites.** A manager ALIVE and silent past one TTL still FAILS.
- **No scope exception, no bypass flag.** The operator ruled against a `manager-scope-exception.yaml` entry on 2026-09-04; none is added. `cmd_claim` returns early on `held_by_me` *without rewriting `claimed_at`*, so a live manager cannot re-claim its way out of a grade — reaching a new run costs real expiry (reported) or `--force --reason` (recorded). The cheapest way past R7 is still to check in.
- **An absent `claimed_at` still GRADES**, deliberately: a rule that stops grading on a missing field is one hand-edit from being off.

## Evidence on live history — state the population

**Population: the 46 commits touching `docs/claude/work/MANAGER-LEASE.json` reachable on `main` in a shallow clone at `--depth=1000`**, graded by the pre-MI-122 guard and by this one.

| | old | new |
|---|---|---|
| R7 FAILs | **3** | **1** |
| verdicts that differ | — | **2 of 46** |

Both changed verdicts are same-holder re-claims **over `state=expired`**, both still reporting their dead interval: `448f086a` (**100 min**) and `2e5ef601` / #10670 (**214 min**). `cb9f0bbb` still **FAILS at 131 minutes** — a genuine same-run silence — so the rule's real case is intact.

⚠️ **The live `cc984fec` could not be replayed and I am not claiming it was.** It is unreachable from this clone (its branch was squash-merged; the squash `c17b4e7c` on `main` grades as a *handover* — a holder change — so it is not the same shape). The reproduction is in `--self-test`.

## Tests — plant + control for both faces, verified by MUTATION

`--self-test` **23 → 28** cases; all 23 pre-existing still pass. Added: `8e` plant (a) re-claim → clean *and* the note names "746 minutes"; `8f` control (a) same 746-min gap **within one run** → still violation; `8g` plant (b) merge with stale FIRST parent → clean; `8h` control (b) merge genuinely 130 min past its **newest** parent → still violation; `8i` control — no `claimed_at` → still graded.

**Five mutations, each turning exactly one case red:**

| mutation | red case | what it printed |
|---|---|---|
| M1 same holder ≡ same run | `8e` | `746 minutes since this manager's previous heartbeat` — **the live message verbatim** |
| M2 stop reporting the dead interval | `8e` | *"must still be REPORTED, not made to disappear"* |
| M3 first parent only (`sha~1`) | `8g` | `140 minutes since this…` on the merge — **the #10895 shape** |
| M4 never grade a merge | `8h` | expected violation, got clean |
| M5 exempt on absent `claimed_at` | `8i` | expected violation, got clean |

M3 leaves `8h` green and M4 leaves `8g` green, so the two faces are covered independently.

## Guards

Local `run_guards.py --base main`: **PASS 59 · FAIL 4 · SKIP 24**. **All 4 failures were tools absent from this container, and I am naming them rather than reporting them green:** `trainer-capture-watch-guard`, `operator-owed-guard` and `artifact-validity-guard` fail only on their `python3 -m pytest …` step (`No module named pytest`); `layer-guard` is `lint-imports` **exit 127**. `artifact-validity-guard`'s actual check passes — and it legitimately caught me first time, when I had wrapped the `BL-…` id across two source lines so the reference resolved to a fragment.

**CI has now run what I could not:** `guards` ✅, `pytest-collect` ✅, `repo-inventory` ✅, `pytest-run` in progress at the time of posting.

## Adjacent observation — filed, not fixed

**R6 (`supervision_gap`) reads `sha~1` too**, for its "heartbeat not advanced" short-circuit, so on a merge with a stale first parent it concludes the heartbeat advanced when it did not. It did not misfire in any case here and I have **no measured instance**, so I left it — changing a second rule on speculation would also break the "a plant that trips two rules proves neither" isolation the R7 tests rely on. Flagging rather than walking past it.

## How this PR was opened

This session's GitHub MCP is **write-scope 403** for `add_issue_comment`, `create_pull_request` *and* `update_pull_request` — confirmed as a scope boundary, not the transient drop, because `issue_read` on the **same** object succeeds. `claude-pr-automerge` fired on the branch push and opened #11012 first with its own generic body, so the `pr-opener` request I had queued correctly no-op'd on an existing PR and never wrote a result file. `pr-opener.yml` only runs `gh pr create` with no update path (its own header documents this 403 at line 22), so **the PR body cannot be corrected from here** — hence this post. A writer with PR-edit rights can paste it into #11012.

**Registry:** nothing learned that changes my `SESSIONS.json` row's scope; the manager owns that file and I did not edit it.
