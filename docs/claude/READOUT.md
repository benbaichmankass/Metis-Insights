# The readout — where the chain is held up, and what that costs

_Generated 2026-09-18T11:04:27+00:00 by `scripts/ops/constraint_readout.py` · cycle `CY-20260906-TRADING-TRUTH` (basis DECIDED)_

> **This is A1, and it is computed rather than judged.** It reports its denominator before its conclusion, because a constraint named over unassessed edges is a fabricated answer wearing a computed label.

## 1 · Where the chain is held up

**Verdict: `insufficient_basis`**

92 of 189 objects (48.7%) have an ASSESSED `blocked_on` basis, below the declared floor of 50%. **No stage is named.** 96 objects carry an empty `blocked_on` that is NOT a claim that nothing blocks them — it is nobody having looked. A stage computed over this graph would describe the 92 rows somebody assessed, not the system.

| population | assessed | coverage | floor |
|---|---|---|---|
| 189 objects | 92 | **48.7%** | 50.0% |

**Edge basis, never collapsed** — `blocked` 29 · `declared_none` 63 · **`unstated` 96** · `malformed` 1.

⚠️ `unstated` is an empty `blocked_on` whose basis says `NOT_ASSESSED` (or which carries no basis at all). It is **nobody having looked**, not a claim that nothing blocks the object. Reading the second as the first is how a false *ready* appears.

⚠️ **23 object(s) disagree with their OWN edge basis.** Both halves were already computed and their INTERSECTION was reported nowhere — a signal written and never read.

| contradiction | n | what it means |
|---|---|---|
| `parked_though_unblocked` | 13 | `waiting` with an ASSESSED basis saying nothing blocks it. That is `ready`. It inflates the set the operator reads as *held up*, in the direction that HIDES real queue depth. |
| `queued_though_blocked` | 9 | `ready`/`in_flight` while a live typed blocker is declared. The opposite direction and the more dangerous one — a session taking it walks into a blocker its own file declares. |
| `closed_though_blocked` | 1 | `done`/`accepted` with a live blocker — residue. Harmless to the queue, but it keeps a spent edge in the graph. |

⚠️ **`unstated` is deliberately NOT a contradiction.** *Nobody looked* cannot disagree with a lifecycle, and counting it would turn the store's coverage gap (96 objects) into 96 findings and bury these. Nor is `malformed`: *we could not read the edge* cannot disagree with anything either.

⚠️ **This decides nothing.** A lifecycle is its object owner's or the manager's to re-grade, and moving one changes what the WIP ceiling and this readout compute over. Each row below names the object; the judgement is theirs.

- `WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE` — **closed_though_blocked** (`done` + basis `blocked`)
- `WO-20260903-CLOSE-WEDGE-LEDGER-ABSENT` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260904-MANAGER-IDLE-IS-UNBOUNDED-AND-NOTHING-WAKES-IT` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260905-PENDING-PINGS-HAS-NO-MERGE-DRIVER-SO-PING-PRS-DIE` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260906-ML-2-THE-PREDICTIVE-BRACKET` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260906-THE-EXIT-GEOMETRY-REBUILD-WAS-SPECIFIED-AND-NEVER-DISPATCHED` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260908-COORDINATION-BOARD-IS-AT-GITHUB-S-2500` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260908-RECONCILE-THE-BYBIT-1-ETHUSDT-ORPHAN-TRADE` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260908-TRADE-SCOPE-THE-ALPACA-CLOSE-OPERATION-AND` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260908-WIRE-MORE-STRATEGIES-TO-ALPACA-LIVE-WHICH` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260910-ROOT-CAUSE-THE-EXIT-EVAL-60S-BREACHES` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260911-LET-AN-APPROVED-TIER-2-PR-LAND` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260912-OPS-LANE-DRIVE-THE-HEALTH-BACKLOG-DOWN` — **parked_though_unblocked** (`waiting` + basis `declared_none`)
- `WO-20260901-PHASE-G` — **queued_though_blocked** (`ready` + basis `blocked`)
- `WO-20260901-PHASE-H` — **queued_though_blocked** (`ready` + basis `blocked`)
- `WO-20260902-DECISION-LOCAL-LLM-WEIGHT` — **queued_though_blocked** (`ready` + basis `blocked`)
- `WO-20260903-CANONIZE-THE-MANAGER-DUTIES-QUEUE-CADENCE` — **queued_though_blocked** (`ready` + basis `blocked`)
- `WO-20260903-EVERY-PR-APPROVAL-REACHES-THE-OPERATOR-AS-AN` — **queued_though_blocked** (`ready` + basis `blocked`)
- `WO-20260907-ALPACA-LIVE-S-ROUTED-REAL-MONEY-LEG` — **queued_though_blocked** (`ready` + basis `blocked`)
- `WO-20260908-DECISION-ALPACA-SCOPED-WRITE-CAPABILITY` — **queued_though_blocked** (`ready` + basis `blocked`)
- `WO-20260908-DECISION-BE-FLOOR-R-ARM-NOTHING` — **queued_though_blocked** (`ready` + basis `blocked`)
- `WO-20260908-DECISION-TRADE-PRIORITISATION-HARNESS` — **queued_though_blocked** (`ready` + basis `blocked`)

Objects by stage: `QUESTION` 16 · `EVIDENCE` 32 · `DECISION` 19 · `DEPLOYMENT` 26 · `OBSERVATION` 7 · `CAPABILITY` 33 · `INTEGRITY` 43 · `(unstated)` 13

⚠️ **24 of 189 of those stages were assigned in BULK FROM THE SOURCE FILENAME, not by reading the row.** The Phase C migration maps `health-review-backlog.json` → `INTEGRITY` and `{ml,performance,research}-review-backlog.json` → `EVIDENCE`, with no per-row judgement, so `INTEGRITY 43` is a census of ONE filename. Only **162** stage(s) in the whole store were chosen per object — and choosing one is not a claim it is RIGHT, only that a filename did not decide it.

Stage by how the stage was arrived at: `bulk_by_source_file` → EVIDENCE 9, INTEGRITY 15 · `per_object` → (unstated) 10, CAPABILITY 33, DECISION 19, DEPLOYMENT 26, EVIDENCE 23, INTEGRITY 28, OBSERVATION 7, QUESTION 16 · `unstated` → (unstated) 3

**The assessed subgraph — every object that declares an edge (29 of 189):**

- **`WO-20260901-PHASE-A`** (CAPABILITY · waiting) — Phase A — survival — the plan carries itself forward
  - `external_event` → `a measured behaviour comparison of sessions with and without the brief, over a stated denominator, which may return NO` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-08
- **`WO-20260901-PHASE-C`** (CAPABILITY · waiting) — Phase C — migration, the WIP ceiling, and the priority that reaches a session
  - `external_event` → `a cold session stating this cycle's priority and citing the CLAUDE.md brief as where it read it` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260901-PHASE-D`** (CAPABILITY · waiting) — Phase D — the constraint, computed rather than judged
  - `external_event` → `a session writing TRUE blocked_on edges, taking assessed coverage over the declared 50% floor so E1 can name a stage instead of refusing` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
  - `external_event` → `a demonstration that the readout supersedes docs/claude/DUE.* in full, or an operator decision to keep both` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260901-PHASE-F`** (CAPABILITY · waiting) — Phase F — the repairs — decision preparation and decision recording
  - `operator_decision` → `DEC-20260909-STRATEGY-REVIEW-WINDOW-FLOOR` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-09
- **`WO-20260901-PHASE-G`** (CAPABILITY · ready) — Phase G — the forcing function — retirement, and the E2 pull rule
  - `external_event` → `assessed `blocked_on` coverage crossing the readout's declared 50% floor, so E1 can NAME a held-up stage instead of refusing` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260901-PHASE-H`** (CAPABILITY · ready) — Phase H — the control half — decisions from the UI, and the read gate
  - `object` → `WO-20260901-PHASE-B` → `done` · ref `resolved` · hold **`stale`** · since 2026-09-01
  - `object` → `BL-20260901-RETIRE-ANDROID-AND-STREAMLIT-FROM-THE-LIVE-FEED` → `dormant` · ref `resolved` · hold **`holding`** · since 2026-09-01
  - `object` → `BL-20260901-DB-EXPLORER-IS-UNGATED-AND-REACHES-DEVICE-TOKENS-RAW-TOKEN-COLUMN` → `done` · ref `resolved` · hold **`stale`** · since 2026-09-01
  - `operator_decision` → `DEC-20260901-READ-GATE-SEQUENCING` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260902-DECISION-LOCAL-LLM-WEIGHT`** (DECISION · ready) — Does the local LLM carry any weight, and at which size — 3B, 1.5B, or none?
  - `operator_decision` → `DEC-20260902-LOCAL-LLM-WEIGHT` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-08-31
- **`WO-20260902-DECISION-REVIEW-PACKET-POPULATION`** (DECISION · waiting) — Which POPULATION does the M7 strategy-review gate decide on — real money, or real blended with paper?
  - `operator_decision` → `DEC-20260902-REVIEW-PACKET-POPULATION` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-02
- **`WO-20260902-EVIDENCE-TRADE-PRIORITISATION-AB`** (EVIDENCE · waiting) — Confidence-first is the LIVE ranking key for competing trades and has never been shown to pick the better trade — and the A/B that would settle it cannot be run
  - `capability` → `an N-book mode in scripts/backtest_system.py plus a ranking-key arm (the shape of its existing --flip-policy), and a workflow that fires it` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-08-31
- **`WO-20260903-CANONIZE-THE-MANAGER-DUTIES-QUEUE-CADENCE`** (DECISION · ready) — Canonize the manager's BASE DUTIES -- running the merge queue and holding a check-in cadence -- as mechanisms, plus the meta-rule that turns operator feedback…
  - `pr` → `10918` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-03
- **`WO-20260903-EVERY-PR-APPROVAL-REACHES-THE-OPERATOR-AS-AN`** (DECISION · ready) — Every PR awaiting operator approval reaches them as an ANSWERABLE Telegram prompt, not as a PR sitting silently in a queue
  - `work_object` → `WO-20260903-THE-REFUSAL-HALF-OF-MANAGER-CONTROL-BLOCK` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-03
- **`WO-20260903-SUNSET-DISPOSITIONS-OWED`** (QUESTION · waiting) — Ten evidenced retirement candidates have sat undispositioned since 2026-09-01, and retiring a leg is Tier-3 so only the operator can move them
  - `operator_decision` → `DEC-20260903-SUNSET-DISPOSITION-POLICY` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-01
- **`WO-20260903-THE-REFUSAL-HALF-OF-MANAGER-CONTROL-BLOCK`** (CAPABILITY · waiting) — The refusal half of manager control: block a manager action on a PR whose author session is still live
  - `external_event` → `PR #10905 merging, and then a manager actually INVOKING the gate` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-03
- **`WO-20260906-ICT-SCALP-5M-TO-SHADOW-THEN-ESTABLISH`** (DECISION · waiting) — ict_scalp_5m to shadow, then establish whether it should trade real money at all
  - `data_accrual` → `a `trades` row for `ict_scalp_5m` on `bybit_1` created after the post-#11121 restart, to serve as the POSITIVE CONTROL denominator against which the absence of a `bybit_2` sibling row can be read` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-11
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
- **`WO-20260907-ALPACA-LIVE-S-ROUTED-REAL-MONEY-LEG`** (QUESTION · ready) — alpaca_live's routed real-money leg cannot place an order
  - `object` → `WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED` → `waiting` · ref `resolved` · hold **`holding`** · since 2026-09-07
- **`WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED`** (EVIDENCE · waiting) — The close-confirmation gate is symbol-scoped while the close is trade-scoped, so a succeeded close is graded a failure and its journal row left open
  - `data_accrual` → `a real IB close on a symbol holding a sibling trade's lots, OBSERVED confirming on the fleet` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-08
  - `object` → `WO-20260907-ALPACA-LIVE-S-ROUTED-REAL-MONEY-LEG` → `ready` · ref `resolved` · hold **`holding`** · since 2026-09-07
  - `backlog_row` → `bybit_2 is NOT exposed and must NOT be "fixed" by adding a confirmation` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-07
- **`WO-20260908-DECISION-ALPACA-SCOPED-WRITE-CAPABILITY`** (DECISION · ready) — 56 shares have been unprotected since 2026-09-03 because every Alpaca write is symbol-wide — build the scoped capability, flatten, or accept it?
  - `operator_decision` → `DEC-20260908-ALPACA-SCOPED-WRITE` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-08
- **`WO-20260908-DECISION-BE-FLOOR-R-ARM-NOTHING`** (DECISION · ready) — be_floor_r is measured and refuted at every commissioned value — does the operator accept that the Tier-3 answer is NONE?
  - `operator_decision` → `DEC-20260908-BE-FLOOR-R-ARM-NOTHING` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-07
- **`WO-20260908-DECISION-TRADE-PRIORITISATION-HARNESS`** (DECISION · ready) — Confidence has been the live primary ranking key on real money since 2026-08-31 and has never been shown to pick the better trade — fund the harness that would…
  - `operator_decision` → `DEC-20260908-TRADE-PRIORITISATION-HARNESS` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-08-31
- **`WO-20260908-RE-ARM-PROTECTION-FOR-THE-56-NAKED`** (DEPLOYMENT · waiting) — Re-arm protection for the 56 naked shares on trade 5414 (alpaca_portfolio/TLT)
  - `capability` → `BL-20260908-ALPACA-PLACE-PROTECTIVE-IGNORES-OCA-KEY-SO-EVERY-RE-ARM-IS-SYMBOL-WIDE` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-08
- **`WO-20260909-DECISION-CLOSED-FLAT-INVARIANT-CANNOT-SEE-A-FAILED-READ`** (INTEGRITY · waiting) — The closed-flat invariant reads a FAILED exchange read as flat - operator answered: fix all three, F-11 + F-12 + F-13
  - `external_event` → `A PLANTED violation observed being reported end to end on the fleet - a row on /api/diag/log_file?name=invariant_violations AND the matching Level.ERROR reaching the operator.` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-10
- **`WO-20260909-DECISION-INTENT-REDUCE-ORPHANS-A-PROTECTIVE-LEG`** (DECISION · waiting) — intent_reduce leaves a stop up to 101.8x the row it protects - operator answered: resize the leg AND add the per-row invariant
  - `data_accrual` → `a real intent-reduce firing on a Bybit account under BYBIT_TPSL_MODE=partial, after the 7b8b6b51d deploy reaches the running trader - and the shrunk row's tracked leg then read back off /api/diag/bybit_open_orders at that row's NEW position_size` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-09
- **`WO-20260911-A-FILED-ROW-THAT-IS-NEVER-ROUTED`** (CAPABILITY · waiting) — A filed row that is never routed has no age: give state transitions an elapsed-time term and a surface
  - `observation` → `the crossing detector observed catching a REAL newly-unrouted row before the operator asks` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-11
- **`WO-20260911-MAKE-THE-DATA-EXPLORER-REACHABLE-LAND-THE`** (CAPABILITY · waiting) — Make the Data Explorer reachable: land the propagation path and size the SPA bearer half
  - `operator_decision` → `originate JWT_SIGNING_KEY + WEBAPP_PASSWORD_SHA256, then a Tier-2 set-env` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-11
- **`WO-20260912-DECIDE-THE-LOUD-FLAG-TRIAGE`** (DECISION · waiting) — `loud` is set on 84.5% of OPEN-ITEMS rows and has stopped selecting anything - a triage is proposed and needs an operator decision
  - `operator_decision` → `DEC-20260912-LOUD-FLAG-TRIAGE` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-12
- **`WO-20260912-ENGINEERING-LANE-MAKE-THE-LANDING-AND-REGISTER`** (BUILD · waiting) — Make the landing and register machinery stop losing the other lanes' work
  - `operator_decision` → `DEC-20260913-WHAT-BOUNDS-AN-OPEN-ITEMS-OBSERVATION` · ref `not_in_store_by_design` · hold **`unverifiable_outside_store`** · since 2026-09-13

⚠️ **1 of the live `object` holds point at a target whose lifecycle is `waiting`, and that is the weakest hold the graph can express.** `waiting` covers two opposite facts — *not delivered yet* and *delivered, awaiting an observation* — and a dependent needs the capability, not the observation. The store cannot tell them apart, so this is published as a caveat rather than resolved into a state nobody measured. Check the target before treating one of these as a real blocker:
  - `WO-20260907-ALPACA-LIVE-S-ROUTED-REAL-MONEY-LEG` → `WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED`

## 2 · The book and the money

**Read state: `read`** · source `https://ict-bot.duckdns.org/api/bot/performance?window=30d`

Population: **Real-money only, closed non-backtest rows inside the window; paper rides in a separate sub-block on the same route and is never blended.** Window `30d`.

| trades | win rate | totalPnl | totalPnlMeasured | pnlCoverage |
|---|---|---|---|---|
| 40 | 30.0% | -20.036 | -20.036 | **75.0%** |

Provenance split — measured 30 · estimated 10 · fabricated 0 · unverified 0.

⚠️ **The count and the sum are over DIFFERENT populations, deliberately** — `pnlCoverage`/`pnlMeasuredCount` are MEASURED-only, `totalPnlMeasured` sums MEASURED+ESTIMATED. Neither may be harmonised to the other.

`journalTrust` — readState `read` · known-divergent ['bybit_2'] · unrecorded [] · unreadable [].
  - ⚠️ **bybit_2 does not reconcile with the venue's wallet.** A row can be `measured` on an account that does not reconcile at all — coverage and trust are different questions.
  - ⚠️ `accountsUnrecorded` is **not** `accountsTrusted`: the ledger is populated by hand, so an absent record means nobody reconciled that account.

## 3 · In flight against the ceiling, and what has stopped moving

**5 in flight against a ceiling of 8** (headroom 3) · 34 waiting.

Ceiling source: scripts/ci/check_wip_ceiling.py::CEILING (imported, not restated). `waiting` is deliberately free of the ceiling — a thing blocked on an operator decision is not consuming the attention the ceiling rations.

In flight: `WO-20260912-UNBLOCK-THE-PROP-ACCOUNT-IT-IS-STARVED` · `WO-20260916-OPERATOR-DIRECTION` · `WO-20260917-ENGINEERING-LANE-STRUCTURAL-FIXES-AND-RESEARCH-INFRA-FITNESS` · `WO-20260917-OPS-LANE-CANONIZE-THE-SESSION-RECEIPT-THEN-REVIEW-AND-TRIAGE` · `WO-20260917-RESEARCH-LANE-ACTIVE-MANAGEMENT-WHERE-TO-FOCUS-NEXT`

Waiting: `WO-20260901-PHASE-A` · `WO-20260901-PHASE-C` · `WO-20260901-PHASE-D` · `WO-20260901-PHASE-F` · `WO-20260902-DECISION-REVIEW-PACKET-POPULATION` · `WO-20260902-EVIDENCE-TRADE-PRIORITISATION-AB` · `WO-20260903-CLOSE-WEDGE-LEDGER-ABSENT` · `WO-20260903-SUNSET-DISPOSITIONS-OWED` · `WO-20260903-THE-REFUSAL-HALF-OF-MANAGER-CONTROL-BLOCK` · `WO-20260904-MANAGER-IDLE-IS-UNBOUNDED-AND-NOTHING-WAKES-IT` · `WO-20260905-PENDING-PINGS-HAS-NO-MERGE-DRIVER-SO-PING-PRS-DIE` · `WO-20260906-ICT-SCALP-5M-TO-SHADOW-THEN-ESTABLISH` · `WO-20260906-ML-2-THE-PREDICTIVE-BRACKET` · `WO-20260906-NO-5M-OR-15M-SCALP-EXIT-HEAD` · `WO-20260906-SHIP-THE-EXIT-HEAD-INTO-ICT-SCALP-THREE-LEGS` · `WO-20260906-THE-EXIT-GEOMETRY-REBUILD-WAS-SPECIFIED-AND-NEVER-DISPATCHED` · `WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED` · `WO-20260908-COORDINATION-BOARD-IS-AT-GITHUB-S-2500` · `WO-20260908-RE-ARM-PROTECTION-FOR-THE-56-NAKED` · `WO-20260908-RECONCILE-THE-BYBIT-1-ETHUSDT-ORPHAN-TRADE` · `WO-20260908-TRADE-SCOPE-THE-ALPACA-CLOSE-OPERATION-AND` · `WO-20260908-WIRE-MORE-STRATEGIES-TO-ALPACA-LIVE-WHICH` · `WO-20260909-DECISION-CLOSED-FLAT-INVARIANT-CANNOT-SEE-A-FAILED-READ` · `WO-20260909-DECISION-INTENT-REDUCE-ORPHANS-A-PROTECTIVE-LEG` · `WO-20260909-GATE-API-BOT-DB-THE-MONEY-DB` · `WO-20260910-ROOT-CAUSE-THE-EXIT-EVAL-60S-BREACHES` · `WO-20260911-A-FILED-ROW-THAT-IS-NEVER-ROUTED` · `WO-20260911-LET-AN-APPROVED-TIER-2-PR-LAND` · `WO-20260911-MAKE-THE-DATA-EXPLORER-REACHABLE-LAND-THE` · `WO-20260912-DECIDE-THE-LOUD-FLAG-TRIAGE` · `WO-20260912-ENGINEERING-LANE-MAKE-THE-LANDING-AND-REGISTER` · `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · `WO-20260912-OPS-LANE-DRIVE-THE-HEALTH-BACKLOG-DOWN` · `WO-20260912-UNBLOCK-THE-MERGE-QUEUE-THE-R13-MERGE`

**Stopped moving** (no declared movement in ≥14d):
- `WO-20260901-PHASE-C` (waiting) — 17d — Phase C — migration, the WIP ceiling, and the priority that reaches a session
- `WO-20260901-PHASE-D` (waiting) — 17d — Phase D — the constraint, computed rather than judged
- `WO-20260902-DECISION-REVIEW-PACKET-POPULATION` (waiting) — 16d — Which POPULATION does the M7 strategy-review gate decide on — real money, or real blended with paper?
- `WO-20260902-EVIDENCE-TRADE-PRIORITISATION-AB` (waiting) — 16d — Confidence-first is the LIVE ranking key for competing trades and has never been shown to pick the better trade — and the A/B that would settle it cannot be run
- `WO-20260903-CLOSE-WEDGE-LEDGER-ABSENT` (waiting) — 15d — The standing close-wedge ledger does not exist, and the digest that replaced the pager reads a non-canonical path for it -- so a downgraded wedge is in NEITHER…
- `WO-20260903-SUNSET-DISPOSITIONS-OWED` (waiting) — 15d — Ten evidenced retirement candidates have sat undispositioned since 2026-09-01, and retiring a leg is Tier-3 so only the operator can move them
- `WO-20260903-THE-REFUSAL-HALF-OF-MANAGER-CONTROL-BLOCK` (waiting) — 15d — The refusal half of manager control: block a manager action on a PR whose author session is still live
- `WO-20260904-MANAGER-IDLE-IS-UNBOUNDED-AND-NOTHING-WAKES-IT` (waiting) — 14d — a manager that stops acting is not detected by anything -- idle is unbounded, the lease expires into nobody, and the only thing that restarted work was the ope…

⚠️ **Basis `declared_dates_only`.** Computed from `opened_at` and each edge's `since`. NOT a filesystem or git observation of when the object last changed. **1 of these objects carry no usable movement date at all** and are counted as *unstated*, never as stalled — silence about movement is not evidence of stillness.

## 4 · Decisions waiting on the operator

**From the work store: 14 `operator_decision` edge(s).**
- `WO-20260901-PHASE-F` (CAPABILITY) → DEC-20260909-STRATEGY-REVIEW-WINDOW-FLOOR · since 2026-09-09
- `WO-20260901-PHASE-H` (CAPABILITY) → DEC-20260901-READ-GATE-SEQUENCING · since 2026-09-01
- `WO-20260902-DECISION-LOCAL-LLM-WEIGHT` (DECISION) → DEC-20260902-LOCAL-LLM-WEIGHT · since 2026-08-31
- `WO-20260902-DECISION-REVIEW-PACKET-POPULATION` (DECISION) → DEC-20260902-REVIEW-PACKET-POPULATION · since 2026-09-02
- `WO-20260903-SUNSET-DISPOSITIONS-OWED` (QUESTION) → DEC-20260903-SUNSET-DISPOSITION-POLICY · since 2026-09-01
- `WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE` (DECISION) → DEC-20260907-TP-VALUE · since 2026-09-07
- `WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE` (DECISION) → DEC-20260907-TP-VOCAB · since 2026-09-07
- `WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE` (DECISION) → DEC-20260907-TP-UNEXAMINED · since 2026-09-07
- `WO-20260908-DECISION-ALPACA-SCOPED-WRITE-CAPABILITY` (DECISION) → DEC-20260908-ALPACA-SCOPED-WRITE · since 2026-09-08
- `WO-20260908-DECISION-BE-FLOOR-R-ARM-NOTHING` (DECISION) → DEC-20260908-BE-FLOOR-R-ARM-NOTHING · since 2026-09-07
- `WO-20260908-DECISION-TRADE-PRIORITISATION-HARNESS` (DECISION) → DEC-20260908-TRADE-PRIORITISATION-HARNESS · since 2026-08-31
- `WO-20260911-MAKE-THE-DATA-EXPLORER-REACHABLE-LAND-THE` (CAPABILITY) → originate JWT_SIGNING_KEY + WEBAPP_PASSWORD_SHA256, then a Tier-2 set-env · since 2026-09-11
- `WO-20260912-DECIDE-THE-LOUD-FLAG-TRIAGE` (DECISION) → DEC-20260912-LOUD-FLAG-TRIAGE · since 2026-09-12
- `WO-20260912-ENGINEERING-LANE-MAKE-THE-LANDING-AND-REGISTER` (BUILD) → DEC-20260913-WHAT-BOUNDS-AN-OPEN-ITEMS-OBSERVATION · since 2026-09-13

⚠️ Zero here does NOT mean no decision is pending — it means no object DECLARES one, and 96 of 189 objects have never been assessed for edges at all.

**From `docs/claude/operator-owed-register.json`: read state `read`, 0 OPEN item(s)** (carry limit 2; 7 terminal, not listed).

- _(none open — every item carries a terminal `status`)_

Status vocabulary: src.runtime.operator_owed (imported). ⚠️ Re-deriving it is not a hypothetical risk — this file's first run keyed on a field the register does not have (`state`, not `status`) and reported all 5 terminal items as open, one of them a question the operator had closed.

The two sources are kept **separate rather than merged** — one says *this work is held by a pending decision*, the other is the durable record of anything whose next action belongs to a person. Neither is a superset.

## 5 · Everything else that is due

> Ported here by operator decision 2026-09-08 (`consolidate_into_the_readout`) so that ONE surface answers *what is due right now*. ⚠️ **This did not improve the diagnosis in §1.** Folding registers in adds ROWS, not assessed `blocked_on` BASIS — §1 still refuses to name a stage, and refusing is still correct. A longer readout is not a better-evidenced one.

**Completeness: `all_sources_read`** · **204 row(s) due** across the sources that answered and are not already covered above (via `scripts/ops/render_due_list.py`).

| source | state | rows | note |
|---|---|---|---|
| `open_items` | `read` | 80 |  |
| `soaks` | `read` | 4 | 4 declared soak(s): ready=1 · accruing=0 · not_writing=0 · unknown=3 |
| `operator_owed` | `read` | _not repeated_ | covered by §4 · Decisions waiting on the operator |
| `research_queue` | `read` | 3 |  |
| `probes` | `read` | 8 | freshness=fresh age=1.2h cadence=daily (cron 20 5 * * *) \| 4 probe result(s) deferred to the `soaks` source,… |
| `red_crons` | `read` | 2 |  |
| `red_main_runs` | `read` | 2 | 10 workflow(s) graded, 2 red · 0 graded at `main`'s tip · none graded behind the tip · 10 with distance_unkno… |
| `unparseable_workflows` | `read` | 0 | 147 workflow(s) known to GitHub · 143 local file(s) declare a name · 143 gradeable (both sides present) · 0 s… |
| `unlanded_automation` | `read` | 66 |  |
| `error_feed` | `read` | 11 | digest 2026-09-18T06:36:33+00:00, age 4.5h |
| `sunset` | `read` | 12 |  |
| `checklist_unrouted` | `read` | 9 | register_freshness=fresh |
| `stuck_branches` | `read` | 1 | 246 branch(es) standing |
| `settled_disposition_owed` | `read` | 0 | settled record grades settled_graded (114 settled row(s)) |
| `spent_decision_edges` | `read` | 5 | 189 object(s) read, 0 unparseable; 13 reported edge(s): 11 spent, 2 unresolvable; 5 on a `waiting` object |
| `manager_queue_watch` | `read` | 1 | watchdog receipt grades never_ran_overdue |

⚠️ `rows` reads `—`, never `0`, for a source that could not be read — the distinction this table exists to preserve. A source marked _not repeated_ WAS read; its rows are rendered in the section named in its note.

**`open_items` — 80 due**

- 🔔 `OI-20260906-RESEARCH-THAT-SPECIFIES-WORK-IS-CARRIED-BY-NOTHING` · 6d — monitoring row 6d since last observation (cadence 3d)
  - docs/research/EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md told its reader to paste it as a new session and no register pointed at it for 14 days. MI-148 now carries the work (#11138 l…
- 🔔 `OI-20260902-BYBIT-GRADED-COVERAGE-SOAK-IS-THE-ONLY-EVIDENCE-FOR-WIDENING-AND-NOTHING-WATCHES-IT` · 1d — loud row — must be reported on every session
  - #10746 ships a soak at runtime_logs/bybit_coverage_soak.jsonl whose rows are the ONLY declared evidence for the operator's conditional widening of the graded-coverage gate to bybi…
- 🔔 `OI-20260902-STRATEGY-REVIEW-PACKET-BLENDS-REAL-AND-PAPER-PNL` — loud row — must be reported on every session
  - scripts/ml/strategy_review_packet.py::pull_decisions filters ONLY is_backtest — it never consults account_class or is_demo — so every M7 review packet and every INDEX row blends r…
- 🔔 `OI-20260901-OPERATING-LAYER-BUILD-IS-IN-FLIGHT-AND-CARRIED-ONLY-BY-THIS-ROW` · 6d — monitoring row 6d since last observation (cadence 1d)
  - The operating-model redesign (operator-directed, 2026-09-01) is DESIGNED and its build has begun. Phase A of 8 is in flight. ⚠️ THE DESIGN IS FOUR DOCUMENTS UNDER docs/design/ THA…
- `OI-20260826-MHG-OVER-COVER-MECHANISM-UNVERIFIED` · 6d — monitoring row 6d since last observation (cadence 2d)
  - The MHG disjoint-OCA over-cover was CLEARED by hand; the mechanism that should have caught and reported it is NOT yet proven.
- `OI-20260826-SESSION-BRIEF-NEVER-READ-BY-A-FRESH-SESSION` · 6d — monitoring row 6d since last observation (cadence 3d)
  - The generated SESSION-BRIEF block in CLAUDE.md is the mechanism this session shipped in place of a cap and an adjective, and NO fresh session has ever read it. Shipped and working…
- 🔔 `OI-20260826-STRAY-OCA-SWEEP-SHIPPED-BUT-UNARMED` · 6d — monitoring row 6d since last observation (cadence 3d)
  - ⚠️ ARMED ON ib_paper 2026-08-31 — THIS ROW'S ID AND ITS OLD SUMMARY BOTH SAY 'UNARMED' AND ARE STALE. The id is deliberately NOT renamed (ROADMAP.md and several backlog rows link…
- 🔔 `OI-20260826-MGC-JOURNAL-QTY-DIVERGENT-UNOWNED` · 5d — monitoring row 5d since last observation (cadence 2d)
  - ⚠️ RE-MEASURED 2026-09-08 AND THIS HAS ESCALATED 43x — do not re-quote the 1-lot framing. ib_paper/MGC: the journal now declares 54 lots across TWO open rows while the venue holds…
- 🔔 `OI-20260903-CAPTURE-WATCH-PROVED-ITSELF-ON-DEMAND-AND-ITS-SCHEDULE-HAS-NEVER-SUCCEEDED` · 7d — monitoring row 7d since last observation (cadence 3d)
  - SUCCESSOR to OI-20260829-TRAINER-IS-NOW-A-DECIDED-DEPENDENCY-AND-IS-UNMONITORED, which CLEARED 2026-09-03 on its clause (a): trainer-capture-watch.yml run 33719856283 graded the t…
- 🔔 `OI-20260829-ALPACA-GOLIVE-BLOCKED-ON-T1-SETTLEMENT-MODEL` · 18d — loud row — must be reported on every session
  - alpaca_live go-live: the T+1 model now EXISTS (PR #10408, merged + deployed 2026-08-29, running at `annotate`). The row stays OPEN because `clears_when` requires the model be SHOW…
- 🔔 `OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED` · 6d — monitoring row 6d since last observation (cadence 2d)
  - Tier-3, operator-approved 2026-08-30: e35 bracket geometry shipped to 9 legs (10 fields). THREE route to bybit_2 = REAL MONEY (trend_donchian, trend_donchian_eth_4h, trend_donchia…
- 🔔 `OI-20260829-E35-REVERSED-LEGS-ARE-A-TIER-3-PROPOSAL-SET-NOT-APPLIED` · 18d — loud row — must be reported on every session
  - ⚠️ THIS ROW SAID '15 SHIPPABLE gate-passing cells across 10 live legs' AND THAT IS STALE — do not re-quote it. Re-measured 2026-08-31 against docs/research/exit-refinement-coverag…
- 🔔 `OI-20260831-PER-ACCOUNT-ARBITRATION-SHIPPED-NOT-YET-ARMED-OR-EXERCISED` · 7d — monitoring row 7d since last observation (cadence 3d)
  - ⚠️ ARMED ON bybit_1 AS OF 2026-08-31T07:47Z — this row's own ID still reads 'NOT-YET-ARMED' and that half is now STALE. The id is deliberately NOT renamed (CLAUDE.md and several b…
- 🔔 `OI-20260831-PROP-RISK-GATE-ENFORCE-ARMED-BUT-HAS-NEVER-CAPPED` · 6d — monitoring row 6d since last observation (cadence 3d)
  - PROP_TICKET_RISK_GATE_MODE=enforce is LIVE on breakout_1 (Tier-3, operator-approved 2026-08-31). It is ARMED and has never CAPPED a ticket. Those are different facts and only the…
- 🔔 `OI-20260831-LIVE-WALLET-TRUTH-CANNOT-REPRODUCE-THE-LEDGER-WINDOW` · 18d — loud row — must be reported on every session
  - The live Bybit wallet-truth path works and is MEASURED, but it does not and cannot currently reproduce the -$262.52 figure it was built to replace -- the two are over almost disjo…
- 🔔 `OI-20260831-RESEARCH-QUEUE-INFEASIBLE-STATE-SHIPPED-BUT-NEVER-REACHED-LIVE` · 18d — monitoring row 18d since last observation (cadence 14d)
  - The stamp chain IS proven live as of 2026-08-31 (995 rows carry `accruing`, read from the corpus). What remains unproven is this row's own subject: the `infeasible` grade has neve…
- 🔔 `OI-20260831-RESEARCH-QUEUE-GPU-ROUTE-AND-SPEND-GATE-NEVER-EXERCISED` · 18d — loud row — must be reported on every session
  - The research queue's GPU route and its spend-gate preflight have NEVER been exercised. Do not describe the GPU spend gates as verified.
- 🔔 `OI-20260831-SESSION-BRIEF-DIFF-SCOPING-SHIPPED-NEVER-REPORTED-INHERITED` · 6d — loud row — must be reported on every session
  - session-brief-guard is diff-scoped now, so a brief that goes stale on the CLOCK can no longer fail a PR that did not touch the registers. The `inherited` verdict has NEVER been em…
- 🔔 `OI-20260831-SCOPE-OVERLAP-ATTRIBUTOR-MISREADS-A-SESSIONS-OWN-START` · 18d — loud row — must be reported on every session
  - scope-overlap-audit works — its FIRST GENUINE catch landed 2026-08-31T17:26Z. But the same comment ALSO reported this session's own board START back at it, because the attributor…
- 🔔 `OI-20260831-LOCAL-LLM-VERDICT-PENDING-A-DIFFERENT-SIZE-ARM` · 17d — loud row — must be reported on every session
  - Whether the half-built local LLM carries any weight is UNDECIDED — and it is undecided BY THE OPERATOR, not for want of evidence. ⚠️ THIS SUMMARY PREVIOUSLY ENDED '#10605 makes th…
- 🔔 `OI-20260831-RESEARCH-READ-DEBT-11-UNREAD-AND-256-SUPERSEDED-UNREAD` · 6d — loud row — must be reported on every session
  - The research pipeline's MACHINERY is healthy and its READING is in debt. Measured 2026-08-31T21:0xZ via research_disposition.survey(): 370 units across 3 corpora — 103 disposition…
- 🔔 `OI-20260831-PROBE-READERS-SHIPPED-BUT-THE-ACTIONS-ONE-HAS-NEVER-RUN-FOR-REAL` · 7d — monitoring row 7d since last observation (cadence 3d)
  - Three new probe SOURCES shipped (probe_file / probe_api / probe_actions_log) taking coverage 3 -> 7 probed. probe_actions_log has NEVER run against the real GitHub Actions API — i…
- 🔔 `OI-20260831-ALPACA-LIVE-FIRST-REAL-MONEY-LEG-ROUTED-BUT-HAS-NEVER-TRADED` · 5d — monitoring row 5d since last observation (cadence 3d)
  - alpaca_live now ROUTES REAL MONEY for the first time since 2026-07-15 — tlt_pullback_1h, Tier-3 operator-approved 2026-08-31. It is ROUTED and has never PLACED AN ORDER. Those are…
- 🔔 `OI-20260901-CYCLE-PRIORITY-IS-RENDERED-BUT-NO-SESSION-HAS-ACTED-ON-IT` · 6d — monitoring row 6d since last observation (cadence 3d)
  - A3 priority propagation shipped (operating-layer Phase C): docs/claude/CYCLE-PRIORITY.json is rendered into CLAUDE.md's SESSION BRIEF by render_session_brief.py, so a session now…
- 🔔 `OI-20260901-REVIEW-PACKET-CANNOT-PROPOSE-AN-ACTION-AND-ITS-EVIDENCE-BLOCK-IS-UNEXERCISED` · 1d — monitoring row 1d since last observation (cadence 1d)
  - ⚠️ CORRECTED 2026-09-09 by session_01DUBXxiaZ3PMERcAfcJce7P — TWO OF THE THREE CLEARS-WHEN CLAUSES ARE NOW MET AND THE TEXT BELOW STILL SAYS THEY ARE NOT. Do not re-quote its (a)…
- 🔔 `OI-20260901-CONSTRAINT-READOUT-SHIPPED-AND-IT-REFUSES-NOBODY-HAS-ACTED-ON-THE-REFUSAL` · 6d — monitoring row 6d since last observation (cadence 3d)
  - E1/A1 shipped (operating-layer Phase D, PR #10680): scripts/ops/constraint_readout.py computes the constraint over the work store's typed blocked_on edges and renders the four-ite…
- 🔔 `OI-20260901-ALPACA-SHARE-HOLD-CLASSIFIER-SHIPPED-NOT-YET-OBSERVED` · 7d — monitoring row 7d since last observation (cadence 3d)
  - PR #10679 (DRAFT, Tier-2 order path, awaiting operator approval) adds classify_share_hold() so an Alpaca close that cannot free its shares says WHY -- four never-collapsed states,…
- 🔔 `OI-20260901-DECISION-ROUNDTRIP-SHIPPED-AND-NO-DECISION-HAS-EVER-MADE-THE-ROUND-TRIP` · 6d — monitoring row 6d since last observation (cadence 3d)
  - Operating-layer PHASE H (the control half) shipped the DECISION ROUND-TRIP as a draft PR (#10705 bot, ict-trader-dashboard#211 SPA): a work object may declare answerable `decision…
- 🔔 `OI-20260901-SUNSET-PASS-SHIPPED-AND-NOTHING-HAS-BEEN-RETIRED` · 12d — monitoring row 12d since last observation (cadence 7d)
  - E3 shipped (Phase G): scripts/ops/sunset_pass.py grades retirement candidates weekly, docs/claude/SUNSET-DISPOSITIONS.json records answers, and check_sunset_dispositions.py escala…
- 🔔 `OI-20260901-CLAUDE-CHANNEL-SEPARATION-SHIPPED-BUT-UNPROVEN` · 6d — monitoring row 6d since last observation (cadence 1d)
  - !! CORRECTED 2026-09-12 BY session_013iSqp4LsU1eq8K326eUtgj (MI-279 U5) -- THE SET-ENV HAS HAPPENED AND THIS SUMMARY SAID IT WAS OUTSTANDING. Do not re-quote the closing sentence…
- 🔔 `OI-20260902-TELEGRAM-DECISION-ROUNDTRIP-SHIPPED-AND-NO-TAP-HAS-EVER-BEEN-MADE` · 12d — loud row — must be reported on every session
  - The TELEGRAM half of the decision round-trip is built (src/runtime/telegram_decisions.py + a `wdec:*` branch in telegram_query_bot.callback_handler + a job-queue sweep): an unansw…
- 🔔 `OI-20260902-BYBIT-COVERAGE-BASIS-MERGED-WITH-ARMING-DELIBERATELY-HELD-FOR-A-SOAK` · 6d — monitoring row 6d since last observation (cadence 3d)
  - #10746 (src/runtime/bybit_coverage_basis.py) is MERGED to main (af9af5e3) and its ARMING IS DELIBERATELY HELD, on an operator instruction given in-conversation 2026-09-02: 'hold i…
- 🔔 `OI-20260902-DECISION-PROMPTS-MOVED-TO-CLAUDEBOT-AND-NO-TAP-HAS-LANDED-THERE` · 12d — loud row — must be reported on every session
  - Work-decision prompts are re-pointed from the TRADER bot to the dedicated Claude bot (@ict_cluade_bot), and the thing that makes that safe ships in the SAME PR: src/bot/claude_dec…
- 🔔 `OI-20260902-CONSTRAINT-READOUT-CRON-SHIPPED-AND-HAS-NEVER-FIRED-ON-SCHEDULE` · 7d — monitoring row 7d since last observation (cadence 3d)
  - [!] THE HEADLINE IN THIS ROW'S OWN ID IS NOW FALSE AND THE ID IS KEPT ONLY BECAUSE OTHER ROWS AND DOCS LINK IT BY NAME -- the same treatment OI-20260902-PR-QUEUE-WATCHER-SHIPPED-A…
- `OI-20260902-SUNSET-CANDIDATES-ARE-NOW-DUE-AND-NINE-ARE-UNDISPOSITIONED` · 12d — monitoring row 12d since last observation (cadence 7d)
  - E3's retirement candidates now REACH a session. scripts/ops/render_due_list.py gained a `sunset` source: every `retire_candidate` in the newest comms/sunset/<date>/INDEX.json with…
- 🔔 `OI-20260902-REPLAY-PREGATE-CANNOT-FINISH-BECAUSE-THE-TRAINER-IS-OUT-OF-MEMORY` · 6d — monitoring row 6d since last observation (cadence 3d)
  - replay-pregate-nightly's nightly red is NOT a network fault and NOT an OOM kill -- the trainer VM is out of memory and swap-thrashing, and the fleet run cannot finish. MEASURED on…
- 🔔 `OI-20260902-REAPER-SHIPPED-AND-THE-KILL-PROVED-A-PARTIAL-LOSS-NOT-A-CLEAN-ONE` · 16d — loud row — must be reported on every session
  - MI-70 built the missing Phase E reaper (scripts/ops/session_reaper.py + .github/workflows/session-reaper.yml) and RAN THE KILL. !! THE HEADLINE IS THAT WO-20260901-PHASE-E'S DONE-…
- 🔔 `OI-20260902-DECISION-DRAIN-ROUTINE-DOES-NOT-EXIST-AND-NOTHING-HAS-EVER-DRAINED` · 6d — monitoring row 6d since last observation (cadence 2d)
  - The decision push-back's REPO half is built and its DELIVERY half has never run once. asked_by records which session asked (measured before: of 584 objects, ZERO requests named th…
- 🔔 `OI-20260902-API-REFERENCE-SPLIT-OUT-OF-CLAUDE-MD-AND-NOT-YET-FOUND-AT-USE` · 12d — monitoring row 12d since last observation (cadence 7d)
  - The per-endpoint API reference (156,448 B: the /api/bot/* route table, BotStats, Position, CORS, and the /api/diag/* table) was moved VERBATIM out of CLAUDE.md into docs/reference…
- 🔔 `OI-20260902-PR-QUEUE-WATCHER-SHIPPED-AND-ITS-CRON-HAS-NEVER-FIRED` · 7d — monitoring row 7d since last observation (cadence 2d)
  - ⚠️ CORRECTED 2026-09-04 — THE HEADLINE IN THIS ROW'S OWN ID IS NOW FALSE AND THE ID IS KEPT ONLY BECAUSE OTHER ROWS LINK IT BY NAME: THE CRON HAS FIRED. Measured over the workflow…
- 🔔 `OI-20260902-HOURLY-DIGEST-CARRIER-PREPARED-AND-HELD-FOR-A-TIER-2-OK` · 6d — monitoring row 6d since last observation (cadence 2d)
  - The hourly work digest has NO durable carrier, and the replacement is PREPARED BUT NOT APPLIED, held on a Tier-2 operator OK. .github/workflows/work-digest.yml declares `20 * * *…
- 🔔 `OI-20260903-OPERATOR-COMMANDS-MOVED-OFF-THE-TRADER-BOT-PREPARED-AND-HELD` · 15d — monitoring row 15d since last observation (cadence 2d)
  - ⚠️ THE TWO ROUTING HALVES ARE NOW OBSERVED AND THIS ROW IS NARROWED, NOT CLEARED — do not re-quote its PREPARED-AND-HELD framing, which was true only until 08:00Z. #10904 was appr…
- 🔔 `OI-20260903-CLOSE-WEDGE-LEDGER-MERGED-AND-HAS-NEVER-BEEN-OBSERVED-PRESENT-ON-THE-FLEET` · 6d — loud row — must be reported on every session
  - #10944 MERGED (squash 717f00eb, Tier-2, operator-approved 2026-09-03) makes the trader's monitor sweep heartbeat an empty-but-PRESENT close-wedge ledger, and stops the reader synt…
- 🔔 `OI-20260904-MANAGER-WAKE-BUILT-AND-ITS-SCHEDULER-DOES-NOT-EXIST` · 13d — loud row — must be reported on every session
  - MI-123 / PR #11014 built the manager durable wake: `manager_wake.py` (assess/brief/receipt), `check_wake_liveness.py`, a committed Routine prompt, 28 tests. The DELIVERY half is O…
- 🔔 `OI-20260905-MI-128-SWEEP-WINDOW-REKEYED-AND-THE-PR-HOLDS-ON-A-BACKFILL-DECISION` · 13d — loud row — must be reported on every session
  - MI-128 / PR #11037 re-keys `_sweep_local_pnl_for_unpriced`'s scan window and ORDER BY from `created_at` (the OPEN) to `COALESCE(closed_at, created_at)` (the CLOSE). Keyed on the o…
- 🔔 `OI-20260906-ICT-SCALP-5M-DEMOTED-OFF-BYBIT2-AND-NOTHING-HAS-SEEN-IT-STOP` · 6d — monitoring row 6d since last observation (cadence 1d)
  - ict_scalp_5m was demoted off the REAL-MONEY bybit_2 account (Tier-3, operator-approved 2026-09-06, scope bybit_2 only) by removing it from that account's `strategies:` list in con…
- 🔔 `OI-20260906-THE-BRACKET-CALIBRATION-INSTRUMENT-EXISTS-AND-ITS-VERDICT-HAS-NOT-BEEN-ACTED-ON` · 12d — monitoring row 12d since last observation (cadence 7d)
  - E3.6's calibration falsifier finally has an instrument (MI-148, PR #11138) and it returns a clear negative: the fleet's take-profits are not predictions. NOTHING HAS BEEN CHANGED…
- 🔔 `OI-20260906-SCALP-EXIT-HEAD-ARTIFACT-IS-THE-SECOND-MISSING-HALF-NOT-THE-FIRST` · 11d — monitoring row 11d since last observation (cadence 7d)
  - MI-154, measured 2026-09-06 at main 957fc81d. The lane was opened on the premise that PR #11140 had SHIPPED the ict_scalp exit-head consumer and that only the artifact was missing…
- 🔔 `OI-20260906-ICT-SCALP-EXIT-HEAD-CONSUMER-SHIPPED-AND-CANNOT-SCORE-UNTIL-A-SCALP-HEAD-IS-PUBLISHED` · 1d — loud row — must be reported on every session
  - MI-150 shipped the M20 exit-head consumer into `ict_scalp` (annotate-only, disarmed) -- the wiring MI-146 identified as the only passed-gate work blocked on missing code. ⚠️ IT CA…
- 🔔 `OI-20260906-ML2-CALIBRATION-PASSED-BUT-THE-SHARPNESS-RESULT-IS-AGAINST-THE-WRONG-BASELINE` · 6d — monitoring row 6d since last observation (cadence 5d)
  - ML-2 was BUILT and REFUTED, and the refutation is the finding. Calibration PASSES on a real corpus (n=9,814 backtest trades, 3 trend_donchian 15m legs, MACE 0.0061); sharpness aga…
- 🔔 `OI-20260907-CLOSE-CONFIRM-RESCOPE-IS-HELD-AND-MI-140-MUST-NOT-BE-FIXED-BEFORE-IT` · 7d — monitoring row 7d since last observation (cadence 3d)
  - ⚠️ ORDERING CONSTRAINT ON REAL MONEY — READ THIS BEFORE PICKING UP MI-140. PR #11279 re-scoped IBClient.close's confirmation from the SYMBOL to the TRADE and is MERGED — squashed…
- 🔔 `OI-20260908-ALPACA-QUANTITY-COVERAGE-SHIPPED-AND-A-56-SHARE-NAKED-POSITION-IS-STANDING` · 7d — monitoring row 7d since last observation (cadence 3d)
  - Two facts, and the SECOND is the urgent one. (1) SHIPPED, NOT PROVEN: AlpacaClient.protection_coverage plus the sweep's covered/partially_naked/coverage_ungradeable/coverage_read_…
- 🔔 `OI-20260908-A-HEDGE-BOOK-FLAT-READ-IS-CLOSING-LIVE-BYBIT-POSITIONS-AND-THE-FLAP-GUARD-CANNOT-SEE-IT` · 6d — monitoring row 6d since last observation (cadence 2d)
  - [!] CORRECTED 2026-09-12 by MI-281 (session_01ALe9gTsSEMVWYyR8X8TY79). THIS SUMMARY SAID 'ROOT CAUSE, UNFIXED AND LIVE' AND THAT IS NOW FALSE IN THE DANGEROUS DIRECTION - a sessio…
- 🔔 `OI-20260908-QLD-TQQQ-EXAMINED-LOCALLY-AND-THE-CI-FEED-PATH-HAS-NEVER-RUN` · 10d — monitoring row 10d since last observation (cadence 7d)
  - THREE FACTS, and they are different — say WHICH you cleared. (1) EXAMINED, locally: MI-195 added a yfinance-direct last rung to e35_shard_plan.resolve_feed_source (planner matrix…
- 🔔 `OI-20260908-THE-REAL-MONEY-CLOSED-POPULATION-IS-447-NOT-15-AND-TWO-SURFACES-DISAGREE-ON-ITS-EXIT-LABELS` · 6d — loud row — must be reported on every session
  - TWO MEASUREMENT TRAPS ON THE REAL-MONEY TRADE SURFACE, both of which have ALREADY produced a wrong operator-facing conclusion (2026-09-08, MI-200). Read this BEFORE measuring real…
- 🔔 `OI-20260909-PROP-STATUS-REQUEST-TRIGGER-DEPLOYED-BUT-THE-SUPPRESSION-HAS-NEVER-BEEN-OBSERVED` · 6d — monitoring row 6d since last observation (cadence 2d)
  - [!] CORRECTED 2026-09-09T22:52Z — CLAUSE (1) IS CLEARED AND THIS ROW'S OWN ID NOW OVERSTATES WHAT IS OPEN. The id is deliberately NOT renamed (the generated SESSION BRIEF, MI-214'…
- 🔔 `OI-20260909-THE-FULL-SYSTEM-AUDITS-TIER-2-AND-TIER-3-PROPOSALS-ARE-FILED-AND-CARRIED-BY-NOTHING` · 6d — monitoring row 6d since last observation (cadence 3d)
  - The 2026-09-09 full-system audit (docs/audits/full-system-audit-2026-09-09.md, report RPT-20260909-171500-audit) filed 68 findings and 19 backlog rows and REMEDIATED NOTHING, corr…
- 🔔 `OI-20260909-CLOSED-FLAT-INVARIANT-CAN-FINALLY-SPEAK-AND-HAS-NOT-YET-SPOKEN` · 1d — loud row — must be reported on every session
  - The closed->exchange-flat invariant -- THE ONE MECHANISM that can independently contradict 'this trade is closed' -- was silent for four independent reasons, each alone sufficient…
- 🔔 `OI-20260909-INTENT-REDUCE-LEG-RESIZE-SHIPPED-AND-NO-LEG-HAS-BEEN-OBSERVED-RESIZED` · 5d — monitoring row 5d since last observation (cadence 3d)
  - MI-227 (Tier-2, operator-approved 2026-09-09, WO-20260909-DECISION-INTENT-REDUCE-ORPHANS-A-PROTECTIVE-LEG, chosen: resize_and_invariant) makes an intent-reduce resize the SHRUNK p…
- 🔔 `OI-20260909-THE-ROLLBACK-REGISTRY-IS-COLLAPSED-AND-THE-ROLLBACK-HAS-NEVER-BEEN-EXERCISED` · 6d — loud row — must be reported on every session
  - MI-229 deleted pipeline._STRATEGY_BUILDERS -- the SECOND strategy->builder registry -- and routed multiplexed_signal_builder + monitor_unit_for through the intent layer's roster (…
- 🔔 `OI-20260910-EXIT-EVAL-NEAR-MISS-BAND-AND-RESTART-GAP-SHIPPED-AND-NEITHER-HAS-GRADED-A-LIVE-BOUNDARY` · 8d — monitoring row 8d since last observation (cadence 7d)
  - MI-230 built the two halves the operator chose on 2026-09-09 (WO-20260909-DECISION-M20-EXIT-EVAL-MARGIN-COLLAPSED, chosen: near_miss_and_restart_gap; audit F-50). (a) exit_loop_he…
- 🔔 `OI-20260910-F39-STRANDED-PACKAGE-LEGS-DETECTOR-BUILT-AND-THE-REATTACH-AWAITS-AN-OPERATOR-YES-NO` · 8d — monitoring row 8d since last observation (cadence 3d)
  - Audit F-39 / MI-231 under WO-20260909-DECISION-PACKAGES-CLOSED-WHILE-HOLDING-OPEN-LEGS (operator answered 2026-09-09, chosen: detector_and_reattach, Tier-2). TWO HALVES AND THEY A…
- 🔔 `OI-20260910-WORKFLOW-PAGE-SHIPPED-AND-THE-OPERATOR-HAS-NOT-SEEN-IT-RENDER` · 8d — monitoring row 8d since last observation (cadence 3d)
  - MI-238, operator-directed 2026-09-10: the manager checklist became a live Workflow page on the SPA with collapsible rows and the open-decision list, so the operator can track sess…
- 🔔 `OI-20260910-ALPACA-LIVE-OPTION-A-FOUR-LEGS-ADDED-TO-REAL-MONEY-AND-THE-ORDER-PATH-HAS-NEVER-BEEN-EXERCISED` · 5d — monitoring row 5d since last observation (cadence 3d)
  - ⚠️ CORRECTED 2026-09-10T11:46Z -- MERGED AND DEPLOYED ARE NOW BOTH TRUE AND THE TEXT BELOW STILL SAYS THEY ARE NOT. Do not re-quote its 'NOT MERGED, NOT DEPLOYED AND NOT OBSERVED'…
- `OI-20260910-ORPHAN-ADOPT-SIZE-GATE-DEPLOYED-AND-HAS-REFUSED-NOTHING` · 6d — monitoring row 6d since last observation (cadence 3d)
  - MI-255 (Tier-2, operator-approved via the manager 2026-09-10, PR #11708) added a size-plausibility gate to the orphan-adopt attribution path: src/runtime/orphan_attribution.py (a…
- `OI-20260911-THE-UNROUTED-ROW-DETECTOR-IS-SEEDED-AND-HAS-REPORTED-NOTHING` · 7d — monitoring row 7d since last observation (cadence 2d)
  - MI-246 (Tier-1, PR #11787) gave a FILED-but-never-ROUTED manager-checklist row an AGE, derived from the git history of docs/claude/work/MANAGER-CHECKLIST.json because items[] carr…
- 🔔 `OI-20260911-BLOCKED-LANE-WATCH-SHIPPED-AND-NO-LANE-HAS-BEEN-WOKEN-BY-IT` · 7d — monitoring row 7d since last observation (cadence 3d)
  - MI-235 shipped the NOTICER for a lane blocked on a manager action: a typed `blocked_on` edge on the SESSIONS.json row (`session_registry.py blocked-on`, which REFUSES a kind it ca…
- 🔔 `OI-20260911-WEBAPP-AUTH-SECRET-MAP-LANDED-AND-HAS-CARRIED-NO-VALUE` · 6d — loud row — must be reported on every session
  - MI-266 (Tier-1, PR #11783) added SECRET_JWT_SIGNING_KEY + SECRET_WEBAPP_PASSWORD_SHA256 to the SECRET_* map in .github/workflows/system-actions.yml, plus docs/runbooks/restore-web…
- 🔔 `OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED` · 1d — monitoring row 1d since last observation (cadence 1d)
  - A DATED REGIME BREAK AND NOBODY NOTICED FOR TWO WEEKS. MEASURED 2026-09-12 (MI-278 U14) over /api/diag/journal?table=trades&limit=1000 -- bybit_1, status=closed, NOT is_backtest,…
- 🔔 `OI-20260911-THE-PROP-ACCOUNT-IS-STARVED-NOT-QUIET-AND-THE-UNBLOCK-IS-A-SEQUENCE-NOT-A-FIX` · 6d — monitoring row 6d since last observation (cadence 1d)
  - [!] CORRECTED 2026-09-12 by MI-282 session_01QF6kgTnqd4yg32y2UYe9FQ — TWO OF THIS ROW'S THREE BLOCKERS ARE MISSTATED AND THE TEXT BELOW STILL SAYS THEM. Do not re-quote blocker (1…
- 🔔 `OI-20260911-THE-TWO-DETECTORS-ARE-BUILT-AND-NEITHER-HAS-EVER-FIRED-ON-THE-FLEET` · 1d — loud row — must be reported on every session
  - MI-276 shipped TWO alert-only Tier-2 detectors for the shape found twice on 2026-09-11, where both investigations ended with the same sentence - the operator noticed before any mo…
- 🔔 `OI-20260912-THE-WINNER-SIZE-COLLAPSE-IS-AN-R-COLLAPSE-AND-ITS-MAGNITUDE-IS-NOT-ESTABLISHED` · 6d — loud row — must be reported on every session
  - MI-277 (docs/research/winner-size-collapse-2026-09-12.md, WO-20260912-DECOMPOSE-THE-WINNER-SIZE-COLLAPSE-AND-GRADE) decomposed MI-271's avg-win collapse on the identity mean(pnl|w…
- 🔔 `OI-20260911-TIER-2-APPROVED-SELF-LANDING-IS-BUILT-AND-NO-PR-HAS-LANDED-OR-BEEN-REFUSED-BY-IT` · 6d — monitoring row 6d since last observation (cadence 3d)
  - pr-landing R15 lets a Tier-2 PR self-land against an approval record it demonstrably could not have written -- the record must exist at the branch's merge-base with main, be byte-…
- 🔔 `OI-20260910-IB-PER-PASS-BREAKER-ARMED-AND-HAS-SKIPPED-NOTHING` · 5d — monitoring row 5d since last observation (cadence 2d)
  - MI-240 (Tier-2, operator-approved as DEC-20260910-EXIT-EVAL-60S-REMEDY, chosen `r2_only`, 2026-09-10T07:52Z) arms R2: once ONE IB-routed fetch in an exit-evaluation pass returns a…
- 🔔 `OI-20260912-MI280-SHIPPED-EIGHT-LANDING-AND-REGISTER-INSTRUMENTS-AND-MOST-HAVE-NEVER-BEEN-SEEN-WORKING` · 1d — loud row — must be reported on every session
  - MI-280 (engineering lane, session_01BjTp5RYwedmpFfkEtkQo1j, 2026-09-12) shipped eight units against four measured, unowned failure modes in the landing/register machinery. MERGED,…
- 🔔 `OI-20260912-FANOUT-APPLY-PATH-REPAIRED-AND-STILL-HAS-NEVER-DISPATCHED` · 6d — monitoring row 6d since last observation (cadence 2d)
  - #11970 (1baff7a1f) carries the round geometry into apply_rounds so the dispatcher stops refusing every round, and grades `applied` through the dispatcher's own validator. VERIFIED…
- 🔔 `OI-20260912-BYBIT-SYMBOL-DEDUPE-REPAIRED-AND-NO-FLEET-READ-HAS-BEEN-SEEN-WITHOUT-THE-DROP` · 5d — monitoring row 5d since last observation (cadence 2d)
  - MI-283 repairs account_open_positions' bybit dedupe from SYMBOL-only to (symbol, position_idx), so a hedge symbol's second LIVE book is no longer discarded. The dropped book was i…
- 🔔 `OI-20260913-A-BYBIT2-HEDGE-BOOK-IS-NEVER-FETCHED-AND-NO-SURFACE-IN-THIS-REPO-CAN-SAY-WHETHER-IT-IS-STILL-THERE` · 5d — monitoring row 5d since last observation (cadence 1d)
  - OPERATOR-RAISED 2026-09-12: a real-money ETHUSDT SHORT 0.05 on bybit_2 open at the venue with no stop and no take-profit, invisible to every bot surface. Filed as
- 🔔 `OI-20260917-THE-GENERATED-CONFLICT-RECOMPUTE-IS-PROVEN-IN-REPLAY-AND-HAS-NEVER-RUN-ON-A-RUNNER` · 1d — loud row — must be reported on every session
  - PR #12472 makes commit-to-main RECOMPUTE a generated artifact on conflict instead of aborting, under three ANDed conditions (a refresh-command is declared; EVERY conflicted path i…
- 🔔 `OI-20260918-THE-TARGET-GEOMETRY-PACKET-EXISTS-AND-ITS-THREE-DECISIONS-ARE-UNANSWERED` · 0d — loud row — must be reported on every session
  - MI-317 (PR #12526) delivered the per-leg target-geometry decision packet over ALL 55 legs in config/strategies.yaml: docs/research/per-leg-target-geometry-packet-2026-09-18.md and…

**`soaks` — 4 due**

- `OI-20260902-BYBIT-GRADED-COVERAGE-SOAK-IS-THE-ONLY-EVIDENCE-FOR-WIDENING-AND-NOTHING-WATCHES-IT` · 16d — soak UNKNOWN — NO PROBE IS DECLARED for this soak, so nothing is reading it. Ready means: verdicts_differ=true. This is a KNOWN, DECLARED g…
  - #10746 ships a soak at runtime_logs/bybit_coverage_soak.jsonl whose rows are the ONLY declared evidence for the operator's conditional widening of the graded-coverage gate to bybi…
- 🔔 `OI-20260906-ICT-SCALP-EXIT-HEAD-CONSUMER-SHIPPED-AND-CANNOT-SCORE-UNTIL-A-SCALP-HEAD-IS-PUBLISHED` · 12d — soak READY — ✅ READY — 41 matching row(s) of 1000 scanned satisfy the declared criterion (decision_state!=not_scored). ⚠️ Ready is not CLEA…
  - MI-150 shipped the M20 exit-head consumer into `ict_scalp` (annotate-only, disarmed) -- the wiring MI-146 identified as the only passed-gate work blocked on missing code. ⚠️ IT CA…
- `OI-20260909-POSITION-READ-STATE-SOAK-DECIDES-WHETHER-THE-COLLAPSED-READ-EVER-FIRES` · 9d — soak UNKNOWN — NO PROBE IS DECLARED for this soak, so nothing is reading it. Ready means: dropped_symbol_dedupe_count>=1. This is a KNOWN,…
  - MI-222 Tier-2 (operator-approved observable-first, 2026-09-09) made the collapsed position read at src/units/accounts/clients.py COUNTABLE without changing what any caller receive…
- `OI-20260909-CLOSED-FLAT-INVARIANT-CAN-FINALLY-SPEAK-AND-HAS-NOT-YET-SPOKEN` · 9d — soak UNKNOWN — NO PROBE IS DECLARED for this soak, so nothing is reading it. Ready means: examined>=1. This is a KNOWN, DECLARED gap (`prob…
  - The closed->exchange-flat invariant -- THE ONE MECHANISM that can independently contradict 'this trade is closed' -- was silent for four independent reasons, each alone sufficient…

**`research_queue` — 3 due**

- `RQ-20260827-001` — research job still queued
  - Re-grade every account against the Lane P compat-matrix standard arm
- `RQ-20260830-002` — research job still queued
  - Monthly re-validation of every SHIPPED bracket-geometry cell on a live leg
- `RQ-20260831-002` — research job still queued
  - Thin-leg bracket-geometry accrual — the five 1d equity legs that cannot reach the power floor

**`probes` — 8 due**

- `OI-20260903-CAPTURE-WATCH-PROVED-ITSELF-ON-DEMAND-AND-ITS-SCHEDULE-HAS-NEVER-SUCCEEDED` — we did not look — this row is currently unwatched
  - probe could not run (exit_2)
- 🔔 `OI-20260831-PROP-RISK-GATE-ENFORCE-ARMED-BUT-HAS-NEVER-CAPPED` — probe FAILED — its declared observation did not hold
  - A prop_ticket_risk_soak row exists, within the last 1000, in which the gate ran under `enforce`, graded a ticket `exceeds_cushion`, and records `would_have_capped: true`.
- 🔔 `OI-20260831-RESEARCH-QUEUE-INFEASIBLE-STATE-SHIPPED-BUT-NEVER-REACHED-LIVE` — probe FAILED — its declared observation did not hold
  - Reads all three committed research corpora and looks for any row stamped research_power_state=infeasible — the R4 grade this row's id says nothing has ever produced. The POSITIVE…
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

**`red_crons` — 2 due**

- 🔔 `pr-queue-watch` — latest scheduled run concluded 'failure'
  - pr-queue-watch
- 🔔 `replay-pregate-nightly` — latest scheduled run concluded 'failure'
  - replay-pregate-nightly

**`red_main_runs` — 2 due**

- 🔔 `.github/workflows/ranking-key-ab.yml` — the latest push-to-main run of .github/workflows/ranking-key-ab.yml concluded 'failure' — the DEFAULT BRANCH is red, and until 2026-09-13 n…
  - .github/workflows/ranking-key-ab.yml
- 🔔 `branch-protection-sync` — the latest push-to-main run of branch-protection-sync concluded 'failure' — the DEFAULT BRANCH is red, and until 2026-09-13 nothing read th…
  - branch-protection-sync

**`unlanded_automation` — 66 due**

- 🔔 `#12529` · 0d — producer output opened a PR that has not landed
  - chore(ml): replay pre-gate fleet report (auto)
- 🔔 `#12511` · 0d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12502` · 0d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12498` · 0d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12493` · 0d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12489` · 1d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12486` · 1d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12484` · 1d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12477` · 1d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12474` · 1d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12469` · 1d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12466` · 1d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12459` · 1d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12456` · 1d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12451` · 1d — producer output opened a PR that has not landed
  - chore(ops): reconcile OPEN-PRS.json after merge (auto)
- 🔔 `#12385` · 1d — producer output opened a PR that has not landed
  - chore(ml): replay pre-gate fleet report (auto)
- 🔔 `#12363` · 1d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12362` · 1d — producer output opened a PR that has not landed
  - chore(ops): queue the daily work digest (auto)
- 🔔 `#12361` · 1d — producer output opened a PR that has not landed
  - chore(ops): PR-queue watcher receipt (auto)
- 🔔 `#12360` · 1d — producer output opened a PR that has not landed
  - chore(ops): trainer capture-watch receipt (auto)
- 🔔 `#12359` · 1d — producer output opened a PR that has not landed
  - chore(ops): session-reaper observations (auto)
- 🔔 `#12358` · 1d — producer output opened a PR that has not landed
  - chore(ops): queue the daily work digest (auto)
- 🔔 `#12357` · 1d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12356` · 1d — producer output opened a PR that has not landed
  - chore(m1): economic-calendar PIT snapshots (auto)
- 🔔 `#12353` · 2d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12343` · 2d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12337` · 2d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12331` · 2d — producer output opened a PR that has not landed
  - chore(ops): refresh the A1 constraint readout (auto)
- 🔔 `#12330` · 2d — producer output opened a PR that has not landed
  - chore(ops): refresh probe results (auto)
- 🔔 `#12328` · 2d — producer output opened a PR that has not landed
  - chore(ml): replay pre-gate fleet report (auto)
- 🔔 `#12326` · 2d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12320` · 2d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12316` · 3d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12312` · 3d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12306` · 3d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12299` · 3d — producer output opened a PR that has not landed
  - chore(ops): refresh the A1 constraint readout (auto)
- 🔔 `#12298` · 3d — producer output opened a PR that has not landed
  - chore(ops): refresh probe results (auto)
- 🔔 `#12297` · 3d — producer output opened a PR that has not landed
  - chore(ml): replay pre-gate fleet report (auto)
- 🔔 `#12295` · 3d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12289` · 3d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12284` · 4d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12279` · 4d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12276` · 4d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12269` · 4d — producer output opened a PR that has not landed
  - chore(ops): refresh the A1 constraint readout (auto)
- 🔔 `#12268` · 4d — producer output opened a PR that has not landed
  - chore(ops): refresh probe results (auto)
- 🔔 `#12265` · 4d — producer output opened a PR that has not landed
  - chore(ml): replay pre-gate fleet report (auto)
- 🔔 `#12263` · 4d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12257` · 4d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12253` · 5d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12249` · 5d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12245` · 5d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12239` · 5d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12231` · 5d — producer output opened a PR that has not landed
  - chore(ops): refresh the A1 constraint readout (auto)
- 🔔 `#12230` · 5d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#12229` · 5d — producer output opened a PR that has not landed
  - chore(ops): refresh probe results (auto)
- 🔔 `#12228` · 5d — producer output opened a PR that has not landed
  - chore(ml): replay pre-gate fleet report (auto)
- 🔔 `#12225` · 5d — producer output opened a PR that has not landed
  - chore(ops): session-reaper observations (auto)
- 🔔 `#11998` · 6d — producer output opened a PR that has not landed
  - chore(ops): trainer capture-watch receipt (auto)
- 🔔 `#11956` · 6d — producer output opened a PR that has not landed
  - chore(m28): valuation snapshots (auto)
- 🔔 `#11953` · 6d — producer output opened a PR that has not landed
  - chore(ops): refresh the error-feed digest (auto)
- 🔔 `#11952` · 6d — producer output opened a PR that has not landed
  - chore(ops): PR-queue watcher receipt (auto)
- 🔔 `#11943` · 6d — producer output opened a PR that has not landed
  - chore(ops): queue the daily work digest (auto)
- 🔔 `#11939` · 6d — producer output opened a PR that has not landed
  - chore(ops): refresh the A1 constraint readout (auto)
- 🔔 `#11919` · 6d — producer output opened a PR that has not landed
  - chore(ops): refresh probe results (auto)
- 🔔 `#11916` · 6d — producer output opened a PR that has not landed
  - chore(ml): replay pre-gate fleet report (auto)
- 🔔 `#11912` · 6d — producer output opened a PR that has not landed
  - chore(m7): strategy review packets (auto)

**`error_feed` — 11 due**

- `ERRFEED-6da04340` · 0d — error-level condition on `operator_alerts`, FIRST SEEN since the last digest — 2 rows 2026-09-18T05:56:33 → 2026-09-18T06:07:12 · symbols=M…
  - [error] NEW x2 ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: mgc_pullback_Nd | Symbol: MGC Reason: candles_u
- `ERRFEED-cf5a4edf` · 0d — error-level condition on `bot_logs`, FIRST SEEN since the last digest — 1 rows 2026-09-18T05:05:31 → 2026-09-18T05:05:31 · accounts=ib_pape…
  - [error] NEW x1 ib_target_naked detected: ib_paper/MGC: position N has N of take-profit coverage against a declared TP of N — the position can only stop out
- `ERRFEED-72e2ad1e` · 4d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 56 rows 2026-09-14T10:02:36 → 2026-09-17T23:16:26 · accou…
  - [error] x56 🧱 Position CLOSE wedged BROKER-SIDE — carried in the digest Account: alpaca_paper Symbol: GLD | Side: long | Qty: N Consecutive close failur
- `ERRFEED-5402e495` · 2d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 47 rows 2026-09-16T23:54:03 → 2026-09-17T01:25:29 · accou…
  - [error] x47 👁 Exchange-side orphan position — policy=detect_only Account: ib_paper Symbol: MES | Side: short | Size: N Entry (Bybit avgPrice): N Note: r
- `ERRFEED-fa77ad80` · 6d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 22 rows 2026-09-12T18:12:44 → 2026-09-18T06:07:12 · symbo…
  - [error] x22 ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: mes_trend_long_Nd | Symbol: MES Reason: candles
- `ERRFEED-9da85381` · 6d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 21 rows 2026-09-12T18:18:11 → 2026-09-18T04:51:30 · symbo…
  - [error] x21 ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: ict_scalp_mgc_Nm | Symbol: MGC Reason: candles_
- `ERRFEED-22c7c0be` · 2d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 14 rows 2026-09-16T19:29:11 → 2026-09-16T19:56:10 · accou…
  - [error] x14 👁 Exchange-side orphan position — policy=detect_only Account: ib_paper Symbol: MES | Side: long | Size: N Entry (Bybit avgPrice): N Note: re
- `ERRFEED-a46d2970` · 6d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 13 rows 2026-09-12T18:12:44 → 2026-09-18T06:07:13 · symbo…
  - [error] x13 ⚠️ MONITOR BLIND — open position has no live dynamic exit Order package: pkg-<hex> Strategy: mhg_pullback_Nd | Symbol: MHG Reason: candles_u
- `ERRFEED-ae533209` · 5d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 6 rows 2026-09-13T14:15:14 → 2026-09-16T13:06:52 · accoun…
  - [error] x6 🔔 Broker close detected by reconciler Account: bybit_N Symbol: AVAXUSDT | Side: long DB trade id: N Package: pkg-<hex> Reason: reconciler Cl
- `ERRFEED-592065b2` · 5d — error-level condition on `operator_alerts`, STANDING (predates the last digest) — 4 rows 2026-09-13T06:58:07 → 2026-09-16T23:24:01 · accoun…
  - [error] x4 🔔 Broker close detected by reconciler Account: bybit_N Symbol: AVAXUSDT | Side: short DB trade id: N Package: pkg-<hex> Reason: reconciler C
- `ERROR-FEED-SUMMARY` — 46 further error group(s) and every warn group are NOT listed above — read `docs/claude/ERROR-FEED-DIGEST.json` for the full set. Digest ve…
  - 81 cause groups over 1304 rows (56 error-level, 25 warn-level, 2 new since the last digest)

**`sunset` — 12 due**

- `strategy:avax_pullback_2h` · 17d — sunset pass proposed RETIRE (persistently_silent) and no disposition is recorded — Tier-3, so propose, never enact
  - strategy_leg · avax_pullback_2h
- `strategy:fade_breakout_4h` · 17d — sunset pass proposed RETIRE (persistently_silent) and no disposition is recorded — Tier-3, so propose, never enact
  - strategy_leg · fade_breakout_4h
- `strategy:fvg_range_15m` · 17d — sunset pass proposed RETIRE (persistently_silent) and no disposition is recorded — Tier-3, so propose, never enact
  - strategy_leg · fvg_range_15m
- `strategy:htf_pullback_trend_2h` · 17d — sunset pass proposed RETIRE (persistently_silent) and no disposition is recorded — Tier-3, so propose, never enact
  - strategy_leg · htf_pullback_trend_2h
- `strategy:ief_pullback_1d` · 17d — sunset pass proposed RETIRE (persistently_silent) and no disposition is recorded — Tier-3, so propose, never enact
  - strategy_leg · ief_pullback_1d
- `strategy:iwm_trend_long_1d` · 17d — sunset pass proposed RETIRE (persistently_silent) and no disposition is recorded — Tier-3, so propose, never enact
  - strategy_leg · iwm_trend_long_1d
- `strategy:mhg_pullback_1d` · 17d — sunset pass proposed RETIRE (persistently_silent) and no disposition is recorded — Tier-3, so propose, never enact
  - strategy_leg · mhg_pullback_1d
- `strategy:qld_trend_long_1d` · 17d — sunset pass proposed RETIRE (persistently_silent) and no disposition is recorded — Tier-3, so propose, never enact
  - strategy_leg · qld_trend_long_1d
- `strategy:qqq_trend_long_1d` · 17d — sunset pass proposed RETIRE (persistently_silent) and no disposition is recorded — Tier-3, so propose, never enact
  - strategy_leg · qqq_trend_long_1d
- `strategy:slv_pullback_1d` · 17d — sunset pass proposed RETIRE (persistently_silent) and no disposition is recorded — Tier-3, so propose, never enact
  - strategy_leg · slv_pullback_1d
- `strategy:slv_trend_1h` · 17d — sunset pass proposed RETIRE (persistently_silent) and no disposition is recorded — Tier-3, so propose, never enact
  - strategy_leg · slv_trend_1h
- `strategy:tlt_pullback_1d` · 17d — sunset pass proposed RETIRE (persistently_silent) and no disposition is recorded — Tier-3, so propose, never enact
  - strategy_leg · tlt_pullback_1d

**`checklist_unrouted` — 9 due**

- `MI-257-THE-CHECKLIST-CARRIES-TWO-COMPETING-STATUS-FIELDS-AND-38-OFF-VOCABULARY-VALUES` · 6d — FILED AND NEVER ROUTED — 167.1h with owner `unassigned — route when a lane frees; the 3-lane cap is holding` and status `queued`, past the…
  - 12 checklist items have `state` and `status` disagreeing, and 38 carry values outside the declared vocabulary — on the page the operator rea
- `MI-263-REGISTRATION-IS-NOT-ATOMIC-WITH-THE-SPAWN-SO-A-COLD-LANE-MEASURES-ITS-OWN-RECORDS-ABSENT` · 6d — FILED AND NEVER ROUTED — 157.1h with owner `unassigned — needs a session` and status `ready`, past the measured 24h threshold. Route it, di…
  - A spawned lane can correctly measure its own pre-spawn registry row as ABSENT, because the manager registers locally and pushes later
- `MI-243-SWEEP-THE-623-DORMANT-WORK-OBJECTS-AND-GIVE-SOAK-BOUND-WORK-ITS-OWN-STATUS` · 6d — FILED AND NEVER ROUTED — 149.3h with owner `unassigned — the retirement needs a session; MI-243's sweep half is delivered` and status `read…
  - Sweep the 623 dormant work objects — close or queue each, and add a lifecycle value for work that is genuinely waiting on a soak or observat
- `MI-264-THE-BLOCKED-LANE-WATCHER-CANNOT-EXPRESS-THE-COMMONEST-BLOCKER-ITS-OWN-REGISTRY-ROW` · 6d — FILED AND NEVER ROUTED — 147.1h with owner `unassigned — small, well-specified, belongs with whoever next touches blocked_la` and status `r…
  - Add a sixth blocked-on kind, registry_confirmed — the class that accounts for 2 of the 5 recorded instances and which the watcher cannot exp
- `MI-270-THE-CONSTRAINT-READOUT-CRON-REGENERATES-THE-SESSION-BRIEF-AND-SESSION-BRIEF-GUARD-REJECTS-ITS-OWN-OUTPUT` · 5d — FILED AND NEVER ROUTED — 139.7h with owner `unassigned` and status `queued`, past the measured 24h threshold. Route it, disposition it, or…
  - The constraint-readout cron regenerates the session brief, and session-brief-guard fails its own output
- `MI-271-ATTRIBUTE-THE-2026-08-30-REGIME-BREAK-E35-GEOMETRY-OR-MARKET` · 5d — FILED AND NEVER ROUTED — 138.3h with owner `unassigned` and status `queued`, past the measured 24h threshold. Route it, disposition it, or…
  - Attribute the 2026-08-30 regime break — e35 bracket geometry, or a market regime change?
- `MI-272-FULL-SYSTEM-REVIEW-THE-BIGGER-PICTURE-BEHIND-THE-BLEED` · 5d — FILED AND NEVER ROUTED — 138.3h with owner `unassigned` and status `queued`, past the measured 24h threshold. Route it, disposition it, or…
  - Full /system-review — the bigger picture around the bleed
- `MI-273-LET-AN-APPROVED-PR-SELF-LAND-SO-THE-OPERATOR-NEVER-HAS-TO-CLICK` · 5d — FILED AND NEVER ROUTED — 138.3h with owner `unassigned` and status `queued`, past the measured 24h threshold. Route it, disposition it, or…
  - Let a Tier-2 PR self-land when a typed operator approval is already recorded against it
- `checklist-unrouted-standing` — The standing stock, carried as ONE row deliberately — the register names every id under `seeded_ids`/`reported_ids`. 69 have been said once…
  - 69 checklist row(s) unrouted past 24h, carried as a stock

**`stuck_branches` — 1 due**

- 🔔 `stuck-branches-changed` — automation/data-commit-35184094134-1, automation/data-commit-35185407037-1, automation/error-feed-digest-35190585909-1, automation/reconcil…
  - 19 automation branch(es) newly stranded, 0 cleared

**`spent_decision_edges` — 5 due**

- 🔔 `spent-edge-WO-20260901-PHASE-F` — Its `blocked_on` names DEC-20260909-STRATEGY-REVIEW-WINDOW-FLOOR, which carries a readable answer (2026-09-09T09:30:00Z) — the edge has not…
  - WO-20260901-PHASE-F is `waiting` on an answered decision
- 🔔 `spent-edge-WO-20260902-DECISION-REVIEW-PACKET-POPULATION` — Its `blocked_on` names DEC-20260902-REVIEW-PACKET-POPULATION, which carries a readable answer (date not recorded) — the edge has not been a…
  - WO-20260902-DECISION-REVIEW-PACKET-POPULATION is `waiting` on an answered decision
- 🔔 `spent-edge-WO-20260903-SUNSET-DISPOSITIONS-OWED` — Its `blocked_on` names DEC-20260903-SUNSET-DISPOSITION-POLICY, which carries a readable answer (2026-09-03) — the edge has not been a block…
  - WO-20260903-SUNSET-DISPOSITIONS-OWED is `waiting` on an answered decision
- 🔔 `stranded-edge-WO-20260911-MAKE-THE-DATA-EXPLORER-REACHABLE-LAND-THE` — Its `blocked_on` ref is 'originate JWT_SIGNING_KEY + WEBAPP_PASSWORD_SHA256, then a Tier-2 set-env', graded unresolvable — no decision requ…
  - WO-20260911-MAKE-THE-DATA-EXPLORER-REACHABLE-LAND-THE is `waiting` on an UNRESOLVABLE edge
- 🔔 `stranded-edge-WO-20260912-ENGINEERING-LANE-MAKE-THE-LANDING-AND-REGISTER` — Its `blocked_on` ref is 'DEC-20260913-WHAT-BOUNDS-AN-OPEN-ITEMS-OBSERVATION', graded unresolvable — no decision request in the store declar…
  - WO-20260912-ENGINEERING-LANE-MAKE-THE-LANDING-AND-REGISTER is `waiting` on an UNRESOLVABLE edge

**`manager_queue_watch` — 1 due**

- 🔔 `manager-queue-watch-never-ran-overdue` · 15d — armed 381.1h ago, on the order of 381 expected firings, and ZERO receipts have ever been committed. The Routine reports SUCCEEDED, so nothi…
  - the Manager Queue Watch Routine has never written its receipt

_This section decides nothing. Every row is for a session to judge._

