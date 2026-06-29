# M18 — Portfolio Capital Allocator: Execution / Delegation Plan

> Companion to the design [`docs/research/capital-allocation-ai-DESIGN.md`](../research/capital-allocation-ai-DESIGN.md)
> and the ROADMAP § "M18 — Portfolio Capital Allocator". This doc is the **durable
> decomposition** the `delegate-work` skill requires: the milestone carved into independent,
> tiered units that can run in parallel sessions, with merge serialization via
> `session-coordination`. **Status: 2026-06-29 kickoff.** Tiering still applies — Tier-3 units are
> propose-and-operator-approve, never self-merge; observe-only until graduated (Prime Directive).

## How this runs (delegate-work)

- **Independent units, serial merges.** Each unit = one session = its own focused PR(s). Claim the
  single `merge_slot` in [`docs/claude/session-board.json`](../claude/session-board.json) before
  merging; sync to `main` **last**; merge on green; release. Register in `active_sessions` at start.
- **Single-writer consolidation.** The lead (whoever owns the soak harness) integrates the
  candidate-batch output; sub-units *return* their piece, they don't all edit the same wiring file.
- **Phase gating is real.** P2+ (selection that influences anything) does not start until P0's soak
  shows the regret signal is measurable and P0a's cost capture is populating — otherwise the learned
  ranker (P3) trains on biased labels. Build the substrate first.

## Unit decomposition

`▶` = ready to start now · `⏸` = gated on a dependency · tier per `docs/CLAUDE-RULES-CANONICAL.md`.

| Unit | Scope | Tier | Files (primary) | Depends on | Parallel? |
|---|---|---|---|---|---|
| **S-M18-P0a** ▶ | **Per-trade cost capture.** Add `fee_*` / `funding_*` columns to `trades` + a close-path writer (broker-fill-sourced where the integration exposes it, else a fixed-estimate flagged approximate). Closes the #1 data gap blocking the P3 ranker label. | **T2** (DB writeback, close path) | `src/units/db/database.py` (schema), `src/units/accounts/execute.py` + `src/runtime/order_monitor.py` (writer), `tests/` | — | **Yes** (independent track) |
| **S-M18-P0b** ▶ | **Candidate-batch exposure.** Surface the pre-aggregation candidate set the multiplexer already gathers (`_collect_intents`) as a `list[SignalPackage]` riding alongside the collapsed signal. Observe-only; legacy collapse stays the live path. | **T1** (observe-only) | `src/runtime/intent_multiplexer.py`, `src/core/signal_contract.py`, `tests/` | — | **Yes** (pipeline head) |
| **S-M18-P0c** ⏸→▶ | **Allocator soak harness.** `allocator_soak.jsonl` writer + `/api/bot/allocator/soak` router + diag `log_file` name + the **regret** metric (EV / realized-R the allocator would have captured vs the per-cell path actually did). Observe-only; nothing reads it back. | **T1** (observe-only) | `src/runtime/allocator_soak.py` (new), `src/web/api/routers/` (new router), `src/web/api/routers/diag.py`, `tests/` | P0b (the batch) — can build against a stub, wire at the end | Starts ∥, wires after P0b |
| **S-M18-P1** ⏸ | **Rules EV scorer.** Pure `EV_net = P_win·R_target − (1−P_win)·R_stop − roundtrip_fee − funding/swap` per candidate (P_win ← conviction lens; fixed cost model until P0a matures), stamped on `SignalPackage.source_context`; soak consumes it. | **T1** (observe-only) | `src/runtime/allocator_ev.py` (new), `src/runtime/conviction.py` (reuse), `tests/` | P0c (soak to observe it) | After P0c |
| **S-M18-P2** ⏸ | **Greedy selector (shadow/annotate).** `EVAllocator(AllocatorInterface)` + enriched `PortfolioState` (free margin + daily-loss budget remaining + max-concurrent); **annotate** (logs the chosen subset, executes nothing). | **T3** | `src/core/allocator.py`, `src/core/portfolio_state.py`, `src/core/coordinator.py` (`build_order_packages`) | P1 + soak evidence | Sequential (post-P1) |
| **S-M18-P3** ⏸ | **Correlation budgeting + learned net-R ranker.** Covariance-adjusted marginal-risk term (logged feature) + a trained expected-net-R ranker on the candidate→shadow→advisory ladder (trainer VM, LightGBM). | **T3** | `ml/configs/` (manifest), trainer dataset family, `src/runtime/` (correlation feature) | P0a (clean labels) + P2 | Two sub-tracks (corr feature ∥ ranker manifest) |
| **S-M18-P4** ⏸ | **Graduate to influence.** Backtest A/B arm in `scripts/backtest_system.py`; live behind `CAPITAL_ALLOCATOR_MODE=off\|annotate\|apply`. | **T3** | `scripts/backtest_system.py`, `src/core/coordinator.py` | P2 + P3 | Sequential (final) |

## The immediate fan-out (what to spawn now)

Two independent tracks can start in parallel today — both land **before** any Tier-3 selection logic:

- **Track 1 (independent): S-M18-P0a** — cost capture. Tier-2, its own PR, one operator OK to ship.
- **Track 2 (pipeline): S-M18-P0b → S-M18-P0c → S-M18-P1** — candidate batch → soak → rules EV.
  All Tier-1 observe-only; the lead of this track owns the soak consolidation (single-writer).

P2–P4 stay **proposed** (this doc + the design) until the P0 soak produces regret evidence and P0a's
labels are clean; they are spawned later, operator-gated, as their own Tier-3 sessions.

## Mode-C spawn prompts (paste to start a parallel session)

> **S-M18-P0a — Per-trade cost capture (Tier-2).**
> You own **S-M18-P0a — per-trade fee/funding capture**. START by reading
> `docs/CLAUDE-RULES-CANONICAL.md` + root `CLAUDE.md` + the `db-wiring` + `db-setup` SKILLs +
> `docs/research/capital-allocation-ai-DESIGN.md` (§4 the cost data gap) +
> `docs/sprint-plans/M18-CAPITAL-ALLOCATOR-EXECUTION-PLAN.md` + `docs/claude/session-board.json`
> (register in `active_sessions`). Add `fee_taker_usd` / `fee_maker_usd` / `funding_paid_usd`
> columns to `trade_journal.db::trades` (via the canonical resolver; new-table-wiring/db-wiring
> guards) and a **close-path writer** in `execute.py` / `order_monitor.py` that records the broker's
> fill fees + cumulative funding where the integration exposes them, else a fixed-estimate
> (`FEE_BPS_ROUNDTRIP` + per-cell swap) flagged `cost_source="estimate"`. Verify non-null on real
> closed trades across brokers that expose fees; honest negative if a broker can't. **Tier-2: one
> operator OK before shipping; open the PR draft + ping.** Coordinate merges via
> `session-coordination`. On exit write a sprint log + prune your board entry. Updates
> `ml-review-backlog` item `MB-20260629-ALLOC-COSTCAP`.

> **S-M18-P0b+c+P1 — Candidate batch → allocator soak → rules EV (Tier-1, observe-only).**
> You own the **S-M18-P0b→P0c→P1 pipeline** (candidate-batch exposure → allocator soak harness →
> rules EV scorer), all observe-only, no order influence. START by reading
> `docs/CLAUDE-RULES-CANONICAL.md` + root `CLAUDE.md` + `docs/research/capital-allocation-ai-DESIGN.md`
> (§5.1–5.2 + §7 the soak/regret) + `docs/sprint-plans/M18-CAPITAL-ALLOCATOR-EXECUTION-PLAN.md` +
> `docs/claude/session-board.json` (register). Build: (P0b) expose the pre-aggregation candidate set
> from `intent_multiplexer._collect_intents` as a `list[SignalPackage]` alongside the collapsed
> signal — legacy collapse stays the live path; (P0c) `src/runtime/allocator_soak.py` →
> `allocator_soak.jsonl` + `/api/bot/allocator/soak` (mirror the `exit_ladder` soak router shape) +
> a diag `log_file` name + the **regret** metric (what the allocator would pick vs what executed);
> (P1) `src/runtime/allocator_ev.py` pure cost-aware EV per candidate (P_win ← `conviction.py`; fixed
> cost model). Fail-permissive throughout — a scoring/exposure error never strands the live signal.
> **Tier-1: you ship on green** via `session-coordination` (claim slot, sync to `main` last). On exit
> write a sprint log + prune your board entry. The soak owner is the single writer for the
> consolidation. Surfaces the soak read-path in `CLAUDE.md` § Dashboard REST API.

## Where the seam already is (don't re-derive)

`AllocatorInterface.allocate(signals, PortfolioState)` (`src/core/allocator.py:25`) is the N-way
host; `PassthroughAllocator` (`:55`) is the identity placeholder to replace; `build_order_packages`
(`src/core/coordinator.py:194`) is the hook point (assembles `PortfolioState`, after intent
collection, before per-account execution); the live wiring is behind `CENTRALIZED_ALLOCATOR`
(`pipeline.py:704`, fed `[one_sig]` today — P0b makes it a real batch). The per-account
`RiskManager.position_size` (`src/units/accounts/risk.py:620`) stays the final sizing authority.
