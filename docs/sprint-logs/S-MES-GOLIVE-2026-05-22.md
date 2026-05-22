# Sprint Log: S-MES-GOLIVE-2026-05-22

## Date Range
- Start: 2026-05-22
- End: 2026-05-22

## Objective
- Primary goal: Take **MES (Micro E-mini S&P 500) paper trading live** on the
  Interactive Brokers paper account, running alongside live BTCUSDT (Bybit) —
  all three strategies (`turtle_soup`, `vwap`, `ict_scalp_5m`) evaluating both
  symbols every tick.
- Secondary goals: Fix the two blockers preventing MES candle fetch; sync all
  canonical documentation to the now-live multi-symbol / IB state.

## Tier
- Tier 2 (code + config that the live VM consumes; doc-only changes are Tier 1).
- Justification: `ib_client.py` / `ib_connector.py` are live trade-path code;
  `config/accounts.yaml` (port + strategies) is operator-gated. The MES paper
  account runs `mode: live` (paper money); the real-money `ib_live` account
  stays `mode: dry_run` throughout.

## Starting Context
- Active roadmap items: M11 S7-IB (IB/MES execution path, wired 2026-05-21).
- Prior sprint reference: S-REFACTOR-S1..S11 (multi-strategy refactor);
  IB execution path wired 2026-05-21.
- Known risks at start: IB Gateway connectivity from the bot was failing
  (API handshake `TimeoutError`); MES had never fetched a live candle.

## Repo State Checked
- Branch/commit reviewed: `main` (post-#1681); fixes branched off latest `main`.
- Deployment state reviewed: live VM `ict-trader-live.service` active; crypto
  (bybit_1/bybit_2) healthy and ticking throughout.
- Canonical docs reviewed: `ARCHITECTURE-CANONICAL.md`, `ROADMAP.md`,
  `CLAUDE.md`, `docs/runbooks/ib-integration.md`,
  `docs/architecture/multi-strategy-architecture-target.md`,
  `docs/sprint-plans/CURRENT-SPRINT.md`, `docs/TRADE-PIPELINE.md`, `README.md`.

## Files and Systems Inspected
- Code files: `src/units/accounts/ib_client.py`, `src/exchange/ib_connector.py`,
  `src/runtime/notify.py`, `src/bot/alert_manager.py`.
- Config files: `config/accounts.yaml` (`ib_paper`/`ib_live`).
- Deployment files: `scripts/install_ib_gateway_docker.sh`,
  `scripts/ops/provision_ib_gateway.sh`, `scripts/ops/gateway_logs.sh`.
- Docs inspected: the canonical set listed above.
- Services/timers: `ict-trader-live.service`; the `ib-gateway` Docker container.
- GitHub Actions workflows: `operator-actions.yml`, `vm-diag-snapshot.yml`,
  `provision-ib-gateway.yml`.

## Work Completed
- **PR #1706 — gateway socat port map.** The gnzsnz IB Gateway binds its paper
  API on container `127.0.0.1:4002` (localhost-only); a connection over Docker's
  NAT bridge was refused (`TimeoutError`). Mapped the host port to the image's
  **socat relay** instead: `docker run … -p 127.0.0.1:4002:4004`. Set
  `config/accounts.yaml ib_paper.ib_port: 7497 → 4002`.
- **PR #1712 — persistent asyncio event loop.** With the connection working,
  every MES `get_ohlcv` failed with `There is no current event loop in thread
  'MainThread'`. Root cause: Telegram alerts use `asyncio.run()`, which calls
  `set_event_loop(None)` on exit, poisoning the thread loop so the next
  `ib_insync` sync call raised. `IBClient` now keeps **one persistent loop**
  (the loop the `IB` is bound to) and re-asserts it on **every** `connect()`
  including the cached path; `get_ohlcv` re-asserts once more before the data
  call. Reproduced the poison locally before fixing; added
  `TestEventLoopResilience` regression tests.
- **Operations (issue-dispatched, autonomous):** `pull-and-deploy` ×2,
  `provision-ib-gateway` (paper), `gateway-logs`, `vm-diag-snapshot`
  (`journalctl ict-trader-live`). Confirmed paper login completes with **no
  2FA**, market-data + HMDS farms OK, and MES candles flowing.
- **Documentation sync (this PR):** runbook, canonical architecture, trade
  pipeline, architecture-target, ROADMAP, CURRENT-SPRINT, README, this log.

## Validation Performed
- Tests run: `pytest tests/test_ib_integration.py tests/test_ib_sizing_and_data.py`
  → 52 passed (incl. 3 new resilience tests). Full CI green on #1706 and #1712.
- Dry-runs / staging checks: live `journalctl` after deploy shows (new PID):
  `Connecting to 127.0.0.1:4002 … Connected … Logged on to server version 176`,
  `Market data farm connection is OK`, `HMDS data farm connection is OK`,
  `VWAP signal builder: symbol=MES timeframe=5m candles=100`,
  `ict_scalp_5m: HTF bias … (MES)` — i.e. all three strategies evaluating MES
  with no `ConnectionRefused` / `TimeoutError` / `no current event loop`.
- Manual code verification: reproduced the loop-poisoning mechanism in a local
  Python 3.11 + ib_insync 0.9.86 harness and verified re-asserting the same
  persistent loop restores it.
- Gaps not yet verified: no live MES order has filled yet (markets sideways /
  no setups — expected, not a fault). The dashboard "Trade Process" tab was
  **not** visually re-verified against the updated `TRADE-PIPELINE.md` (the
  dashboard work is the next session); doc content is code-accurate.

## Documentation Updated
- Rules doc updates: none required (CLAUDE.md operating rules unaffected).
- Architecture doc updates: `docs/ARCHITECTURE-CANONICAL.md` (Steps 1/2/6 +
  Change-log row); `docs/architecture/multi-strategy-architecture-target.md`
  (IB account block port/strategies, invariant #7, change log).
- Trade pipeline doc updates: `docs/TRADE-PIPELINE.md` (Stage 1 multi-symbol +
  IB routing; Stage 7 IB execution branch).
- Roadmap updates: `ROADMAP.md` (M11 row, S-REFACTOR-S7 note, deferred-items
  section now ✅).
- GitHub Actions doc updates: none (workflows unchanged this sprint).
- Subsystem doc updates: `docs/runbooks/ib-integration.md` (status LIVE, port
  4002/socat, no-2FA-for-paper, event-loop section, Docker-current vs
  superseded-IBC reorg); `docs/sprint-plans/CURRENT-SPRINT.md`; `README.md`.
- Historical docs marked superseded: the native-IBC section of the IB runbook
  is explicitly flagged superseded by the Docker path.

## Contradictions or Drift Found
- Runbook + architecture-target + accounts.yaml disagreed on the paper port
  (docs said `7497`, code is `4002` after #1706) — fixed.
- ROADMAP / CURRENT-SPRINT marked S7 IB/MES "deferred / blocked on credentials"
  while the integration was wired and now live — fixed.
- Runbook framed MES as "remaining wire-up" and claimed paper login waits for a
  2FA tap; both stale — fixed (MES live; paper logs in with no 2FA).

## Risks and Follow-Ups
- Remaining technical risks: delayed CME data (~10–15 min lag) is fine for
  strategy refinement/training but not latency-sensitive execution; switching to
  real-time needs the CME Real-Time (NP,L1) subscription + `IB_MARKET_DATA_TYPE=1`.
- Remaining product decisions (Tier 3): promoting the real-money `ib_live`
  account to `mode: live` (separate 2FA handling) — operator-gated.
- Blockers: none.

## Deferred Items
- MES-specific ML models / training pipeline — chicken-and-egg; needs
  accumulated MES paper trades first.
- Dashboard: BTCUSDT + MES performance tabs with TradingView-style
  signal / TP / SL / PnL overlays on the live chart (next session).

## Next Recommended Sprint
- Suggested next: (1) kick off a starter MES training run that pulls a large
  history; (2) build the dashboard performance tabs (BTCUSDT + MES) with
  signal/trade overlays while training runs; (3) review training results.
- Why next: MES paper trades are now accumulating, and the operator wants live
  per-symbol performance visibility with trade context on the chart.
- Required verification before starting: confirm the MES paper account is still
  ticking (it is) and that delayed data remains the intended mode.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated; the dashboard Trade Process tab visual re-verification is deferred to the dashboard session (noted above).
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
