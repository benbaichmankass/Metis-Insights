# § 8 — PM decisions needed

Items the PM must weigh in on before the corresponding PR ships. Claude
will pause and post `/sprintlet_status decision needed: <topic>` for each.

> **Status (post-PM, post-PR C4):** all seven items have been answered
> and incorporated into PRs B1 → C4. This document is now a historical
> record of the decision rationale; the PR numbers in each section show
> where the decision shipped or is still pending.

## #1 — Single-process vs per-strategy systemd services — APPROVED (b) (PR C4)

> **PM decision:** Approved single-process. Guardrail: Claude must
> explicitly confirm no shared mutable state between strategy modules
> (no shared position tracker, no shared connector that could deadlock).
>
> **Guardrail audit (PR C4, 2026-04-29):** confirmed. Findings:
>
> - `src/units/strategies/turtle_soup.py`: only module-level state is
>   `_DEFAULTS` (read-only constants); `_resolve_params` returns a fresh
>   dict on every call.
> - `src/units/strategies/vwap.py`: only `_ENTRY_STD_THRESHOLD = 1.0`
>   read-only constant.
> - `src/units/strategies/_base.py`: pure helper functions, no globals.
> - `src/runtime/pipeline.py`: `STRATEGIES`, `STRATEGY_RISK_PCT`,
>   `_STRATEGY_BUILDERS`, `HALT_FLAG_PATH` — read-only after module
>   load. `_build_killzone_exchange()` constructs a fresh connector
>   instance per call (no singleton, no cache).
> - `src/units/accounts/account.py` + `risk.py`: stateful
>   (`self.positions`, `self.daily_pnl`) but **per-instance** — one
>   `TradingAccount` + `RiskManager` pair per account, no cross-account
>   or cross-strategy mutable state.
>
> Both `order_package` functions are pure (cfg + DataFrame in, dict
> out). Strategies cannot deadlock each other because they share no
> connector and emit no callbacks. Single-process is safe.


- **Ambiguity:** Configs (`strategies.yaml`, `units.yaml`) declare
  per-strategy `service:` fields (`ict-trader-vwap`, `ict-trader-ict`,
  `ict-trader-breakout`) but the runtime is single-process — these
  per-strategy units have never existed in `deploy/`.
- **Options:**
  - (a) Author the missing `.service` files and keep per-strategy
    services. ≈ +6 service files, +Telegram bot per-service status,
    duplicate connectors per process.
  - (b) Drop the `service:` field from both YAMLs and remove every
    consumer that maps a strategy to a unit. Single
    `ict-trader-live.service` runs all strategies in one process.
- **Recommendation:** **(b).** The runtime already works this way; the
  per-strategy fields are aspirational metadata that caused the failure
  triggering this sprint.
- **Blast radius if wrong:** Low. If the PM later wants per-strategy
  services, they can be re-introduced in a future sprint without changing
  the strategy code, only the unit-file generation and the dispatcher.
- **Default action without PM input:** apply (b). PM may veto via Telegram
  before D-phase ships.

## #2 — Turtle Soup go-live readiness (BLOCKS PR E2 / DoD final)

- **Ambiguity:** Sprint guardrail #4 says do not promote turtle_soup to
  live unless it has unit tests, a backtest entry, and demonstrably-firing
  risk caps. After PR C2/E3 merge, those gates exist. PM still confirms.
- **Options:**
  - (a) Promote turtle_soup to `enabled: true` + live-default for the
    live account.
  - (b) Ship S-012 with turtle_soup `enabled: false` (or in
    held-dry-run via per-account override) and document the gate for the
    next sprint.
- **Recommendation:** **(a) iff** all of {`tests/test_s012_turtle_soup.py`
  green, risk-cap tests in PR E3 green for turtle_soup, backtest entry in
  `bin/backtest_ict.py` runs on a small fixture}. Otherwise (b).
- **Blast radius if wrong:** Medium. If turtle_soup ships live with a
  bug, real money is at risk. Mitigation: `pos_size`/`daily_usd` caps
  bound the loss to the configured account limits.
- **Default action without PM input:** ship (b) — held in dry-run with an
  explicit go-live criterion documented in the sprint summary.

## #3 — Account ID space reconciliation (BLOCKS PR B3)

- **Ambiguity:** `config/accounts.yaml` defines `bybit_1`, `bybit_2`,
  `prop_breakout_1`. `config/units.yaml::accounts` defines `live`. These
  two spaces never overlap; the live trader uses one and the risk-cap
  loader uses the other.
- **Options:**
  - (a) Pick a canonical name set and rename in one direction. Likely
    keep `bybit_1`/`bybit_2`/`prop_breakout_1` and rewrite the
    `units.yaml` `live` account to point at one of them.
  - (b) Collapse `units.yaml::accounts` into `accounts.yaml` — single
    source of truth — and have `Coordinator.list_accounts()` read from
    `accounts.yaml`.
- **Recommendation:** **(b).** One file owns account identity. Units file
  retains only strategies + dashboards + bot configuration.
- **Blast radius if wrong:** Low — internal refactor, no external API.
- **Default action without PM input:** apply (b). PM may veto.

## #4 — `/accounts` dry/live toggle from S-011 PR #141

- **Ambiguity:** Sprint S-012 prompt asks PM to confirm whether the per-
  account dry/live toggle from S-011 stays.
- **Options:**
  - (a) Keep as a per-account override; default state is `live`.
  - (b) Remove entirely; only the global `DRY_RUN` env var controls.
- **Recommendation:** **(a).** Cheap operator escape hatch for staging
  prop accounts. Document the semantics clearly in
  `docs/claude/deployment-ops.md`.
- **Default action without PM input:** keep (a).

## #5 — Phantom service VM-side investigation

- **Ambiguity:** `ict-trader-bak` and `ict-trader-example` do not appear
  anywhere in this repo. They must originate from VM-side state outside
  the repo (a hardcoded list in a non-repo wrapper, a stale
  `*.wants/` symlink, or shell history). § 4 lists the diagnostic
  commands. Phase D2 cannot complete from inside Claude.
- **Options:**
  - (a) PM runs the four diagnostic commands in § 4.5 and pastes output;
    Claude removes the actual source.
  - (b) Ignore the VM-side trace; ship the repo-side regression test
    (PR D3) so future hardcoded lists are caught at CI; mark this item
    as "known unresolved, low risk" in the sprint summary.
- **Recommendation:** **(a)** if PM has 2 minutes to run the commands;
  otherwise (b).
- **Blast radius if wrong (option b):** Low. The phantom calls fail
  loudly and don't affect `ict-trader-live`. The harm is operator
  confusion, not lost money.

## #6 — `max_dd_pct` semantics (BLOCKS PR E3a)

- **Ambiguity:** `max_dd_pct` is configured but never enforced
  (§ 7.3). To enforce it, the meaning must be settled.
- **Options:**
  - (a) Intra-day drawdown: equity drop from today's high. Resets at
    UTC midnight.
  - (b) Inception-to-date drawdown: equity drop from running max equity
    since account creation.
  - (c) Rolling N-day drawdown.
- **Recommendation:** **(a)** — bounded blast radius, easy to reason
  about, common prop-firm contract.
- **Blast radius if wrong:** Medium. Wrong semantics could let a
  drawdown event slip through or could spuriously kill a legitimate
  account day.
- **Default action without PM input:** Implement (a). PM may veto the
  semantic before PR E3a ships.

## #7 — Killzone, ICT, breakout_confirmation deletion (sprint prompt
decision-request #3)

- **Ambiguity:** The sprint prompt asks PM to confirm deletion of any of
  these if they were merged in S-008+ as deliberate production strategies.
- **Evidence from § 1, § 3:**
  - `breakout_confirmation` is `enabled: false` in both YAMLs and gated
    behind a missing model artefact. Almost certainly scaffolding.
  - `ict` is `enabled: true` and has signal-builder code in
    `src/runtime/strategies/ict.py`. Was treated as an active strategy
    in S-008.
  - `killzone` is `enabled: true` and has working order-package code.
    Was treated as active in S-008.
- **Recommendation:** **delete all three** per PM intent in the prompt
  ("strategy roster after sprint: turtle_soup + vwap only"). PM confirms.
- **Blast radius:** None on the live account today (the live unit dispatch
  reaches all four; removing them stops their signal generation, which is
  the explicit PM goal).

## Summary table

| # | Decision | Blocks | Default if no input |
|---|---|---|---|
| 1 | Single-process vs per-strategy services | PR D2 | apply (b) single-process |
| 2 | Turtle Soup go-live | PR E2 + DoD final | (b) held in dry-run |
| 3 | Account ID space reconciliation | PR B3 | apply (b) collapse to accounts.yaml |
| 4 | Keep `/accounts` toggle | none — clarification | keep (a) |
| 5 | VM-side phantom investigation | PR D2 hard-clean | (b) ship regression test only |
| 6 | `max_dd_pct` semantics | PR E3a | (a) intra-day |
| 7 | Delete killzone/ict/breakout | PRs B1/B2/C5 | apply — delete all three |
