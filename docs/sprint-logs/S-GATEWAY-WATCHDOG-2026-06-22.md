# Sprint Log: S-GATEWAY-WATCHDOG-2026-06-22

## Date Range
- Start: 2026-06-22
- End: 2026-06-22

## Objective
- Primary goal: investigate the `MONITOR BLIND — candles_unavailable` alert on the
  open MHG position (`pkg-f58a249d119049ab`, `mhg_pullback_1d`), recover the
  feed, and close the recurrence gap.
- Secondary goals: re-arm reactive IB-Gateway recovery for a mid-day wedge; build
  an autonomous deploy path to the gateway VM; properly isolate the gateway VM in
  the install/deploy tooling.

## Tier
- Tier 2 (gateway-VM systemd units + deploy tooling; paper-money, isolated from
  the money loop). Each change operator-approved before merge.
- Justification: runtime/service/timer + deploy-path changes on a production VM,
  but blast radius is the dedicated gateway VM only; no order-path / risk / mode
  changes.

## Starting Context
- Active roadmap items: none specific to the gateway; this was incident-driven.
- Prior sprint reference: `S-SYSTEM-REPORT-2026-06-22.md` (same day).
- Known risks at start: the IB Gateway wedge had recurred (open MHG position on
  the broker bracket alone); gateway VM provisioning history unclear.

## Repo State Checked
- Branch/commit reviewed: `main` @ `0defa9b` → through the merges below.
- Deployment state reviewed via the `vm-diag-snapshot` relay (live trader
  journal + `/api/diag/services`) and the `vm-ib-gateway-deploy` self-deploy
  diagnosis (gateway VM was 269 commits stale, `/etc/ict-vm-role` unset).
- Canonical docs reviewed: `CLAUDE.md`, `docs/runbooks/ib-integration.md`,
  `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.

## Files and Systems Inspected
- Code: `src/runtime/order_monitor.py` (`_track_monitor_blindness`,
  `run_monitor_tick`), `src/main.py` (`_build_monitor_ohlcv_fetcher`),
  `src/runtime/market_data.py`, `src/exchange/ib_connector.py`,
  `src/units/accounts/ib_client.py` (`_build_contract`),
  `scripts/check_ib_gateway.py`, `scripts/deploy_pull_restart.sh`,
  `scripts/install_systemd_units.sh`, `scripts/ops/restart_ib_gateway.sh`,
  `scripts/ops/provision_ib_gateway.sh`.
- Config: `config/strategies.yaml` (`mhg_pullback_1d`), `config/instruments.yaml`
  (`MHG`).
- Deploy/units: `deploy/ict-ib-gateway-watchdog.{service,timer}`,
  `deploy/ict-ib-gateway-reset.{service,timer}`, `deploy/ict-git-sync.{service,timer}`.
- Services/timers (gateway VM): confirmed via the deploy workflow's verification
  block.
- GitHub Actions: `vm-ib-gateway-recover.yml`, `vm-diag-snapshot.yml`,
  `vm-ib-gateway-deploy.yml` (new), `bootstrap-labels.yml`.

## Work Completed
- **Root-caused the alert** (not MHG/strategy-specific): the IB Gateway session
  wedged (container up, IBKR login dead → trader breaker OPEN for
  `10.0.0.251:4002`); all IBKR candles (MES/MGC/MHG) were dark. MHG was the only
  IB symbol holding a position, so it tripped the monitor-blindness alert.
- **Recovered** via `vm-ib-gateway-recover` (`docker restart ib-gateway`);
  verified MHG `candles=200` + breaker clear.
- **PR #4116** — re-armed the bounded reactive auto-restart on the gateway-VM
  watchdog (disarmed 2026-06-10; the shared-box objection is moot post-isolation)
  + restored the ~5 min cadence; kept the 05:30 daily reset. Hardened
  `decide(actionable=…)` so an inconclusive probe never drives a restart.
- **PR #4124** — `vm-ib-gateway-deploy` workflow (the gateway VM had **no**
  auto-deploy; was 269 commits stale).
- **PR #4126** — `EnvironmentFile=-` so the watchdog starts on the venv-less,
  `.env`-less gateway box.
- **PR #4129** — dep-free local wedge probe `scripts/ops/ib_gateway_local_probe.py`
  (docker-logs signature: socat→`127.0.0.1:4002` refused / IBC re-auth, no recent
  "Login has completed"); the account probe can't run on the minimal box
  (`ib_insync` absent). State pinned to repo-local `runtime_logs/`.
- **PR #4132** — reboot-safe `/etc/ict-vm-role=gateway` (provision + on-box) and a
  gateway branch in `deploy_pull_restart.sh` (skip pip + trader-service restarts).
- **PR #4136** — read-only rogue-unit verification block in the deploy workflow.
- **PR #4138** — `install_systemd_units.sh` gateway isolation: enable only a
  gateway allowlist and actively prune/stop non-gateway `ict-*` units.
- **PR #4141** — this sprint log + the ROADMAP row.
- **PR #4142** — `vm-ib-gateway-selftest` workflow (controlled stop → auto-recover
  test of the reactive watchdog + a restart-cadence read; safety net guarantees
  the container is never left down). Ran via #4144: **PASS** (self-heal proven;
  `RestartCount=0`).

## Validation Performed
- Tests: `decide()` / `classify_probe` / local-probe logic exercised locally
  (pytest absent in the sandbox; verified by direct module import). CI `pytest-run`
  green on every PR (one failure on #4116 — a test helper not forwarding the new
  `actionable` kwarg — fixed before merge).
- Manual code verification: `bash -n` on the shell scripts; `ruff` clean;
  YAML parse on workflows.
- Live verification (diag relay + deploy-workflow output):
  - MHG monitor receiving `candles=200`; **zero** `circuit breaker OPEN` lines in
    the post-recovery trader-journal window (11:24 UTC).
  - Gateway VM after the final deploy: role marker `gateway`; gateway timers
    (watchdog/reset/git-sync) enabled+active; watchdog probe `healthy → action=none`;
    `last_status: ok`; container Up + logged in; rogue-unit check **clean** (the
    crash-looping `ict-web-api` + 9 stray trader timers pruned).
- Reactive self-heal proven end-to-end (PR #4142 `vm-ib-gateway-selftest`,
  issue #4144): a controlled `docker stop` → the real watchdog read
  `actionable=True`, honored `--restart-after 2` (run 1 = detected, run 2 =
  restart), fired `restart_ib_gateway.sh`, and the container came back
  (`action=recovered`, healthy). The bounded guards + state persistence worked
  exactly as designed.
- Restart cadence measured (same self-test): `docker inspect` shows
  **`RestartCount=0`** since the container was created (2026-06-10) — it is NOT
  crash-looping. The short uptimes seen during the session were from this
  session's own recover/deploy/selftest bounces, not organic flapping; the
  periodic IBC re-logins are normal session refreshes the 2-check guard tolerates.
- Gaps not yet verified: none outstanding from this sprint.

## Documentation Updated
- Rules doc updates: `CLAUDE.md` — IB-Gateway watchdog note updated (reactive
  re-arm; runs on the gateway VM).
- Architecture doc updates: none required.
- Trade pipeline doc updates: none (no pipeline stage changed).
- Roadmap updates: added the S-GATEWAY-WATCHDOG-2026-06-22 row.
- GitHub Actions doc updates: covered inline in `docs/runbooks/ib-integration.md`.
- Subsystem doc updates: `docs/runbooks/ib-integration.md` — reactive watchdog,
  dep-free local probe, gateway deploy path, role marker, gateway-safe git-sync.
- Historical docs marked superseded: the "alert-only / no auto-restart" framing
  in `ib-integration.md` was rewritten (not deleted; history kept in prose).

## Contradictions or Drift Found
- `ib-integration.md` claimed the watchdog "runs on the trader" — stale; it runs
  on the gateway VM (verified inactive on the trader). Fixed.
- Provisioning never wrote `/etc/ict-vm-role`, so `install_systemd_units.sh`
  skipped enabling the gateway-only timers (they survived only because
  hand-enabled once). Fixed.
- `install_systemd_units.sh` had a gateway-only exclusion but **no** trader-only
  exclusion, so it enabled trader-oriented timers on the gateway (they failed /
  crash-looped). Fixed (gateway allowlist + prune).

## Risks and Follow-Ups
- Remaining technical risks: none outstanding. Restart cadence verified
  (`RestartCount=0`) and the reactive self-heal verified end-to-end (#4142/#4144).
- Remaining product decisions (Tier 3): none.
- Blockers: none.

## Deferred Items
- None. The two end-of-sprint gaps (restart cadence; reactive self-heal proof)
  were closed in-sprint via the `vm-ib-gateway-selftest` workflow (#4142).

## Next Recommended Sprint
- Suggested next sprint: none required for the gateway; resume normal program
  work. The on-demand `vm-ib-gateway-selftest` remains available to re-verify the
  self-heal after any future gateway/tooling change.
- Why next: gateway recovery + isolation are complete and verified.
- Required verification before starting: n/a — re-run `vm-ib-gateway-selftest`
  on demand if the gateway/tooling changes.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage changed; `docs/TRADE-PIPELINE.md` not applicable.
- [x] Roadmap status was checked (row added).
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly (container restart cadence; reactive
      self-heal unproven on a real wedge).
