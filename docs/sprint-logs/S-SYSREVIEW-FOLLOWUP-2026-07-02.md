# Sprint Log: S-SYSREVIEW-FOLLOWUP-2026-07-02

## Date Range
- Start: 2026-07-02
- End: 2026-07-02

## Objective
- Primary goal: work through the findings of the 2026-07-02 `/system-review`
  report (`RPT-20260702-061700-since-last`, PR #5420) — an IB Gateway daily
  wedge, a trainer OOM that stranded ~half the daily manifest fleet, two
  regime shadow models that looked silently stale, and an `alpaca_live`
  sizing floor the operator had already tried (and not fully fixed) once.
- Secondary goals: log Alpaca's official MCP server as a documented,
  scoped-read-only diagnostic resource for future sessions (operator request).

## Tier
- Mixed: Track A is Tier-2 (gateway-VM timer/service change); Tracks B, C, F
  are Tier-1 (trainer-VM-only / diagnosis-only / docs-only).
- Justification: Track A changes a live timer on the gateway VM that governs
  MES/IBKR recovery timing — operator sign-off required per the VM-authority
  split. Tracks B/C/F touch only the trainer VM, produce no code change (C),
  or are documentation — all autonomous per `CLAUDE-RULES-CANONICAL.md`.

## Starting Context
- Active roadmap items: M14 (ML Optimization Program, in progress), M18
  (Portfolio Capital Allocator, propose-only), M19 (AI Model Strategy,
  propose-only) — none of this sprint's tracks touch these directly.
- Prior sprint reference: the `/system-review` session earlier the same day
  (PR #5420, report `RPT-20260702-061700-since-last`) that surfaced all four
  findings this sprint works through.
- Known risks at start: IB Gateway wedging daily around 06:00Z (recurring,
  previously seen 2026-06-23); a trainer OOM had just stranded ~30-40 of 68
  manifests; two regime shadow models (`btc-regime-1h-lgbm-yz-v1`,
  `btc-regime-5m-lgbm-yz-v1`) appeared to have stopped logging predictions;
  `alpaca_live` signals were sizing to 0 shares despite a 2026-06-30 risk-cap
  raise.

## Repo State Checked
- Branch or commit reviewed: `claude/system-review-ng0dvn`, reset from
  `main` at the start of this sprint; squash-merged back to `main` as
  `efc44687` (PR #5424).
- Deployment state reviewed: live trader VM (`141.145.193.91`), IB gateway
  VM (`10.0.0.251`, private), trainer VM (`158.178.209.121`) — the latter
  was unreachable over SSH for the diag relay three separate times this
  sprint (07:42Z, 07:48Z, 08:12Z — "Connection timed out during banner
  exchange"), so Track B's post-merge unit activation did not complete.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/ARCHITECTURE-CANONICAL.md`, `ROADMAP.md`.

## Files and Systems Inspected
- Code files inspected: `deploy/ict-ib-gateway-reset.timer`,
  `scripts/check_ib_gateway.py`, `deploy/ict-ib-gateway-watchdog.{service,timer}`,
  `scripts/ops/run_training_cycle.sh`, `deploy/training-vm-cloud-init.yaml`,
  `ml/shadow/factory.py`, `src/runtime/regime_bar_scoring.py`,
  `ml/registry/model_registry.py`, `src/units/accounts/risk.py` (re-read
  from the earlier planning phase, not re-modified this sprint).
- Config files inspected: none changed this sprint (Track D, the one config
  change on the table, stayed blocked).
- Deployment files inspected: `deploy/ict-ib-gateway-reset.timer`,
  `deploy/ict-ib-gateway-watchdog.service`, `deploy/ict-mes-ibkr-pull.timer`,
  `deploy/training-vm-cloud-init.yaml`.
- Docs inspected: `docs/runbooks/ib-integration.md`, `docs/ml/training-center.md`,
  `docs/runbooks/training-vm.md`, `docs/claude/trainer-vm-mode.md`,
  `docs/claude/diag-relay.md`, `docs/claude/health-review-backlog.json`,
  `docs/claude/ml-review-backlog.json`.
- Services or timers inspected: `ict-ib-gateway-reset.timer`,
  `ict-ib-gateway-watchdog.{service,timer}`, `ict-trainer.service`,
  `ict-trainer.timer` (new `ict-trainer-catchup.{service,timer}` added).
- GitHub Actions workflows inspected: `vm-diag-snapshot.yml`,
  `trainer-vm-diag.yml`, `pytest-run.yml` (via check-run status on PR #5424).

## Work Completed
- **Track A** — root-caused the recurring ~06:00Z IB Gateway wedge:
  `ict-ib-gateway-reset.timer` fired at 05:30 UTC, 15 minutes *inside*
  IBKR's own documented ~03:45–05:45 UTC reset window, so the one
  deterministic restart the recovery design relies on was racing the outage
  it existed to fix. Retimed to 06:05 UTC; added
  `scripts/check_ib_gateway.py --suppress-window-utc` (wired into the
  watchdog at `03:45-05:45`) so the reactive watchdog logs but doesn't burn
  a restart attempt inside the window. Caught and fixed a real gap in the
  same commit: recovery detection didn't treat a `"suppressed"` last-status
  as equivalent to `"wedged"`, so a recovery right after a suppressed
  episode would have gone unreported.
- **Track B** — root-caused the trainer OOM's full-day blast radius:
  `ict-trainer.service` had no memory cap (systemd default `OOMPolicy=stop`
  kills the whole cgroup) and no checkpoint, so a mid-cycle OOM stranded
  every not-yet-trained manifest until the next day's timer fire. Added
  per-manifest checkpoint/resume (`runtime_logs/trainer/cycle_progress_<date>.json`,
  flock-guarded), a same-day `ict-trainer-catchup.timer` (05:00 UTC), and
  `MemoryHigh=4G`/`MemoryMax=5G`/`OOMPolicy=continue` on the service.
- **Track C** — diagnosed the two "stale" yz regime shadow models: live
  `shadow_stats` query showed both actively producing predictions minutes
  before the check. The apparent staleness was a stage-label artifact — both
  models were correctly demoted `advisory`→`shadow` on 2026-06-23/06-25
  (known calibration gap), and the report's staleness check had kept
  looking at the old `advisory` stage label. No code changed; logged
  `BL-20260702-001` as a methodology note for future reviews.
- **Track F** — wrote `docs/claude/alpaca-mcp-server.md` documenting
  Alpaca's official MCP server as a read-only diagnostic resource (operator
  request), with the binding rule that its trading toolset must never be
  enabled on a session touching this repo (bypasses `RiskManager.position_size()`
  and the journal entirely). Cross-referenced from `CLAUDE.md`.
- **Track D investigation** (not shipped — see Deferred Items): traced why
  the operator's earlier 2026-06-30 risk-cap raise didn't fix `alpaca_live`'s
  sizing floor — it fixed the risk-tolerance sizing stage, but a second,
  independent margin/buying-power cap (`leverage: 0` → 1x, `RiskManager`'s
  `max_qty_by_margin`) still floors small ETF orders to 0 shares. While
  looking for the account's margin toggle in the Alpaca dashboard, found
  `alpaca_live` carries only ~$150 equity — under FINRA Reg T's $2,000
  minimum to extend *any* margin, regardless of a UI setting. Track D is now
  blocked on funding, not just a config edit.

## Validation Performed
- Tests run: `pytest tests/test_ib_gateway_watchdog.py tests/test_run_training_cycle_sh.py -q`
  — 41/41 passing (34 IB gateway incl. 7 new; 7 trainer checkpoint/resume,
  all new). `ruff check` clean on all touched Python files.
- Dry-runs or staging checks: manual kill-mid-cycle scenarios for the
  checkpoint/resume logic verified against a real fixture git repo + a
  PATH-shimmed `python`, before being locked in as permanent pytest coverage.
- Manual code verification: read `ml/shadow/factory.py::discover_shadow_stage_model_ids`
  and `model_registry.py::promote_stage` directly to rule out a raw-string
  vs canonical-alias stage-matching bug before concluding Track C was a
  methodology false-positive, not a code bug.
- Gaps not yet verified: Track B's `ict-trainer.service` resource caps and
  the new `ict-trainer-catchup.timer` were **not** confirmed live-activated
  — the trainer VM was unreachable over SSH for the whole back half of this
  sprint (see Risks and Follow-Ups). Track A's gateway-VM deploy was not
  independently re-verified post-merge within this sprint (self-deploys via
  `ict-git-sync`, expected within ~5min of the 08:09Z merge).

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: `docs/ARCHITECTURE-CANONICAL.md` — two new
  Change-log rows (Track A retime, Track B checkpoint/resume), the IB
  Gateway topology table row updated to 06:05 UTC.
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): not touched — no
  pipeline-stage change this sprint.
- Roadmap updates: this sprint log + `ROADMAP.md`'s "Last Updated" narrative
  (see below) — done as part of this doc-freshness pass, since none of the
  four tracks land in a milestone table row of their own (operational/
  incident-response work, not a strategy/ML initiative).
- GitHub Actions doc updates: none.
- Subsystem doc updates: `docs/runbooks/ib-integration.md`, `CLAUDE.md`
  (both repos' worth of references — the 05:30→06:05 retime and the
  Alpaca MCP server cross-reference), `docs/ml/training-center.md`,
  `docs/runbooks/training-vm.md`, `docs/claude/alpaca-mcp-server.md` (new),
  `docs/claude/health-review-backlog.json` (`BL-20260702-001` appended).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- No doc-vs-doc or doc-vs-reality contradictions found in the canonical set
  (`CLAUDE.md`, `CLAUDE-RULES-CANONICAL.md`, `ARCHITECTURE-CANONICAL.md`,
  `ROADMAP.md`) as of this pass — `scripts/ci/check_canonical_doc_coherence.py`
  passes all 4 checks (dead VM IP single-source, removed gates not live,
  no 7-stage ML ladder, instruction-hierarchy mirror), and every 05:30/06:05
  IB-gateway-timing reference across `CLAUDE.md`, `ARCHITECTURE-CANONICAL.md`,
  `docs/runbooks/ib-integration.md`, and `deploy/ict-mes-ibkr-pull.timer`
  is consistently framed (06:05 live, 05:30 explicitly historical).
- Code/doc mismatch found and fixed *during* this sprint (not left as
  drift): `docs/runbooks/training-vm.md` claimed the training cycle
  "short-circuits on the first manifest failure with overall_rc=1" — the
  actual script (`run_training_cycle.sh`) continues past a failed manifest
  and only sets `overall_rc=1` at the end. Corrected in the same commit
  that added the checkpoint/resume documentation.
- Gap found and closed by this doc-freshness pass itself: this sprint's
  work had not yet been recorded in `ROADMAP.md`'s narrative or in a sprint
  log, unlike the comparable 2026-06-22 gateway-watchdog incident session
  (which has both). This sprint log and the `ROADMAP.md` update below close
  that gap.

## Risks and Follow-Ups
- Remaining technical risks: the trainer VM (`158.178.209.121`) failed SSH
  three times this sprint (07:42Z, 07:48Z, 08:12Z) — may be a genuine outage,
  not a transient blip. Track B's `daemon-reload` + `enable --now
  ict-trainer-catchup.timer` + resource-cap confirmation is still pending;
  the script change itself ships regardless on the trainer's next self-pull
  of `main`, but the new catch-up timer and memory caps won't be active
  until the box answers and the activation issue is re-run.
- Remaining product decisions (Tier 3): Track D (`alpaca_live` leverage
  config) needs the operator to fund the account to at least Reg T's $2,000
  minimum before margin is even offered by the broker, then decide whether
  to also narrow `alpaca_live`'s 1h-cadence strategies to stay clear of the
  $25k PDT threshold. Operator is taking this up in a separate session.
- Blockers: none for the tracks that shipped this sprint (A, B, C, F all
  complete). Track D is blocked on the operator's funding decision (Track E
  is passive, resolves alongside D).

## Deferred Items
- Trainer VM `ict-trainer-catchup.timer` activation + `ict-trainer.service`
  resource-cap confirmation (`trainer-vm-diag-request` issue #5430, timed
  out — will retry when the box is reachable).
- Track A post-deploy spot-check (`ict-ib-gateway-reset.timer` next-fire
  time, watchdog suppression behaviour over the next 1-2 daily cycles) —
  not performed within this sprint's window.
- Track D (blocked on operator funding decision, see above).
- Track B fast-follow (not blocking, filed as a note in PR #5424, not yet a
  tracked backlog item): log peak RSS per manifest so a future OOM
  recurrence is diagnosed by culprit instead of guessed.

## Next Recommended Sprint
- Suggested next sprint: once the operator funds `alpaca_live` above the
  Reg T $2,000 minimum (or decides against margin), a short Tier-3 sprint
  to land the `leverage:` config change (or the alternative — restricting
  `alpaca_live` to instruments its current cash can actually afford).
- Why next: Track D was the one item this sprint could not close, and it's
  now better-scoped (funding first, then config) than when the sprint
  started (thought to be a pure config fix).
- Required verification before starting: confirm the account's current
  equity and margin status in the Alpaca dashboard directly (not inferred),
  and re-check the PDT day-trade count question raised in the original plan
  before touching `config/accounts.yaml`.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries (read
      `ml/shadow/factory.py`, `model_registry.py`, `run_training_cycle.sh`,
      the IB gateway timer/watchdog files, and `risk.py` directly this
      sprint rather than trusting the source report's characterizations).
- [x] Documentation was reviewed and updated as part of the sprint (see
      Documentation Updated above).
- [ ] `docs/TRADE-PIPELINE.md` — not applicable, no pipeline-stage touched.
- [x] Roadmap status was checked (see Roadmap updates above; this sprint's
      work doesn't map to an existing milestone row, so it's recorded in
      the narrative + this sprint log instead).
- [x] Contradictions were recorded (see Contradictions or Drift Found).
- [x] Remaining unknowns were stated clearly (trainer VM reachability,
      Track D funding status).
