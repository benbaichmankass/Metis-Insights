# The readout — where the chain is held up, and what that costs

_Generated 2026-09-08T17:35:28+00:00 by `scripts/ops/constraint_readout.py` · cycle `CY-20260906-TRADING-TRUTH` (basis DECIDED)_

> **This is A1, and it is computed rather than judged.** It reports its denominator before its conclusion, because a constraint named over unassessed edges is a fabricated answer wearing a computed label.

## 1 · Where the chain is held up

**Verdict: `insufficient_basis`**

50 of 686 objects (7.3%) have an ASSESSED `blocked_on` basis, below the declared floor of 50%. **No stage is named.** 636 objects carry an empty `blocked_on` that is NOT a claim that nothing blocks them — it is nobody having looked. A stage computed over this graph would describe the 50 rows somebody assessed, not the system.

| population | assessed | coverage | floor |
|---|---|---|---|
| 686 objects | 50 | **7.3%** | 50.0% |

**Edge basis, never collapsed** — `blocked` 19 · `declared_none` 31 · **`unstated` 636** · `malformed` 0.

⚠️ `unstated` is an empty `blocked_on` whose basis says `NOT_ASSESSED` (or which carries no basis at all). It is **nobody having looked**, not a claim that nothing blocks the object. Reading the second as the first is how a false *ready* appears.

Objects by stage: `QUESTION` 16 · `EVIDENCE` 92 · `DECISION` 10 · `DEPLOYMENT` 24 · `OBSERVATION` 6 · `CAPABILITY` 22 · `INTEGRITY` 514 · `(unstated)` 2

⚠️ **576 of 686 of those stages were assigned in BULK FROM THE SOURCE FILENAME, not by reading the row.** The Phase C migration maps `health-review-backlog.json` → `INTEGRITY` and `{ml,performance,research}-review-backlog.json` → `EVIDENCE`, with no per-row judgement, so `INTEGRITY 514` is a census of ONE filename. Only **109** stage(s) in the whole store were chosen per object — and choosing one is not a claim it is RIGHT, only that a filename did not decide it.

Stage by how the stage was arrived at: `bulk_by_source_file` → EVIDENCE 78, INTEGRITY 498 · `per_object` → (unstated) 1, CAPABILITY 22, DECISION 10, DEPLOYMENT 24, EVIDENCE 14, INTEGRITY 16, OBSERVATION 6, QUESTION 16 · `unstated` → (unstated) 1

**The assessed subgraph — every object that declares an edge (19 of 686):**

- **`WO-20260901-PHASE-A`** (CAPABILITY · waiting) — Phase A — survival — the plan carries itself forward
  - `external_event` → `a measured behaviour comparison of sessions with and without the brief, over a stated denominator, which may return NO` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-08
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
- **`WO-20260908-RECONCILE-THE-BYBIT-1-ETHUSDT-ORPHAN-TRADE`** (EVIDENCE · waiting) — Reconcile the bybit_1 ETHUSDT orphan trade 5569
  - `operator_decision` → `Tier-2 OK to change _bybit_position_protection's book selection and _recently_closed_adopted_orphan's exit_reason allowlist (src/runtime/order_monitor.py — closes live positions and gates the adopt path)` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-08

⚠️ **1 of the live `object` holds point at a target whose lifecycle is `waiting`, and that is the weakest hold the graph can express.** `waiting` covers two opposite facts — *not delivered yet* and *delivered, awaiting an observation* — and a dependent needs the capability, not the observation. The store cannot tell them apart, so this is published as a caveat rather than resolved into a state nobody measured. Check the target before treating one of these as a real blocker:
  - `WO-20260907-ALPACA-LIVE-S-ROUTED-REAL-MONEY-LEG` → `WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED`

## 2 · The book and the money

**Read state: `read`** · source `https://ict-bot.duckdns.org/api/bot/performance?window=30d`

Population: **Real-money only, closed non-backtest rows inside the window; paper rides in a separate sub-block on the same route and is never blended.** Window `30d`.

| trades | win rate | totalPnl | totalPnlMeasured | pnlCoverage |
|---|---|---|---|---|
| 40 | 37.5% | -3.8794 | -3.8794 | **67.5%** |

Provenance split — measured 27 · estimated 13 · fabricated 0 · unverified 0.

⚠️ **The count and the sum are over DIFFERENT populations, deliberately** — `pnlCoverage`/`pnlMeasuredCount` are MEASURED-only, `totalPnlMeasured` sums MEASURED+ESTIMATED. Neither may be harmonised to the other.

`journalTrust` — readState `read` · known-divergent ['bybit_2'] · unrecorded [] · unreadable [].
  - ⚠️ **bybit_2 does not reconcile with the venue's wallet.** A row can be `measured` on an account that does not reconcile at all — coverage and trust are different questions.
  - ⚠️ `accountsUnrecorded` is **not** `accountsTrusted`: the ledger is populated by hand, so an absent record means nobody reconciled that account.

## 3 · In flight against the ceiling, and what has stopped moving

**4 in flight against a ceiling of 8** (headroom 4) · 15 waiting.

Ceiling source: scripts/ci/check_wip_ceiling.py::CEILING (imported, not restated). `waiting` is deliberately free of the ceiling — a thing blocked on an operator decision is not consuming the attention the ceiling rations.

In flight: `WO-20260906-ICT-SCALP-5M-TO-SHADOW-THEN-ESTABLISH` · `WO-20260906-ML-2-THE-PREDICTIVE-BRACKET` · `WO-20260906-THE-EXIT-GEOMETRY-REBUILD-WAS-SPECIFIED-AND-NEVER-DISPATCHED` · `WO-20260908-COORDINATION-BOARD-IS-AT-GITHUB-S-2500`

Waiting: `WO-20260901-PHASE-A` · `WO-20260901-PHASE-C` · `WO-20260901-PHASE-D` · `WO-20260902-DECISION-REVIEW-PACKET-POPULATION` · `WO-20260902-EVIDENCE-TRADE-PRIORITISATION-AB` · `WO-20260903-CLOSE-WEDGE-LEDGER-ABSENT` · `WO-20260903-SUNSET-DISPOSITIONS-OWED` · `WO-20260903-THE-REFUSAL-HALF-OF-MANAGER-CONTROL-BLOCK` · `WO-20260904-MANAGER-IDLE-IS-UNBOUNDED-AND-NOTHING-WAKES-IT` · `WO-20260905-PENDING-PINGS-HAS-NO-MERGE-DRIVER-SO-PING-PRS-DIE` · `WO-20260906-NO-5M-OR-15M-SCALP-EXIT-HEAD` · `WO-20260906-SHIP-THE-EXIT-HEAD-INTO-ICT-SCALP-THREE-LEGS` · `WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED` · `WO-20260908-RE-ARM-PROTECTION-FOR-THE-56-NAKED` · `WO-20260908-RECONCILE-THE-BYBIT-1-ETHUSDT-ORPHAN-TRADE`

**Nothing in flight or waiting has been still for ≥14d** on declared dates.

⚠️ **Basis `declared_dates_only`.** Computed from `opened_at` and each edge's `since`. NOT a filesystem or git observation of when the object last changed. **1 of these objects carry no usable movement date at all** and are counted as *unstated*, never as stalled — silence about movement is not evidence of stillness.

## 4 · Decisions waiting on the operator

**From the work store: 9 `operator_decision` edge(s).**
- `WO-20260901-PHASE-H` (CAPABILITY) → DEC-20260901-READ-GATE-SEQUENCING · since 2026-09-01
- `WO-20260902-DECISION-LOCAL-LLM-WEIGHT` (DECISION) → DEC-20260902-LOCAL-LLM-WEIGHT · since 2026-08-31
- `WO-20260902-DECISION-REVIEW-PACKET-POPULATION` (DECISION) → DEC-20260902-REVIEW-PACKET-POPULATION · since 2026-09-02
- `WO-20260903-SUNSET-DISPOSITIONS-OWED` (QUESTION) → DEC-20260903-SUNSET-DISPOSITION-POLICY · since 2026-09-01
- `WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE` (DECISION) → DEC-20260907-TP-VALUE · since 2026-09-07
- `WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE` (DECISION) → DEC-20260907-TP-VOCAB · since 2026-09-07
- `WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE` (DECISION) → DEC-20260907-TP-UNEXAMINED · since 2026-09-07
- `WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED` (EVIDENCE) → Tier-3 approval of the held PR re-scoping IBClient.close's confirmation, AND a stated choice on IB_CLOSE_CONFIRM_PARTIAL (strict | reduction) · since 2026-09-07
- `WO-20260908-RECONCILE-THE-BYBIT-1-ETHUSDT-ORPHAN-TRADE` (EVIDENCE) → Tier-2 OK to change _bybit_position_protection's book selection and _recently_closed_adopted_orphan's exit_reason allowlist (src/runtime/order_monitor.py — closes live positions and gates the adopt path) · since 2026-09-08

⚠️ Zero here does NOT mean no decision is pending — it means no object DECLARES one, and 636 of 686 objects have never been assessed for edges at all.

**From `docs/claude/operator-owed-register.json`: read state `read`, 0 OPEN item(s)** (carry limit 2; 5 terminal, not listed).

- _(none open — every item carries a terminal `status`)_

Status vocabulary: src.runtime.operator_owed (imported). ⚠️ Re-deriving it is not a hypothetical risk — this file's first run keyed on a field the register does not have (`state`, not `status`) and reported all 5 terminal items as open, one of them a question the operator had closed.

The two sources are kept **separate rather than merged** — one says *this work is held by a pending decision*, the other is the durable record of anything whose next action belongs to a person. Neither is a superset.

## 5 · Everything else that is due

> Ported here by operator decision 2026-09-08 (`consolidate_into_the_readout`) so that ONE surface answers *what is due right now*. ⚠️ **This did not improve the diagnosis in §1.** Folding registers in adds ROWS, not assessed `blocked_on` BASIS — §1 still refuses to name a stage, and refusing is still correct. A longer readout is not a better-evidenced one.

**Completeness: `all_sources_read`** · **116 row(s) due** across the sources that answered and are not already covered above (via `scripts/ops/render_due_list.py`).

| source | state | rows | note |
|---|---|---|---|
| `open_items` | `read` | 57 |  |
| `soaks` | `read` | 2 | 2 declared soak(s): ready=1 · accruing=0 · not_writing=0 · unknown=1 |
| `operator_owed` | `read` | _not repeated_ | covered by §4 · Decisions waiting on the operator |
| `research_queue` | `read` | 3 |  |
| `probes` | `read` | 10 | freshness=stale age=31.1h cadence=daily (cron 20 5 * * *) \| 2 probe result(s) deferred to the `soaks` source,… |
| `red_crons` | `read` | 11 |  |
| `unlanded_automation` | `read` | 21 |  |
| `error_feed` | `read` | 12 | digest 2026-09-08T05:10:01+00:00, age 12.4h |
| `sunset` | `read` | 0 |  |

⚠️ `rows` reads `—`, never `0`, for a source that could not be read — the distinction this table exists to preserve. A source marked _not repeated_ WAS read; its rows are rendered in the section named in its note.

**`open_items` — 57 due**

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
- 🔔 `OI-20260826-MGC-JOURNAL-QTY-DIVERGENT-UNOWNED` · 0d — loud row — must be reported on every session
  - ⚠️ RE-MEASURED 2026-09-08 AND THIS HAS ESCALATED 43x — do not re-quote the 1-lot framing. ib_paper/MGC: the journal now declares 54 lots across TWO open rows while the venue holds…
- 🔔 `OI-20260903-CAPTURE-WATCH-PROVED-ITSELF-ON-DEMAND-AND-ITS-SCHEDULE-HAS-NEVER-SUCCEEDED` · 5d — monitoring row 5d since last observation (cadence 3d)
  - SUCCESSOR to OI-20260829-TRAINER-IS-NOW-A-DECIDED-DEPENDENCY-AND-IS-UNMONITORED, which CLEARED 2026-09-03 on its clause (a): trainer-capture-watch.yml run 33719856283 graded the t…
- 🔔 `OI-20260829-ALPACA-GOLIVE-BLOCKED-ON-T1-SETTLEMENT-MODEL` · 8d — loud row — must be reported on every session
  - alpaca_live go-live: the T+1 model now EXISTS (PR #10408, merged + deployed 2026-08-29, running at `annotate`). The row stays OPEN because `clears_when` requires the model be SHOW…
- 🔔 `OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED` · 2d — monitoring row 2d since last observation (cadence 2d)
  - Tier-3, operator-approved 2026-08-30: e35 bracket geometry shipped to 9 legs (10 fields). THREE route to bybit_2 = REAL MONEY (trend_donchian, trend_donchian_eth_4h, trend_donchia…
- 🔔 `OI-20260829-E35-REVERSED-LEGS-ARE-A-TIER-3-PROPOSAL-SET-NOT-APPLIED` · 8d — loud row — must be reported on every session
  - ⚠️ THIS ROW SAID '15 SHIPPABLE gate-passing cells across 10 live legs' AND THAT IS STALE — do not re-quote it. Re-measured 2026-08-31 against docs/research/exit-refinement-coverag…
- 🔔 `OI-20260831-PER-ACCOUNT-ARBITRATION-SHIPPED-NOT-YET-ARMED-OR-EXERCISED` · 2d — loud row — must be reported on every session
  - ⚠️ ARMED ON bybit_1 AS OF 2026-08-31T07:47Z — this row's own ID still reads 'NOT-YET-ARMED' and that half is now STALE. The id is deliberately NOT renamed (CLAUDE.md and several b…
- 🔔 `OI-20260831-TRADE-PRIORITISATION-IS-LIVE-BUT-UNPROVEN-AND-ITS-AB-HARNESS-DOES-NOT-EXIST` — loud row — must be reported on every session
  - CONFIDENCE IS NOW THE LIVE PRIMARY RANKING KEY for competing trades (2026-08-31, PR #10544) and it has NEVER been shown to pick the better-performing trade. It went live on a CORR…
- 🔔 `OI-20260831-PROP-RISK-GATE-ENFORCE-ARMED-BUT-HAS-NEVER-CAPPED` · 2d — loud row — must be reported on every session
  - PROP_TICKET_RISK_GATE_MODE=enforce is LIVE on breakout_1 (Tier-3, operator-approved 2026-08-31). It is ARMED and has never CAPPED a ticket. Those are different facts and only the …
- 🔔 `OI-20260831-LIVE-WALLET-TRUTH-CANNOT-REPRODUCE-THE-LEDGER-WINDOW` · 8d — loud row — must be reported on every session
  - The live Bybit wallet-truth path works and is MEASURED, but it does not and cannot currently reproduce the -$262.52 figure it was built to replace -- the two are over almost disjo…
- 🔔 `OI-20260831-RESEARCH-QUEUE-INFEASIBLE-STATE-SHIPPED-BUT-NEVER-REACHED-LIVE` · 8d — loud row — must be reported on every session
  - The stamp chain IS proven live as of 2026-08-31 (995 rows carry `accruing`, read from the corpus). What remains unproven is this row's own subject: the `infeasible` grade has neve…
- 🔔 `OI-20260831-RESEARCH-QUEUE-GPU-ROUTE-AND-SPEND-GATE-NEVER-EXERCISED` · 8d — loud row — must be reported on every session
  - The research queue's GPU route and its spend-gate preflight have NEVER been exercised. Do not describe the GPU spend gates as verified.
- 🔔 `OI-20260831-SESSION-BRIEF-DIFF-SCOPING-SHIPPED-NEVER-REPORTED-INHERITED` · 8d — monitoring row 8d since last observation (cadence 7d)
  - session-brief-guard is diff-scoped now, so a brief that goes stale on the CLOCK can no longer fail a PR that did not touch the registers. The `inherited` verdict has NEVER been em…
- 🔔 `OI-20260831-SCOPE-OVERLAP-ATTRIBUTOR-MISREADS-A-SESSIONS-OWN-START` · 8d — loud row — must be reported on every session
  - scope-overlap-audit works — its FIRST GENUINE catch landed 2026-08-31T17:26Z. But the same comment ALSO reported this session's own board START back at it, because the attributor …
- 🔔 `OI-20260831-LOCAL-LLM-VERDICT-PENDING-A-DIFFERENT-SIZE-ARM` · 7d — loud row — must be reported on every session
  - Whether the half-built local LLM carries any weight is UNDECIDED — and it is undecided BY THE OPERATOR, not for want of evidence. ⚠️ THIS SUMMARY PREVIOUSLY ENDED '#10605 makes th…
- 🔔 `OI-20260831-RESEARCH-READ-DEBT-11-UNREAD-AND-256-SUPERSEDED-UNREAD` · 8d — monitoring row 8d since last observation (cadence 7d)
  - The research pipeline's MACHINERY is healthy and its READING is in debt. Measured 2026-08-31T21:0xZ via research_disposition.survey(): 370 units across 3 corpora — 103 disposition…
- 🔔 `OI-20260831-PROBE-READERS-SHIPPED-BUT-THE-ACTIONS-ONE-HAS-NEVER-RUN-FOR-REAL` · 2d — loud row — must be reported on every session
  - Three new probe SOURCES shipped (probe_file / probe_api / probe_actions_log) taking coverage 3 -> 7 probed. probe_actions_log has NEVER run against the real GitHub Actions API — i…
- 🔔 `OI-20260831-ALPACA-LIVE-FIRST-REAL-MONEY-LEG-ROUTED-BUT-HAS-NEVER-TRADED` · 2d — loud row — must be reported on every session
  - alpaca_live now ROUTES REAL MONEY for the first time since 2026-07-15 — tlt_pullback_1h, Tier-3 operator-approved 2026-08-31. It is ROUTED and has never PLACED AN ORDER. Those are…
- 🔔 `OI-20260901-CYCLE-PRIORITY-IS-RENDERED-BUT-NO-SESSION-HAS-ACTED-ON-IT` · 2d — loud row — must be reported on every session
  - A3 priority propagation shipped (operating-layer Phase C): docs/claude/CYCLE-PRIORITY.json is rendered into CLAUDE.md's SESSION BRIEF by render_session_brief.py, so a session now …
- 🔔 `OI-20260901-REVIEW-PACKET-CANNOT-PROPOSE-AN-ACTION-AND-ITS-EVIDENCE-BLOCK-IS-UNEXERCISED` · 2d — monitoring row 2d since last observation (cadence 1d)
  - Phase F/C3 is now COMPLETE AS PLUMBING — cron (#10649), committed path, and a reader (GET /api/bot/strategy-reviews, PR #10681) — and the decision surface STILL PROPOSES NOTHING. …
- 🔔 `OI-20260901-CONSTRAINT-READOUT-SHIPPED-AND-IT-REFUSES-NOBODY-HAS-ACTED-ON-THE-REFUSAL` · 2d — loud row — must be reported on every session
  - E1/A1 shipped (operating-layer Phase D, PR #10680): scripts/ops/constraint_readout.py computes the constraint over the work store's typed blocked_on edges and renders the four-ite…
- 🔔 `OI-20260901-ALPACA-SHARE-HOLD-CLASSIFIER-SHIPPED-NOT-YET-OBSERVED` · 0d — loud row — must be reported on every session
  - PR #10679 (DRAFT, Tier-2 order path, awaiting operator approval) adds classify_share_hold() so an Alpaca close that cannot free its shares says WHY -- four never-collapsed states,…
- 🔔 `OI-20260901-DECISION-ROUNDTRIP-SHIPPED-AND-NO-DECISION-HAS-EVER-MADE-THE-ROUND-TRIP` · 2d — loud row — must be reported on every session
  - Operating-layer PHASE H (the control half) shipped the DECISION ROUND-TRIP as a draft PR (#10705 bot, ict-trader-dashboard#211 SPA): a work object may declare answerable `decision…
- 🔔 `OI-20260901-SUNSET-PASS-SHIPPED-AND-NOTHING-HAS-BEEN-RETIRED` · 2d — loud row — must be reported on every session
  - E3 shipped (Phase G): scripts/ops/sunset_pass.py grades retirement candidates weekly, docs/claude/SUNSET-DISPOSITIONS.json records answers, and check_sunset_dispositions.py escala…
- 🔔 `OI-20260901-CLAUDE-CHANNEL-SEPARATION-SHIPPED-BUT-UNPROVEN` · 7d — monitoring row 7d since last observation (cadence 1d)
  - The dedicated Claude bot (@ict_cluade_bot) is CONFIGURED and its drain is FIXED and DEPLOYED, and pings still land in the TRADER chat. #10674 is merged (5c45ca52) and live on the …
- 🔔 `OI-20260902-TELEGRAM-DECISION-ROUNDTRIP-SHIPPED-AND-NO-TAP-HAS-EVER-BEEN-MADE` · 2d — loud row — must be reported on every session
  - The TELEGRAM half of the decision round-trip is built (src/runtime/telegram_decisions.py + a `wdec:*` branch in telegram_query_bot.callback_handler + a job-queue sweep): an unansw…
- 🔔 `OI-20260902-BYBIT-COVERAGE-BASIS-MERGED-WITH-ARMING-DELIBERATELY-HELD-FOR-A-SOAK` · 2d — loud row — must be reported on every session
  - #10746 (src/runtime/bybit_coverage_basis.py) is MERGED to main (af9af5e3) and its ARMING IS DELIBERATELY HELD, on an operator instruction given in-conversation 2026-09-02: 'hold i…
- 🔔 `OI-20260902-DECISION-PROMPTS-MOVED-TO-CLAUDEBOT-AND-NO-TAP-HAS-LANDED-THERE` · 2d — loud row — must be reported on every session
  - Work-decision prompts are re-pointed from the TRADER bot to the dedicated Claude bot (@ict_cluade_bot), and the thing that makes that safe ships in the SAME PR: src/bot/claude_dec…
- 🔔 `OI-20260902-CONSTRAINT-READOUT-CRON-SHIPPED-AND-HAS-NEVER-FIRED-ON-SCHEDULE` · 0d — loud row — must be reported on every session
  - [!] THE HEADLINE IN THIS ROW'S OWN ID IS NOW FALSE AND THE ID IS KEPT ONLY BECAUSE OTHER ROWS AND DOCS LINK IT BY NAME -- the same treatment OI-20260902-PR-QUEUE-WATCHER-SHIPPED-A…
- 🔔 `OI-20260902-REPLAY-PREGATE-CANNOT-FINISH-BECAUSE-THE-TRAINER-IS-OUT-OF-MEMORY` · 2d — loud row — must be reported on every session
  - replay-pregate-nightly's nightly red is NOT a network fault and NOT an OOM kill -- the trainer VM is out of memory and swap-thrashing, and the fleet run cannot finish. MEASURED on…
- 🔔 `OI-20260902-REAPER-SHIPPED-AND-THE-KILL-PROVED-A-PARTIAL-LOSS-NOT-A-CLEAN-ONE` · 6d — loud row — must be reported on every session
  - MI-70 built the missing Phase E reaper (scripts/ops/session_reaper.py + .github/workflows/session-reaper.yml) and RAN THE KILL. !! THE HEADLINE IS THAT WO-20260901-PHASE-E'S DONE-…
- 🔔 `OI-20260902-DECISION-DRAIN-ROUTINE-DOES-NOT-EXIST-AND-NOTHING-HAS-EVER-DRAINED` · 2d — monitoring row 2d since last observation (cadence 2d)
  - The decision push-back's REPO half is built and its DELIVERY half has never run once. asked_by records which session asked (measured before: of 584 objects, ZERO requests named th…
- 🔔 `OI-20260902-API-REFERENCE-SPLIT-OUT-OF-CLAUDE-MD-AND-NOT-YET-FOUND-AT-USE` · 2d — loud row — must be reported on every session
  - The per-endpoint API reference (156,448 B: the /api/bot/* route table, BotStats, Position, CORS, and the /api/diag/* table) was moved VERBATIM out of CLAUDE.md into docs/reference…
- 🔔 `OI-20260902-PR-QUEUE-WATCHER-SHIPPED-AND-ITS-CRON-HAS-NEVER-FIRED` · 2d — monitoring row 2d since last observation (cadence 2d)
  - ⚠️ CORRECTED 2026-09-04 — THE HEADLINE IN THIS ROW'S OWN ID IS NOW FALSE AND THE ID IS KEPT ONLY BECAUSE OTHER ROWS LINK IT BY NAME: THE CRON HAS FIRED. Measured over the workflow…
- 🔔 `OI-20260902-DIGEST-MOVED-OFF-A-CRON-THAT-DOES-NOT-FIRE-AND-THE-NEW-TRIGGER-HAS-NEVER-FIRED` · 2d — monitoring row 2d since last observation (cadence 1d)
  - The operator's digest was moved OFF GitHub cron and onto push:main (#10845, MI-80), because cron does not fire here: work-digest had FOUR runs in its entire life against an hourly…
- 🔔 `OI-20260902-HOURLY-DIGEST-CARRIER-PREPARED-AND-HELD-FOR-A-TIER-2-OK` · 2d — monitoring row 2d since last observation (cadence 2d)
  - The hourly work digest has NO durable carrier, and the replacement is PREPARED BUT NOT APPLIED, held on a Tier-2 operator OK. .github/workflows/work-digest.yml declares `20 * * * …
- 🔔 `OI-20260903-OPERATOR-COMMANDS-MOVED-OFF-THE-TRADER-BOT-PREPARED-AND-HELD` · 5d — monitoring row 5d since last observation (cadence 2d)
  - ⚠️ THE TWO ROUTING HALVES ARE NOW OBSERVED AND THIS ROW IS NARROWED, NOT CLEARED — do not re-quote its PREPARED-AND-HELD framing, which was true only until 08:00Z. #10904 was appr…
- 🔔 `OI-20260903-CLOSE-WEDGE-LEDGER-MERGED-AND-HAS-NEVER-BEEN-OBSERVED-PRESENT-ON-THE-FLEET` · 5d — loud row — must be reported on every session
  - #10944 MERGED (squash 717f00eb, Tier-2, operator-approved 2026-09-03) makes the trader's monitor sweep heartbeat an empty-but-PRESENT close-wedge ledger, and stops the reader synt…
- 🔔 `OI-20260904-MANAGER-WAKE-BUILT-AND-ITS-SCHEDULER-DOES-NOT-EXIST` · 3d — loud row — must be reported on every session
  - MI-123 / PR #11014 built the manager durable wake: `manager_wake.py` (assess/brief/receipt), `check_wake_liveness.py`, a committed Routine prompt, 28 tests. The DELIVERY half is O…
- 🔔 `OI-20260905-MI-128-SWEEP-WINDOW-REKEYED-AND-THE-PR-HOLDS-ON-A-BACKFILL-DECISION` · 3d — loud row — must be reported on every session
  - MI-128 / PR #11037 re-keys `_sweep_local_pnl_for_unpriced`'s scan window and ORDER BY from `created_at` (the OPEN) to `COALESCE(closed_at, created_at)` (the CLOSE). Keyed on the o…
- 🔔 `OI-20260906-ICT-SCALP-5M-DEMOTED-OFF-BYBIT2-AND-NOTHING-HAS-SEEN-IT-STOP` · 0d — loud row — must be reported on every session
  - ict_scalp_5m was demoted off the REAL-MONEY bybit_2 account (Tier-3, operator-approved 2026-09-06, scope bybit_2 only) by removing it from that account's `strategies:` list in con…
- 🔔 `OI-20260906-THE-BRACKET-CALIBRATION-INSTRUMENT-EXISTS-AND-ITS-VERDICT-HAS-NOT-BEEN-ACTED-ON` · 2d — loud row — must be reported on every session
  - E3.6's calibration falsifier finally has an instrument (MI-148, PR #11138) and it returns a clear negative: the fleet's take-profits are not predictions. NOTHING HAS BEEN CHANGED …
- 🔔 `OI-20260906-SCALP-EXIT-HEAD-ARTIFACT-IS-THE-SECOND-MISSING-HALF-NOT-THE-FIRST` · 1d — loud row — must be reported on every session
  - MI-154, measured 2026-09-06 at main 957fc81d. The lane was opened on the premise that PR #11140 had SHIPPED the ict_scalp exit-head consumer and that only the artifact was missing…
- 🔔 `OI-20260906-ICT-SCALP-EXIT-HEAD-CONSUMER-SHIPPED-AND-CANNOT-SCORE-UNTIL-A-SCALP-HEAD-IS-PUBLISHED` · 2d — loud row — must be reported on every session
  - MI-150 shipped the M20 exit-head consumer into `ict_scalp` (annotate-only, disarmed) -- the wiring MI-146 identified as the only passed-gate work blocked on missing code. ⚠️ IT CA…
- 🔔 `OI-20260906-ML2-CALIBRATION-PASSED-BUT-THE-SHARPNESS-RESULT-IS-AGAINST-THE-WRONG-BASELINE` · 2d — loud row — must be reported on every session
  - ML-2 was BUILT and REFUTED, and the refutation is the finding. Calibration PASSES on a real corpus (n=9,814 backtest trades, 3 trend_donchian 15m legs, MACE 0.0061); sharpness aga…
- 🔔 `OI-20260907-BE-FLOOR-R-IS-MEASURED-AND-REFUTED-AND-ITS-PR-IS-A-DRAFT-NOBODY-CAN-CLEAR` · 1d — loud row — must be reported on every session
  - MI-163's Proposal B (be_floor_r, a fleet-wide break-even floor) has now been MEASURED PATH-AWARE and REFUTED, and the refutation is the finding. POPULATION: 28 graded ratchet legs…
- 🔔 `OI-20260907-CLOSE-CONFIRM-RESCOPE-IS-HELD-AND-MI-140-MUST-NOT-BE-FIXED-BEFORE-IT` · 1d — loud row — must be reported on every session
  - ⚠️ ORDERING CONSTRAINT ON REAL MONEY — READ THIS BEFORE PICKING UP MI-140. PR #11279 (Tier-3, landing: hold) re-scopes IBClient.close's confirmation from the SYMBOL to the TRADE. …
- 🔔 `OI-20260907-TELEMETRY-HOOK-IS-APPROVED-AND-GREEN-IN-AN-UNMERGED-PR-AND-OBSERVED-ON-NOTHING` · 1d — loud row — must be reported on every session
  - MI-163 measured 9 of the 44 enabled+live legs (the 8 ict_scalp_* legs + squeeze_breakout_4h) as structurally invisible: record_position_telemetry is called from INSIDE a unit's mo…
- 🔔 `OI-20260908-ALPACA-QUANTITY-COVERAGE-SHIPPED-AND-A-56-SHARE-NAKED-POSITION-IS-STANDING` · 0d — loud row — must be reported on every session
  - Two facts, and the SECOND is the urgent one. (1) SHIPPED, NOT PROVEN: AlpacaClient.protection_coverage plus the sweep's covered/partially_naked/coverage_ungradeable/coverage_read_…
- 🔔 `OI-20260908-A-HEDGE-BOOK-FLAT-READ-IS-CLOSING-LIVE-BYBIT-POSITIONS-AND-THE-FLAP-GUARD-CANNOT-SEE-IT` · 0d — loud row — must be reported on every session
  - TWO FACTS, and neither is the orphan the operator was alerted about. (1) ROOT CAUSE, UNFIXED AND LIVE: src/runtime/order_monitor.py:8821 does `pos = rows[0]` on a symbol-scoped ge…
- 🔔 `OI-20260908-QLD-TQQQ-EXAMINED-LOCALLY-AND-THE-CI-FEED-PATH-HAS-NEVER-RUN` · 0d — loud row — must be reported on every session
  - THREE FACTS, and they are different — say WHICH you cleared. (1) EXAMINED, locally: MI-195 added a yfinance-direct last rung to e35_shard_plan.resolve_feed_source (planner matrix …

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

**`probes` — 10 due**

- 🔔 `PROBE-REPORT-STALE` · 1d — EVERY probe verdict below was observed at 2026-09-07T10:31:05.344186+00:00, NOT today. The probes job did not land a fresh report, so treat…
  - probe report is 31.1h old — expected daily (cron 20 5 * * *)
- `OI-20260903-CAPTURE-WATCH-PROVED-ITSELF-ON-DEMAND-AND-ITS-SCHEDULE-HAS-NEVER-SUCCEEDED` — we did not look — this row is currently unwatched [observed 2026-09-07T10:31:05.344186+00:00, 31.1h ago]
  - probe could not run (exit_2)
- 🔔 `OI-20260831-PROP-RISK-GATE-ENFORCE-ARMED-BUT-HAS-NEVER-CAPPED` — probe FAILED — its declared observation did not hold [observed 2026-09-07T10:31:05.344186+00:00, 31.1h ago]
  - A prop_ticket_risk_soak row exists, within the last 1000, in which the gate ran under `enforce`, graded a ticket `exceeds_cushion`, and records `would_have_capped: true`.
- 🔔 `OI-20260831-RESEARCH-QUEUE-INFEASIBLE-STATE-SHIPPED-BUT-NEVER-REACHED-LIVE` — probe FAILED — its declared observation did not hold [observed 2026-09-07T10:31:05.344186+00:00, 31.1h ago]
  - Reads all three committed research corpora and looks for any row stamped research_power_state=infeasible — the R4 grade this row's id says nothing has ever produced. The POSITIVE …
- `OI-20260831-RESEARCH-QUEUE-GPU-ROUTE-AND-SPEND-GATE-NEVER-EXERCISED` — we did not look — this row is currently unwatched [observed 2026-09-07T10:31:05.344186+00:00, 31.1h ago]
  - probe could not run (exit_2)
- `OI-20260831-SESSION-BRIEF-DIFF-SCOPING-SHIPPED-NEVER-REPORTED-INHERITED` — we did not look — this row is currently unwatched [observed 2026-09-07T10:31:05.344186+00:00, 31.1h ago]
  - probe could not run (exit_2)
- 🔔 `OI-20260902-BYBIT-COVERAGE-BASIS-MERGED-WITH-ARMING-DELIBERATELY-HELD-FOR-A-SOAK` — probe FAILED — its declared observation did not hold [observed 2026-09-07T10:31:05.344186+00:00, 31.1h ago]
  - A bybit_coverage_soak row exists in which the SIDE-AWARE grade actually DISAGREED with the side-blind sum (verdicts_differ: true). That is the load-bearing half of this row's thre…
- `OI-20260902-SUNSET-CANDIDATES-ARE-NOW-DUE-AND-NINE-ARE-UNDISPOSITIONED` — we did not look — this row is currently unwatched [observed 2026-09-07T10:31:05.344186+00:00, 31.1h ago]
  - probe could not run (exit_2)
- 🔔 `OI-20260902-DECISION-DRAIN-ROUTINE-DOES-NOT-EXIST-AND-NOTHING-HAS-EVER-DRAINED` — probe FAILED — its declared observation did not hold [observed 2026-09-07T10:31:05.344186+00:00, 31.1h ago]
  - Whether the decision push-back drain has recorded a run inside its window, graded over the committed receipt docs/claude/work/DECISION-DRAIN.json. Four states, never collapsed: fr…
- `OI-20260902-DIGEST-MOVED-OFF-A-CRON-THAT-DOES-NOT-FIRE-AND-THE-NEW-TRIGGER-HAS-NEVER-FIRED` — we did not look — this row is currently unwatched [observed 2026-09-07T10:31:05.344186+00:00, 31.1h ago]
  - probe could not run (exit_2)

**`red_crons` — 11 due**

- 🔔 `constraint-readout` — latest scheduled run concluded 'failure'
  - constraint-readout
- 🔔 `due-list` — latest scheduled run concluded 'failure'
  - due-list
- 🔔 `error-feed-digest` — latest scheduled run concluded 'cancelled'
  - error-feed-digest
- 🔔 `macro-valuation-snapshot` — latest scheduled run concluded 'failure'
  - macro-valuation-snapshot
- 🔔 `pr-queue-watch` — latest scheduled run concluded 'failure'
  - pr-queue-watch
- 🔔 `probes` — latest scheduled run concluded 'failure'
  - probes
- 🔔 `replay-pregate-nightly` — latest scheduled run concluded 'failure'
  - replay-pregate-nightly
- 🔔 `session-reaper` — latest scheduled run concluded 'failure'
  - session-reaper
- 🔔 `strategy-review-packets` — latest scheduled run concluded 'failure'
  - strategy-review-packets
- 🔔 `sunset-pass` — latest scheduled run concluded 'failure'
  - sunset-pass
- 🔔 `trainer-capture-watch` — latest scheduled run concluded 'cancelled'
  - trainer-capture-watch

**`unlanded_automation` — 21 due**

- 🔔 `#11426` · 0d — producer output opened a PR that has not landed
  - chore(ops): queue the daily work digest (auto)
- 🔔 `#11425` · 0d — producer output opened a PR that has not landed
  - chore(ops): PR-queue watcher receipt (auto)
- 🔔 `#11423` · 0d — producer output opened a PR that has not landed
  - chore(ops): trainer capture-watch receipt (auto)
- 🔔 `#11422` · 0d — producer output opened a PR that has not landed
  - chore(ops): session-reaper observations (auto)
- 🔔 `#11420` · 0d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#11416` · 0d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#11408` · 0d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#11404` · 0d — producer output opened a PR that has not landed
  - chore(ops): session-reaper observations (auto)
- 🔔 `#11402` · 0d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#11399` · 0d — producer output opened a PR that has not landed
  - chore(ops): session-reaper observations (auto)
- 🔔 `#11397` · 0d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#11393` · 0d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#11386` · 0d — producer output opened a PR that has not landed
  - chore(m28): valuation snapshots (auto)
- 🔔 `#11382` · 0d — producer output opened a PR that has not landed
  - chore(ops): PR-queue watcher receipt (auto)
- 🔔 `#11376` · 0d — producer output opened a PR that has not landed
  - chore(ops): session-reaper observations (auto)
- 🔔 `#11368` · 0d — producer output opened a PR that has not landed
  - chore(ops): refresh the due-list (auto)
- 🔔 `#11367` · 0d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#11366` · 0d — producer output opened a PR that has not landed
  - chore(ops): refresh probe results (auto)
- 🔔 `#11362` · 0d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#11361` · 0d — producer output opened a PR that has not landed
  - chore(m7): strategy review packets (auto)
- 🔔 `#11353` · 0d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)

**`error_feed` — 12 due**

- 🔔 `ERROR-FEED-DIGEST-STALE` · 0d — EVERY group below was observed at 2026-09-08T05:10:01+00:00, NOT now. The digest workflow did not land a fresh run, so an absent condition …
  - error-feed digest is 12.4h old — expected hourly
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
- `ERRFEED-a46d2970` · 6d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 25 rows 2026-09-02T02:03:23 → 2026-09-07T22:49:26 · symbo…
  - [error] x25 ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: mhg_pullback_Nd | Symbol: MHG Reason: candles_u
- `ERRFEED-fa77ad80` · 6d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 20 rows 2026-09-02T02:03:23 → 2026-09-07T22:49:25 · symbo…
  - [error] x20 ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: mes_trend_long_Nd | Symbol: MES Reason: candles
- `ERROR-FEED-SUMMARY` — 75 further error group(s) and every warn group are NOT listed above — read `docs/claude/ERROR-FEED-DIGEST.json` for the full set. Digest ve…
  - 124 cause groups over 1481 rows (85 error-level, 39 warn-level, 4 new since the last digest)

_This section decides nothing. Every row is for a session to judge._

