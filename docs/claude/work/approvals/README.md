# Tier-2 landing approvals

> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

One file per approved branch: **`docs/claude/work/approvals/{slug}.json`**.

A record here is a **landing credential**. `pr-landing-guard`'s **R15**
(`scripts/ci/check_pr_landing.py`) lets a Tier-2 PR carry `"landing": "self"`
when — and only when — it cites a record in this directory that it
**demonstrably could not have written**.

## Why this directory is not just a docs folder

`docs/claude/work/approvals/**` is listed in `LANDING_MACHINERY`. That single
line is the mechanism, not a filing convention:

- **Writing** a record is changing the landing route, so **R12 holds that PR for
  a human read.**
- **Using** a record costs nothing.

That asymmetry is what closes the **two-PR chain**. Without it a session could
self-land PR A (Tier-1, docs-only) writing an approval for its own PR B, then
self-land B against it — approving itself in two acts instead of one. Move these
records under an ordinary `docs/**` path and that hole re-opens silently.

## The shape

```json
{
  "decision_id": "DEC-20260910-EXAMPLE-NOT-A-REAL-RECORD",
  "work_object": "WO-20260910-EXAMPLE",
  "branch": "claude/example-branch",
  "tier": 2,
  "verdict": "approved",
  "channel": "operator_conversational_relayed",
  "scope_paths": ["src/runtime/exit_loop_breaker.py", "tests/**"],
  "decided_on": "2026-09-10",
  "decided_by": "operator",
  "text": "<the operator's own wording, verbatim>"
}
```

Every field is checked. `verdict` ∈ `approved` | `approved_with_conditions` —
`pending` and `none_recorded` are deliberately **absent**, because a credential
that can say "pending" is one a reader will misread as a grant. `channel` ∈
`decision_round_trip` | `operator_conversational_relayed` | `operator_wrote_it`.
`work_object` must name a file that actually exists under
`docs/claude/work/objects/`.

## `scope_paths` is the field that does the work

It is a glob list and it is **compared to the diff**, the way R5 compares tier.
A scope recorded only in prose grants everything.

⚠️ **AND A SCOPE THAT CANNOT BE EXPRESSED AS PATHS IS A FINDING, NOT A REASON TO
WRITE `["**"]`.** Measured on PR #11738, the change that motivated this rule:
the operator approved `r2_only` and explicitly excluded `r1` — but **both
remedies live in `src/main.py`**, so no path set separates them. (Read
adversarially, the PR does honour it: `_apply_per_account_leverage()` still
precedes `_start_exit_loop(settings)` on both sides, 697→738 at the merge-base
and 883→924 on the PR head, and neither symbol appears on any `+`/`-` line. The
point is that **R15 cannot establish that** — a human did.)

When a scope is not path-expressible, the honest move is to **read the diff,
confirm it, and enumerate the paths you confirmed** in the record. That read is
the human read R12 already puts in front of this file. Do not widen the glob to
make the check pass; a record whose scope is `["**"]` is not an approval, it is
a signature on a blank cheque.

## What a record does NOT establish

R15 proves an approval was **separate from the branch it lands** — *not* that
the operator originated it. Measured 2026-09-11: every squash-merge on `main`
carries the same author (`Ben`) and committer (`GitHub`) identity, so commit
metadata cannot tell a session-written record from an operator-written one.
`channel` records a **claim** about provenance.

What actually stands behind that claim is **R12** — a human read the PR that
created the record — and, where it is knowable, **R15(j)**: the session that
landed the record must not be one that committed on the branch spending it.
⚠️ Clause (j) only ever **adds** a refusal. `Claude-Session:` trailers cover
**10 of the 15 most recent commits on `main`** (66.7%; automation commits carry
none), so their absence is *we did not look* and is never read as a pass.

## Tier-3 is out of scope entirely

There is no record that admits a Tier-3 diff, and listing a Tier-3 path in
`scope_paths` does not create one. Strategy logic, risk caps, sizing,
account-mode flips and live promotion stay human-gated at the merge itself
(`docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers). **R15 must never be
widened to change that.**

## ⚠️ One thing this gate does NOT settle: "the trading path"

`docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers hard-blocks a self-merge for
the enumerated order-path files **and** adds *"any unit file the live VM consumes
on the trading path"*, with the merge classified Tier-3 *"set by the merge gate,
not the prep"*.

R15 grades Tier-3 by `TIER3_PATHS`, which enumerates the **named** files. That
trailing phrase is **broader**, and covers files R15 grades Tier-2 — `src/main.py`
and `src/runtime/order_monitor.py` among them. **PR #11738, the change that
motivated this whole rule, touches two of them.**

Deciding which files are "on the trading path" from a path glob is exactly the
unvouchable judgement `TIER1_SURFACE`'s allowlist polarity exists to refuse, so
**this guard does not invent that list and does not claim to have settled the
question.** It prints the caveat on every admission instead, so whoever writes a
record meets it rather than having to find it.

**If a record's `scope_paths` reaches the live trading path, its author owns that
judgement and should say so in `text`.** The guard has not made it for them.
