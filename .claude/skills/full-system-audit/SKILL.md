---
name: full-system-audit
description: Periodic whole-system audit across all three repos (bot, dashboard, android) for structural compliance with the canonical docs AND — critically — for DEAD/zombie infrastructure that consistency checks alone can never catch (retired integrations, services, brokers, env-gates still sitting in the repo after their purpose was removed). Use when the operator says "run a full system audit", "audit the whole system", "/full-system-audit", or for a periodic governance pass. Produces per-repo findings + per-repo cleanup PRs. Composes with workplan-vs-architecture (intent↔design↔reality drift) and doc-freshness (doc consistency) but adds the liveness axis they both lack. NOT a code-quality review (use `review`) and NOT a runtime health check (use `health-review`).
---

# /full-system-audit — reconcile the whole system, and hunt the corpses

This skill exists because the **2026-06-10 audit miss** proved that
consistency-checking is not enough. The full-system audit (#3233/#88/#43)
reconciled the canonical docs against the code and reported the spine sound —
yet two whole **retired** integrations (Cloudflare tunnel, Tradovate broker)
were still sitting in the repo, and the audit flagged neither. The full
root-cause is in `docs/audits/audit-blindspot-zombies-2026-06-10.md`. Read it
once; the short version is:

> A retired-but-present integration is **internally consistent** — its
> stale-positive doc and its stale-positive code agree with each other — so a
> consistency check finds **zero contradictions** and passes. The audit
> confirmed the corpse's papers were in order; it never checked for a pulse.

So this audit runs on **two axes**, not one:

| Axis | Question | Existing skill | Gap this skill closes |
|---|---|---|---|
| **Consistency** | Do the docs agree with each other and with the code? | `doc-freshness`, `workplan-vs-architecture` | — (reuse them) |
| **Liveness** | Is each thing in the repo actually ALIVE — reachable, run, still wanted? | *none* | **the whole point** |

Do not skip the liveness axis because the consistency pass came back clean. A
clean consistency pass is exactly the state in which zombies hide.

## Scope — three repos

| Repo | What to audit |
|---|---|
| `ict-trading-bot` | the system of record: `src/`, `config/`, `deploy/`, `.github/workflows/`, the canonical docs, the skills catalog |
| `ict-trader-dashboard` | the Streamlit consumer — its `CLAUDE.md` must still only *point* at the bot's rules, and every endpoint it calls must still exist on the bot |
| `ict-trader-android` | the Kotlin consumer — every endpoint/field it reads must still exist; its CI hygiene |

Produce **per-repo findings and per-repo cleanup PRs** (drafts, Tier-3 files
gated on operator merge) — never one cross-repo PR.

## Pass 1 — Consistency (reuse, don't reinvent)

Run the two existing skills and fold their reports in:

1. **`workplan-vs-architecture`** — intent (ROADMAP) ↔ design (ARCHITECTURE) ↔
   reality (code). Gives you the aligned spine + the drift classes.
2. **`doc-freshness`** — doc-vs-doc, doc-vs-reality, precedence. Gives you the
   contradictions.

These are necessary and sufficient for *drift*. They are **blind to
deadness** — that is Pass 2.

## Pass 2 — Liveness / deadness (the zombie hunt) — THE CORE

Build the **integration inventory**: every externally-facing or
independently-toggleable thing the repo names. At minimum sweep for —

- **Brokers / exchanges:** every entry in
  `src/units/accounts/integrator.py::EXCHANGE_MAP` and every
  `*_client_for` factory in `clients.py`.
- **Services / units:** every file in `deploy/*.service` / `*.timer` and every
  unit named in `scripts/install_systemd_units.sh` and in `diag.py`'s
  `_CANONICAL_UNITS`.
- **Workflows:** every `.github/workflows/*.yml` and every action in the
  `system-actions.yml` allowlist.
- **Env-gates:** every `*_ENABLED` / `*_DISABLED` / `*_SOURCE` / `*_MODE` flag
  the runtime reads (these are the Prime-Directive hot spots).
- **External transports:** tunnels, proxies, CDNs, edge functions, third-party
  feeds.

For **each** inventory item, run the three liveness probes. An item is a
**zombie** if it fails reachability *and* runtime usage, or if provenance shows
a retire-arc with no delete-arc.

### Probe A — Reachability (static)

Grep the **call sites**, not the definition. A class/function/unit that is only
referenced by its own definition, its own tests, and a registry entry — with
nothing on a live path constructing or dispatching to it — is unreachable.

```
# does anything outside the package + its tests use it?
rg -l '<Symbol>' --glob '!**/tradovate/**' --glob '!**/tests/**'
```

For a broker: is any `config/accounts.yaml` account's `exchange:` set to it? If
no account routes to an `EXCHANGE_MAP` entry, the entry is dead weight.

### Probe B — Runtime usage (dynamic — pull it yourself)

Use the diag relays (skill: `diag-data`) — do **not** ask the operator.

- **Services:** `/api/diag/services` — is the unit `enabled` + `active`? A unit
  file in `deploy/` that the live VM doesn't run is a candidate corpse (confirm
  it isn't a manual/on-demand unit).
- **Brokers:** does the live VM's `.env` carry that broker's creds? Does the
  balance snapshot or any trade ever reference it?
- **Env-gates:** is the flag set on the VM, and does the code path behind it
  ever fire (audit log / journalctl)?

### Probe C — Decision provenance (historical)

This is the probe that catches the **chat-only retirement** (Cause 1 of the
blind-spot). Grep the *historical* record for retirement language:

```
rg -i 'retire|deprecat|abandon|superseded|do not reintroduce|purge|tear.?down|sunset' \
   docs/ ROADMAP.md
git log --oneline --all | rg -i '<thing>'   # build-arc vs delete-arc
```

- A thing with a **build arc and a retire arc but no delete arc is a zombie.**
- A thing whose retirement you can find **only in conversation, never in a
  commit or a canonical doc** is itself a finding — see Decision capture below.

## The disposition flip — the rule that would have caught both corpses

> An artifact that is **present in code but unreachable / unrouted / unrun** is
> presumed a **corpse to remove or to explicitly justify in writing** — NOT an
> inventory gap to document.

This is the deliberate inversion of `workplan-vs-architecture`'s "Reality → no
intent → add it to the inventory" instinct. To *keep* an orphan you must
affirmatively produce one of:

1. a **live consumer** (a reachable call site or an active runtime route), or
2. a **written "kept on purpose" justification** in a canonical doc (e.g.
   "`DASHBOARD_ORIGIN` is a no-op kept for a future browser-direct consumer" —
   that is a legitimate documented keep; an undocumented dead `.service` is
   not).

Absent both, it is flagged for removal in the cleanup PR. When you remove,
preserve the **historical record** (sprint logs, audit docs, "why we tried X"
notes) — purge the *active* code/config/wiring, keep the memory of why.

## Decision capture — close the root cause, don't just sweep

The deepest cause of the 2026-06-10 miss is that **operator decisions made in
chat never landed in the repo.** So the audit's job is partly to *force* them in:

- When a finding's resolution depends on a decision you can only find in
  conversation (a retirement, a "we're not doing X anymore", a scope cut), do
  not silently act on memory. **Write the decision into a canonical doc** (or
  flag it for the operator if it's Tier-3) as part of the PR, so the next audit
  reads it from the repo instead of needing the chat.
- A retirement that is real but undocumented is a **first-class finding**, not a
  footnote — it is the exact gap that produced the zombie.

## Output — per repo

A short structured report per repo:

- **Consistency (Pass 1):** the drift findings from the two sub-skills (aligned
  spine + each drift item, class, evidence, fix direction, tier).
- **Liveness (Pass 2):** the integration inventory with each item marked
  **LIVE** / **documented-keep** / **ZOMBIE**, and for each zombie the three
  probe results (unreachable / unrun / retire-arc-no-delete) as evidence.
- **Decision-capture findings:** retirements or scope-cuts found only in chat,
  now to be written into the repo.
- **Cleanup PR(s):** per repo, draft; Tier-3 files (`config/accounts.yaml`,
  `config/strategies.yaml`, risk caps, order code, unit files) gated on operator
  merge. Purge active code/config/wiring; keep historical record.

## Honesty

Mark an item **ZOMBIE** only when you actually ran the probes and have the
evidence — an unreachable grep AND a dynamic check AND/OR the provenance arc.
Do not delete an integration on a hunch from a filename; and do not call
something live just because it's documented (documented-but-dead is the whole
failure mode). "I inventoried N items, all LIVE or documented-keep" is a
complete, valuable result.

## Composes with

- `workplan-vs-architecture` — Pass 1 intent↔design↔reality drift.
- `doc-freshness` — Pass 1 doc consistency; also run at session end.
- `diag-data` — Probe B runtime-usage pulls (services, env, routes).
- `git-actions` — dispatch the diag/relay workflows for Probe B.
- `new-broker` / `new-strategy` — the inverse operation; their checklists are
  the inventory of touch-points a removed broker/strategy must be scrubbed from.
- `sprint-format` — log the audit as a sprint when it ships cleanup PRs.
