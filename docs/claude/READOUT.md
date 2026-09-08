# The readout — where the chain is held up, and what that costs

_Generated 2026-09-08T10:10:51+00:00 by `scripts/ops/constraint_readout.py` · cycle `CY-20260906-TRADING-TRUTH` (basis DECIDED)_

> **This is A1, and it is computed rather than judged.** It reports its denominator before its conclusion, because a constraint named over unassessed edges is a fabricated answer wearing a computed label.

## 1 · Where the chain is held up

**Verdict: `insufficient_basis`**

49 of 682 objects (7.2%) have an ASSESSED `blocked_on` basis, below the declared floor of 50%. **No stage is named.** 633 objects carry an empty `blocked_on` that is NOT a claim that nothing blocks them — it is nobody having looked. A stage computed over this graph would describe the 49 rows somebody assessed, not the system.

| population | assessed | coverage | floor |
|---|---|---|---|
| 682 objects | 49 | **7.2%** | 50.0% |

**Edge basis, never collapsed** — `blocked` 18 · `declared_none` 31 · **`unstated` 633** · `malformed` 0.

⚠️ `unstated` is an empty `blocked_on` whose basis says `NOT_ASSESSED` (or which carries no basis at all). It is **nobody having looked**, not a claim that nothing blocks the object. Reading the second as the first is how a false *ready* appears.

Objects by stage: `QUESTION` 16 · `EVIDENCE` 90 · `DECISION` 9 · `DEPLOYMENT` 24 · `OBSERVATION` 6 · `CAPABILITY` 21 · `INTEGRITY` 514 · `(unstated)` 2

⚠️ **576 of 682 of those stages were assigned in BULK FROM THE SOURCE FILENAME, not by reading the row.** The Phase C migration maps `health-review-backlog.json` → `INTEGRITY` and `{ml,performance,research}-review-backlog.json` → `EVIDENCE`, with no per-row judgement, so `INTEGRITY 514` is a census of ONE filename. Only **105** stage(s) in the whole store were chosen per object — and choosing one is not a claim it is RIGHT, only that a filename did not decide it.

Stage by how the stage was arrived at: `bulk_by_source_file` → EVIDENCE 78, INTEGRITY 498 · `per_object` → (unstated) 1, CAPABILITY 21, DECISION 9, DEPLOYMENT 24, EVIDENCE 12, INTEGRITY 16, OBSERVATION 6, QUESTION 16 · `unstated` → (unstated) 1

**The assessed subgraph — every object that declares an edge (18 of 682):**

- **`WO-20260901-PHASE-A`** (CAPABILITY · waiting) — Phase A — survival — the plan carries itself forward
  - `external_event` → `a COLD session reporting on this work, citing the CLAUDE.md brief` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260901-PHASE-C`** (CAPABILITY · waiting) — Phase C — migration, the WIP ceiling, and the priority that reaches a session
  - `external_event` → `a cold session stating this cycle's priority and citing the CLAUDE.md brief as where it read it` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260901-PHASE-D`** (CAPABILITY · waiting) — Phase D — the constraint, computed rather than judged
  - `external_event` → `a session writing TRUE blocked_on edges, taking assessed coverage over the declared 50% floor so E1 can name a stage instead of refusing` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
  - `external_event` → `a demonstration that the readout supersedes docs/claude/DUE.* in full, or an operator decision to keep both` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260901-PHASE-G`** (CAPABILITY · ready) — Phase G — the forcing function — retirement, and the E2 pull rule
  - `external_event` → `assessed `blocked_on` coverage crossing the readout's declared 50% floor, so E1 can NAME a held-up stage instead of refusing` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260901-PHASE-H`** (CAPABILITY · ready) — Phase H — the control half — decisions from the UI, and the read gate
  - `object` → `WO-20260901-PHASE-B` → `done` · ref `resolved` · hold **`stale`** · since 2026-09-01
  - `object` → `BL-20260901-RETIRE-ANDROID-AND-STREAMLIT-FROM-THE-LIVE-FEED` → `dormant` · ref `resolved` · hold **`holding`** · since 2026-09-01
  - `object` → `BL-20260901-DB-EXPLORER-IS-UNGATED-AND-REACHES-DEVICE-TOKENS-RAW-TOKEN-COLUMN` → `dormant` · ref `resolved` · hold **`holding`** · since 2026-09-01
  - `operator_decision` → `DEC-20260901-READ-GATE-SEQUENCING` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260902-DECISION-LOCAL-LLM-WEIGHT`** (DECISION · ready) — Does the local LLM carry any weight, and at which size — 3B, 1.5B, or none?
  - `operator_decision` → `DEC-20260902-LOCAL-LLM-WEIGHT` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-08-31
- **`WO-20260902-DECISION-REVIEW-PACKET-POPULATION`** (DECISION · waiting) — Which POPULATION does the M7 strategy-review gate decide on — real money, or real blended with paper?
  - `operator_decision` → `DEC-20260902-REVIEW-PACKET-POPULATION` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-02
- **`WO-20260902-EVIDENCE-TRADE-PRIORITISATION-AB`** (EVIDENCE · waiting) — Confidence-first is the LIVE ranking key for competing trades and has never been shown to pick the better trade — and the A/B that would settle it cannot be run
  - `capability` → `an N-book mode in scripts/backtest_system.py plus a ranking-key arm (the shape of its existing --flip-policy), and a workflow that fires it` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-08-31
- **`WO-20260903-CANONIZE-THE-MANAGER-DUTIES-QUEUE-CADENCE`** (DECISION · ready) — Canonize the manager's BASE DUTIES -- running the merge queue and holding a check-in cadence -- as mechanisms, plus the meta-rule that turns operator feedback …
  - `pr` → `10918` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-03
- **`WO-20260903-EVERY-PR-APPROVAL-REACHES-THE-OPERATOR-AS-AN`** (DECISION · ready) — Every PR awaiting operator approval reaches them as an ANSWERABLE Telegram prompt, not as a PR sitting silently in a queue
  - `work_object` → `WO-20260903-THE-REFUSAL-HALF-OF-MANAGER-CONTROL-BLOCK` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-03
- **`WO-20260903-SUNSET-DISPOSITIONS-OWED`** (QUESTION · waiting) — Ten evidenced retirement candidates have sat undispositioned since 2026-09-01, and retiring a leg is Tier-3 so only the operator can move them
  - `operator_decision` → `DEC-20260903-SUNSET-DISPOSITION-POLICY` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260903-THE-REFUSAL-HALF-OF-MANAGER-CONTROL-BLOCK`** (CAPABILITY · waiting) — The refusal half of manager control: block a manager action on a PR whose author session is still live
  - `external_event` → `PR #10905 merging, and then a manager actually INVOKING the gate` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-03
- **`WO-20260906-NO-5M-OR-15M-SCALP-EXIT-HEAD`** (OBSERVATION · waiting) — No 5m or 15m scalp exit-head artifact exists — and the CONSUMER is missing too, so the artifact is the SECOND half, not the first
  - `pr` → `11140` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-06
  - `backlog_row` → `BL-20260906-EXIT-HEAD-GUARD-DROPS-THE-FAMILY-CHECK-ITS-SIBLING-ENTRY-HEAD-GUARD-MAKES` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-06
- **`WO-20260906-SHIP-THE-EXIT-HEAD-INTO-ICT-SCALP-THREE-LEGS`** (DEPLOYMENT · waiting) — Ship the M20 exit-head consumer into the ict_scalp unit (MI-146 recommendation #2) — wiring only, annotate-first, arming NOT taken
  - `review` → `PR #11140 is a DRAFT declaring tier 3 / landing `hold` (.github/pr-landing/ict-scalp-exit-head-20260906.json), so it does not self-merge and the manager routes it. Not a defect — the dispatch said so explicitly ("I route the merge — this touches a live strategy unit and does not self-merge").` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-06
  - `artifact` → `No ict_scalp exit-head artifact is published to the live mirror. Measured 2026-09-06 on /api/diag/shadow_stats (last_seen 10:00:01Z): exactly two exit_head model_ids exist — exit-head-donchian-1h-v1 (advisory) and exit-head-donchian-peak-1h-v1 (shadow), count 52 each — and BOTH are 1h, while all 8 ict_scalp legs are 5m or 15m. maybe_score_exit_head's in-distribution tf guard therefore refuses every leg. This blocks the OBSERVED half of the done-condition only; the SHIPPED half is delivered.` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-06
- **`WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE`** (DECISION · done) — 25 of 44 enabled+live legs have no reachable take-profit - the per-leg Tier-3 repair
  - `operator_decision` → `DEC-20260907-TP-VALUE` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-07
  - `operator_decision` → `DEC-20260907-TP-VOCAB` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-07
  - `operator_decision` → `DEC-20260907-TP-UNEXAMINED` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-07
- **`WO-20260907-ALPACA-LIVE-S-ROUTED-REAL-MONEY-LEG`** (QUESTION · dormant) — alpaca_live's routed real-money leg cannot place an order
  - `object` → `WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED` → `waiting` · ref `resolved` · hold **`holding`** · since 2026-09-07
- **`WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED`** (EVIDENCE · waiting) — The close-confirmation gate is symbol-scoped while the close is trade-scoped, so a succeeded close is graded a failure and its journal row left open
  - `operator_decision` → `Tier-3 approval of the held PR re-scoping IBClient.close's confirmation, AND a stated choice on IB_CLOSE_CONFIRM_PARTIAL (strict | reduction)` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-07
  - `object` → `WO-20260907-ALPACA-LIVE-S-ROUTED-REAL-MONEY-LEG` → `dormant` · ref `resolved` · hold **`holding`** · since 2026-09-07
  - `backlog_row` → `bybit_2 is NOT exposed and must NOT be "fixed" by adding a confirmation` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-07
- **`WO-20260908-RE-ARM-PROTECTION-FOR-THE-56-NAKED`** (DEPLOYMENT · waiting) — Re-arm protection for the 56 naked shares on trade 5414 (alpaca_portfolio/TLT)
  - `capability` → `BL-20260908-ALPACA-PLACE-PROTECTIVE-IGNORES-OCA-KEY-SO-EVERY-RE-ARM-IS-SYMBOL-WIDE` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-08

⚠️ **1 of the live `object` holds point at a target whose lifecycle is `waiting`, and that is the weakest hold the graph can express.** `waiting` covers two opposite facts — *not delivered yet* and *delivered, awaiting an observation* — and a dependent needs the capability, not the observation. The store cannot tell them apart, so this is published as a caveat rather than resolved into a state nobody measured. Check the target before treating one of these as a real blocker:
  - `WO-20260907-ALPACA-LIVE-S-ROUTED-REAL-MONEY-LEG` → `WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED`

## 2 · The book and the money

**Read state: `read`** · source `https://ict-bot.duckdns.org/api/bot/performance?window=30d`

Population: **Real-money only, closed non-backtest rows inside the window; paper rides in a separate sub-block on the same route and is never blended.** Window `30d`.

| trades | win rate | totalPnl | totalPnlMeasured | pnlCoverage |
|---|---|---|---|---|
| 39 | 38.5% | -3.6266 | -3.6266 | **66.7%** |

Provenance split — measured 26 · estimated 13 · fabricated 0 · unverified 0.

⚠️ **The count and the sum are over DIFFERENT populations, deliberately** — `pnlCoverage`/`pnlMeasuredCount` are MEASURED-only, `totalPnlMeasured` sums MEASURED+ESTIMATED. Neither may be harmonised to the other.

`journalTrust` — readState `read` · known-divergent ['bybit_2'] · unrecorded [] · unreadable [].
  - ⚠️ **bybit_2 does not reconcile with the venue's wallet.** A row can be `measured` on an account that does not reconcile at all — coverage and trust are different questions.
  - ⚠️ `accountsUnrecorded` is **not** `accountsTrusted`: the ledger is populated by hand, so an absent record means nobody reconciled that account.

## 3 · In flight against the ceiling, and what has stopped moving

**4 in flight against a ceiling of 8** (headroom 4) · 14 waiting.

Ceiling source: scripts/ci/check_wip_ceiling.py::CEILING (imported, not restated). `waiting` is deliberately free of the ceiling — a thing blocked on an operator decision is not consuming the attention the ceiling rations.

In flight: `WO-20260906-ICT-SCALP-5M-TO-SHADOW-THEN-ESTABLISH` · `WO-20260906-ML-2-THE-PREDICTIVE-BRACKET` · `WO-20260906-THE-EXIT-GEOMETRY-REBUILD-WAS-SPECIFIED-AND-NEVER-DISPATCHED` · `WO-20260908-COORDINATION-BOARD-IS-AT-GITHUB-S-2500`

Waiting: `WO-20260901-PHASE-A` · `WO-20260901-PHASE-C` · `WO-20260901-PHASE-D` · `WO-20260902-DECISION-REVIEW-PACKET-POPULATION` · `WO-20260902-EVIDENCE-TRADE-PRIORITISATION-AB` · `WO-20260903-CLOSE-WEDGE-LEDGER-ABSENT` · `WO-20260903-SUNSET-DISPOSITIONS-OWED` · `WO-20260903-THE-REFUSAL-HALF-OF-MANAGER-CONTROL-BLOCK` · `WO-20260904-MANAGER-IDLE-IS-UNBOUNDED-AND-NOTHING-WAKES-IT` · `WO-20260905-PENDING-PINGS-HAS-NO-MERGE-DRIVER-SO-PING-PRS-DIE` · `WO-20260906-NO-5M-OR-15M-SCALP-EXIT-HEAD` · `WO-20260906-SHIP-THE-EXIT-HEAD-INTO-ICT-SCALP-THREE-LEGS` · `WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED` · `WO-20260908-RE-ARM-PROTECTION-FOR-THE-56-NAKED`

**Nothing in flight or waiting has been still for ≥14d** on declared dates.

⚠️ **Basis `declared_dates_only`.** Computed from `opened_at` and each edge's `since`. NOT a filesystem or git observation of when the object last changed. **1 of these objects carry no usable movement date at all** and are counted as *unstated*, never as stalled — silence about movement is not evidence of stillness.

## 4 · Decisions waiting on the operator

**From the work store: 8 `operator_decision` edge(s).**
- `WO-20260901-PHASE-H` (CAPABILITY) → DEC-20260901-READ-GATE-SEQUENCING · since 2026-09-01
- `WO-20260902-DECISION-LOCAL-LLM-WEIGHT` (DECISION) → DEC-20260902-LOCAL-LLM-WEIGHT · since 2026-08-31
- `WO-20260902-DECISION-REVIEW-PACKET-POPULATION` (DECISION) → DEC-20260902-REVIEW-PACKET-POPULATION · since 2026-09-02
- `WO-20260903-SUNSET-DISPOSITIONS-OWED` (QUESTION) → DEC-20260903-SUNSET-DISPOSITION-POLICY · since 2026-09-01
- `WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE` (DECISION) → DEC-20260907-TP-VALUE · since 2026-09-07
- `WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE` (DECISION) → DEC-20260907-TP-VOCAB · since 2026-09-07
- `WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE` (DECISION) → DEC-20260907-TP-UNEXAMINED · since 2026-09-07
- `WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED` (EVIDENCE) → Tier-3 approval of the held PR re-scoping IBClient.close's confirmation, AND a stated choice on IB_CLOSE_CONFIRM_PARTIAL (strict | reduction) · since 2026-09-07

⚠️ Zero here does NOT mean no decision is pending — it means no object DECLARES one, and 633 of 682 objects have never been assessed for edges at all.

**From `docs/claude/operator-owed-register.json`: read state `read`, 0 OPEN item(s)** (carry limit 2; 5 terminal, not listed).

- _(none open — every item carries a terminal `status`)_

Status vocabulary: src.runtime.operator_owed (imported). ⚠️ Re-deriving it is not a hypothetical risk — this file's first run keyed on a field the register does not have (`state`, not `status`) and reported all 5 terminal items as open, one of them a question the operator had closed.

The two sources are kept **separate rather than merged** — one says *this work is held by a pending decision*, the other is the durable record of anything whose next action belongs to a person. Neither is a superset.

## 5 · Everything else that is due

> Ported here by operator decision 2026-09-08 (`consolidate_into_the_readout`) so that ONE surface answers *what is due right now*. ⚠️ **This did not improve the diagnosis in §1.** Folding registers in adds ROWS, not assessed `blocked_on` BASIS — §1 still refuses to name a stage, and refusing is still correct. A longer readout is not a better-evidenced one.

**Completeness: `partial`** · **79 row(s) due** across the sources that answered and are not already covered above (via `scripts/ops/render_due_list.py`).

> ⚠️ **`79` IS A LOWER BOUND.** Could not read: `red_crons`, `unlanded_automation`. A cron that could not be READ is not a cron that is GREEN, and an unlanded-PR list that could not be FETCHED is not an EMPTY queue. An empty source below may mean nothing is due, or may mean nobody looked — read the per-source state, never the row count alone.

| source | state | rows | note |
|---|---|---|---|
| `open_items` | `read` | 54 |  |
| `soaks` | `read` | 2 | 2 declared soak(s): ready=1 · accruing=0 · not_writing=0 · unknown=1 |
| `operator_owed` | `read` | _not repeated_ | covered by §4 · Decisions waiting on the operator |
| `research_queue` | `read` | 3 |  |
| `probes` | `read` | 9 | freshness=fresh age=23.7h cadence=daily (cron 20 5 * * *) \| 2 probe result(s) deferred to the `soaks` source,… |
| `red_crons` | `could_not_read` | — | HTTPError: HTTP Error 403: Forbidden |
| `unlanded_automation` | `could_not_read` | — | HTTPError: HTTP Error 403: Forbidden |
| `error_feed` | `read` | 11 | digest 2026-09-08T05:10:01+00:00, age 5.0h |
| `sunset` | `read` | 0 |  |

⚠️ `rows` reads `—`, never `0`, for a source that could not be read — the distinction this table exists to preserve. A source marked _not repeated_ WAS read; its rows are rendered in the section named in its note.

**`open_items` — 54 due** · showing 8, **46 not shown** (cap 8/source)

- 🔔 `OI-20260906-BYBIT-CLOSE-QTY-SNAP-MERGED-BUT-NEVER-OBSERVED-ON-THE-FLEET` · 2d — loud row — must be reported on every session
  - PR #11125 merged 2026-09-06T18:43:22Z as 9604b8a5 (squash), Tier-3, operator-approved, routed by the manager rather than self-merged. Content VERIFIED on origin/main directly, not…
- 🔔 `OI-20260906-RESEARCH-THAT-SPECIFIES-WORK-IS-CARRIED-BY-NOTHING` · 2d — loud row — must be reported on every session
  - docs/research/EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md told its reader to paste it as a new session and no register pointed at it for 14 days. MI-148 now carries the work (#11138 l…
- 🔔 `OI-20260902-BYBIT-GRADED-COVERAGE-SOAK-IS-THE-ONLY-EVIDENCE-FOR-WIDENING-AND-NOTHING-WATCHES-IT` · 2d — loud row — must be reported on every session
  - #10746 ships a soak at runtime_logs/bybit_coverage_soak.jsonl whose rows are the ONLY declared evidence for the operator's conditional widening of the graded-coverage gate to bybi…
- 🔔 `OI-20260902-STRATEGY-REVIEW-PACKET-BLENDS-REAL-AND-PAPER-PNL` — loud row — must be reported on every session
  - scripts/ml/strategy_review_packet.py::pull_decisions filters ONLY is_backtest — it never consults account_class or is_demo — so every M7 review packet and every INDEX row blends r…
- 🔔 `OI-20260901-OPERATING-LAYER-BUILD-IS-IN-FLIGHT-AND-CARRIED-ONLY-BY-THIS-ROW` · 7d — monitoring row 7d since last observation (cadence 1d)
  - The operating-model redesign (operator-directed, 2026-09-01) is DESIGNED and its build has begun. Phase A of 8 is in flight. ⚠️ THE DESIGN IS FOUR DOCUMENTS UNDER docs/design/ THA…
- `OI-20260826-MHG-OVER-COVER-MECHANISM-UNVERIFIED` · 2d — monitoring row 2d since last observation (cadence 2d)
  - The MHG disjoint-OCA over-cover was CLEARED by hand; the mechanism that should have caught and reported it is NOT yet proven.
- 🔔 `OI-20260826-STRAY-OCA-SWEEP-SHIPPED-BUT-UNARMED` · 2d — loud row — must be reported on every session
  - ⚠️ ARMED ON ib_paper 2026-08-31 — THIS ROW'S ID AND ITS OLD SUMMARY BOTH SAY 'UNARMED' AND ARE STALE. The id is deliberately NOT renamed (ROADMAP.md and several backlog rows link …
- 🔔 `OI-20260903-CAPTURE-WATCH-PROVED-ITSELF-ON-DEMAND-AND-ITS-SCHEDULE-HAS-NEVER-SUCCEEDED` · 5d — monitoring row 5d since last observation (cadence 3d)
  - SUCCESSOR to OI-20260829-TRAINER-IS-NOW-A-DECIDED-DEPENDENCY-AND-IS-UNMONITORED, which CLEARED 2026-09-03 on its clause (a): trainer-capture-watch.yml run 33719856283 graded the t…

**`soaks` — 2 due**

- `OI-20260902-BYBIT-GRADED-COVERAGE-SOAK-IS-THE-ONLY-EVIDENCE-FOR-WIDENING-AND-NOTHING-WATCHES-IT` · 6d — soak UNKNOWN — NO PROBE IS DECLARED for this soak, so nothing is reading it. Ready means: verdicts_differ=true. This is a KNOWN, DECLARED g…
  - #10746 ships a soak at runtime_logs/bybit_coverage_soak.jsonl whose rows are the ONLY declared evidence for the operator's conditional widening of the graded-coverage gate to bybi…
- 🔔 `OI-20260906-ICT-SCALP-EXIT-HEAD-CONSUMER-SHIPPED-AND-CANNOT-SCORE-UNTIL-A-SCALP-HEAD-IS-PUBLISHED` · 2d — soak READY — ✅ READY — 37 matching row(s) of 754 scanned satisfy the declared criterion (decision_state!=not_scored). ⚠️ Ready is not CLEAR…
  - MI-150 shipped the M20 exit-head consumer into `ict_scalp` (annotate-only, disarmed) -- the wiring MI-146 identified as the only passed-gate work blocked on missing code. ⚠️ IT CA…

**`research_queue` — 3 due**

- `RQ-20260827-001` — research job still queued
  - Re-grade every account against the Lane P compat-matrix standard arm
- `RQ-20260830-002` — research job still queued
  - Monthly re-validation of every SHIPPED bracket-geometry cell on a live leg
- `RQ-20260831-002` — research job still queued
  - Thin-leg bracket-geometry accrual — the five 1d equity legs that cannot reach the power floor

**`probes` — 9 due** · showing 8, **1 not shown** (cap 8/source)

- `OI-20260903-CAPTURE-WATCH-PROVED-ITSELF-ON-DEMAND-AND-ITS-SCHEDULE-HAS-NEVER-SUCCEEDED` — we did not look — this row is currently unwatched
  - probe could not run (exit_2)
- 🔔 `OI-20260831-PROP-RISK-GATE-ENFORCE-ARMED-BUT-HAS-NEVER-CAPPED` — probe FAILED — its declared observation did not hold
  - A prop_ticket_risk_soak row exists, within the last 1000, in which the gate ran under `enforce`, graded a ticket `exceeds_cushion`, and records `would_have_capped: true`.
- 🔔 `OI-20260831-RESEARCH-QUEUE-INFEASIBLE-STATE-SHIPPED-BUT-NEVER-REACHED-LIVE` — probe FAILED — its declared observation did not hold
  - Reads all three committed research corpora and looks for any row stamped research_power_state=infeasible — the R4 grade this row's id says nothing has ever produced. The POSITIVE …
- `OI-20260831-RESEARCH-QUEUE-GPU-ROUTE-AND-SPEND-GATE-NEVER-EXERCISED` — we did not look — this row is currently unwatched
  - probe could not run (exit_2)
- `OI-20260831-SESSION-BRIEF-DIFF-SCOPING-SHIPPED-NEVER-REPORTED-INHERITED` — we did not look — this row is currently unwatched
  - probe could not run (exit_2)
- 🔔 `OI-20260902-BYBIT-COVERAGE-BASIS-MERGED-WITH-ARMING-DELIBERATELY-HELD-FOR-A-SOAK` — probe FAILED — its declared observation did not hold
  - A bybit_coverage_soak row exists in which the SIDE-AWARE grade actually DISAGREED with the side-blind sum (verdicts_differ: true). That is the load-bearing half of this row's thre…
- `OI-20260902-SUNSET-CANDIDATES-ARE-NOW-DUE-AND-NINE-ARE-UNDISPOSITIONED` — we did not look — this row is currently unwatched
  - probe could not run (exit_2)
- 🔔 `OI-20260902-DECISION-DRAIN-ROUTINE-DOES-NOT-EXIST-AND-NOTHING-HAS-EVER-DRAINED` — probe FAILED — its declared observation did not hold
  - Whether the decision push-back drain has recorded a run inside its window, graded over the committed receipt docs/claude/work/DECISION-DRAIN.json. Four states, never collapsed: fr…

**`error_feed` — 11 due** · showing 8, **3 not shown** (cap 8/source)

- `ERRFEED-b0711dd4` · 1d — error-level condition on `bot_logs`, FIRST SEEN since the last digest — 3 rows 2026-09-07T12:00:59 → 2026-09-08T00:05:05 · accounts=ib_pape…
  - [error] NEW x3 ib_stop_over_cover detected: ib_paper/MGC: position N but resting STOP qty totals N (N%) across N DISJOINT OCA groups oca-protect-tN(clientI
- `ERRFEED-897ca384` · 0d — error-level condition on `bot_logs`, FIRST SEEN since the last digest — 1 rows 2026-09-08T05:07:41 → 2026-09-08T05:07:41 · accounts=alpaca_…
  - [error] NEW x1 alpaca_partial_stop_coverage detected: alpaca_portfolio/TLT: position N carries a resting stop for only N — N unprotected. The netted positi
- `ERRFEED-cf5a4edf` · 1d — error-level condition on `bot_logs`, FIRST SEEN since the last digest — 1 rows 2026-09-07T11:49:36 → 2026-09-07T11:49:36 · accounts=ib_pape…
  - [error] NEW x1 ib_target_naked detected: ib_paper/MGC: position N has N of take-profit coverage against a declared TP of N — the position can only stop out
- `ERRFEED-725f746a` · 0d — error-level condition on `operator_alerts`, FIRST SEEN since the last digest — 1 rows 2026-09-08T02:13:28 → 2026-09-08T02:13:28 · accounts=…
  - [error] NEW x1 🛑 Position CLOSE failing — won't flatten Account: bybit_N Symbol: ETHUSDT | Side: long | Qty: N Consecutive close failures: N share_hold: no
- `ERRFEED-86505576` · 6d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 155 rows 2026-09-02T08:33:53 → 2026-09-04T23:53:07 · acco…
  - [error] x155 🛑 Position CLOSE failing — won't flatten Account: alpaca_paper Symbol: GLD | Side: long | Qty: N Consecutive close failures: N share_hold: o
- `ERRFEED-02a919f1` · 4d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 55 rows 2026-09-04T08:01:23 → 2026-09-07T23:25:05 · accou…
  - [error] x55 🧱 Position CLOSE wedged BROKER-SIDE — carried in the digest Account: alpaca_paper Symbol: GLD | Side: long | Qty: N Consecutive close failur
- `ERRFEED-9fd2fc82` · 14d — error-level condition on `bot_logs`, STANDING (predates the last digest) — 49 rows 2026-08-25T05:13:10 → 2026-08-25T22:48:45 · symbols=MGC …
  - [error] x49 strategy_builder exception: RuntimeError: ict_scalp_mgc_Nm: no candle data for symbol=MGC timeframe=Nm.
- `ERRFEED-6eb9d669` · 7d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 27 rows 2026-09-01T20:12:41 → 2026-09-02T08:17:06 · accou…
  - [error] x27 🛑 Position CLOSE failing — won't flatten Account: alpaca_paper Symbol: GLD | Side: long | Qty: N Consecutive close failures: N Last error: e

_This section decides nothing. Every row is for a session to judge._

