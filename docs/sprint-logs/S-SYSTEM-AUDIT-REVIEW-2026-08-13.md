# S-SYSTEM-AUDIT-REVIEW-2026-08-13 — combined full-system audit + system review

## Date Range

2026-08-13 (single session, in flight at the time of writing)

## Objective

Operator-directed: run `/full-system-audit` **and** `/system-review` together.
The audit half was explicitly framed as **bidirectional** — the docs are the
yardstick, but where live reality is the deliberate, correct state and the doc
lagged, the **doc** gets fixed. The review half was given one stated priority:
**drain the backlog**, especially the high-severity items blocking other
research sessions.

## Tier

Tier-1 throughout. No `src/` order-path file, no `config/`, no live-VM mutation,
no account-mode flip. Two Tier-2/3 findings are **raised, not enacted**.

## Starting Context

- `main` at `a787872` (#8948); the overnight M20 arc had just closed with the
  headline at `360/360 = 100.0%` and the done-condition at **37 open, every
  cell blocked**.
- Backlogs at session start: health **118 open + 49 kept_open** (34 high, 2
  critical), performance **6 + 43**, ml **3 + 32**.

## Repo State Checked

- All three repos on `claude/system-audit-review-oy5kon`.
- **Live VM (diag #8953, 2026-08-13T08:14Z):** trader + web-api **active** on
  `git_sha a7878725` — matches `main` HEAD, so the deploy is current.
  Heartbeat 22 s (`running`). `vm_health` cpu 0.0 / mem 11.1 / disk 41.4.
  Exit-loop decouple **`state: fresh`**, 19 passes, max pass 21.7 s — the M20
  decouple is genuinely running.
- **`ict-exchange-fills-pull.service` = `failed`** on the live VM.
- `ict-ib-gateway-watchdog.timer` reads `inactive` on the trader and that is
  **correct, not a finding** — it is auto-enabled only where
  `/etc/ict-vm-role == gateway`.

## Files and Systems Inspected

Phase 0 (rules) by hand; Phases 3A/3B/3C fanned out to three background agents
over (a) the liveness/zombie inventory, (b) doc-vs-code across the bot's
declared contracts, (c) the two consumer repos. Lead verified every finding
acted on.

Directly re-derived by the lead rather than taken from an agent:
`docs/research/exit-refinement-coverage.json` ↔ `config/strategies.yaml`
set-difference · every halt-flag site in `src/` · the sudoers grant + its
installer · `replay-pregate-nightly`'s notify gating · the cron'd-workflow
population.

## Work Completed

**Shipped (3 draft PRs, one per repo):**

1. **Metis-Insights #8964**
   - `runtime_flags.halt_flag_path()` — single-homed the halt flag. It had
     three definitions and **two paths**: `pipeline.py` (the only consumer that
     halts) checks `/data/bot-data/trader_halt.flag`, while
     `GET /api/bot/config` **and** the Telegram readout both hardcoded
     `/tmp/trader_halt.flag` and never read the env var. Both operator-facing
     surfaces could report **RUNNING while the trader was halted**. 7 tests.
   - `m20_coverage_rollup.validate()` — added the **config→matrix** direction.
     Only matrix→config was enforced, so a live leg with *no row at all* was
     invisible. Ships green (45 = 45, both differences empty).
   - `claude-run-failure-alert.yml` — registered the 9 unwatched cron'd
     workflows.
2. **ict-trader-dashboard #205** — per-strategy `Measured %` column.
3. **ict-trader-android #117** — per-strategy provenance mark + legend.

**Backlog movement:** 1 resolved
(`BL-20260810-COVERAGE-MATRIX-LEG-IDS-DO-NOT-JOIN-TO-CONFIG`), 1
partially-resolved (`…CRON-WORKFLOWS-FAIL-SILENTLY…`), 4 filed. **Net +3 open,
deliberately** — the audit surfaced more than the drain closed, and inflating
the close count by filing thin resolutions would defeat the point.

## Validation Performed

- `check_canonical_doc_coherence.py`: **5/5 PASS**.
- Guard suite: **30 pass / 6 diff-scoped** locally; **CI guards green** on
  #8964 after one round-trip.
- 8/8 M20 join self-tests · 7/7 halt-flag tests · 106 pre-existing m20 tests.
- Halt flag verified **end to end**, not merely imported:
  `build_config()["trading_mode"]["halted"]` goes `False → True → False` as the
  real file is created and removed, and the endpoint's resolver and the
  pipeline's agree on the default.
- Dashboard coverage cell exercised across all three states incl. the one that
  matters (all-ungraded renders `—`, **not** `0%`).
- **NOT validated:** the Android Kotlin compile (no toolchain in the sandbox) —
  CI owns it. Stated on the PR.

## Documentation Updated

- `m20_coverage_rollup.py` docstring: it quoted `47 × 8 = 376` and `319/376` as
  *"the one to keep quoting"* in the present tense. Both stale, and stale in
  the direction that matters now the headline and done-condition have
  separated. Population is now computed, not restated in prose.
- `runtime_flags.py` docstring: the line *"the halt flag lives in /tmp … NOT
  managed here"* was both stale and the reason for the defect.
- `claude-run-failure-alert.yml` header: its "scoped to Claude-driven
  workflows" framing no longer described its contents.

## Contradictions or Drift Found

| finding | direction | disposition |
|---|---|---|
| Halt flag: reporters vs consumer | **CODE-DRIFT** | fixed (#8964) |
| M20 join enforced one-way | **CODE-DRIFT** | fixed (#8964) |
| 9 cron'd workflows silent on failure | **CODE-DRIFT** | fixed (#8964) |
| `claude-vm-runner` sudoers root grant | **ZOMBIE** | filed, Tier-2, operator |
| Caddy/duckdns transport in zero docs | **DOC-STALE** (live is correct) | filed |
| Velotrade doc in present tense, says `breakout` is deprecated — exactly backwards | **DOC-STALE** | filed by agent, not yet fixed |
| `binance_connector.py` named in ARCH, file does not exist | **DOC-STALE** | not yet fixed |
| ARCH "54 strategy cells" vs 55 | **DOC-STALE** | not yet fixed |
| 10 live routes absent from the CLAUDE.md API table | **DOC-STALE** | not yet fixed |
| `totalR`/`expectancyR`/`rCoverage`/`totalPnlMeasured`: **zero readers in 3 of 3 frontends** | **CODE-DRIFT** | per-strategy half fixed (#205, #117); the R-family is still unread |

## Risks and Follow-Ups

- **`BL-20260813-VM-RUNNER-ZOMBIE-SUDOERS-ROOT-GRANT` (Tier-2, operator).** The
  file **must be split, not deleted** — it also carries a justified
  `/usr/sbin/ufw` grant.
- **`replay-pregate-nightly` is still failing nightly.** #8964 makes the next
  failure loud; it does not make the run pass. The trainer-SSH-under-load cause
  is open.
- **`ict-exchange-fills-pull.service` is `failed`** on the live VM — observed,
  not yet investigated.
- **Zero of 11 accounts declare a gross-exposure ceiling**, and
  `alpaca_portfolio` / `alpaca_paper` are running ~2.0× exposure. This is the
  distribution the exposure soak exists to produce; the ceiling value is the
  operator's.
- **This session's `DIAG_BASE_URL` points at `158.178.210.252`** — the x86 micro
  terminated 2026-06-16. Harmless today (sandbox egress is firewalled, so the
  relay is the only channel) but it would hit a dead host if a future session
  were created at Full network access.

## Deferred Items

Three probes of my own were wrong and are recorded because the corrections are
the useful part:

1. **"8 guards FAIL"** — missing `pytest`/`import-linter` locally, not real
   failures. Would have been a false alarm.
2. **"10 of 10 cron'd workflows are silent"** — my probe grepped for a literal
   `api.telegram.org`; `Health Snapshot` notifies via
   `scripts/notify_session.py`. Honest count is 9. Committed the sub-class-C
   unasserted negative *while auditing for that class*.
3. **My new guard flagged `xauusd_trend_1h`** — **the matrix was right.** That
   leg is `enabled: false` *with* `execution: live`, and the live trader's
   52-leg loaded list does not contain it. Had I trusted the guard over the
   field, I would have corrupted a correct row.

## Next Recommended Sprint

1. Operator decision on the sudoers grant, then the Tier-1 repo-side purge.
2. The trainer-SSH-under-load cause behind the pre-gate failures.
3. The doc-stale backfill batch (Velotrade, binance, 54→55, the 10 API rows,
   the undocumented kill-switches + `POSITIONS_CACHE_*`).
4. The R-metric family (`totalR`/`expectancyR`/`rCoverage`) — still zero
   readers across all three frontends.

## Wrap-Up Check

- [x] Board `START` posted before the first substantive call; progress posted.
- [x] Findings filed to the correct backlog with evidence + tier.
- [x] Tier-2/3 findings raised, **not** enacted.
- [x] Coverage stated honestly, including what was not reached.
- [ ] `doc-freshness` closing run — session still in flight.
- [ ] ROADMAP entry — pending, lands with the audit report.
- [ ] Audit report to `comms/reports/` (`window: "audit"`) — the program's
      closing deliverable, owed once the fixes are merged + live-verified.
