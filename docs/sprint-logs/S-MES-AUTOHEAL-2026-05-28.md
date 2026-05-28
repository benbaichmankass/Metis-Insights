# Sprint Log: S-MES-AUTOHEAL-2026-05-28

## Date Range
- Start: 2026-05-28
- End: 2026-05-28

## Objective
- Primary goal: Restore MES (`ib_paper`) to live trading after the overnight
  IBKR-reset wedged the IB Gateway session, and ship an automated guard so it
  self-heals the next time instead of going dark for hours.
- Secondary goals: Root-cause the recurring "pytest flake" red CI (it was not
  flake) and make `pytest-run` self-diagnosing; make the new watchdog
  observable/verifiable on the diag surface.

## Tier
- Mixed: **Tier 2** (new systemd service+timer, `pull-and-deploy` deploys,
  service restarts — runtime/deploy changes) + **Tier 1** (CI workflow step,
  diag read-path allowlist, docs).
- Justification: the watchdog unit/timer + the two production deploys are
  Tier-2 and were taken with an explicit operator OK in chat ("deploy now",
  "make it verifiable now"). The `pytest-run` job-summary step, the `diag.py`
  `_CANONICAL_UNITS` addition, and this documentation are Tier-1.

## Starting Context
- Active roadmap items: MES live since S-MES-GOLIVE (2026-05-22); M13 AI
  analyst; Android app track.
- Prior sprint reference: `S-ANDROID-S8-2026-05-27.md` (latest dated log).
- Known risks at start: MES could go dark for hours when IBKR's overnight
  server-reset wedges the Gateway session (only mitigation was a manual
  `vm-ib-gateway-recover` dispatch); CI's `pytest-run` had been intermittently
  red and was being written off as "flake".

## Repo State Checked
- Branch / commit reviewed: `claude/health-check-CjAWV`; `main` started this
  session at `6972a9f` and was advanced to `4d4d0c3`.
- Deployment state reviewed: live VM (`instance-20260414-1555`) at `6972a9f`
  pre-session; deployed to `4d4d0c3` via `pull-and-deploy` (issue #2196).
- Canonical docs reviewed: `CLAUDE.md`, `docs/runbooks/ib-integration.md`,
  `docs/claude/health-review-backlog.json`.

## Files and Systems Inspected
- Code files inspected: `src/web/api/routers/diag.py` (`_CANONICAL_UNITS`,
  `_normalize_unit` — the `unknown_unit` 400 gate), `scripts/check_ib_gateway.py`,
  `scripts/deploy_pull_restart.sh` (the "no new commits" skip at L136-140 vs the
  `install_systemd_units.sh` call at L155), `scripts/ops/status_check.sh`.
- Config / deploy files inspected: `deploy/ict-ib-gateway-watchdog.service`,
  `deploy/ict-ib-gateway-watchdog.timer`.
- Docs inspected: `docs/runbooks/ib-integration.md` (§ Auto-heal watchdog),
  `CLAUDE.md` (Important Notes), `docs/claude/health-review-backlog.json`.
- Services / timers inspected: `ict-ib-gateway-watchdog.{service,timer}`,
  `ict-trader-live.service`, `ict-web-api.service`, `ict-liveness-watchdog.timer`,
  `ict-git-sync.timer` (via `/api/diag/services` + the `pull-and-deploy` post-restart
  systemd dumps).
- GitHub Actions workflows inspected: `system-actions.yml` (pull-and-deploy),
  `vm-diag-snapshot.yml` (diag relay); the `pytest-run` CI job (job-summary step).

## Work Completed
- Diagnosed MES down to the IBKR overnight-reset wedge: the Docker Gateway
  stayed *up* but its IBKR session died (data farms "broken"; a logged-out
  Gateway still reports `connected=true` but `net_liquidation=None`), so every
  MES request timed out. Recovered the session (operator-directed) — backlog
  item resolved at 07:05 UTC.
- Shipped the **IB Gateway auto-heal watchdog** (#2183, `6972a9f`):
  `scripts/check_ib_gateway.py` + `deploy/ict-ib-gateway-watchdog.{service,timer}`
  (5-min probe of `ib_paper`; restart via `scripts/ops/restart_ib_gateway.sh`
  after 2 wedged checks; `--max-restarts 3 --cooldown-min 20` guard rails) +
  tests (`tests/test_ib_gateway_watchdog.py`) + the runbook § Auto-heal watchdog
  + the backlog update.
- Root-caused the "pytest flake": it was **not** flake. A clock-relative test
  detonated on the 2026-05-28 date boundary plus an unregistered pytest marker;
  both fixed deterministically in #2183 (`tests/test_insights_router.py`,
  `tests/test_s012_service_consolidation.py`). Deliberately did **not** add
  `pytest-rerunfailures` — auto-retry would have masked a deterministic failure
  and hidden future time-bombs.
- Made `pytest-run` self-diagnosing (#2192): the job now writes a JUnit report
  (`--junitxml`) and a `if: failure()` step parses it into `$GITHUB_STEP_SUMMARY`,
  listing each failing test + its first message line. The step always exits 0
  (never masks the real failure) and is skipped on green runs. Motivated by the
  fact that a web Claude session cannot read the raw CI run log, so a failing
  test name was previously invisible.
- Exposed the watchdog on the diag surface (#2192): added
  `ict-ib-gateway-watchdog.{service,timer}` to `diag.py::_CANONICAL_UNITS`, so
  `/api/diag/services` and `/api/diag/journalctl` can query it (previously 400
  `unknown_unit`). Added to `diag.py` only, **not** `status_check.sh` — the
  latter's unit list pass/fails on `active`, which a oneshot would falsely trip.
- Deployed `4d4d0c3` to the live VM (`pull-and-deploy`, #2196) and verified
  (below).

## Validation Performed
- Tests run: CI on #2192 went green on all 11 required checks (`pytest-run`
  completed `success` at 07:50:24 UTC after the ready-flip re-trigger).
- Dry-runs / staging checks: `pull-and-deploy` (#2196) exited 0; the full
  deploy path ran (`6972a9f → 4d4d0c3`, pip install, `install_systemd_units.sh`
  "in sync", all `ict-*` units restarted clean).
- Manual code verification: read `deploy_pull_restart.sh` to confirm the
  "no new commits" branch `exit 0`s before the unit installer (explains why the
  *first* manual deploy skipped install — the `ict-git-sync` timer's own
  new-commit pull is what had already run the installer + `enable --now`'d the
  timer); read `diag.py::_normalize_unit` to confirm the failed watchdog-journal
  pull was an allowlist 400, not a web-api outage.
- **Watchdog verified live** via the #2196 post-restart systemd dump:
  `ict-ib-gateway-watchdog.service` Loaded, `TriggeredBy: ● ict-ib-gateway-watchdog.timer`
  (timer active), and the probe fired:
  `probe: healthy=True reason=net_liquidation=1000600.6 action=none`. The trader
  itself reconnected to IB Gateway cleanly (`clientId 498 … API connection ready`,
  data farms OK). A `/api/diag/services` pull confirmed `ict-web-api` + trader
  active.
- Gaps not yet verified: the watchdog's **restart path** (the actual
  `docker restart` on a real wedge) has not fired in production — only the
  healthy-probe path is observed. The next overnight IBKR reset (~04:30 UTC) is
  the real end-to-end test. The IBC nightly auto-restart **root cause** remains
  open; the watchdog is a mitigation, not a fix.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none this session (the watchdog was already added to
  `docs/ARCHITECTURE-CANONICAL.md` in #2183).
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): n/a — no pipeline stage
  changed (the watchdog is an out-of-band ops guard, not a tick stage).
- Roadmap updates: none — the watchdog is ops hardening within the existing
  S-MES-GOLIVE milestone, not a new milestone row. Flag for next session if a
  row is wanted.
- GitHub Actions doc updates: none (the `pytest-run` step is self-explanatory;
  no allowlist/contract changed).
- Subsystem doc updates: `docs/runbooks/ib-integration.md` § Auto-heal watchdog
  (shipped in #2183); `docs/claude/health-review-backlog.json`
  `BL-20260527-003` → partially-resolved; this sprint log; a `CLAUDE.md`
  Important-Notes bullet mirroring the `ict-liveness-watchdog` one.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- `scripts/deploy_pull_restart.sh` "no new commits" short-circuit (L136-140)
  `exit 0`s **before** `install_systemd_units.sh` (L155). Not a bug — the
  `ict-git-sync` timer's own new-commit pull runs the installer — but a sharp
  edge: a manual `pull-and-deploy` issued *after* git-sync has already advanced
  HEAD will not (re)install units. Recorded as an observation, no change made.
- `scripts/ops/status_check.sh::CANONICAL_UNITS` only tracks long-running
  services and fails the check if any is not `active`; a oneshot watchdog would
  false-fail it. This is why the watchdog was added to `diag.py` only. No drift,
  documented here for the next session.

## Risks and Follow-Ups
- Remaining technical risks: watchdog restart path unproven in production (only
  the healthy probe is observed). Watch the next overnight IBKR reset.
- Remaining product decisions (Tier 3): none.
- Blockers: none. `BL-20260527-003` (IBC nightly auto-restart unreliable) stays
  open as a Tier-2 investigation.

## Deferred Items
- Optional `ROADMAP.md` status row for this ops-hardening sprint.
- A full `doc-freshness` skill pass (a targeted check was done: runbook +
  deployed unit + backlog all consistent).

## Next Recommended Sprint
- Suggested next sprint: confirm the watchdog's restart path end-to-end on the
  next overnight IBKR reset; if it heals MES with no manual intervention, mark
  `BL-20260527-003` fully resolved. Otherwise investigate the IBC nightly
  auto-restart root cause (jts.ini/ibc.ini + entrypoint around the 11:59 PM
  window).
- Why next: the watchdog is deployed and the healthy path is proven, but its
  whole purpose — automatic recovery — is only validated by a real wedge.
- Required verification before starting: a real (or forced) wedge, then
  `/api/diag/journalctl?unit=ict-ib-gateway-watchdog.service` showing
  `action=restart` followed by a healthy probe.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. (n/a — no pipeline stage changed.)
- [x] Roadmap status was checked (no row added — ops hardening within S-MES-GOLIVE).
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly (watchdog restart path unproven; IBC root cause open).
