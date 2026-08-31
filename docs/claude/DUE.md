# What is due right now

_Generated 2026-08-31T12:52:51+00:00 · verdict **partial**_

> ⚠️ **This list is a LOWER BOUND.** Could not read: `red_crons`, `unlanded_automation`. An empty section below may mean nothing is due, or may mean nobody looked.

- 🔔 **OI-20260829-ALPACA-GOLIVE-BLOCKED-ON-T1-SETTLEMENT-MODEL** (open_items · 2d) — loud row — must be reported on every session
  - alpaca_live go-live: the T+1 model now EXISTS (PR #10408, merged + deployed 2026-08-29, running at `annotate`). The row stays OPEN because `clears_when` requires the model be SHOWN ACTING, and at `ann
- 🔔 **OI-20260829-TRAINER-IS-NOW-A-DECIDED-DEPENDENCY-AND-IS-UNMONITORED** (open_items · 2d) — loud row — must be reported on every session
  - The operator DECIDED (2026-08-29) to keep the trainer VM for the L2 order-flow capture. That converts the box from 'a candidate for retirement' into a STATED DEPENDENCY for a forward-only stream nothi
- 🔔 **OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED** (open_items · 1d) — loud row — must be reported on every session
  - Tier-3, operator-approved 2026-08-30: e35 bracket geometry shipped to 9 legs (10 fields). THREE route to bybit_2 = REAL MONEY (trend_donchian, trend_donchian_eth_4h, trend_donchian_xrp_4h). It is DEPL
- 🔔 **OI-20260829-E35-REVERSED-LEGS-ARE-A-TIER-3-PROPOSAL-SET-NOT-APPLIED** (open_items) — loud row — must be reported on every session
  - The e35 matrix re-check found 15 SHIPPABLE gate-passing cells across 10 live legs the matrix had recorded as honest_negative/blocked. The matrix records them as `passed_unshipped`. NONE is applied — d
- 🔔 **OI-20260830-BYBIT-HEDGE-MODE-ARMED-BUT-UNEXERCISED** (open_items · 0d) — loud row — must be reported on every session
  - Bybit HEDGE position mode is now live on ALL EIGHT (account, symbol) pairs — bybit_1 SOL/ETH/BNB/BTC and bybit_2 BTC/ETH/XRP/ADA (bybit_2 is MAINNET). The mechanism is proven WIRED (a real pair opened
- 🔔 **OI-20260830-BYBIT-HEDGE-MODE-ARMED-BUT-UNEXERCISED** (probes) — probe FAILED — its declared observation did not hold
  - A pairs_soak `open` row exists in which at least one leg was placed carrying a hedge `position_idx` (1 or 2), and at least one leg reports a concurrent non-pairs `directional_open: present` — the nett
- 🔔 **OI-20260831-LIVE-WALLET-TRUTH-CANNOT-REPRODUCE-THE-LEDGER-WINDOW** (open_items · 0d) — loud row — must be reported on every session
  - The live Bybit wallet-truth path works and is MEASURED, but it does not and cannot currently reproduce the -$262.52 figure it was built to replace -- the two are over almost disjoint windows. Switchin
- 🔔 **OI-20260831-PER-ACCOUNT-ARBITRATION-SHIPPED-NOT-YET-ARMED-OR-EXERCISED** (open_items · 0d) — loud row — must be reported on every session
  - ⚠️ ARMED ON bybit_1 AS OF 2026-08-31T07:47Z — this row's own ID still reads 'NOT-YET-ARMED' and that half is now STALE. The id is deliberately NOT renamed (CLAUDE.md and several backlog rows link it b
- 🔔 **OI-20260831-PROP-RISK-GATE-ENFORCE-ARMED-BUT-HAS-NEVER-CAPPED** (open_items · 0d) — loud row — must be reported on every session
  - PROP_TICKET_RISK_GATE_MODE=enforce is LIVE on breakout_1 (Tier-3, operator-approved 2026-08-31). It is ARMED and has never CAPPED a ticket. Those are different facts and only the second is proof the c
- 🔔 **OI-20260831-PROP-RISK-GATE-ENFORCE-ARMED-BUT-HAS-NEVER-CAPPED** (probes) — probe FAILED — its declared observation did not hold
  - A prop_ticket_risk_soak row exists, within the last 1000, in which the gate ran under `enforce`, graded a ticket `exceeds_cushion`, and records `would_have_capped: true`.
- 🔔 **OI-20260831-RESEARCH-QUEUE-GPU-ROUTE-AND-SPEND-GATE-NEVER-EXERCISED** (open_items · 0d) — loud row — must be reported on every session
  - The research queue's GPU route and its spend-gate preflight have NEVER been exercised. Do not describe the GPU spend gates as verified.
- 🔔 **OI-20260831-RESEARCH-QUEUE-INFEASIBLE-STATE-SHIPPED-BUT-NEVER-REACHED-LIVE** (open_items · 0d) — loud row — must be reported on every session
  - The R4 gate's `infeasible` state is now a LABEL rather than a refusal, and it has still never travelled end to end — no corpus row carries any admission stamp. It is deployed, not proven; do not cite 
- 🔔 **OI-20260831-SESSION-BRIEF-DIFF-SCOPING-SHIPPED-NEVER-REPORTED-INHERITED** (open_items · 0d) — loud row — must be reported on every session
  - session-brief-guard is diff-scoped now, so a brief that goes stale on the CLOCK can no longer fail a PR that did not touch the registers. The `inherited` verdict has NEVER been emitted by a real CI ru
- 🔔 **OI-20260831-TRADE-PRIORITISATION-IS-LIVE-BUT-UNPROVEN-AND-ITS-AB-HARNESS-DOES-NOT-EXIST** (open_items) — loud row — must be reported on every session
  - CONFIDENCE IS NOW THE LIVE PRIMARY RANKING KEY for competing trades (2026-08-31, PR #10544) and it has NEVER been shown to pick the better-performing trade. It went live on a CORRECTNESS argument -- i
- **OI-20260826-MHG-OVER-COVER-MECHANISM-UNVERIFIED** (open_items · 2d) — monitoring row 2d since last observation (cadence 2d)
  - The MHG disjoint-OCA over-cover was CLEARED by hand; the mechanism that should have caught and reported it is NOT yet proven.
- **RQ-20260827-001** (research_queue) — research job still queued
  - Re-grade every account against the Lane P compat-matrix standard arm
- **RQ-20260830-002** (research_queue) — research job still queued
  - Monthly re-validation of every SHIPPED bracket-geometry cell on a live leg
- **RQ-20260831-002** (research_queue) — research job still queued
  - Thin-leg bracket-geometry accrual — the five 1d equity legs that cannot reach the power floor

_This list decides nothing. Every row is for a session to judge._

