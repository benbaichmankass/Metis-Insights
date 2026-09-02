# The readout — where the chain is held up, and what that costs

_Generated 2026-09-02T12:01:38+00:00 by `scripts/ops/constraint_readout.py` · cycle `CY-20260901-OPERATING-LAYER` (basis DECIDED)_

> **This is A1, and it is computed rather than judged.** It reports its denominator before its conclusion, because a constraint named over unassessed edges is a fabricated answer wearing a computed label.

## 1 · Where the chain is held up

**Verdict: `insufficient_basis`**

9 of 587 objects (1.5%) have an ASSESSED `blocked_on` basis, below the declared floor of 50%. **No stage is named.** 578 objects carry an empty `blocked_on` that is NOT a claim that nothing blocks them — it is nobody having looked. A stage computed over this graph would describe the 9 rows somebody assessed, not the system.

| population | assessed | coverage | floor |
|---|---|---|---|
| 587 objects | 9 | **1.5%** | 50.0% |

**Edge basis, never collapsed** — `blocked` 9 · `declared_none` 0 · **`unstated` 578** · `malformed` 0.

⚠️ `unstated` is an empty `blocked_on` whose basis says `NOT_ASSESSED` (or which carries no basis at all). It is **nobody having looked**, not a claim that nothing blocks the object. Reading the second as the first is how a false *ready* appears.

⚠️ **Chain coverage is PARTIAL: `QUESTION`, `DEPLOYMENT`, `OBSERVATION` hold ZERO objects.** The store cannot locate a hold-up on a stage it has no objects for, so a stage histogram over this store describes what got migrated (review-backlog defect rows), not where the chain is stuck.

Objects by stage: `EVIDENCE` 79 · `DECISION` 2 · `CAPABILITY` 8 · `INTEGRITY` 498

⚠️ **576 of 587 of those stages were assigned in BULK FROM THE SOURCE FILENAME, not by reading the row.** The Phase C migration maps `health-review-backlog.json` → `INTEGRITY` and `{ml,performance,research}-review-backlog.json` → `EVIDENCE`, with no per-row judgement, so `INTEGRITY 498` is a census of ONE filename. Only **11** stage(s) in the whole store were chosen per object — and choosing one is not a claim it is RIGHT, only that a filename did not decide it.

Stage by how the stage was arrived at: `bulk_by_source_file` → EVIDENCE 78, INTEGRITY 498 · `per_object` → CAPABILITY 8, DECISION 2, EVIDENCE 1

**The assessed subgraph — every object that declares an edge (9 of 587):**

- **`WO-20260901-PHASE-A`** (CAPABILITY · waiting) — Phase A — survival — the plan carries itself forward
  - `external_event` → `a COLD session reporting on this work, citing the CLAUDE.md brief` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260901-PHASE-B`** (CAPABILITY · waiting) — Phase B — visibility — the read-only work view and the daily digest
  - `external_event` → `the work view rendered from the deployed SPA, by someone who is not this session` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
  - `external_event` → `the daily digest firing once on a real cadence` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260901-PHASE-C`** (CAPABILITY · waiting) — Phase C — migration, the WIP ceiling, and the priority that reaches a session
  - `external_event` → `a cold session stating this cycle's priority and citing the CLAUDE.md brief as where it read it` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260901-PHASE-D`** (CAPABILITY · waiting) — Phase D — the constraint, computed rather than judged
  - `external_event` → `a session writing TRUE blocked_on edges, taking assessed coverage over the declared 50% floor so E1 can name a stage instead of refusing` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
  - `external_event` → `a demonstration that the readout supersedes docs/claude/DUE.* in full, or an operator decision to keep both` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260901-PHASE-G`** (CAPABILITY · in_flight) — Phase G — the forcing function — retirement, and the E2 pull rule
  - `external_event` → `assessed `blocked_on` coverage crossing the readout's declared 50% floor, so E1 can NAME a held-up stage instead of refusing` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260901-PHASE-H`** (CAPABILITY · in_flight) — Phase H — the control half — decisions from the UI, and the read gate
  - `object` → `WO-20260901-PHASE-B` → `waiting` · ref `resolved` · hold **`holding`** · since 2026-09-01
  - `object` → `BL-20260901-RETIRE-ANDROID-AND-STREAMLIT-FROM-THE-LIVE-FEED` → `dormant` · ref `resolved` · hold **`holding`** · since 2026-09-01
  - `object` → `BL-20260901-DB-EXPLORER-IS-UNGATED-AND-REACHES-DEVICE-TOKENS-RAW-TOKEN-COLUMN` → `dormant` · ref `resolved` · hold **`holding`** · since 2026-09-01
  - `operator_decision` → `DEC-20260901-READ-GATE-SEQUENCING` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260902-DECISION-LOCAL-LLM-WEIGHT`** (DECISION · waiting) — Does the local LLM carry any weight, and at which size — 3B, 1.5B, or none?
  - `operator_decision` → `DEC-20260902-LOCAL-LLM-WEIGHT` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-08-31
- **`WO-20260902-DECISION-REVIEW-PACKET-POPULATION`** (DECISION · waiting) — Which POPULATION does the M7 strategy-review gate decide on — real money, or real blended with paper?
  - `operator_decision` → `DEC-20260902-REVIEW-PACKET-POPULATION` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-02
- **`WO-20260902-EVIDENCE-TRADE-PRIORITISATION-AB`** (EVIDENCE · waiting) — Confidence-first is the LIVE ranking key for competing trades and has never been shown to pick the better trade — and the A/B that would settle it cannot be run
  - `capability` → `an N-book mode in scripts/backtest_system.py plus a ranking-key arm (the shape of its existing --flip-policy), and a workflow that fires it` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-08-31

⚠️ **1 of the live `object` holds point at a target whose lifecycle is `waiting`, and that is the weakest hold the graph can express.** `waiting` covers two opposite facts — *not delivered yet* and *delivered, awaiting an observation* — and a dependent needs the capability, not the observation. The store cannot tell them apart, so this is published as a caveat rather than resolved into a state nobody measured. Check the target before treating one of these as a real blocker:
  - `WO-20260901-PHASE-H` → `WO-20260901-PHASE-B`

## 2 · The book and the money

**Read state: `read`** · source `https://ict-bot.duckdns.org/api/bot/performance?window=30d`

Population: **Real-money only, closed non-backtest rows inside the window; paper rides in a separate sub-block on the same route and is never blended.** Window `30d`.

| trades | win rate | totalPnl | totalPnlMeasured | pnlCoverage |
|---|---|---|---|---|
| 31 | 48.4% | 19.9201 | 19.9201 | **64.5%** |

Provenance split — measured 20 · estimated 11 · fabricated 0 · unverified 0.

⚠️ **The count and the sum are over DIFFERENT populations, deliberately** — `pnlCoverage`/`pnlMeasuredCount` are MEASURED-only, `totalPnlMeasured` sums MEASURED+ESTIMATED. Neither may be harmonised to the other.

`journalTrust` — readState `read` · known-divergent ['bybit_2'] · unrecorded [] · unreadable [].
  - ⚠️ **bybit_2 does not reconcile with the venue's wallet.** A row can be `measured` on an account that does not reconcile at all — coverage and trust are different questions.
  - ⚠️ `accountsUnrecorded` is **not** `accountsTrusted`: the ledger is populated by hand, so an absent record means nobody reconciled that account.

## 3 · In flight against the ceiling, and what has stopped moving

**4 in flight against a ceiling of 8** (headroom 4) · 7 waiting.

Ceiling source: scripts/ci/check_wip_ceiling.py::CEILING (imported, not restated). `waiting` is deliberately free of the ceiling — a thing blocked on an operator decision is not consuming the attention the ceiling rations.

In flight: `WO-20260901-PHASE-E` · `WO-20260901-PHASE-F` · `WO-20260901-PHASE-G` · `WO-20260901-PHASE-H`

Waiting: `WO-20260901-PHASE-A` · `WO-20260901-PHASE-B` · `WO-20260901-PHASE-C` · `WO-20260901-PHASE-D` · `WO-20260902-DECISION-LOCAL-LLM-WEIGHT` · `WO-20260902-DECISION-REVIEW-PACKET-POPULATION` · `WO-20260902-EVIDENCE-TRADE-PRIORITISATION-AB`

**Nothing in flight or waiting has been still for ≥14d** on declared dates.

⚠️ **Basis `declared_dates_only`.** Computed from `opened_at` and each edge's `since`. NOT a filesystem or git observation of when the object last changed. Every object counted here carries at least one usable date.

## 4 · Decisions waiting on the operator

**From the work store: 3 `operator_decision` edge(s).**
- `WO-20260901-PHASE-H` (CAPABILITY) → DEC-20260901-READ-GATE-SEQUENCING · since 2026-09-01
- `WO-20260902-DECISION-LOCAL-LLM-WEIGHT` (DECISION) → DEC-20260902-LOCAL-LLM-WEIGHT · since 2026-08-31
- `WO-20260902-DECISION-REVIEW-PACKET-POPULATION` (DECISION) → DEC-20260902-REVIEW-PACKET-POPULATION · since 2026-09-02

⚠️ Zero here does NOT mean no decision is pending — it means no object DECLARES one, and 578 of 587 objects have never been assessed for edges at all.

**From `docs/claude/operator-owed-register.json`: read state `read`, 0 OPEN item(s)** (carry limit 2; 5 terminal, not listed).

- _(none open — every item carries a terminal `status`)_

Status vocabulary: src.runtime.operator_owed (imported). ⚠️ Re-deriving it is not a hypothetical risk — this file's first run keyed on a field the register does not have (`state`, not `status`) and reported all 5 terminal items as open, one of them a question the operator had closed.

The two sources are kept **separate rather than merged** — one says *this work is held by a pending decision*, the other is the durable record of anything whose next action belongs to a person. Neither is a superset.

