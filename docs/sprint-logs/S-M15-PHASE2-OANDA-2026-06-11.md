# Sprint Log: S-M15-PHASE2-OANDA-2026-06-11

## Date Range
- Start: 2026-06-10 (operator green-light in chat, post-#3303 approval)
- End: 2026-06-11 (build complete; smoke test pending operator creds)

## Objective
- Primary goal: wire OANDA v20 into the execution path per the Phase-0
  verdict (gold first) and the `new-broker` checklist — shipped fully
  inert behind independent gates.
- Secondary goals: pre-position the Alpaca secret names in the
  propagation path (Phase 2b prep).

## Tier
- Tier 3 — PR #3324 touches `config/accounts.yaml` + order-path files
  (`execute.py`, `coordinator.py`); **draft until explicit operator
  approval**. All changes inert: the new branch/dispatch is unreachable
  for every configured account, and `oanda_practice` ships dry_run +
  `strategies: []` + creds unset.

## Starting Context
- Active roadmap items: M15 (Phase 0 complete, Phase 1 merged #3303).
- Prior sprint reference: `S-M15-PHASE0-2026-06-10.md` ("next sprint"
  section scoped this one).
- Known risks at start: OANDA auth is token+account-id (no key/secret
  pair → `resolve_credentials` doesn't apply); FOK market orders can be
  created-then-cancelled on a 201; closeout requests on empty sides are
  rejected by OANDA.

## Repo State Checked
- Branch: `claude/happy-heisenberg-p3vt9j` over `main` @ a288629 (#3303
  squash).
- Canonical docs/skills read in full: `new-broker` SKILL,
  `credentials-and-vm-mutations` (bright-line check on the runbook),
  `integrator.py`, `clients.py` (velotrade/ib factory patterns),
  `execute.py::_submit_order`, coordinator client-construction switch,
  `accounts.yaml` (ib_live dry-run-guard marker precedent),
  `master-secrets.template.yaml`, `sync-vm-secrets.yml` +
  `sync_vm_secrets.sh` (list-driven — no script edit needed).

## Work Completed
- `src/units/accounts/oanda_client.py` — v20 REST execution client
  (MARKET + `stopLossOnFill`/`takeProfitOnFill`; FOK cancel → retCode
  −3; NAV balance; positions; idempotent close naming only open legs;
  metals/JPY 3dp vs FX 5dp price formatting; practice host default).
- `clients.py::oanda_client_for` (env-direct, `None` on missing creds);
  `integrator.py::OandaAPI` + `EXCHANGE_MAP["oanda"]`; `execute.py`
  oanda branch (velotrade retCode contract); coordinator dispatch.
- `config/accounts.yaml::oanda_practice` (inert, dry-run-guard marker,
  demo, XAUUSD, 0.5%/trade risk block);
  `master-secrets.template.yaml` `no_secret` block.
- `sync-vm-secrets.yml`: OANDA + Alpaca names in `OPTIONAL_SECRETS` and
  both env blocks.
- `tests/test_oanda_wiring.py` (13); runbook
  `docs/runbooks/oanda-integration.md`; `ARCHITECTURE-CANONICAL.md`
  Step 6; ROADMAP M15 row.

## Validation Performed
- Tests: 13/13 new; executor + multi-account suites 59/59; accounts
  layer + master-template drift guard 80/80 (local). **CI on #3324:
  11/11 green** (incl. dry-run-guard accepting the inline marker and
  the full pytest run).
- Gaps not yet verified: live practice round-trip (needs operator
  creds); OANDA-vs-Dukascopy candle fidelity; weekend-gate wiring
  (deliberately deferred to the strategy-assignment PR).

## Documentation Updated
- Architecture doc: Step 6 broker-execution paragraph.
- Roadmap: M15 row (Phase 1 merged, Phase 2 built).
- Subsystem: new runbook `oanda-integration.md`.

## Contradictions or Drift Found
- None new this sprint. (`new-broker` skill says "package under
  `src/units/accounts/<broker>/`"; the established real precedent —
  `dxtrade_client.py` — is a single self-contained module, which this
  follows; right-sized for a one-token REST broker.)

## Risks and Follow-Ups
- Remaining technical risks: FOK vs weekend `MARKET_HALTED` behaviour
  to observe in the practice soak; unit rounding (floor 1) means very
  small balances size to 1 unit of XAU (~$2.3k notional ≈ micro-lot
  scale on 50:1 — verify margin headroom in the smoke test).
- Remaining product decisions (Tier 3): merge #3324; then the
  strategy-assignment PR (`xauusd_trend_1h` clone, shadow, routed to
  `oanda_practice` only, + FX weekend gate).
- Blockers: operator — OANDA practice account + paste
  `OANDA_API_TOKEN`/`OANDA_ACCOUNT_ID` (#3302 slots) + ping.

## Deferred Items
- Alpaca paper wiring (Phase 2b — same checklist; secret names already
  propagated).
- Market-hours gate call-site wiring (strategy-assignment PR).

## Next Recommended Sprint
- S-M15-PHASE3-XAU-SHADOW: after #3324 merges + creds land — sync,
  smoke test, candle cross-check, strategy-assignment PR, shadow soak.
- Required verification before starting: #3324 merged; secrets present;
  smoke test green.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] Pipeline stage touched (Step 6 executor) — `ARCHITECTURE-CANONICAL.md` updated; `docs/TRADE-PIPELINE.md` unchanged (no flow change — a new branch in the existing dispatch).
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
