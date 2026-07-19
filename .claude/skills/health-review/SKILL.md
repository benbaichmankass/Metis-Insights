---
name: health-review
description: Autonomous layer-2 review of the LIVE ICT TRADING BOT's TECHNICAL runtime health — pipeline plumbing, DB integrity, data validity, service state, alert delivery, sprint-doc drift. Reviews the cron health-snapshot report and reconstructs the same view from the diag relays since the last review. Drains docs/claude/health-review-backlog.json (system bugs / wiring gaps / minor doc drift). Does NOT score trades and does NOT review model performance — those moved to /performance-review and /ml-review respectively (2026-05-26 split). Use when the operator says "run the health review", "/health-review", or "do the layer-2 system review". NOT a code review or security audit.
---

# /health-review — technical/pipeline/data-health review of the live ICT bot

This is the **system-health** session of the three-way review split (the
others are `/performance-review` for trading + strategy scoring and
`/ml-review` for the training center + model lifecycle). It reviews the
**live trading system's runtime state**, not the codebase. Fully
autonomous: Claude fetches state itself through the diag relays, grades
plumbing + integrity, drains its backlog, and emits the response JSON.
The operator pastes nothing, downloads nothing, SSHes nowhere.

If the user asked for a *code* review, *codebase audit*, *security
review*, or *dependency check* — STOP, wrong skill. Point them at
`review` or `security-review`.

If the user asked about *strategy/trade performance*, *trade scoring*, or
*tweaks to consider* — STOP, wrong skill. Use `/performance-review`.

If the user asked about *model performance*, *training sessions*, or
*promote/demote a model* — STOP, wrong skill. Use `/ml-review`.

## Scope (what this skill DOES)

1. **Establish the window** — review everything *since the last
   health-review*, not a fixed slice (§ "The review window").
2. **Pull live runtime state** via the diag relays (§ "Fetching runtime
   state").
3. **Read the cron health report** — the artifacts surfaced by
   `/api/bot/health/{latest,history,services}` and the live VM's
   `artifacts/health/` snapshots (§ "The health report").
4. **Grade full-pipeline plumbing** — signal→order→trade wiring, monitor
   cadence, strategy silence, alert delivery, state consistency
   (§ "Pipeline rubric").
5. **Validate DB integrity + data validity** (§ "DB integrity &
   validity").
6. **Grade trainer-VM service health only** — is the timer running, is
   the unit healthy. Model/dataset/registry detail is **out of scope** —
   that's `/ml-review` (§ "Trainer service touch").
7. **Audit broker-account reachability (MANDATORY)** — confirm EVERY
   declared-live broker account is reachable (§ "Broker-account
   reachability"). A down live account is a can't-miss flag, never a
   line in the body.
8. **Review recent sprint logs** for doc correctness (§ "Sprint-doc
   review").
9. **Ingest the orphan-events log** (§ "Orphan-events ingest") — every
   NEW orphan trade row since the last review MUST be tracked + driven to
   reconciliation. Orphan is a problem to solve, never a resting status.
10. **Run the security-breach check (MANDATORY)** — actively look for any
    detected breach / intrusion signal since the last review across the
    surfaces this skill can reach (§ "Security-breach check"). "No breach
    detected" must be a stated verification, never an omission.
11. **Surface soak promotion/demotion decisions that are DUE** — flag any
    shadow model or strategy whose soak has reached its gate and now needs
    a promote/demote call, so it can't sit un-actioned (§ "Soak decisions
    due"). This skill SURFACES the decision; the recommendation itself is
    made by `/ml-review` (models) or `/performance-review` (strategies).
12. **Audit new work since the last review for rule compliance (MANDATORY)**
    — every PR merged + commit landed on `main` (and any live-VM / workflow /
    config change) in the window, checked against the canonical ruleset
    (§ "New-work compliance audit"). This is CHANGE-driven and distinct from
    the static weekday rotation (§ "Compliance audit rotation").
13. **Drain the health-review backlog** — triage every open item, fix
    what you can (§ "Draining the backlog").
14. **Emit the response JSON** + **post a one-line update to the Claude
    channel** (§ "Output" + § "Posting to the Claude channel").

## Out of scope (DO NOT do here)

- **Per-order-package trade scoring** — moved to `/performance-review`.
  `comms/claude_strategy_scores.jsonl` is no longer written by this
  skill.
- **Model status reports** — moved to `/ml-review`. No `model_status[]`
  in this skill's output.
- **Strategy tweak proposals** — `/performance-review`.
- **Promotion / demotion RECOMMENDATIONS** — `/ml-review` (models) /
  `/performance-review` (strategies). This skill still **SURFACES** that a
  soak has hit its gate and a decision is *due* (§ "Soak decisions due") —
  raising the flag is health's job; making the call is not. Surface + route;
  never write the recommendation or the rubric here.

## The review window — "since the last review"

The window runs from the last health-review to now. Determine "last
review" in this order:

1. The newest `reviewed_at` recorded in a prior health-review JSON
   (look at the Claude channel ping for the last review, or the
   newest `backlog_drain` action timestamp in
   `docs/claude/health-review-backlog.json`).
2. If neither is available, fall back to the last 24h.

Cap practical pulls at the diag limits (audit `limit=600` ≈ 6h at full
cadence). If the gap exceeds one pull, page back with
`since`/`until` on `journalctl` and note in the response that older
events were summarized, not enumerated. **Cover the whole gap.**

## Fetching runtime state (use the diag-data skill)

This skill is a **consumer** of `diag-data` and `git-actions`. Follow
those skills for the transport mechanics; this section lists the
specific pulls health-review needs.

**Required pulls (live VM, via `vm-diag-request` issue or direct HTTP):**

| Pull | Path | Use |
|---|---|---|
| Audit tail | `audit?limit=600` | ticks / `*_eval` signals / monitor events; filter to the window |
| Order packages | `journal?table=order_packages&limit=100` | signal→order plumbing only (NOT scoring) |
| Trades | `journal?table=trades&limit=100` | order→trade plumbing only (NOT scoring) |
| Status | `status` | heartbeat + status.json + `vm_health` (cpu/mem/disk) |
| Services | `services` | `systemctl is-active` per allowlisted unit |
| Older windows | `journalctl?unit=ict-trader-live.service&since=<iso>&until=<iso>` | page back across a long gap |
| Health snapshot — latest | (HTTP) `GET /api/bot/health/latest` via the `vm-health-snapshot-fetch` flavour of the diag relay, OR ride a direct call to `/api/bot/health/latest` when configured | the most-recent cron health snapshot the trader wrote |
| Health snapshot — history | `GET /api/bot/health/history?hours=N` | newest-first list of snapshots in the window |
| Health services | `GET /api/bot/health/services` | systemd state of `ict-trader-live` + `ict-web-api` |

**Batch these into ONE `vm-diag-request` issue, not nine.** Per the
`diag-data` skill's default pattern (MB-20260706-CI-MINUTES — every
relay issue is its own billed Actions job, and this repo hit its 2,000
min/month cap opening 427 issues in 5.5 days), open a single issue with
the body as a JSON array (or one path per line) covering every row of
this table you actually need this run, e.g.:
```json
["audit?limit=600", "journal?table=order_packages&limit=100",
 "journal?table=trades&limit=100", "status", "services",
 "api/bot/health/latest", "api/bot/health/history?hours=24", "api/bot/health/services"]
```
(The health-snapshot endpoints live under `/api/bot/…`, so their relay
paths MUST carry the `api/bot/` prefix — the bare `health/latest` form
returns `{"error":"fetch_failed"}` because the relay only resolves
un-prefixed paths under `/api/diag/*`. Root-caused 2026-07-13,
BL-20260712-HEALTH-RELAY-FETCH-FAILED.)

The `vm-diag-snapshot` workflow fetches all of them over one ssh session
and posts one combined comment (`## <path>` per result). Only fall back
to separate single-path issues for a path you need to re-fetch later in
the review (e.g. a follow-up `journalctl` window after seeing the first
batch's results).

**Trainer VM (light touch only — service health):**

Open a `trainer-vm-diag-request` issue with:

```
cmd: |
  systemctl is-enabled ict-trainer.service; systemctl is-active ict-trainer.service
  systemctl is-enabled ict-trainer.timer;   systemctl is-active ict-trainer.timer
  systemctl show ict-trainer.service --property=ExecMainStatus,ActiveEnterTimestamp,ActiveExitTimestamp
```

That's all health-review needs. **Do not** also pull
`training_cycle.jsonl`, `python -m ml list-models`, `dataset_builds.jsonl`,
or registry data — those belong to `/ml-review`. Pulling them here just
inflates the issue comment.

**On relay failure:** if the live relay returns curl exit 7, fire
`vm-web-api-recover` and retry once. If it still fails, downgrade
gracefully — emit the review with `api_errors` = concern,
`operator_attention_required: true`, and a note that the live pull
couldn't be performed. **Never fabricate findings.**

## The health report

The cron `health-snapshot.yml` writes per-run snapshots to
`artifacts/health/health_check_<TS>.json` on the live VM and surfaces
them through the `/api/bot/health/*` endpoints. Read both the
**latest** snapshot (current state) and the **history** for the window
(trend), then:

- Cross-check the snapshot's findings against your own diag pulls — if
  the snapshot says `heartbeat: ok` but your `status` pull shows a
  stale `heartbeat.txt`, the snapshot is lying and that's itself a
  `concern` (snapshot generator broken).
- For every `concern` in the snapshot history that has NOT recovered by
  the latest snapshot, surface it as an open anomaly.
- For repeating-and-recovering issues (e.g. transient API errors every
  few hours), grade `watch` not `concern` and note the cadence.

If `/api/bot/health/latest` returns `{present: false}`, the cron
generator hasn't run since this artifact dir was last cleared — note
it as `health_snapshot: watch` (not concern; the live diag pull is
the canonical view, the snapshot is a convenience).

## Pipeline rubric

Beyond freshness counts, judge **plumbing quality** across the window:

- **Signal → order plumbing.** Every actionable signal in the audit
  tail should produce an `order_packages` row within seconds. Gaps →
  `orders` concern.
- **Order → trade plumbing.** Every filled order should have a
  `trades` row. Orphans (filled order, no trade; trade, no parent
  order) → `trades` concern.
- **Side / size sanity.** Spot-check 3–5 orders: side matches signal
  direction; qty within the per-account `pos_size` cap in
  `config/accounts.yaml`; no absurd leverage. (This is a plumbing
  check, not a strategy-quality check — strategy quality is
  `/performance-review`.)
- **SL/TP wiring.** Each order should carry SL+TP metadata. Missing →
  `watch`; systematic absence → `concern`.
- **Repeated rejections.** Consecutive `failed_exchange` /
  `failed_risk_gate` / `borrow_unavailable` on one symbol → `orders`
  concern (something upstream wedged).
- **Monitoring cadence.** `run_monitor_tick` events on the documented
  cadence; long gaps → `monitoring` concern.
- **Strategy silence.** Every strategy enabled in
  `config/strategies.yaml` should emit per-tick `*_eval` events. An
  enabled strategy with **zero `*_eval`** for > 1h of an active session
  → `strategy_silence` concern. **`execution: shadow` strategies still
  run and still emit `*_eval`** — the silence check applies to them
  but trade-row checks do not.
- **State consistency.** For each account, YAML `mode` vs runtime
  `live` field in `runtime_status.json`. Drift → `state_consistency`
  concern.
- **Alert delivery.** Confirm the `AlertsQueue` is drained — known-trip
  events with no accompanying drain log → `alert_delivery` concern
  ("alerts queued, drainer silent — operator unnotified").

Status grades: `ok` (nothing to flag) / `watch` (bounded anomaly) /
`concern` (operator should look ⇒ `operator_attention_required: true`).
Overall: `healthy` (all ok) / `caution` (≥1 watch, no concern) /
`investigate` (any concern).

## Trainer service touch

Grade exactly one dimension: `trainer_service` ∈
`ok | watch | concern | skip`. `ok` when the timer is enabled+active
and the unit has not died with non-zero `ExecMainStatus`. Everything
else about the trainer (models, datasets, registry, training metrics)
is **`/ml-review`**'s job — do not duplicate it here.

The trainer is not a live-trading blocker. Don't set
`operator_attention_required` on a trainer-only issue unless an
`advisory`+/`live_approved` model is involved (and even then, that
finding belongs to `/ml-review` — this skill just notes "trainer
service stale, see /ml-review").

## Broker-account reachability (MANDATORY — 2026-06-29)

A supposed-to-be-live broker account reading **unreachable** (IB gateway
logged out, exchange API 401-ing, creds rotated out) is a money-at-risk
condition that must surface as a **loud, standalone flag** — not a line
buried in the report body. This section exists because the IB gateway was
in fact dark across one or more reviews and went unflagged.

**Scope — all declared-live, non-shelved accounts.** Check every account
with `mode: live` on a probeable exchange (`bybit` / `interactive_brokers`
/ `alpaca` / `oanda`). This excludes the intentionally-shelved dry accounts
(`ib_live` 2FA-blocked, `oanda_practice`) and the API-less `breakout_1`
prop bridge — the same set the in-process latch checks.

**How to read reachability** (any one is sufficient evidence of down):

- `GET /api/diag/exchange_positions` — per-account `positions: null` ⇒
  could-not-read (down); `[]` or a list ⇒ reachable.
- the in-process latch state file
  `runtime_logs/account_reachability_alert_state.json` (pull via
  `/api/diag/log_file` or read `account_reachability_alert.down_accounts()`)
  — any account with `down: true` is currently latched down.
- `GET /api/bot/accounts/balances` — `api_ok: false` for a live account.

**What to do when an account is down:**

1. It is a **MANDATORY** entry in the response's flags / a standalone
   high-priority Claude-channel ping (the in-process latch already pings
   Telegram on the cross-into-down; the review must ALSO surface it so a
   review run is never the thing that quietly skips it).
2. Recommend the fix inline: IB → `vm-ib-gateway-recover`; otherwise →
   check broker API/creds. If the down state is sustained, this is exactly
   the trigger to open/continue a remediation pass in THIS session.
3. Drive it — don't just note it. A live account dark for the whole window
   is a `concern`-grade finding with `operator_attention_required` set.

If all live accounts are reachable, say so explicitly ("all N live
accounts reachable") — an empty reachability finding must be a stated
verification, never an omission.

## DB integrity & validity

- **`db_integrity`** — the diag relay can't run `PRAGMA
  integrity_check`, so grade from journal recency + counts:
  `age_seconds` of the newest `trades`/`order_packages` row should be
  small during active sessions (hours-stale while signals fire →
  `concern`); table totals non-decreasing run-over-run (a drop →
  truncation/restore `concern`); large `-wal` with a small main DB →
  `watch`. Note "integrity_check not fetched (relay can't run PRAGMA)".
- **`data_validity`** — values are *sane*, not just present: no
  negative `position_size`/`pnl` where impossible, no null in required
  columns, timestamps monotonic (`opened_at ≤ closed_at`), closed
  trades carry an `exit_reason` + `pnl`, and net positions reconcile
  with open rows. Bad values → `watch`; systemic corruption signals →
  `concern` with `operator_attention_required: true`. Use the
  `db-wiring` skill's checks as the reference.
- **`db_write_path_integrity`** (Phase-4 guardrail) — run
  `scripts/check_db_integrity.py` (read-only, `mode=ro`) for the INV-1..5
  write-path invariants from
  `docs/audits/dashboard-truth-and-persistence-2026-06-16.md`. It separates a
  RECENT regression (`recent_count > 0` ⇒ `alert` ⇒ a live write-path bug, e.g.
  a row that just closed without `closed_at`/`pnl`/`account_class` or with a
  broken package link) from the LEGACY pre-backfill backlog (`total_count` only
  ⇒ informational, the P1-E backfill clears it). Grade any `recent_count > 0`
  as `concern`; legacy-only as `watch`/note. The hourly `ict-db-integrity.timer`
  already pings `[WARN] DB integrity: …` on a recent regression — cross-check
  that the alert fired. (Pull it via the diag relay or, when configured, run
  the checker directly against the live DB.)

## Sprint-doc review

Read the sprint logs under `docs/sprint-logs/` created since the last
review (newest few). For each, sanity-check: does it follow the
canonical template (`sprint-format` skill), does it report verified
reality rather than intent, and does any claim contradict a canonical
doc or the live state you just pulled? Record issues in
`sprint_doc_review[]` with severity `nit | drift | contradiction`. A
`contradiction` against a canonical doc is fixed in-place (Tier-1) or
logged to the backlog — never walked past.

## Compliance audit rotation (2026-06-02)

One repo section per review, rotated by day-of-week so the full repo is
audited against the **current** canonical rules
(`docs/CLAUDE-RULES-CANONICAL.md` § Generation Discipline) over a week.
This is the enforcement loop for Rule 2 (precedents-not-authoritative):
artifacts drift as rules evolve, and this rotation is how the drift
gets surfaced and queued for fix.

Pick the section by `weekday`:

| Weekday | Section |
|---|---|
| Mon | `docs/runbooks/` |
| Tue | `.github/workflows/` + `scripts/ops/` |
| Wed | `.claude/skills/` |
| Thu | `config/` |
| Fri | `src/units/accounts/` (broker integrations) |
| Sat | `src/runtime/` + `src/core/` |
| Sun | `src/units/strategies/` + `ml/` |

For each artifact in the day's section:

1. Run the bright-line scan from `before-asking-the-operator` (operator
   instructions that should be runner-dispatched) and
   `credentials-and-vm-mutations` (operator-attributed VM/credential
   work that should route through `sync-vm-secrets` or
   `system-actions`).
2. Cross-check against any rule in `docs/CLAUDE-RULES-CANONICAL.md`
   that the artifact category is subject to (tier, autonomy mandate,
   prime directive, generation discipline, ship-autonomously rule).
3. Per Rule 2 of Generation Discipline:
   - **Compliant** → no action.
   - **Non-compliant + the review session is shipping a fix for the
     containing system** → fix in the same PR.
   - **Non-compliant + non-blocking** → log to
     `docs/claude/health-review-backlog.json` with the artifact path,
     the specific rule it violates, the bright-line phrase or pattern
     observed, and a one-line suggested fix.

The audit findings appear in `compliance_audit` in the response JSON:

```json
"compliance_audit": {
  "section": "docs/runbooks/",
  "artifacts_scanned": 16,
  "findings": [
    {
      "artifact": "docs/runbooks/ib-integration.md",
      "rule": "before-asking-the-operator",
      "pattern": "operator-attributed systemd edit at line 87",
      "severity": "drift",
      "logged_to_backlog": "BL-20260603-001"
    }
  ]
}
```

This rotation does NOT touch artifacts outside the day's section — the
weekly cycle is the coverage guarantee, not a per-session full sweep.

## New-work compliance audit (MANDATORY — 2026-07-19)

Operator directive: **everything built since the last review gets audited
for compliance with the repo's rules.** The static rotation above catches
drift in *unchanged* artifacts as the rules evolve (a coverage guarantee
over a week); this sweep is its complement — it catches non-compliance in
*new* work **at the moment it lands**, so a rule-breaking change can't sit
merged-and-unreviewed until its file's rotation day comes around. Both run
every review; they are not substitutes.

**What to audit — the delta since the last review:**

1. **Every PR merged to `main` in the window** — enumerate via
   `mcp__github__list_pull_requests` (state `closed`, newest-first, filter
   to `merged_at` inside the window) or `git log --merges --since=<window>`.
2. **Every commit landed on `main`** not covered by a PR (direct Tier-1
   commits) — `git log origin/main --since=<window>`.
3. **Any live-VM / workflow / config mutation** in the window — new or
   edited `.github/workflows/**`, `system-actions` allowlist changes,
   systemd unit/timer additions, `.env`/cgroup changes applied via the
   relays (cross-check the `system-action` issues you saw this run).

**Audit each change against the canonical ruleset** (`CLAUDE.md` +
`docs/CLAUDE-RULES-CANONICAL.md`) — the bright lines:

- **Permission tiers.** Classify the change's tier from its diff. A **Tier-2**
  change (runtime / deploy / order-path / service / timer / DB-writeback /
  data-mutation) must show an **operator OK in chat** before it shipped; a
  **Tier-3** change (strategy logic/params, risk caps/sizing, account-mode flip,
  live promotion, or any edit to `config/strategies.yaml` / `config/accounts.yaml`
  / `config/risk_caps.yaml` / `src/runtime/orders.py` / `src/runtime/risk_counters.py`
  / a live-consumed unit file) must show **explicit operator approval** on the PR
  before merge. A Tier-2/3 change merged **without** the required approval is a
  `concern` + `operator_attention_required`.
- **Prime Directive.** No new **auto-flip / breaker that toggles `mode:`**, no
  **"safety" default that goes dry on boot**, and — the recurring one — **no new
  default-off `*_ENABLED` flag in front of a *required* capability** (the pattern
  that stranded MES and regressed the netting guard). A new capability gate must
  be a declared, default-**permissive** switch (or a `*_MODE` observe→apply
  ladder), never a default-off enable. Account-mode writes only via
  `set-account-mode`.
- **Generation Discipline (Rule 1 + 2).** Did the change derive from the matching
  **skill** (skill-first) rather than copy a precedent? Did it replicate a
  non-compliant precedent's shape? A new operator-facing runbook / workflow /
  instruction that attributes work to the operator which a runner could do
  (bright-line phrases from `before-asking-the-operator` /
  `credentials-and-vm-mutations`) is a violation.
- **Honesty.** Does the PR/commit claim work **verified** that the diff or the
  live state contradicts (e.g. "verified live" with no evidence, a green-CI
  claim that didn't run, a "resolved" that didn't land)? Cross-check load-bearing
  "verified/deployed/live" claims against the runtime state you already pulled.
- **Field-beats-comment + canonical-doc-coherence.** Did a change flip a
  YAML field/config constant on inference against a surrounding note, or leave a
  canonical doc contradicting the new reality?

**What to do with a finding:**

- **Compliant** → no action (still counted in `audited` so the coverage is
  legible).
- **Non-compliant, still live / unmerged-reversible** → a `concern`-grade,
  can't-miss finding: set `operator_attention_required`, name the PR + the exact
  rule, and recommend the concrete remediation (revert, add the missing approval
  gate, convert the `*_ENABLED` to permissive, fix the stale doc). If it's a
  Tier-1 doc/backlog fix you can make, make it.
- **Non-compliant but low-risk / already-past** → log to
  `docs/claude/health-review-backlog.json` with the PR, the rule, and a
  one-line fix, and note it in the output.

Record the result in `new_work_compliance` in the response JSON: the window's
`prs_audited` / `commits_audited` count and one `findings[]` entry per
non-compliant change (`{ref, tier, rule, severity, disposition}`). **A clean
sweep is a stated negative** — `findings: []` + an explicit note ("N PRs / M
commits audited since <window>, all tier-appropriate + rule-compliant"), never
an omission. This is compliance auditing of *what was built*, NOT a code-quality
review (that stays with `review` / `security-review`).

## Orphan-events ingest (orphan is NEVER a resting status)

Operator directive (2026-06-24): an orphan trade row is a **red flag to be
reconciled**, not a status to accept. The trader writes one JSON line per
orphan-row creation to `runtime_logs/orphan_events.jsonl`
(`execution_diagnostics.enqueue_orphan_created_flag`: `account`, `symbol`,
`side`, `trade_id`, `origin`, `ts`) and fires a CRITICAL "initiate a
/system-review" Telegram red-flag at the same time.

Every health-review (and the master /system-review) MUST:

1. **Pull the tail** since the last review — `diag log_file?name=orphan_events`
   (relay) or the live VM file. Also cross-check the DB: any `trades` row still
   carrying an orphan marker (`setup_type='adopted_orphan'` /
   `strategy_name='orphan_adopt'`, or `status='orphaned'`) — query via the Data
   Explorer (`/api/bot/db/table/trades?filter_col=setup_type&filter_op=eq&filter_val=adopted_orphan`).
2. **For each orphan not already tracked**, append a `BL-…` item to
   `docs/claude/health-review-backlog.json` (origin, account/symbol, trade_id,
   the reconcile target if recoverable) so it is durably tracked — and **drive it
   to resolution**: reconcile to its real trade/order package, or, only after
   exhausting that, mark it explicitly `unreconciled` (never leave it resting as
   `adopted_orphan`).
3. **Flag loudly** in the review output if any orphan persisted unreconciled
   across the window — that is a standing failure of the no-resting-orphan
   invariant, not a routine item.

## Security-breach check (MANDATORY)

A live trading system is a money-at-risk target. Every review MUST actively
look for a **detected breach / intrusion signal since the last review** and
state the result — a clean check is a *stated verification*, never an
omission. This is **breach DETECTION on the surfaces this skill reaches**,
NOT a code-vulnerability audit — deep code/dependency security review stays
with the `/security-review` skill (route there if a signal points at code).

**Sweep these sources for the window:**

- **GitHub secret scanning** — `mcp__github__run_secret_scanning` (or the
  secret-scanning alerts list). Any NEW exposed-credential alert is a breach
  signal (a leaked key can be used against the brokers/VMs).
- **External / non-collaborator activity** — the repo is **public** with the
  "limit to repository collaborators" interaction limit + the
  `external-comment-alert.yml` auto-hide/alert workflow. Check for any
  comment / issue / PR / fork-push from a non-owner, non-collaborator actor,
  and confirm `external-comment-alert.yml` actually fired if one appeared.
- **Repo/supply-chain mutations** — unexpected new collaborators or deploy
  keys, changed/added repo **Actions secrets**, force-pushes to `main`, new
  branches/tags you can't attribute, and especially **edits to
  `.github/workflows/**`** (a workflow edit is a code-execution + secret-exfil
  vector). Cross-check against the session's own known changes.
- **Actions anomalies** — workflow runs triggered by an unexpected actor, or
  a spike/burst that doesn't match Claude-session or scheduled activity.
- **VM host signals (via the diag relay)** — `journalctl` for
  `sshd`/auth (failed-then-succeeded logins, new sessions/users), unexpected
  new systemd units / timers / cron, unexpected listening ports or processes,
  and host-agent tampering of the `/dev/null` clobber class. The 2026-06-28
  intrusion audit (`BL-20260628-SEC-HARDENING-FOLLOWUPS`) and the `/dev/null`
  investigation are the standing precedents — reconcile any new signal against
  them before calling it novel.

**What to do:**

1. **Any confirmed breach signal is a can't-miss, standalone finding** —
   `security` = `concern`, `operator_attention_required: true`, and a
   **high-priority** Claude-channel ping. Never bury it in the body.
2. **Drive containment** where you can (hide an external comment, open the
   fix issue, route a code-vuln to `/security-review`), and tell the operator
   the exact credential/action they must own (rotate key X, revoke deploy key).
3. **If nothing is found, say so explicitly** — e.g. "no breach detected
   since <window>: 0 secret-scanning alerts, 0 external actors, no workflow /
   secret / collaborator changes, auth log clean." That stated negative is
   the deliverable; an empty `security` finding is a review failure.

Grade `security` ∈ `ok | watch | concern`. `watch` for an unconfirmed /
low-signal anomaly worth a second look; `concern` (⇒ operator attention) for a
confirmed breach or exposed credential.

## Soak decisions due — surface, don't decide

Operator directive: **anything whose soak has reached a promotion/demotion
gate must be SURFACED here** so a met gate never sits un-actioned between the
deeper reviews. This skill **flags the decision as due and routes it** — it
does **not** compute the recommendation (that rubric lives in `/ml-review`
for models and `/performance-review` for strategies; see Out of scope).

**Check each soak's gate state for the window:**

- **Shadow models** — `/api/bot/shadow/stats` (+ `/drift`): days-in-shadow,
  prediction volume, the "wired" check, drift verdict. A model that has
  accrued its soak volume + time with stable drift is a **promotion decision
  due**; one drifting or degenerate (e.g. all-zero scores) is a **demotion /
  hold decision due**.
- **Strategies** — `/api/bot/strategies/{name}/review` M7 packets: any packet
  whose action badge is `PROMOTE` / `DEMOTE_SHADOW` / `KILL` is a decision
  due (especially with a Tier-3 SLA due-by that has passed).
- **Observe-only soaks that graduate on a gate** — exit-ladder, fc-geometry,
  allocator, conviction-sizing/arbitration, exit-lever, news-influence. If one
  has hit the row-count / evidence bar its design names for graduation, flag
  that its Tier-3 graduation call is due.

**For each gate met:** surface `{what, gate_met, owner_skill, sla_state}` in
`soak_decisions_due[]` — do NOT write the promote/demote recommendation.
**Escalate loudly** if a gate has been met and sat un-actioned across ≥2
reviews (a stalled decision is exactly what this section exists to catch).
**If nothing has reached a gate, state "no soak decisions due."**

## Draining the backlog — a HARD COMPLETION GATE (not a sample)

**A health-review is NOT complete until every open item in
`docs/claude/health-review-backlog.json` has been triaged THIS run.**
Triaging "the recent few", "the ones I touched", or a sample is a
**review failure** — the backlog IS the standing open-task list, so a
review that leaves open items unlooked-at has not done its core job.
(`/performance-review` and `/ml-review` own their own backlogs — do not
touch those here; but each of the three enforces this same gate on its
own list.)

**The procedure — enumerate the FULL open set, then walk it 100%:**

1. **Count first.** Load the file, filter to every item whose `status`
   is not a terminal-resolved value (`resolved`/`closed`/`done`/`fixed`/
   `wont_fix`/`invalid`/`superseded`). Record `open_at_start`. This is
   your denominator — you must touch every one.
2. **For EACH open item** (all of them, oldest to newest):
   - **Re-validate against current live state.** Does its trigger still
     apply? Cross-check it against the diag pulls / DB / services you
     already fetched this run (e.g. a "gateway wedge at 06:00Z" item
     against the `ib_state` you pulled; a "devnull clobbered" item
     against whether this session's operator-actions actually ran; a
     "trainer stale" item against the trainer relay). **Drive a
     verification pull if one cheap check resolves it** — don't leave an
     item "unverified" when a single relay call would settle it.
   - **Disposition it into exactly one bucket:**
     - **resolved** — this session verified it fixed / no longer
       reproduces. Mark `resolved` + a verification note.
     - **fixed-now** — an in-scope Tier-1 fix (a doc this skill may
       write, a workflow/relay-allowlist edit, the backlog file itself,
       a CLAUDE.md correctness fix). Make the fix, mark `resolved`.
       (A change to `src/`, `config/`, or any Tier-2/3 file is NOT
       fixed here — it goes to `kept-open` + `recommended_action`, even
       for a comment-only edit to a Tier-3 file.)
     - **stale/invalid** — the condition is gone or the item was wrong.
       Mark `invalid`/`superseded` with the reason.
     - **kept-open** — genuinely still open (needs a Tier-2/3 code
       change, an operator decision, a soak to mature, or future work).
       Keep it, but **add an update** with this run's re-validation
       result + the current blocker, so it never sits stale-and-unlooked.
3. **Write it back.** Edit the backlog file: statuses updated, notes/
   updates appended. Record EVERY item's disposition in the response's
   `backlog_drain[]` (one entry per open item — the array length equals
   `open_at_start`).

**Coverage assertion (the gate).** Emit
`backlog_coverage: {open_at_start, triaged, resolved, fixed_now,
closed_stale, kept_open, count_untriaged}` in the response.
**`count_untriaged` MUST be 0** and `triaged` MUST equal `open_at_start`.
If they don't, the review is INCOMPLETE — do not post the completion
ping or call the review done; finish the drain first. The Claude-channel
ping MUST cite `X/Y backlog items triaged, Z resolved`.

## Posting to the Claude channel

Every health-review run ends with a **one-line update to the Claude
channel** (`@claude_ict_comms_bot`), per
[`docs/claude/telegram-pings.md`](../../docs/claude/telegram-pings.md).

**Primary path — `send-ping` system-action (use this).** Open a
`system-action`-labelled GitHub issue:

```
action: send-ping
target: claude
priority: normal      # or 'high' if operator_attention_required
message: /health-review — <overall_assessment>: <one-line summary>. <N> concerns, <M> watches. <recommended_action or "no action">.
```

The `system-actions` workflow SSHes to the VM and runs
`scripts/ops/send_ping_action.sh`; the bridge drains within ~5s.
Latency: ~30–60s, no git push needed. Full contract:
`docs/claude/system-actions.md` § `send-ping`.

**Fallback path — `pending-pings.jsonl`.** Only if the issue path is
unavailable: append a line to `docs/claude/pending-pings.jsonl` and
commit. The VM git-sync timer picks it up within ≤5 min. Hash-based
dedup prevents re-fires.

The ping is a status beacon, not the review itself — keep it ≤200
chars, cite the overall grade + concern count, and point the operator
at the response JSON (in chat) for detail.

## Output

Emit a single JSON object conforming to
`comms/schema/health_review_response.template.json`. The narrowed
shape (post-2026-05-26 split):

- `findings.*` — pipeline + DB + service dimensions only (no
  `trainer_models`), **including the mandatory `security` dimension**
  (§ "Security-breach check").
- `security_check` — the breach-sweep result: `{status, sources_checked[],
  signals[], note}`. `signals` empty + an explicit clean `note` is the
  required stated-negative when nothing is found.
- `soak_decisions_due[]` — soaks that hit their gate this window
  (`{what, gate_met, owner_skill, sla_state}`), SURFACED not decided
  (§ "Soak decisions due"). Empty ⇒ state "no soak decisions due".
- `new_work_compliance` — `{prs_audited, commits_audited, findings[], note}`
  from the MANDATORY new-work audit (§ "New-work compliance audit"). Each
  finding `{ref, tier, rule, severity, disposition}`. `findings: []` + an
  explicit clean `note` is the required stated-negative.
- `sprint_doc_review[]`.
- `backlog_drain[]` — one entry per OPEN item (array length ==
  `open_at_start`); the full-triage record (§ "Draining the backlog").
- `backlog_coverage` — `{open_at_start, triaged, resolved, fixed_now,
  closed_stale, kept_open, count_untriaged}`. The completion gate:
  `count_untriaged` MUST be 0. A review with `count_untriaged > 0` is
  incomplete and must not be reported as done.
- `anomalies[]` — free-form notable items.
- `recommended_action` + `operator_attention_required`.

`trade_decision_grades[]` and `model_status[]` are **removed** from
this skill's output — they live in `/performance-review` and
`/ml-review` respectively.

Set `reviewed_at` to now (UTC ISO-8601), `reviewer` to `claude`. Each
`note` ≤120 chars, citing specifics (counts, ages, symbols/qtys) so
the operator can verify fast.

## What you DO write (and what you don't)

**Write:**
- Edit `docs/claude/health-review-backlog.json` to drain it.
- Fix Tier-1 doc contradictions surfaced by the sprint-doc / backlog
  pass.
- Append the Claude-channel ping (via `send-ping` system-action, or
  fallback `docs/claude/pending-pings.jsonl`).
- The read-only diag-trigger issues (`vm-diag-request`,
  `trainer-vm-diag-request`, `vm-web-api-recover`) — they auto-close.

**Do NOT:**
- Touch `src/`, `config/`, or any live-path file. Reviews don't trade.
- Append to `comms/claude_strategy_scores.jsonl` (that belongs to
  `/performance-review` now).
- Modify `docs/claude/performance-review-backlog.json` or
  `docs/claude/ml-review-backlog.json` (those belong to their
  respective skills).
- Modify `comms/follow_ups.json` (deferred until the comms cleanup
  session).
- Ask the operator to paste/download/SSH a snapshot — autonomy-mandate
  failure. Pull it yourself.
- Ask scoping questions — the scope is fixed (this file).

## If the relays are unreachable

The only legitimate stop condition. If the live diag relay fails even
after a `vm-web-api-recover` retry, emit the partial review with
`api_errors` = concern, `operator_attention_required: true`, and a note
that the live pull couldn't be performed — and still drain the backlog
+ do the sprint-doc review + post the Claude-channel ping (those are
repo-local and don't need the VM). Do not synthesize live findings
without evidence.
