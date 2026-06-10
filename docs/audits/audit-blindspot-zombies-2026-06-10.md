# Why the full-system audit missed the Cloudflare + Tradovate zombies

**Date:** 2026-06-10
**Trigger:** The full-system audit (PR #3233 bot / #88 dashboard / #43 android)
reconciled the canonical docs against the code and reported the spine sound —
yet two whole retired integrations were still sitting in the repo and the audit
did not flag either as a corpse to remove:

- **Cloudflare tunnel** — `ict-cloudflared-tunnel.service` + drop-in, four
  `*_cloudflare_tunnel.sh` scripts, `*-cloudflare-tunnel` system-actions, a
  runbook. The Streamlit pivot (PR #32, 2026-05-12) made it dead; it lingered
  ~4 weeks. Purged in #3233.
- **Tradovate broker** — the whole `src/units/accounts/tradovate/` package,
  tests, runbook, `accounts.yaml` account, secret-wiring. Evaluated and retired
  (cost + integration fit) in a prior operator session; the dead wiring stayed.
  Purged in #3269.

The operator's question was the right one: **don't just patch the canonical
docs — find where the bug actually came from so the next audit doesn't repeat
it.** This is that investigation.

## What the audit actually did, and why it passed

The audit was, at its core, a **consistency reconciliation**: it diffed the
canonical docs against each other and against the code on disk, using two
existing skills as its method —

- **`doc-freshness`** — checks three failure modes: doc-vs-doc, doc-vs-reality,
  precedence-violation. All three are *agreement* checks.
- **`workplan-vs-architecture`** — reconciles intent (ROADMAP) ↔ design
  (ARCHITECTURE) ↔ reality (code), classifying each disagreement.

Plus one mechanical guard, **`repo_inventory.py`** (the `repo-inventory` CI
check), which counts files by extension/size and flags `.bak`/`.tmp`/`~` junk.

**None of these can see a zombie.** Here is the precise reason, broken into the
three compounding causes.

### Cause 1 — The retirement was never written back into the repo (documentation-origin failure)

This is the operator's first hypothesis, and it is the deepest cause.

For **Tradovate**, the git history shows only *build*-phase commits (#2649,
#2650, #2732) — they documented Tradovate **into** ROADMAP, ARCHITECTURE, and a
runbook as *"WIRED, INERT — validate, then promote and deprecate IBKR."* The
later decision to **retire** it (too costly, poor fit) was made in an operator
conversation **in a different session** and **never entered the repo**: no
ROADMAP edit, no ARCHITECTURE edit, no runbook "RETIRED" banner, no deletion PR.
So the canonical record did not go *stale by neglect* — it was **never updated,
because the retirement decision never became a commit at all.** The decision
lived only in chat.

For **Cloudflare**, the canonical docs *did* acknowledge the retirement
("retired/kept for now", "tear-downable") — but the acknowledgement was
**permission to remove, not an instruction to remove**, and the code was left
in place. The docs and the corpse agreed: "this is obsolete but still here."

**The lesson:** an audit that only reads the repo cannot catch a decision that
never reached the repo. The audit's job must therefore *include forcing
chat-only decisions into the repo* — a retirement you can only find in
conversation, never in a commit, is itself the highest-value finding.

### Cause 2 — The audit checked consistency, not liveness (methodology blind spot)

A retired-but-present integration is **internally consistent**: its
stale-positive doc and its stale-positive code *agree with each other*.

- Cloudflare: the unit file existed AND the docs described a CF tunnel. ✔ consistent.
- Tradovate: the package existed AND the runbook said "wired, pending validation." ✔ consistent.

Consistency-checking is structurally blind to *"agreed-upon but wrong"* — two
stale positives that match produce **zero contradictions**. The audit confirmed
the corpse's papers were in order; it never checked for a pulse. There was no
axis anywhere in the method that asked: *is this thing alive? is it reachable on
a live path? does anything actually use it? should it still exist?*

### Cause 3 — When the artifact WAS seen, the framing said "absorb it," not "interrogate it"

The structural sweep literally saw `ict-cloudflared-tunnel.service` on disk. But
`workplan-vs-architecture`'s resolution for the "Reality → no intent" class is
*"either the roadmap is stale (update it) or scope crept (raise it)"* — **both
resolutions add the thing to the inventory.** Neither asks "is this a corpse?"
The skill's default disposition toward an unrecognized artifact is to
**document what exists**, not to **question whether it should exist**. So even
direct observation defaulted to absorption.

And the one inventory mechanism that might have helped — `repo_inventory.py` — is
byte-level: extension counts, file sizes, junk suffixes. A zombie `.service` or
a dead Python package is just another counted file to it. There is no
reachability or liveness notion anywhere in the toolchain.

## The three causes compound

1. The retirement never entered the repo (Cause 1), so there was nothing for a
   consistency check to catch.
2. Even if it had been half-written, consistency-checking is blind to two
   matching stale positives (Cause 2).
3. And when the artifact was observed directly, the method's instinct was to
   absorb it into the inventory rather than interrogate it (Cause 3).

## What the fix must add: a liveness/deadness ("zombie") axis

The new `full-system-audit` skill is built around the missing axis. For **every
integration / service / broker / dependency / env-gate** named in code, config,
or deploy units, the audit does not ask *"is it documented?"* — it asks **"is it
ALIVE?"**, via three independent probes:

1. **Reachability (static):** is the symbol imported / instantiated / dispatched
   anywhere on a live path? Grep the *call sites*, not just the definition. A
   class that only its own tests and `EXCHANGE_MAP` reference, with no account
   routing to it, is unreachable.
2. **Runtime usage (dynamic):** does the live VM actually run/route to it? Is the
   systemd unit enabled+active (diag `/services`)? Does any account route to that
   exchange? Is the env var set? (Pull this yourself via the diag relays.)
3. **Decision provenance (historical):** grep the *historical* record — sprint
   logs, PR titles, backlog, audit docs — for retirement language ("retired",
   "deprecated", "abandoned", "do not reintroduce", "superseded", "purge"). A
   thing with a **build arc and a retire arc but no delete arc is a zombie.**

And the **default disposition flips**: an artifact that is present-in-code but
unreachable / unrouted / unrun is presumed a **corpse to remove or explicitly
justify in writing** — *not* an inventory gap to document. You must
affirmatively find either a live consumer or a written "kept on purpose"
justification; absent both, it is flagged for removal.

Finally, the skill carries a **decision-capture step**: any decision the audit
can only find in conversation (never in a commit) is treated as a finding in its
own right — the audit forces it into a canonical doc or into code, closing
Cause 1 at the source.

See `.claude/skills/full-system-audit/SKILL.md`.
