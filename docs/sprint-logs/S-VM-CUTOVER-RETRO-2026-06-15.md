# Sprint Log: S-VM-CUTOVER-RETRO-2026-06-15

## Date Range
- Start: 2026-06-15 (retro of work spanning 2026-06-13 → 2026-06-15)
- End: 2026-06-15

## Objective
- Primary goal: Audit the live→Ampere VM cutover for remaining loose ends, and
  produce the retrospective the 40+-PR cutover effort never got (no sprint log
  was written at the time — the migration runbook's "Cutover completed" section
  was the only record).
- Secondary goals: fix the loose ends that are tractable now; harden the
  migration runbook so a future move is smoother.

## Tier
- Tier 1 (audit/docs) + Tier 2 (unit-file + deploy-tooling fixes, operator-ack
  to merge/deploy) + operator-gated actions (micro decommission, reserved-IP
  assign).
- Justification: read-only audit is Tier-1; the unit/installer fixes touch files
  the live VM consumes (Tier-2, shipped as a draft PR for operator merge); the
  micro termination and the reserved-IP `assign` swap are operator-gated.

## Starting Context
- Active roadmap items: post-M15; the cutover itself was infra, not a milestone.
- Prior sprint reference: none — the cutover had no sprint log (this is it,
  after the fact). Record lived in `docs/runbooks/live-vm-migration-ampere.md`.
- Known risks at start: live money at risk; the move had a real-money outage
  (Bybit ErrCode 10010) and a zombie-micro incident in its tail.

## Repo State Checked
- Branch/commit reviewed: `main` @ `e495b13` (= the live VM's running `git_sha`).
- Deployment state reviewed: live VM `ict-bot-arm` (141.145.193.91) via the
  `vm-diag-snapshot` relay (issues #3720/#3721) + `reserve-live-ip describe`
  (#3722); `config/accounts.yaml` modes cross-checked against runtime.
- Canonical docs reviewed: CLAUDE.md, the migration runbook, the health-review
  backlog (cutover-window BL-* entries), ROADMAP.

## Files and Systems Inspected
- Code files inspected: `scripts/daily_heartbeat.py`, `scripts/ops/terminate_instance.py`.
- Config files inspected: `config/accounts.yaml` (per-account `mode`).
- Deployment files inspected: `deploy/ict-heartbeat.service`,
  `deploy/ict-health-snapshot.service`, `deploy/ict-hourly-snapshot.service`,
  `scripts/install_systemd_units.sh`.
- Docs inspected: migration runbook, `docs/claude/trading-mode-flags.md`,
  health-review-backlog.json.
- Services/timers inspected (live VM, via diag snapshot): trader-live, web-api,
  telegram-bot (active); git-sync/liveness/insights/hourly/health timers
  (active); **ict-heartbeat.service = failed**; ib-gateway timers (inactive —
  correctly masked on the trader box).
- Workflows inspected: `cutover-live.yml`, `terminate-instance.yml`,
  `reserve-live-ip.yml`, `vm-diag-snapshot.yml`.

## Work Completed
- **Verified the cutover succeeded at its core purpose:** live VM memory 9.5%
  (~1.1 GB of 12 GB) vs the 90%+/kswapd-thrash on the 1 GB micro that drove the
  move; real-money path (`bybit_2`) trading + reconciling with real closed PnL,
  no ErrCode 10010; auto-deploy working (running latest `e495b13`); watchdogs
  armed; account modes match `accounts.yaml` (no cutover-induced drift).
- **Found a new, undocumented loose end:** `ict-heartbeat.service` (the daily
  13:00 UTC operator digest) had failed every trigger since cutover —
  `Failed to load environment files`. Root cause: it was the only unit pointing
  at a non-optional `EnvironmentFile=…/.env.live`; every sibling uses `.env`,
  which is what the cutover replicated. Fixed the unit (`-…/.env`) +
  `daily_heartbeat.py` env loading (`.env` preferred, `.env.live` back-compat).
  Logged `BL-20260615-HEARTBEAT-ENV`.
- **Confirmed the live VM public IP is EPHEMERAL** (the root architectural
  fragility behind the Bybit 10010 ripple). Dispatched `reserve-live-ip
  describe` (#3722) + `allocate` (#3723) to pre-create a reserved IP; the
  disruptive `assign` swap is staged for operator coordination (Bybit pre-bind).
- **Durable fix for `BL-20260614-INSTALLER-GATEWAY-TIMERS`:** made
  `install_systemd_units.sh`'s auto-enable loop topology-aware — the IB-gateway
  timers are skipped unless `/etc/ict-vm-role` == `gateway` (replaces the manual
  `systemctl mask` workaround on the candidate).
- **Fixed `BL-20260614-HEALTHSNAP-PY`:** `ict-health-snapshot.service` ExecStart
  now uses the trader venv python (was system `/usr/bin/python3` → false
  "ib_insync not installed").
- **Doc drift:** tightened `docs/claude/trading-mode-flags.md` closing paragraph
  (single survivor after `MONITOR_RECONCILE_ENABLED` removal). `BL-20260614-DOC-1`
  found already moot (CLAUDE.md prelude no longer mis-describes the removed flag).
- Hardened the migration runbook with a pre-cutover **environment-contract
  checklist** + a step to record the old box's OCI display name.

## Validation Performed
- Dry-runs/staging checks: live state pulled twice via the diag relay
  (snapshot + journalctl); reserved-IP `describe` confirmed EPHEMERAL.
- Manual code verification: read the terminate/reserve workflows + scripts end
  to end before dispatching; confirmed `terminate_instance.py` is exact-match +
  single-match-guarded.
- Gaps not yet verified: the micro's exact OCI display name is NOT recorded in
  the repo (blocks the terminate dispatch — operator to supply); the
  heartbeat/health-snapshot/installer fixes are shipped as a draft PR, not yet
  merged/deployed.

## Documentation Updated
- Subsystem doc updates: `docs/runbooks/live-vm-migration-ampere.md` (hardened
  checklist + display-name capture); `docs/claude/trading-mode-flags.md` (drift).
- This sprint log (the missing cutover record).
- Backlog: closed the resolved cutover items; added `BL-20260615-HEARTBEAT-ENV`.

## Contradictions or Drift Found
- `ict-heartbeat.service` unit referenced a `.env.live` file that does not exist
  on the canonical-`.env` system (latent bug; cutover exposed it).
- Runbook Phase 4 referenced "the micro's display name" but that name was never
  recorded anywhere (gap that blocks autonomous termination).
- `trading-mode-flags.md` "second survivor / Both" wording stale after the
  `MONITOR_RECONCILE_ENABLED` removal.
- ROADMAP records `oanda_practice` flipped live-on-practice (2026-06-11) but
  `accounts.yaml` now declares `mode: dry_run` (runtime matches config — a
  ROADMAP lag, not cutover drift; flagged for doc-freshness).

## Risks and Follow-Ups
- Remaining technical risks: live VM still on an **ephemeral IP** until the
  reserved-IP `assign` swap lands — any VM stop/move repeats the IP ripple.
- Remaining product decisions (Tier 3): none in this sprint.
- Blockers: micro decommission needs the operator-supplied OCI display name;
  reserved-IP `assign` needs an operator Bybit pre-bind + a low-activity window.

## Deferred Items
- Optional dedicated `/data` block volume for the candidate (today a boot-volume
  directory; fine, but doesn't match the micro's posture).
- Reserved-IP `assign` swap (operator-coordinated).

## Next Recommended Sprint
- Suggested next: complete the reserved-IP adoption (`assign` swap) once the
  operator has bound the reserved address on Bybit; then a fresh-VM rehearsal
  drill validating the hardened checklist end to end.
- Why next: removes the single biggest source of future-move fragility.
- Required verification before starting: confirm the reserved IP is bound on
  both Bybit keys; schedule an off-killzone window.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [ ] N/A — no pipeline stage touched; `docs/TRADE-PIPELINE.md` unchanged.
- [x] Roadmap status was checked (oanda doc-lag noted).
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
