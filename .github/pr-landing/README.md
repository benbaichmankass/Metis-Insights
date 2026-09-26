# `pr-landing` — every PR says its TIER and how it means to LAND

One file per branch: **`.github/pr-landing/<branch-slug>.json`**, where the slug
is the branch name with a leading `claude/` stripped and `/` → `-` — the same
derivation `claude-pr-automerge.yml` uses, so the two can never disagree about
which branch asked.

Enforced by `pr-landing-guard` (`scripts/ci/check_pr_landing.py`), which runs on
every PR. Read that file's docstring for the rule-by-rule reasoning; this is the
operator's-eye summary.

> ### THE MERGE-SLOT CLAIM: USE THE PER-BRANCH ROUTE
>
> ```bash
> python3 scripts/ops/claim_merge_slot.py --branch-claim \
>     --branch <your-branch> --held-by <your session id>
> ```
>
> It writes `.github/merge-slots/<slug>.json`, which cannot conflict with another
> branch's claim. Commit it in the same push that arms the route.
>
> ⚠️ **FIXED 2026-09-24 (E51).** Until then, R13's own failure message told you
> to satisfy it by writing `merge_slot` into `docs/claude/session-board.json` —
> a file the 2026-09-21 operating reset had archived (now at
> `docs/archive/2026-09-21-operating-reset/registers/session-board.json`), so
> the remedy the guard printed could not be performed. `check_pr_landing.py`'s
> R13 message (and `slot_claim_state`'s underlying `detail`) now name the
> per-branch route above and state plainly that the legacy file is retired and
> must not be resurrected. This note originally filed the bug as
> `PI-20260921-0002` in `docs/claude/work/PIPELINE.jsonl` — by the time E51
> looked, that id had been silently overwritten by an unrelated finding
> through the append-collision defect `docs/claude/work/PIPELINE.jsonl` itself
> documents (`python3 scripts/ops/pipeline.py --check` lists it as one of 3
> GRANDFATHERED collisions), so the original filing is no longer readable
> there. The fix landed directly in code instead; this box is kept short as a
> pointer, not a workaround.

## Why this exists

On 2026-09-03, seven of the night shift's PRs sat open, green and unlanded,
waiting on the manager. Tier-1 work — which `docs/CLAUDE-RULES-CANONICAL.md`
§ Permission Tiers says needs **no** human OK to merge — was being routed
through a human on every single session, by convention.

Three of those PR bodies blamed `pr-opener.yml` for "creating every PR as a
draft regardless of `draft:false`".

⚠️ **This paragraph used to say "That is false and should not be repeated," and
that rebuttal was itself wrong.** Those sessions were reporting a real defect.
At the time, `pr-opener.yml` read the flag as `jq '.draft // true'`, and jq's
`//` treats an explicit `false` as *absent* — so
`"draft": false` was genuinely **unreachable** and every PR the relay opened came
out a draft whatever the request asked for. Filed as
`BL-20260903-PR-OPENER-DRAFT-FALSE-IS-UNREACHABLE-SO-TIER-1-CAN-NEVER-SELF-LAND-THROUGH-THE-RELAY`,
whose resolution criteria require, in terms, that this file stop asserting the
reporting sessions were wrong. This correction is that.

**Both halves are now fixed.** The expression is `jq -r '.draft // false'`, and
the default itself flipped to **`false`** on 2026-09-03 by operator ruling (see
the header of `pr-opener.yml`) — so the old sentence "`true` is only the default"
is stale twice over. **Verified by observation, not by reading the diff:** PR
#11966 was opened through this relay from a request file containing
`"draft": false` and comes back `draft: false` on `pull_request_read`, opened by
`github-actions[bot]`.

What the original paragraph got right is that a blanket "open as draft"
instruction in the spawn template, plus the permissions asymmetry that stops a
sub-session clearing its own draft, were **also** in play. Two causes, not one;
the relay bug was the half this file denied.

## The two mechanisms are not alternatives

Either one alone leaves the work sitting. Tier-1 self-landing needs **both**:

| | what it decides | on its own |
|---|---|---|
| **`"draft": false`** (in `automation/pr-requests/<name>.json`, or a direct `create_pull_request`) | **readiness** — "approved to land" | a ready, green PR that waits for a human click. This is the failure, not the fix. |
| **`.github/pr-automerge-requests/<slug>.txt`** | **landing** — `claude-pr-automerge` enables native auto-merge; GitHub merges **only when required checks pass** | against a **draft** PR it is **refused**, by design — that refusal is a correct safety property and must not be weakened |

## The four lines

Tier-1 work that should land itself on green:

```json
{
  "tier": 1,
  "landing": "self",
  "why": "docs + CI only; no runtime, order path, or config touched"
}
```

...and add `.github/pr-automerge-requests/<slug>.txt` (any contents — its
**path** is the signal) and open the PR **not** as a draft.

Tier-2 work that the operator has already approved, where the approval is
**already recorded on `main`**:

```json
{
  "tier": 2,
  "landing": "self",
  "approved_by": "docs/claude/work/approvals/{slug}.json",
  "why": "runtime exit-loop change, Tier-2 by path, landing on a recorded approval"
}
```

...plus the same arming file and merge-slot claim Tier-1 self-landing needs.
See **R15** below and `docs/claude/work/approvals/README.md` for the record's
shape. **Tier-3 never self-lands and no record admits it.**

And **one Tier-3 shape lands on a MANDATE** (R16, 2026-09-25):

```json
{
  "tier": 3,
  "landing": "mandate",
  "mandate": "MD-DEMOTE-S2-S1",
  "run_id": "<the r4-demotion-gate run id, matching the branch>",
  "workflow": ".github/workflows/r4-demotion-gate.yml",
  "why": "automated Stage-2 roster cut under a granted derisk_only mandate"
}
```

⚠️ **This is NOT a Tier-3 `landing: "self"`, and R4 is unchanged.** `"self"` at
tier 3 still fails R4 outright and no record buys one. `"mandate"` is a separate
route, graded clause by clause by `scripts/ci/check_mandate_autoland.py`
(`mandate-autoland-guard`, which `pr-landing-guard` delegates to), admitting
**exactly one diff shape**:

| clause | what it checks |
|---|---|
| **A1** | every changed path is on a closed allowlist — the roster file, the mirror-window records and their source runs, the firing records, and this branch's own declaration and merge-slot claim. **`config/mandates.yaml` is not on it**, so the PR cannot grant its own authorization. |
| **A2** | `config/accounts.yaml` changes by REMOVALS from `strategies:` rosters and nothing else — proven by filtering the removed legs out of the merge-base document and requiring the result to equal HEAD. |
| **A3** | each removed leg carries a committed mirror-window record and a firing record added by this diff, citing it, naming a mandate, with matching accounts and a reachable `source_run`. |
| **A4** | that mandate is in the **merge-base** `mandates:` list, is not `blocked_until`, and its `direction` is `derisk_only`. |
| **A5** | `scripts/ops/mandate_resolver.py`, replayed over the merge-base configs plus this diff's evidence, returns **FIRE** and proposes exactly this removal and no addition. |
| **A6** | the live account and its mirror are cut together, so the strict-equality mirror tests stay green. |
| **A7** | the branch is `automation/r4-demotion-<run_id>`, every commit is authored *and* committed by `github-actions[bot]`, the PR's own author is that bot, and an attributable merge-slot claim names the run. |
| **A8** | the granted mandate carries `autoland: true` — **the operator's arming switch**. |

⚠️ **BUILT, NOT ARMED.** No entry in `config/mandates.yaml` carries
`autoland: true`, so today every `r4-demotion-gate` run falls back to
`landing: "hold"` and a human merges — by the route working, not by its absence.
Adding that field is the operator's act, the same asymmetry that makes granting
a mandate theirs. `tests/test_mandate_autoland.py::test_the_route_ships_unarmed`
asserts it, and is meant to fail loudly on the arming commit.

⚠️ **What the route rests on, said plainly.** A7 proves the branch carries the
workflow's identity and that `github-actions[bot]` opened the PR — not that
nobody could forge `git config user.email`. What stands behind it is that the PR
author comes from a token no session holds, and that a forged branch is still
worthless: it must carry a resolver FIRE over operator-granted evidence and may
still only ever REMOVE. A wrong verdict here takes a leg **off** a real-money
book.

Anything a human must approve, or that is simply not ready:

```json
{
  "tier": 2,
  "landing": "hold",
  "hold_reason": "tier_2_3_needs_approval",
  "hold_text": "touches src/runtime/pipeline.py; needs one operator OK in chat",
  "why": "runtime pipeline plumbing, Tier-2 by path"
}
```

`hold_reason` is a **closed vocabulary** — a hold is a stated *kind* of hold, not
an adjective:

| reason | means | how it is checked |
|---|---|---|
| `tier_2_3_needs_approval` | Tier-2/3 work | say what approval, from whom |
| `changes_landing_machinery` | this PR edits the landing route itself | **verified against the diff** |
| `depends_on_unmerged_pr` | must land after another PR | **must name it as `#N`** |
| `awaiting_evidence` | prepared, but an observation must land first | say *which* observation |
| `operator_asked_to_hold` | an explicit instruction | quote it |
| `unvouchable_paths` | Tier-1 work touching a path outside `TIER1_SURFACE` | **verified against the diff** |

## What bites, and why it can

`pr-landing-guard` is a required check, and auto-merge merges only on green. So
a branch that arms auto-merge while under-declaring its tier **holds itself out
of `main` by failing its own guard**. No manager, no reminder, no race.

The checks that matter most:

- **R10** — arming auto-merge with no valid Tier-1 self-land declaration fails.
  This is **not** grandfathered for old branches: age excuses not knowing the
  rule, it does not excuse asking to merge without approval.
- **R5** — `tier: 1` is checked against the diff. The Tier-1 surface is an
  **allowlist** (docs, tests, CI, `.github/`, `comms/`, lint config). A path it
  does not recognise is not thereby dangerous — it is one the guard cannot
  vouch for, and self-landing is refused on it rather than granted by default.
- **R15** — a **Tier-2** PR may self-land, but only against an approval record
  it **demonstrably could not have written**: the record must already exist at
  the branch's merge-base with `main`, be byte-identical at HEAD, be absent from
  the diff entirely, name **this** branch, and carry a `scope_paths` glob list
  that **covers the diff**. The record is parsed from the merge-base blob, never
  from the worktree.
  ⚠️ **R4's reason is not weakened.** R4 held that a change and its own approval
  must not be the same act — correct — but used *"a human clicks Merge"* as the
  proxy. R15 replaces the proxy with the property. **Tier-3 is still refused
  outright**, and a `scope_paths` entry naming a Tier-3 path does not buy one.
  ⚠️ **What it buys, stated honestly:** for a single one-off Tier-2 PR this is
  roughly a wash — one human read of the approval instead of one merge click.
  The win is that an approval is **per-decision, not per-PR**: every subsequent
  push under an existing record lands free, and the record is a 12-line JSON
  carrying the operator's verbatim words rather than a thousand-line diff.
  ⚠️ **And what it does NOT establish:** that the operator *originated* the
  record. Commit metadata cannot tell a session-written record from an
  operator-written one — measured 2026-09-11, every squash-merge on `main`
  carries the same author and committer. What stands behind it is R12.
- **R12** — a PR that changes the landing machinery may not land itself by it.
  ⚠️ **`docs/claude/work/approvals/**` is landing machinery**, so *writing* an
  approval is a held, human-read PR while *using* one is free. That asymmetry is
  what closes the two-PR chain (self-land PR A writing an approval for your own
  PR B, then self-land B against it).
- **R13** — a branch that ARMS auto-merge must hold **an attributable,
  timestamped merge-slot claim, made in its own diff**. Arming is not a
  request to merge, it **is** the merge.
  ⚠️ **TWO ROUTES SATISFY R13, AND ON A LANE BRANCH YOU WANT THE SECOND.** The
  guard tries the per-branch route **first** and either is sufficient
  (`check_pr_landing.py::slot_claim_state`):

  | route | file | use it |
  |---|---|---|
  | legacy / automation | `docs/claude/session-board.json::merge_slot` | `commit-to-main` and the 27 workflows behind it. Still passes, byte-for-byte. |
  | **per-branch (prefer this)** | `.github/merge-slots/{slug}.json` | **every session branch** |

  ```
  python3 scripts/ops/claim_merge_slot.py --branch-claim \
    --branch claude/<your-branch> --held-by <session-id> --purpose "<what and which PR>"
  ```

  ⚠️ **`--branch-claim` IS NOT THE DEFAULT — you must pass the flag.** Without
  it the script splices the shared field, which is the conflicting route.
  **Why prefer it:** the shared `merge_slot` is ONE field in ONE file that every
  armed branch must overwrite, so every armed branch conflicts with `main` the
  moment anything else claims it. Measured over the last 40 commits to `main`
  touching `session-board.json` (2026-09-11T23:12Z → 2026-09-12T09:30Z),
  **39 of 40 moved `merge_slot.branch`** — median gap 11.5 minutes, 26 of 38
  under 16. Resolving the conflict pushes a new head and **restarts CI**, so
  resolving faster does not help. Observed with a control the same morning: all
  four ARMED PRs of one session went `dirty` together, while the only two that
  stayed CLEAN were the two declaring `landing: "hold"` — which write no claim
  at all. **Nothing is given up:** R13 never serialized (see below), so the
  shared field bought a guaranteed conflict and no exclusion. Full rationale:
  `.github/merge-slots/README.md`. The `PreToolUse` merge-slot guard in
  `.claude/settings.json` matches only `mcp__github__merge_pull_request` and
  `mcp__github__enable_pr_auto_merge`, and the arming route calls **neither** —
  the merge is performed by `claude-pr-automerge.yml` under `GITHUB_TOKEN`, so
  no hook fires even in the runtimes that do load hooks. R6 therefore mandates
  the one landing route on which the slot guard is structurally silent.
  ⚠️ R13 makes that claim **attributable and fails-closed**; it does **not**
  serialize. A committed claim reaches no other session until the branch merges
  (`BL-20260810-MERGE-SLOT-MIRROR-UNWRITABLE-PRE-MERGE`), so concurrent-merge
  safety still rests on branch-protection required status checks.
### How the AUTOMATION lane satisfies R13

Automation PRs are opened by `.github/actions/commit-to-main/action.yml`, which
**27 workflows** call. It writes all three things R11/R6/R13 ask for — the
declaration, the arming file, and the `merge_slot` claim — in the **same
commit**, because there is no session and no human in the loop to write them.

⚠️ **It did not write the claim until 2026-09-09, and the consequence was
repo-wide.** Every PR it opened failed R13, a required check, and could never
merge; measured that morning at **47 open PRs, 46 of them automation**. Because
the digest receipt is one of the things that could not land,
`check_digest_liveness` then graded `stale` — and that guard is time-based and
repo-wide, so it **reds every open PR**, trading PRs included. A generator that
cannot land its own paperwork takes the repo with it. Twice a manager
hand-pushed the missing claim to unblock one receipt; that is a workaround, and
`held_by` is how you tell them apart — `github-actions[bot] · <workflow> run
<id>` is the action, a `session_01…` id is a human intervening.

⚠️ **The claim is SPLICED into `session-board.json`, never re-serialised**
(`scripts/ops/claim_merge_slot.py`). That file is hand-maintained and matches no
`json.dumps` indent, so a naive dump rewrites ~139 lines to change 4 — and since
it is the one file every session edits, that would turn every concurrent edit
into a whole-file conflict instead of a four-line one.

⚠️ **R13 does not serialize, so the four-line conflict is routine and is
RESOLVED rather than aborted.** Two automation runs in flight each write a valid
claim and never see one another. `commit-to-main`'s stale-branch refresh takes
**main's** board — preserving the other branch's claim and anyone's
`active_sessions` edits — and re-asserts its own claim over it. **That heal is
inside the action, so it covers the automation lane only; a human branch still
resolves this by hand.**

- **R8** — `changes_landing_machinery` is verified against the diff, and the
  branch's own declaration file is excluded from that evidence. An excuse every
  branch satisfies excuses nothing.

## What this guard does *not* claim

It never says a diff **is** Tier-1. Silence from the Tier-2/3 path lists is the
guard not recognising anything — a negative with no denominator — never proof
the change is safe. Your `why` is where that judgement is recorded, in your own
words, and a human reads it on every PR that does not self-land.

## Branches cut before this existed

They report `undeclared_predates_guard`: a **pass on age**, printed loudly and
never silently. The guard arms itself as those branches drain — there is no flag
to set and none to unset. Merge `main` and declare.

## ⚠️ Quoting these paths in an issue or PR body

GitHub strips `<…>` as HTML in **issue and PR bodies**, *even inside code
fences* — the same trap `docs/claude/coordination-board.md` records for the
coordination board. Writing ``.github/pr-landing/<slug>.json`` in a PR body
renders as `.github/pr-landing/.json`, which reads as a real path and is not
one. **Measured on PR #10894**, this feature's own PR, which hit it twice.

Use `{slug}` in an issue or PR body. Inside a repository `.md` file — like this
one — the angle brackets are safe.
