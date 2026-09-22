# Metis Insights — CLAUDE.md

> **Doc status:** `live` · category `instruction` · last verified `2026-09-21` · registered in [`docs/DOCUMENT-INDEX.md`](docs/DOCUMENT-INDEX.md)

> **Production environment — live money is at risk.** You have full, autonomous
> access to everything you need to operate this system. The operator grants
> permission by tier; they do not do the work for you.

> ### 🔄 This file was cut on 2026-09-21 (operator-directed operating reset)
> It was 547 KB, of which a generated SESSION-BRIEF block was 39.4% — ~215 KB of
> overdue monitoring rows injected ahead of every session's first tool call. That
> block, its generator, and the eight-register operating model it served are
> **retired**. Reference material moved verbatim to [`docs/reference/`](docs/reference/);
> the pre-cut file is preserved at
> [`docs/archive/2026-09-21-operating-reset/CLAUDE.md.pre-cut`](docs/archive/2026-09-21-operating-reset/CLAUDE.md.pre-cut).
> **Scope of record: [`docs/plans/OPERATING-PLAN-2026-09-21.md`](docs/plans/OPERATING-PLAN-2026-09-21.md).**

---

## 🔍 RULE ONE — Always verify

**Before you assert anything, check it. Every time.** Full text + worked
examples: [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md)
§ "RULE ONE — Always verify". The short form, because the failures never come
from skipping a *hard* check — they come from a one-liner that looked
conclusive:

- **A search returning nothing is not proof of absence.** Show the probe can
  find a positive before trusting that it is quiet; a negative needs a
  denominator.
- **Read the field, not the prose about it.** Config/DB/code are the truth;
  comments, refs and docs are claims about it. When they disagree the field
  wins (*field beats comment*) and the prose gets fixed.
- **Always state the population.** Every quantitative claim names what it was
  measured over. A figure whose sign flips on a filter choice is not a figure.
- **Cross-check with arithmetic**, not a careful re-read.
- **Verify your own output too**, hardest when it confirms what you expected.
- **"It was already like that" / "the doc says so" / "a previous session
  checked it" are not verification.** If you did not check it this session, say
  so plainly rather than assert it.

## ⚠️ If you see something, say something

Don't leave bugs lying around. A session that observes broken, degraded or
suspicious infrastructure either **fixes it** (in tier and in scope), **files it
with enough detail that a later session can act without re-deriving your
observation**, or **flags it to the operator** when it is tier-gated. "Not my
task" and "it was already like that" are not dispositions.

⚠️ **Filing is not a parking lot.** Measured over 30 days before the reset: 951
open backlog rows, 404 research memos, and **one** disposition ever marked
`actioned`. If a thing matters, it becomes a checklist row or a queued research
question. If it doesn't, say that instead of filing it.

## Honesty

Give only true, verifiable answers. If you don't know something, say "I don't
know" and state how you'd find out. Never guess, speculate, or report work you
didn't do as done. On a live trading system a confident wrong answer is worse
than "I need to check" — verify against the actual code, config, diag output or
database before you assert.

**Merged ≠ deployed ≠ observed.** These are three different facts and this repo
has paid for collapsing them more than any other mistake. Say which one you
established.

---

## How work is organised

### Instruction hierarchy

When sources disagree, the higher one wins. If a higher doc is silent, defer
to the next. Mirrored verbatim in
[`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md) § "Document
Priority" — **the two must always agree** (`canonical-doc-coherence` enforces it).

1. `docs/CLAUDE-RULES-CANONICAL.md` — how you operate: access, honesty,
   permission tiers, session discipline.
2. `docs/ARCHITECTURE-CANONICAL.md` — system architecture, trade/comms
   pipeline, contracts.
3. `docs/plans/OPERATING-PLAN-2026-09-21.md` — the promotion ladder, the
   research loop, and how work is chosen. Adopted 2026-09-21.
4. `docs/claude/work/MANAGER-CHECKLIST.json` — what is actually being worked
   right now, and by whom.
5. Skills under `.claude/skills/` (binding, composable workflows) — the manager
   contract is `.claude/skills/manager/SKILL.md`.
6. The root `CLAUDE.md` — repo orientation and pointers.
7. Focused implementation specs and workflow-helper docs (e.g.
   `docs/github-actions-workflows.md`, `docs/reference/*`).
8. `ROADMAP.md`, `docs/sprint-logs/`, `docs/claude/*` and historical notes —
   context only.


**Two categories. That is the whole taxonomy.**

| A question | → `research/queue/<id>.yaml` |
|---|---|
| **A build** | → **a row in [`docs/claude/work/MANAGER-CHECKLIST.json`](docs/claude/work/MANAGER-CHECKLIST.json)** |

Nothing else is work. A spec file, a memo or a design doc that no checklist row
and no queue unit points at **is not work — it is a memo**, and this repo has
404 of those. The checklist is served to the operator as the live **Workflow
page** on the SPA (`GET /api/bot/work/checklist`), which reads the file from the
VM's working tree; `ict-git-sync` pulls `main` every ~5 min, so the page is
exactly as fresh as the last push **to `main`**. **Push, then answer.**

### The follow-through pipeline — what exists, and what does not

**`docs/claude/work/PIPELINE.jsonl` is the intake for anything that will need
picking up later.** Append-only JSONL, never a JSON array — a shared array is
what produced the archived registers' merge conflicts. Schema, every invariant
and the reasoning: `scripts/ops/pipeline.py` (guarded by `pipeline-guard`).

An item is **refused** unless it carries three things, and each refusal maps to
a reason work used to get dropped (plan § 3b):

| required | because, without it |
|---|---|
| `due_when` | nothing ever makes it ask for attention |
| `origin.rerun` | nobody can re-ask whether the finding still applies |
| `terminal_reason` (to close) | it can stop being mentioned instead of ending |

**Due is COMPUTED, not declared** — an item becomes due because the clock or
the condition says so, whether or not anyone chose to look. `killed` is a
first-class outcome: closing a dead row *with a stated reason* is worth more
than carrying it.

⚠️ **THE PULL IS BUILT BUT NOT YET CONNECTED, AND THAT DISTINCTION IS THE
WHOLE POINT.** `render_section_0()` and `unrouted_count()` exist, are tested,
and are what the brief consumes. **Nothing runs them on a schedule yet, because
the brief that displays them is `A3` and is not built.** So today the pipeline
will hold what you put in it and correctly tell you what is due *when asked* —
and asking is still voluntary, which is reason (5), the one that killed
`DUE.md`. **A7 is not finished until A3 renders section 0 on the operator's own
page.** Do not read "the pipeline exists" as "things no longer get dropped".

⚠️ **The 1,065 archived backlog rows and 91 monitoring rows are NOT imported.**
The store is seeded empty on purpose; importing them is `A8`, and most of them
should be *killed explicitly* rather than carried. Until A8 runs, those rows are
still in git history with nothing reading them.

**If you are filing something that needs picking up later, it goes in the
pipeline** — not into a new register, and not into a memo.

**Retired 2026-09-21** and archived under
[`docs/archive/2026-09-21-operating-reset/`](docs/archive/2026-09-21-operating-reset/):
the work store (`docs/claude/work/objects|intents|steps/`), `OPEN-ITEMS.json`,
the four review backlogs, `SESSIONS.json`, `MANAGER-LEASE.json`,
`MERGE-QUEUE.json`, `DUE.*`, `READOUT.md`, the coordination board, the
generated SESSION-BRIEF block and its generator, and ~24 governance crons. Do
not resurrect them.

### The promotion ladder — edge is decided OFFLINE

```
STAGE 0  Backtest        offline harness
         Is there an edge, NET OF THE FULL COST STACK?
         Leaves when — the result clears the rule REGISTERED BEFORE THE RUN,
         at the declared power. Nothing reaches a book without a committed
         evidence record.
                          ▼  GATE 1  ▼
STAGE 1  Soak             bybit_1 · alpaca_paper
         Do the mechanics work, and does REALIZED COST match what the
         backtest assumed? This stage may be wide — it is an instrument,
         not a decision surface. It is also where legs that have not passed
         shadow live data so we can learn how to tweak them.
                          ▼  GATE 2  ▼
STAGE 2  Live + mirror    bybit_2 + bybit_portfolio · alpaca_live + alpaca_portfolio
         Real money. The mirror carries the IDENTICAL roster and the
         IDENTICAL trades, at honest size. Its net-of-cost window is the
         DEMOTION signal.
```

**A live or paper book never establishes edge.** It checks mechanics and cost.
If a proposal advances a leg on live-book P&L, that is a category error.

**Stage 2 is one stage, not two** (operator, 2026-09-21): the mirror and the
live account carry the same strategies and take the same trades at all times.
Both halves are enforced by strict equality in
`tests/test_paper_portfolio_accounts.py` —
`test_bybit_portfolio_mirrors_bybit_2_exactly` and
`test_alpaca_portfolio_mirrors_alpaca_live_exactly_minus_proxies`. The Alpaca
one carries the only sanctioned divergence: the two declared affordability
proxies (`splg_trend_long_1d`, `iaum_pullback_1d`) are dropped, because
mirroring a sub-$100 proxy on a ~$98k paper book doubles the exposure its
primary already carries.

**The Alpaca invariant is now LANDED AND SATISFIED.** Until 2026-09-21 the
Alpaca half was a SUBSET assertion — it guaranteed no live leg trades without a
paper counterpart and deliberately allowed the mirror to run extra, so **Gate
2's demotion signal could not be read off the Alpaca mirror's aggregate.**
DECIDED 2026-09-21 (operator, plan item **B2**): strict equality, like Bybit,
with the surplus-leg cost accepted — and the roster was cut the same day, so
the test and the config now agree. MEASURED by parsing `config/accounts.yaml`:
`alpaca_live` **3** legs (2 after the proxy carve-out), `alpaca_portfolio` cut
**14 → 2**, so **12 legs removed**. What ends for those 12 is narrower than
"they stop trading" — all 12 also sit on `alpaca_paper`, so each keeps a paper
record; what ends is their record at the *portfolio* risk basis and their
presence in `/api/bot/performance`'s `paperPortfolio` block.

⚠️ **And the EMPTY-`alpaca_live` case is decided too: equality wins, no
exception** (operator, same day). If Gate 2 demotes the last live leg the
mirror empties with it — accepted, with the cost stated on the test: at that
moment the Alpaca demotion signal disappears and nothing flags the gap. Do not
reintroduce a non-empty guard without reopening that decision.

⚠️ Two earlier drafts of this paragraph were wrong in ways worth remembering:
one said *"no invariant exists"* (inferred, never checked); another said *"live
5 legs"*, true only before **A6** (#12673) pulled `tlt_pullback_1h` and
`tlt_pullback_1d` the same day — which is why the surplus the operator approved
was **12, not the 9** every note said.

### The daily sync, and standing authorizations

One session a day with the operator. The manager pushes the brief **before** the
sync. Six sections, fixed order: **what came due · taken under mandate ·
decisions for you · what moved · what is running · spend.** Contract:
[`.claude/skills/manager/SKILL.md`](.claude/skills/manager/SKILL.md).

⚠️ **The sync is NOT measured in time, and there is no cap on decisions**
(operator, 2026-09-21). Two earlier drafts of this file set a 30-minute ceiling
and then a 30-minute floor; both were rejected — *"it takes however long it
takes… I don't want us tracking an arbitrary time limit to measure
performance."* **Do not report session length as a metric.**

**THE LADDER IS FULLY AUTOMATED — operator grant, 2026-09-21.** Every ladder
transition, both gates and both directions, fires on evidence with no human in
the path — **including promotion to a real-money roster**. A **mandate** is an
authorization granted once, in advance, in `config/mandates.yaml`. When one
fires: a realtime ping, then the evidence record in section 1 of the next
brief. A decision arriving twice in the same shape is raised as *"should this
become a mandate?"*

⚠️ **"If the evidence supports it" is the entire safety property** once nobody
is in the path: a committed evidence record, named harness, stated n, **net of
the full cost stack**, clearing a rule registered before the run. A claim in a
PR body is not a record. **B1** makes it checkable; **B5** builds the mechanism.

⚠️ **The real-money promotion mandate does not arm until D1 lands** — the
harnesses default slippage and funding to `0.0`, so today's corpus is fee-only
and optimistic by an unknown amount (+0.57R on the one leg measured). Arming
against it would route real money on numbers already known to be wrong in the
favourable direction. The `derisk_only` mandates carry no such block.

⚠️ **A mandate is not a third execution gate** — see § "The two execution
gates", which is unchanged. It does not decide what RUNS; it decides what may
be **CHANGED without asking**.

### Every session

1. Read this file and [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md).
2. Read your lane's checklist row. If you are managing, invoke the **`manager`**
   skill first.
3. Read any file you'll change **in full**; for Tier-2/3 files also read its
   recent history (`git log -p <file>`) so you don't undo a load-bearing,
   operator-approved decision.
4. **Before `cat >` / Write on a path you did not create this session, check it
   exists first** (`git cat-file -e origin/main:<path>`). On 2026-08-26 a
   session wrote a guard from scratch and destroyed the working one of that
   name.
5. **End by running the `close-out` skill** —
   [`.claude/skills/close-out/SKILL.md`](.claude/skills/close-out/SKILL.md).
   It is a completion test, not a register: seven checks against the checklist,
   the pipeline and git. **The one question it asks is whether this session
   could end right now, without warning, and nothing be lost or silently
   dropped.**

   ⚠️ **Run it when you stop early too.** Budget, context and time run out —
   that is normal, and it is a HANDOFF rather than a completion. A session that
   ends mid-task having said so is fine; one that ends mid-task silently is the
   drop. Budget for close-out as part of the work, or you will reach the
   boundary unable to afford landing what you built.

   The two that catch the most: **`landed_unproven` is not `done`** and must
   name the observation that would close it, and **a finding is only *filed*
   when it is in the pipeline or on the checklist** — a chat message, a PR
   comment and a new memo are none of them.

---

## Permission tiers

You work on `main` and commit there directly for Tier-1 work. You ask the
operator only when the tier requires it. Full definitions:
[`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md) § Permission Tiers.

| Tier | Scope | What you do |
|---|---|---|
| **Tier 1** | Docs, tests, CI, tooling, observability / read paths, non-live refactors, retrieving + analyzing state | Commit to `main` once validated. No approval needed. |
| **Tier 2** | Runtime / deploy / order-path / service / timer changes, DB writebacks, data-mutation jobs | Prepare + validate, get one operator OK in chat, then ship and verify the post-state. |
| **Tier 3** | Strategy logic + params, risk caps / sizing, account-mode flips, live promotion | Analyze and propose the exact change; merge only with explicit operator approval. |

## The two execution gates

Exactly two declared, default-permissive switches decide whether a strategy
trades — both visible in YAML and surfaced on `/api/bot/config`:

- **Account level** — `config/accounts.yaml::mode: live | dry_run`. The only
  path that may write `mode:` is the `set-account-mode` system-action
  (operator-gated).
- **Strategy level** — `config/strategies.yaml::execution: live | shadow`.
  `live` (default) executes; `shadow` runs and logs order packages everywhere
  but never sends a live order. Enforced in `Coordinator.multi_account_execute`
  by folding into the same `effective_dry` resolution as `mode:` — no new order
  path. *(Exception: the M22 market-neutral pairs sleeve is an ISOLATED 2-leg
  order path — `src.units.strategies.pairs_executor.run_pairs_tick`, called once
  per tick from `src/main.py`, NOT via `multi_account_execute`. Its gate lives
  in `config/pairs.yaml`.)*

Both default permissive, so omitting either never strands capability. **There is
no third gate**: never hide a capability behind a default-off `*_ENABLED` flag
(the pattern that stranded MES). What `accounts.yaml` / `strategies.yaml`
declare, runs.

⚠️ **`accounts.yaml::symbols` IS NOT A GATE, and it was one until 2026-09-22.**
The `strategies:` roster is the single source of truth for what an account
trades; `symbols:` is a **purely additive DATA-PULL list** that legitimately
names instruments no leg trades. ⚠️ **The count of such entries moves with every
roster edit — re-run it, never quote it.** It read **21** at midday on
2026-09-22 and **19** four hours later, because `ada_pullback_2h` joining
`bybit_2`'s roster turned ADAUSDT from declared-but-untraded into
declared-and-traded. `python3 scripts/ci/check_roster_symbol_reachability.py`
prints the current count beside its denominators for exactly that reason.

Until E42 the tick's fetch set came from the pull
lists **alone**, so a rostered leg whose symbol nobody had declared got no
candles, no signal and no order while reading as wired — the MES pattern spelled
as an omission instead of a flag. `_resolve_tick_symbols` now fetches
`UNION(roster-implied, declared)`. **A symbol missing from a pull list is a bug
in the PULL LIST, never a reason a rostered leg does not trade** (operator,
2026-09-22); `roster-symbol-reachability` reports it in those words. Reverse
only by reopening that decision.

The trader runs 24/7 and never switches itself off — no auto-flip, no breaker
that toggles mode, no "safety" default that goes dry on boot. Transient issues
route through `RiskManager` per-trade: the account stays live and individual
trades are refused with a logged cause. Full Prime Directive:
[`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md) § Prime Directive.

⚠️ **CI is asymmetric, and this is the single mechanical fact behind the roster
drift.** `scripts/check_dry_run_in_diff.py` fails any PR that adds a
`mode: dry_run` or `execution: shadow` line without an operator marker; across
all 78 guards **nothing blocks turning a leg on**, and nothing requires evidence
before it reaches a real-money roster. That is why the roster went 36 → 55 while
every memo said cut.

⚠️ **And it is sharper than "off is guarded, on is free"** (verified 2026-09-21
by running the guard against a real diff): there are **two spellings of off**,
and the guard sees only one. Setting `execution: shadow` is guarded; **removing
a leg from an account's `strategies:` roster is not.** So **B1 must key on
roster membership**, not on the `mode`/`execution` fields — a guard watching
only the fields can be walked around by editing the list, in either direction.

---

## What this is

Automated multi-strategy trading system (ICT + pairs + macro/value) on two
Oracle VMs, with a FastAPI on `:8001` consumed by one front end.

```
ict-bot-arm (141.145.193.91) — live trader
  ├── ict-trader-live.service  — trading pipeline (src/main.py → pipeline.py)
  ├── ict-web-api.service      — FastAPI :8001  (/api/bot/*, /api/diag/*, /ws/market)
  └── caddy.service            — HTTPS at ict-bot.duckdns.org → localhost:8001

ict-trainer-vm (158.178.209.121) — ML lifecycle (datasets, training, registry, eval)
ict-ib-gateway (10.0.0.251)      — IB Gateway, isolated on its own box
```

**The Svelte SPA is the only live consumer** (operator decision, 2026-09-01) —
`benbaichmankass/ict-trader-dashboard` on GitHub Pages, calling the API
**browser-direct** through Caddy. **CORS is load-bearing.** The Streamlit
dashboard and the Android app are retired from the live feed.

| you want | go to |
|---|---|
| endpoint shapes, field caveats, `BotStats`/`Position`, CORS list | [`docs/reference/bot-api-reference.md`](docs/reference/bot-api-reference.md) |
| which tier a route is / adding a route | [`docs/api-tier-policy.md`](docs/api-tier-policy.md) (CI-enforced) |
| env vars and runtime toggles | [`docs/reference/env-vars.md`](docs/reference/env-vars.md) |
| DB schema, provenance rules, collapsed states | [`docs/reference/data-model.md`](docs/reference/data-model.md) |
| VM topology + the authority split | [`docs/reference/vm-topology.md`](docs/reference/vm-topology.md) |
| reaching `/api/diag/*` from a session | [`docs/reference/diag-access.md`](docs/reference/diag-access.md) |
| what a web session can/can't do, GitHub-MCP quirks, relays | [`docs/reference/session-capabilities.md`](docs/reference/session-capabilities.md) |
| watchdogs, naked-position autoprotect, retired surfaces | [`docs/reference/runtime-notes.md`](docs/reference/runtime-notes.md) |
| system architecture, trade/comms pipeline, contracts | [`docs/ARCHITECTURE-CANONICAL.md`](docs/ARCHITECTURE-CANONICAL.md) |
| prop-account design (Breakout manual bridge) | [`docs/integrations/prop-accounts-architecture-DESIGN.md`](docs/integrations/prop-accounts-architecture-DESIGN.md) |

**Read these on demand, not at session start.** They answer *"what does this
endpoint return"* / *"what does this var do"* — questions you only have once you
are in the code.

### Three things from the reference that bind you before you open it

1. **Numbers carry provenance.** `src/runtime/provenance.py` owns the
   vocabulary: `MEASURED` (broker fill) · `ESTIMATED` (defensible
   reconstruction) · `FABRICATED` (synthesised, no anchor) · `UNVERIFIED` (none
   recorded — **never** folded into measured). Import it; do not add another
   bespoke `exclude_*` predicate.
2. **A count that reads `0` may mean "we could not look".** Many fields return
   `null` for an unmeasured quantity and publish an explicit read-state beside
   it. When a field encodes a condition, ask whether *"we did not look"* and
   *"we looked and found nothing"* are distinguishable; if not, that is the bug
   (`scripts/ci/check_collapsed_states.py`).
3. **Render a missing/null field as "—", never `0` or "unknown".**

---

## Access & autonomy

Everything you need is wired into the repo:

- **VMs** — the SSH key (`VM_SSH_KEY`) and diag token (`DIAG_READ_TOKEN`) are in
  Actions secrets. You read both VMs and run tiered changes through GitHub
  Actions workflows you dispatch yourself. Skills: `diag-data`, `vm-ops`,
  `git-actions`.
- **Databases** — full read access via the diag/journal relays and the Data
  Explorer API. Skill: `db-wiring`.
- **GitHub** — issues, PRs, files, branches, CI via the GitHub MCP tools.

Retrieve the state you need yourself, then act. The only actions you genuinely
cannot perform are physical or credential ones: rotating exchange/prop account
keys, clearing an OCI console CAPTCHA, or anything needing a human at a broker.
When you hit one, say so plainly and say exactly what the operator must do.
Before writing any "operator: do X manually" instruction, check the
`before-asking-the-operator` skill.

**Tiered production mutations** run through the `system-actions` workflow's
fixed allowlist — dispatch by opening a labelled issue. Tier-1 fires
autonomously; Tier-2 after an operator OK in chat. Allowlist:
[`docs/claude/system-actions.md`](docs/claude/system-actions.md).

---

## Skills

Workflows live under [`.claude/skills/`](.claude/skills/). **Skill-first lookup
is binding** — before generating any task output, scan the catalog; if a skill
matches, derive from it rather than from a precedent artifact. Precedents are
example outputs, not authority.

**The manager contract is `.claude/skills/manager/SKILL.md`.** Read it before
spawning or supervising anything.

⚠️ **Ten process skills were retired on 2026-09-21** and archived under
`docs/archive/2026-09-21-operating-reset/skills/`: `duty`, `delegate-work`,
`research-driver`, `session-coordination`, `session-handoff`,
`session-receipt`, `backlog-drain`, `full-system-audit`, `system-review`,
`workplan-vs-architecture`. They served the retired operating model. Domain
skills (backtesting, the three reviews, VM/DB/ops, new-strategy, …) are
unchanged.

### The three reviews stay separate

`/health-review` (technical/pipeline/data health) · `/performance-review`
(trading + strategy performance, per-package grading) · `/ml-review` (model
lifecycle, promotion/demotion against the `candidate → shadow → advisory`
ladder). Each has its own rubric and output template. The `/system-review`
roll-up that ran all three plus register maintenance is retired — the operator
called it bloated and its backlog-drive mandate is gone with the backlogs.

---

## Repo identity

Renamed to **`benbaichmankass/Metis-Insights`** on 2026-07-23; the old
`ict-trading-bot` name still works via GitHub's 301 redirect — **do not chase a
rename sweep.**

- **The MCP repo name is whatever THIS session's scope says — read it, don't
  assume.** A denial on either name is a scope mismatch, not an outage. Re-read
  the session's allowed list before concluding anything about GitHub.
- **VM clone dirs stay `/home/ubuntu/ict-trading-bot` and
  `/opt/ict-trading-bot`.** A GitHub rename does not move the on-disk clone;
  sweeping those paths would break the deploys.
