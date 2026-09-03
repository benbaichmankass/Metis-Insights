# `pr-landing` — every PR says its TIER and how it means to LAND

One file per branch: **`.github/pr-landing/<branch-slug>.json`**, where the slug
is the branch name with a leading `claude/` stripped and `/` → `-` — the same
derivation `claude-pr-automerge.yml` uses, so the two can never disagree about
which branch asked.

Enforced by `pr-landing-guard` (`scripts/ci/check_pr_landing.py`), which runs on
every PR. Read that file's docstring for the rule-by-rule reasoning; this is the
operator's-eye summary.

## Why this exists

On 2026-09-03, seven of the night shift's PRs sat open, green and unlanded,
waiting on the manager. Tier-1 work — which `docs/CLAUDE-RULES-CANONICAL.md`
§ Permission Tiers says needs **no** human OK to merge — was being routed
through a human on every single session, by convention.

Three of those PR bodies blamed `pr-opener.yml` for "creating every PR as a
draft regardless of `draft:false`". **That is false and should not be repeated.**
`pr-opener.yml` honours `draft:false`; `true` is only the default, and those
sessions' request files asked for `"draft": true`. The cause was a blanket
instruction in the spawn template plus a permissions asymmetry, not a bug in the
relay.

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
- **R12** — a PR that changes the landing machinery may not land itself by it.
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
