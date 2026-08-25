"""S-051 — Read-only diagnostic endpoints for off-VM Claude / operator scripts.

Token-gated by ``DIAG_READ_TOKEN``. GET-only. Never returns secret material.
The PM-side / web-sandbox session has no mutation authority on the VM by
design — see ``docs/claude/vm-operator-mode.md`` § 9.

Allowlists for tables, systemd units, and log files are hard-coded at module
load. There is no path-traversal or arbitrary-SQL surface: callers pass an
alias which the server resolves via a static mapping. The sqlite connection
is opened with ``mode=ro`` so a downstream bug introducing UPDATE/DELETE
would still fail at the driver level.

Failure modes:
- 503 ``diag_disabled`` if ``DIAG_READ_TOKEN`` is unset (feature off).
- 401 ``missing_token`` / ``invalid_token`` on bad bearer.
- 400 ``unknown_<thing>`` on requests outside the allowlists.
- 503 ``journal_unavailable`` on a structural sqlite3.Error inside
  ``_journal_select`` (S-067 — was previously a silent ``[]``).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from src.runtime.exit_loop_health import (
    STATE_FILE_NAME as EXIT_LOOP_HEALTH_STATE_FILE,
)
from src.utils.paths import runtime_logs_dir, trade_journal_db_path
from src.web.api._account_read_executor import run_account_read
from src.web.runtime_status import _resolve_git_sha

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diag", tags=["diag"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(trade_journal_db_path())
# Every runtime-log reader resolves through runtime_logs_dir() so DATA_DIR /
# RUNTIME_LOGS_DIR overrides match the writers (heartbeat.py,
# signal_audit_logger.py, runtime_status.py). The 2026-05-11 silent
# freeze (trader wrote /data/bot-data/runtime_logs/heartbeat.txt while
# diag read /home/ubuntu/ict-trading-bot/runtime_logs/heartbeat.txt) is
# the canonical incident this PR (T2) closes.
_AUDIT_LOG = runtime_logs_dir() / "signal_audit.jsonl"
_BOT_LOG = _REPO_ROOT / "bot.log"
_HEARTBEAT = runtime_logs_dir() / "heartbeat.txt"
_STATUS_JSON = runtime_logs_dir() / "runtime_status.json"
_IB_STATE_JSON = runtime_logs_dir() / "ib_state.json"

_JOURNAL_TABLES: dict[str, str] = {
    "order_packages": "datetime(updated_at)",
    "trades": "id",
    # 2026-05-29 — M13 AI-analyst tables. Before this, the insights cache
    # had NO read path on the /api/diag/* relay: the cache files
    # (runtime_logs/insights/*.json) aren't in _LOG_FILES, the generator
    # units weren't in _CANONICAL_UNITS, and /api/bot/insights/* lives
    # outside /api/diag/* so the relay can't reach it. A relay-only review
    # session (e.g. /performance-review's M13 cross-check) was therefore
    # blind to the analyst's output AND to whether the generator was even
    # alive. Exposing these two tables (both keyed by autoincrement id) lets
    # a session read the analyst's history + spend via journal?table=...
    # and confirm the generator is writing. Read-only, no secrets.
    "insights_history": "id",
    "insights_usage": "id",
}

_CANONICAL_UNITS: tuple[str, ...] = (
    # NB: the retired pre-rename trader unit "ict-bot.service" was removed here
    # (2026-06-28 full-system audit) — the live trader is ict-trader-live.service
    # (below). ict-bot.service has no deploy/ file and is not installed, so its
    # presence only made /api/diag/services perpetually report a not-found unit.
    # Do not re-add it.
    "ict-trader-live.service",
    "ict-web-api.service",
    # 2026-08-13 (BL-20260813-CADDY-HTTPS-TRANSPORT-UNDOCUMENTED-AND-UNWATCHED).
    # The HTTPS front for the Svelte SPA: ict-bot.duckdns.org ->
    # reverse_proxy localhost:8001 (deploy/caddy/Caddyfile, installed by
    # scripts/ops/install_caddy.sh). /ws/market streams WSS through it.
    #
    # THIS ENTRY IS HAND-MAINTAINED AND NO GUARD PROTECTS IT. Every other
    # unit here is cross-checked by scripts/check_diag_unit_allowlist.py,
    # but that guard globs deploy/*.service + deploy/*.timer and caddy
    # ships NO unit file of ours (it comes from the Caddy apt package), so
    # caddy.service is outside the guard's scan entirely -- it is neither
    # flagged as uncovered nor flagged as stale if this line is deleted.
    # That invisibility is exactly why it went unwatched: a Caddy outage
    # takes the SPA + WSS down while Streamlit (which calls the API
    # server-side over plain HTTP) stays green, so nothing else reports it.
    # Do not remove without giving the SPA transport another liveness
    # surface first.
    "caddy.service",
    "ict-telegram-bot.service",
    # NB: the retired daily-digest unit "ict-heartbeat.service" was removed here
    # (2026-07-26 full-system audit, WS-B). The daily operator digest was retired
    # 2026-07-08 (notification streamlining) — its timer is in _RETIRED_TIMERS in
    # scripts/install_systemd_units.sh and is never re-enabled, so the service is
    # dead/inactive on the VM. Keeping it in this allowlist only made
    # /api/diag/services perpetually report a retired-and-inactive unit as though
    # it were a monitored one. Superseded by ict-hourly-snapshot (below). Do not re-add.
    "ict-git-sync.service",
    "ict-git-sync.timer",
    # 2026-05-11 — external liveness watchdog (PR #950). Both the
    # oneshot service and its driving timer need to be queryable so
    # the operator (and Claude sessions) can verify the dead-man
    # switch is firing on cadence and inspect its decisions.
    "ict-liveness-watchdog.service",
    "ict-liveness-watchdog.timer",
    # 2026-05-28 — IB Gateway auto-heal watchdog (BL-20260527-003). The
    # oneshot + its driving timer, queryable so a session can verify the
    # MES dead-man switch is enabled and firing on cadence (and read its
    # probe/restart decisions) — same rationale as the liveness-watchdog
    # pair above.
    "ict-ib-gateway-watchdog.service",
    "ict-ib-gateway-watchdog.timer",
    # Web-API self-heal watchdog. The oneshot + its driving timer, queryable
    # so a session can verify the ict-web-api.service dead-man switch is
    # enabled and firing on cadence (and read its probe/restart decisions) —
    # same rationale as the watchdog pairs above.
    "ict-web-api-watchdog.service",
    "ict-web-api-watchdog.timer",
    # 2026-06-17 — DB-integrity checker (dashboard-truth Phase 4). The hourly
    # oneshot + its driving timer, queryable so a session can verify the
    # "alert us when intake breaks" check is enabled and firing on cadence
    # (and tail its WARN/CRITICAL decisions) — same rationale as the watchdog
    # pairs above.
    "ict-db-integrity.service",
    "ict-db-integrity.timer",
    # 2026-05-29 — the Claude update-channel drainer (@claude_ict_comms_bot).
    # It is the SOLE consumer of runtime_logs/pending_claude_pings, but was
    # never queryable from the diag surface, so when the channel went silent
    # (operator received no pings) there was no read path to see whether the
    # bridge was active or what its send errors were. Adding it here makes
    # `/api/diag/services` report its state and `/api/diag/journalctl?unit=
    # ict-claude-bridge.service` tail its journal (the unit now logs to
    # journald — see deploy/ict-claude-bridge.service).
    "ict-claude-bridge.service",
    # 2026-05-29 — M13 AI-analyst generator (fast tier every 15 min) + its
    # per-strategy slow tier (every 60 min) and their driving timers. These
    # are the SOLE writers of the insights cache + insights_history/usage
    # tables, but were never queryable from the diag surface — so when
    # /performance-review's M13 cross-check found the cache unreadable, there
    # was no read path to tell whether the generator was alive, stale, or
    # erroring. Adding them makes `/api/diag/services` report state and
    # `/api/diag/journalctl?unit=ict-insights-generator.service` tail the
    # generator log (cadence, budget skips, API errors).
    "ict-insights-generator.service",
    "ict-insights-generator.timer",
    "ict-insights-generator-strategies.service",
    "ict-insights-generator-strategies.timer",
    # 2026-06-13 — the hourly + daily reporter oneshots and their timers.
    # ict-hourly-snapshot is the SOLE writer of runtime_logs/balance_snapshots.json
    # (the dashboard + risk-gate account-balance view); ict-health-snapshot is the
    # SOLE writer of the artifacts/health/* cron snapshots. Both were invisible on
    # the diag surface, so when ict-hourly-snapshot's balance write silently diverged
    # to the repo path at the data-dir migration, the stall hid for ~3 weeks with no
    # read path to catch it (BL-20260611-M15-2). Making them queryable lets a session
    # verify the writer is firing on cadence and tail its journal for errors — same
    # rationale as the watchdog / bridge / insights pairs above.
    "ict-hourly-snapshot.service",
    "ict-hourly-snapshot.timer",
    "ict-health-snapshot.service",
    "ict-health-snapshot.timer",
    # 2026-06-28 (full-system audit Workstream B) — two recurring trader-VM
    # timers that were installed + enabled by scripts/install_systemd_units.sh
    # but missing from the diag allowlist, so /api/diag/services + journalctl
    # could not report them. Both **live-verified enabled+active on the trader**
    # via the status-check enumeration added in #4942 (system-action #4946):
    #   - ict-devnull-guard: re-asserts /dev/null 0666 every 60s (the OCI agent
    #     resets it to 0444, which once wedged auto-deploy for ~16h). Timer
    #     active/waiting, service inactive/dead between fires (the normal
    #     oneshot+timer shape — same as ict-liveness-watchdog).
    #   - ict-shadow-log-rotate: daily rotation of shadow_predictions.jsonl. Its
    #     unit-file header still says "disabled by default", but the installer's
    #     enable-all-non-gateway-timers loop enables it on the trader — the probe
    #     confirmed timer active/enabled (field beats the stale comment).
    "ict-devnull-guard.service",
    "ict-devnull-guard.timer",
    "ict-shadow-log-rotate.service",
    "ict-shadow-log-rotate.timer",
    # 2026-07-26 (full-system audit Workstream B) — three recurring data-ingest
    # timers installed + enabled by scripts/install_systemd_units.sh (deploy/*.timer
    # glob) but missing from this allowlist, so /api/diag/services + journalctl
    # could not report them. Each exists *because* its store silently went empty
    # once, and each is precisely the "silently-skipped scheduled job" class the
    # "if you see something, say something" rule targets — invisible to diag +
    # health-review is how those stalls hid:
    #   - ict-exchange-fills-pull: populates runtime_state/exchange_fills.sqlite
    #     (backs /api/bot/pnl/exchange). Went empty once — BL-20260713.
    #   - ict-exchange-funding-pull: feeds the broker-truth funding/cost sweep —
    #     BL-20260719-FUNDING-NO-TIMER.
    #   - ict-mes-ibkr-pull: keeps the trainer MES base candle set fresh —
    #     BL-20260626-MES-BASE-STALE.
    # Both the oneshot service and its driving timer are queryable so a session
    # can verify the pull is firing on cadence and tail its journal — same
    # rationale as the watchdog / insights / snapshot pairs above.
    "ict-exchange-fills-pull.service",
    "ict-exchange-fills-pull.timer",
    "ict-exchange-funding-pull.service",
    "ict-exchange-funding-pull.timer",
    "ict-mes-ibkr-pull.service",
    "ict-mes-ibkr-pull.timer",
    # 2026-07-31 (full-system audit P2.4) — the HOURLY IBKR executions pull
    # (feeds exchange_fills_ib.closed_pnl_from_fills, the IB broker-truth PnL
    # reader shipped 2026-07-30). Landed AFTER the 07-26 sweep above and was
    # immediately invisible to /api/diag/services — the THIRD recurrence of
    # the installed-but-unqueryable class, which is why this entry ships
    # together with scripts/check_diag_unit_allowlist.py: from now on a
    # deploy/ unit missing from this tuple (and not explicitly exempted
    # there with a reason) fails CI instead of waiting for the next audit.
    "ict-ib-executions-pull.service",
    "ict-ib-executions-pull.timer",
    # 2026-08-02 (R4 P1) — the daily research→results gate reporter. A oneshot
    # + its driving timer that write the observe-only per-leg measured-net
    # (totalPnlMeasured / pnlCoverage) verdict report under runtime_logs. It is
    # the SOLE writer of runtime_logs/research_results_gate/report-*.json (the
    # R4 evidence trail, design §6 P1) and enforces nothing. Queryable so a
    # session can confirm the reporter is firing on cadence and tail its
    # journal — same rationale as the snapshot / pull pairs above, and it ships
    # with the deploy/ units so check_diag_unit_allowlist.py stays green.
    "ict-research-results-gate.service",
    "ict-research-results-gate.timer",
)

_ADVISORY_LOG = runtime_logs_dir() / "advisory_decisions.jsonl"
_SHADOW_PRED_LOG = runtime_logs_dir() / "shadow_predictions.jsonl"
_SHADOW_PRED_BACKFILL_LOG = runtime_logs_dir() / "shadow_predictions_backfill.jsonl"
_IBKR_MES_PULL_LOG = runtime_logs_dir() / "ibkr_mes_pull.jsonl"
_NEWS_DECISIONS_LOG = runtime_logs_dir() / "news_decisions.jsonl"
_CONVICTION_SIZING_LOG = runtime_logs_dir() / "conviction_sizing.jsonl"
_CONVICTION_ARBITRATION_LOG = runtime_logs_dir() / "conviction_arbitration.jsonl"
_EXIT_LADDER_SOAK_LOG = runtime_logs_dir() / "exit_ladder_soak.jsonl"
_FC_GEOMETRY_SOAK_LOG = runtime_logs_dir() / "fc_geometry_soak.jsonl"
_EXIT_LEVER_SOAK_LOG = runtime_logs_dir() / "exit_lever_soak.jsonl"
_TARGET_EXTENSION_SOAK_LOG = runtime_logs_dir() / "target_extension_soak.jsonl"
# The protection RE-ASSERT soak (2026-08-23). At the default `annotate` mode
# this is the exact row list to review before flipping PROTECTION_REASSERT_MODE
# to `apply` — and without an allowlist entry it would be written and
# unreadable on the one surface a relay-bound session can reach, which is the
# defect #8778 shipped with `exit_loop_health`.
_PROTECTION_REASSERT_SOAK_LOG = runtime_logs_dir() / "protection_reassert_soak.jsonl"
_ALLOCATOR_SOAK_LOG = runtime_logs_dir() / "allocator_soak.jsonl"
_PAIRS_SOAK_LOG = runtime_logs_dir() / "pairs_soak.jsonl"
_EXPOSURE_SOAK_LOG = runtime_logs_dir() / "exposure_soak.jsonl"
_NETTING_ATTRIBUTION_SOAK_LOG = (
    runtime_logs_dir() / "netting_attribution_soak.jsonl"
)
# The git sha this PROCESS was loaded from, captured ONCE at import.
#
# BL-20260823-DIAG-VERSION-REPORTS-DISK-SHA-NOT-RUNNING-CODE. `_resolve_git_sha`
# shells `git rev-parse --short HEAD` against the working tree at CALL time, so
# calling it per-request reports what is on DISK -- which a `git pull` advances
# without restarting anything. `/api/diag/version` existed specifically to
# assert "a post-deploy restart actually rolled the running code forward", and
# reading disk is the one thing that cannot answer that: it reports the new sha
# while the old code serves, which IS the 2026-05-09 24h-stale-code state the
# endpoint was built to catch.
#
# Worse, the assertion in scripts/deploy_pull_restart.sh compared this value to
# its own `git rev-parse --short HEAD` over the SAME tree -- X == X, a check
# that could not fail whether or not the service restarted.
#
# Measured 2026-08-23: /api/diag/version reported `fced7279` while the same
# process returned HTTP 400 for an allowlist entry that exists in `fced7279`
# (control: a name allowlisted earlier returned 200). Disk had moved; the
# process had not.
#
# Captured at import so it names the code actually loaded. Resolving it here
# costs one `git rev-parse` per process start, not one per request.
_RUNNING_GIT_SHA: str = _resolve_git_sha()

_ORPHAN_EVENTS_LOG = runtime_logs_dir() / "orphan_events.jsonl"
# Exit-loop liveness state (M20 decouple, #8778). NOT a .jsonl — a single
# small JSON object rewritten atomically by exit_loop_health.write_state_file.
_EXIT_LOOP_HEALTH_STATE = runtime_logs_dir() / EXIT_LOOP_HEALTH_STATE_FILE
# Exit-path leg-coverage latch (2026-08-18). Also a single small JSON object,
# not a soak log: one entry per order package whose open legs the monitor
# cannot reach.
_PACKAGE_LEG_COVERAGE_STATE = (
    runtime_logs_dir() / "package_leg_coverage_state.json"
)
_EXIT_INTERVAL_SOAK_LOG = runtime_logs_dir() / "exit_interval_soak.jsonl"

_LOG_FILES: dict[str, Path] = {
    "audit": _AUDIT_LOG,
    "status": _STATUS_JSON,
    "heartbeat": _HEARTBEAT,
    "bot_log": _BOT_LOG,
    # M11 S10: ML advisory-score audit log. Written by
    # Coordinator.log_advisory_scores() when advisory-stage models are active.
    # Empty/absent when no advisory models are wired (expected for most installs).
    "advisory_decisions": _ADVISORY_LOG,
    # WS7 shadow-prediction audit log. Written by with_shadow_preds() on every
    # actionable signal once a shadow-stage model is auto-wired (the default).
    # Exposing the tail here lets a layer-2 health review confirm models are
    # actually logging in real time — the operator's "shadow-or-live, and a
    # non-logging model is a critical error" directive (2026-05-21). Absent
    # only if no shadow predictions have ever been written.
    "shadow_predictions": _SHADOW_PRED_LOG,
    "shadow_predictions_backfill": _SHADOW_PRED_BACKFILL_LOG,
    # Progress log for the operator-gated MES IBKR historical pull
    # (scripts/ops/pull_mes_ibkr_history.sh, run via the pull-mes-ibkr-history
    # system-action). Detached + paced, so this tail is how a session monitors
    # it. Absent until the pull has been run at least once.
    "ibkr_mes_pull": _IBKR_MES_PULL_LOG,
    # M9 news layer soak log. One JSON line per actionable signal the news
    # layer evaluated (decision/adjustment/veto/query/symbol), written by
    # src.news.news_audit only while the layer is active. The LOG is observe-only,
    # but it is NOT the case that the veto can't yet gate live money: when the
    # source is active the veto (pipeline.py) gates a live trade by default
    # (NEWS_VETO_ENABLED default-on; CLAUDE.md "selecting rss is the deliberate
    # activation"). The observe-until-opt-in half is the influence SIZING
    # (NEWS_INFLUENCE_MODE, default off), not the veto.
    # Absent until the news layer is active (NEWS_SOURCE=rss, or newsapi + NEWS_API_KEY).
    "news_decisions": _NEWS_DECISIONS_LOG,
    # Unified-confidence soak logs (observe-only, no order influence). Exposing
    # the tail here is how a session VERIFIES the conviction soak is actually
    # accruing evidence on the live VM before P4/P5 graduate it to driving
    # money. ``conviction_sizing`` (P2, #3796): one line per order — the would-be
    # conviction size vs the RiskManager qty. ``conviction_arbitration`` (P3,
    # #3810): one line per multi-intent aggregation — the would-be conviction
    # winner/target vs the actual priority/max-qty pick. Both written by the
    # observe-only annotators; neither ever changes an order. Absent until the
    # respective code path first runs (sizing: every order; arbitration: only
    # when ≥2 conviction-bearing intents compete on a symbol).
    "conviction_sizing": _CONVICTION_SIZING_LOG,
    "conviction_arbitration": _CONVICTION_ARBITRATION_LOG,
    # Exit-ladder soak (P3, dynamic-take-profit consistency): one line per
    # executed order (venue=api live broker order / venue=prop manual ticket) —
    # the materialized laddered exit that WOULD be used vs the single SL/TP
    # bracket actually placed. Observe-only; never changes an exit. Tail it to
    # verify the soak is accruing before P4 graduates the ladder to the real
    # exit. Absent until the first live opening order runs.
    "exit_ladder_soak": _EXIT_LADDER_SOAK_LOG,
    # fc-geometry soak (M19 D1): one line per live opening order — the SL/TP
    # actually placed next to the decision-time quantile-forecast snapshot
    # (forecast_live's fc_* row). Observe-only; never changes an exit. The
    # fc-scaled counterfactual + censored-aware outcome resolution live
    # trainer-side (scripts/ml/fc_geometry_resolve.py). Tail it to verify the
    # soak is accruing + its fc coverage. Absent until the first live opening
    # order runs post-deploy. Also surfaced at /api/bot/fc-geometry/soak.
    "fc_geometry_soak": _FC_GEOMETRY_SOAK_LOG,
    # M20 exit-lever annotate soak (observe-only): "the stale-stop would have
    # exited here" rows written by trend_donchian._stale_stop_verdict while a
    # strategy has NOT declared stale_exit_bars in YAML. The pre-declare
    # evidence trail for the Tier-3 stale-stop rollout (memo:
    # docs/research/M20-exit-refinement-2026-07-12.md § 5). Absent until the
    # first would-fire trade.
    "exit_lever_soak": _EXIT_LEVER_SOAK_LOG,
    "target_extension_soak": _TARGET_EXTENSION_SOAK_LOG,
    "protection_reassert_soak": _PROTECTION_REASSERT_SOAK_LOG,
    # Allocator soak (M18 P0c, portfolio capital allocator): one line per tick
    # with ≥2 actionable candidates — what a capital allocator WOULD pick (the
    # top-ranked candidate of the full opportunity set) vs what the aggregator
    # actually routed, + the regret between them. Observe-only; routing is
    # unchanged. Tail it to verify the soak is accruing regret evidence before
    # M18 P2+ graduates the allocator to actually select the subset. Absent until
    # the first multi-candidate tick runs.
    "allocator_soak": _ALLOCATOR_SOAK_LOG,
    # M22 D2 market-neutral pairs sleeve soak — per-pair spread/z decision +
    # placement/close outcome (also surfaced publicly at /api/bot/pairs/soak).
    "pairs_soak": _PAIRS_SOAK_LOG,
    # Gross-exposure observation soak (also public at /api/bot/exposure/soak).
    "exposure_soak": _EXPOSURE_SOAK_LOG,
    # Netting partial-close ATTRIBUTION soak (BL-20260801). One line per journal
    # row the reconciler would reduce/close to account for a netted partial
    # close, with the SELECTION basis (`leg_gone` / `fifo`) and the PRICE
    # provenance (`anchor_status`) kept as separate fields — they answer
    # different questions and conflating them is how an inferred close starts
    # reading as a measured one. Written in BOTH modes, so at the default
    # `annotate` this is the exact row list to review before flipping
    # NETTING_ATTRIBUTION_MODE=apply. Absent until the first confirmed divergence.
    "netting_attribution_soak": _NETTING_ATTRIBUTION_SOAK_LOG,
    # Exit-loop liveness (M20 decouple, #8778). The exit evaluation now runs on
    # its OWN thread, which took it outside the liveness watchdog's coverage --
    # that coverage was never a probe, it was the fact that exit evaluation ran
    # INLINE on the tick the heartbeat measures. So a stalled exit loop is now a
    # condition nothing else can see, and this is how a relay-bound session sees
    # it. Four states, so the field can say WE DID NOT LOOK: `unknown` (module
    # unreadable) / `never_ran` (loop not started, or decouple disabled --
    # emphatically NOT "healthy") / `fresh` / `stale`. Read `max_pass_ms` beside
    # `passes`: a max over 3 passes is not the claim a max over 3000 is.
    # Shipped WITHOUT this entry in #8778 -- write_state_file's own docstring
    # says "for the diag surface" while the only surface a relay can reach did
    # not serve it, the written-but-not-readable shape of #8665's exposure block.
    # The exit-path leg-coverage latch (2026-08-18). Not a soak log — a small
    # JSON object naming every package whose open legs the monitor cannot
    # manage. Allowlisted in the SAME change that ships the writer: #8778
    # shipped exit_loop_health's writer with no entry here and the state was
    # written and unreadable by the only surface a relay-bound session can
    # reach.
    "package_leg_coverage": _PACKAGE_LEG_COVERAGE_STATE,
    "exit_loop_health": _EXIT_LOOP_HEALTH_STATE,
    # The DURABLE half of the above, and the reason it had to exist: every field
    # in `exit_loop_health` lives in module globals that start empty and are
    # never reloaded, so `max_interval_ms` is scoped to ONE process -- and the
    # trader redeploys off `main` via `ict-git-sync` (FIVE observed processes in
    # ~10h, measured 2026-08-16 from `process_started_utc` -- counting merges
    # instead over-counted it by one, since a merge does not promptly restart
    # the trader). A max over a short window is systematically LOW, so the
    # in-memory grade reads most reassuring exactly when the system is busiest;
    # the only reading that ever approached the 60s requirement came from the
    # one process that survived a quiet overnight window (n=694, 98.2%). This
    # append-only log makes the max a property of the DATA, not of a process's
    # lifetime. One row per completed pass -- `interval_ms: null` marks the
    # first pass of a process (no prior completion to measure from), which is a
    # different fact from an interval of zero and is what makes the process
    # boundary visible instead of being mistaken for a real interval.
    "exit_interval_soak": _EXIT_INTERVAL_SOAK_LOG,
    # Broker-account-down + trainer-down latch state (BL-20260707-DIAG-
    # ALLOWLIST-REACHABILITY-LOG): the health-review skill reads these to see
    # which accounts / whether the trainer are currently latched down —
    # previously referenced by the skill but missing here, forcing reviews to
    # infer down-state from exchange_positions nullness instead. JSON state
    # files (not JSONL) — the tail reader returns the whole file.
    "account_reachability_alert_state":
        runtime_logs_dir() / "account_reachability_alert_state.json",
    "trainer_reachability_alert_state":
        runtime_logs_dir() / "trainer_reachability_alert_state.json",
    # The other three durable alert latches. Two of five were readable and
    # three were not, which is an inconsistency rather than a policy — and the
    # gap matters most for the newest of them. Each of these latches SUPPRESSES
    # an operator page, and each fails LOUD when its state file cannot be read
    # (alerting is the only safe direction for a safety page). So a
    # permanently-unwritable latch reproduces exactly the spam it exists to
    # stop, and from outside the two are indistinguishable: the alert rate
    # looks the same either way. Reading the latch is what tells them apart.
    # BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART shipped the
    # target-naked latch; these entries are its read half.
    "silent_refusal_alert_state":
        runtime_logs_dir() / "silent_refusal_alert_state.json",
    "prop_fills_staleness_state":
        runtime_logs_dir() / "prop_fills_staleness_state.json",
    "target_naked_alert_state":
        runtime_logs_dir() / "target_naked_alert_state.json",
    # Registered IN THE SAME COMMIT that ships its writer. #8778 shipped
    # `exit_loop_health`'s writer with no allowlist entry, so the state was
    # written and unreadable on the one surface a relay-bound session can
    # reach; a latch that suppresses a CRITICAL page and cannot be inspected
    # is strictly worse than no latch.
    "stop_over_cover_alert_state":
        runtime_logs_dir() / "stop_over_cover_alert_state.json",
    # NEW orphan trade rows (operator directive 2026-06-24: orphan is a problem
    # to reconcile, never a resting status). One JSON line per orphan-created
    # event (account/symbol/side/trade_id/origin/ts), written by
    # execution_diagnostics.enqueue_orphan_created_flag at every orphan-row
    # creation. The /health-review (and /system-review) drain this tail into the
    # health-review backlog so every orphan is tracked for reconciliation. Absent
    # until the first orphan row is created.
    "orphan_events": _ORPHAN_EVENTS_LOG,
}

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 1000
_DEFAULT_JOURNAL_LINES = 200
_MAX_JOURNAL_LINES = 2000
# 2026-05-18: bumped 10 → 30 after repeated `journalctl?lines=30..300`
# calls timed out on the live VM mid-health-review. Root cause is a
# large persistent journal whose backwards-scan exceeds the prior 10 s
# cap even for small `-n` values. The companion curl --max-time in
# .github/workflows/vm-diag-snapshot.yml was bumped from 20 → 40 so
# the HTTP layer doesn't preempt the new server-side limit. A longer
# tail-scan is bounded by the FastAPI worker thread budget; the read
# is the only path to `order_monitor:` lines (the trader writes to
# journal only — bot.log went stale 2026-05-03) so the diag surface
# stops working entirely if this is too tight.
_JOURNALCTL_TIMEOUT_S = 30
_SYSTEMCTL_TIMEOUT_S = 5

# Strict ISO-8601 form accepted by /api/diag/journalctl?since=… / ?until=…
# before forwarding to journalctl --since/--until. Matches:
#   2026-05-10T21:13:00            (naive UTC, journalctl assumes local)
#   2026-05-10T21:13:00Z           (explicit UTC)
#   2026-05-10T21:13:00+00:00      (explicit offset)
#   2026-05-10 21:13:00            (space-separated, journalctl-native)
# Rejects everything else — defence in depth even though the subprocess
# is invoked via argv list (no shell). FU-20260511-001.
_ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:?\d{2})?$"
)


def _diag_token() -> str | None:
    tok = os.environ.get("DIAG_READ_TOKEN", "").strip()
    return tok or None


def _require_diag_token(request: Request) -> None:
    expected = _diag_token()
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "diag_disabled"},
        )
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    presented = auth[len("Bearer "):].strip()
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )


def _clamp(value: int | None, default: int, max_: int) -> int:
    if value is None or value < 1:
        return default
    return min(value, max_)


def _normalize_unit(unit: str) -> str:
    canonical = unit if "." in unit else f"{unit}.service"
    if canonical not in _CANONICAL_UNITS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unknown_unit", "allowed": list(_CANONICAL_UNITS)},
        )
    return canonical


def _heartbeat_snapshot() -> dict[str, Any]:
    from src.runtime.heartbeat import heartbeat_label  # local import to keep router cheap
    if not _HEARTBEAT.exists():
        return {"present": False, "mtime": None, "age_seconds": None, "label": "stopped"}
    mtime = _HEARTBEAT.stat().st_mtime
    age = time.time() - mtime
    return {
        "present": True,
        "mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
        "age_seconds": round(age, 2),
        "label": heartbeat_label(age),
    }


def _status_json_payload() -> dict[str, Any] | None:
    if not _STATUS_JSON.exists():
        return None
    try:
        with _STATUS_JSON.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # S-067 borderline: was silently `return None`. Keep the
        # `None` sentinel (callers branch on it) but log so a
        # corrupt status.json is visible in bot.log next time.
        logger.warning(
            "diag: status_json read failed: %s: %s",
            type(exc).__name__, exc,
        )
        return None


def _audit_tail(limit: int) -> list[dict[str, Any]]:
    if not _AUDIT_LOG.exists():
        return []
    try:
        with _AUDIT_LOG.open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        # S-067 borderline: was silently `return []`. Log so a
        # signal_audit.jsonl read failure surfaces.
        logger.warning(
            "diag: audit_tail read failed: %s: %s",
            type(exc).__name__, exc,
        )
        return []
    out: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def _journal_select(table: str, limit: int) -> list[dict[str, Any]]:
    if table not in _JOURNAL_TABLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unknown_table", "allowed": sorted(_JOURNAL_TABLES.keys())},
        )
    if not _DB_PATH.exists():
        # Genuine "DB hasn't been created yet" — distinct from "DB
        # reachable but broken". Keep the empty-list shape here so a
        # fresh install doesn't 503 out of the gate.
        return []
    order_col = _JOURNAL_TABLES[table]
    try:
        # mode=ro guarantees no mutation can happen here even if a future
        # change accidentally introduces an UPDATE/DELETE statement.
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                f"SELECT * FROM {table} ORDER BY {order_col} DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # S-067: "no such table" / schema mismatch / locked DB / corrupt
        # file used to be silently swallowed and surfaced as ``[]`` —
        # indistinguishable from "table empty". The /db_info endpoint
        # was added in #624 specifically to work around this; this is
        # the actual fix. Operator scripts and off-VM Claude sessions
        # now see a real 503 instead of a misleading empty result.
        logger.exception("diag: _journal_select(table=%s) sqlite read failed", table)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "journal_unavailable",
                "table": table,
                "reason": f"sqlite error: {type(exc).__name__}",
            },
        )


# ---------------------------------------------------------------------------
# Historical audit query (2026-06-01) — the time/event-filtered reader the
# line-capped /audit + /log_file tails cannot provide.
#
# /audit and /log_file?name=audit return only the last _MAX_LIMIT (1000) lines
# of signal_audit.jsonl — on a busy day that is ~15 min of history — so an
# off-VM session cannot retrieve an arbitrary historical window or grep for a
# specific event type (e.g. all `regime_shadow_gate` rows on a given day; the
# PERF-20260601-008/011 regime-router verification needs exactly that). The
# full audit stream is dual-written to trade_journal.db::signals
# (signal_audit_logger._dual_write_to_db, on by default; SIGNAL_DUAL_WRITE_
# DISABLED opts out) with the typed columns PLUS the entire original payload
# as JSON in `meta`. This reader SELECTs that table with since/until (on the
# indexed logged_at_utc) + optional event / strategy / symbol / side filters
# and offset paging, so a historical-window verification is one bounded query
# instead of an unreachable tail.
# ---------------------------------------------------------------------------

# `event` is matched inside the `meta` JSON blob via LIKE; restrict it to a
# safe identifier charset so a caller cannot smuggle LIKE wildcards (% / _) or
# otherwise alter the match. strategy / symbol / side are bound params
# (injection-safe) so they need no charset guard.
_EVENT_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _normalize_utc_bound(iso: str) -> str:
    """Convert a validated ISO-8601 bound to the canonical column format
    (UTC, ``+00:00`` offset) so a plain TEXT comparison against
    ``logged_at_utc`` is correct regardless of whether the caller sent
    ``Z`` / ``+00:00`` / a naive timestamp.

    ``logged_at_utc`` is always written as
    ``datetime.now(timezone.utc).isoformat()`` (fixed ``+00:00`` offset), so
    once the bound is in that same representation a lexicographic ``>=`` /
    ``<=`` is also chronological — and, unlike wrapping the column in SQLite
    ``datetime()``, this does NOT depend on the live SQLite version's support
    for the ``T`` separator / ``Z`` suffix (added only in SQLite 3.42).
    """
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Already regex-validated upstream; fall back to the raw string.
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _signals_query(
    *,
    since: str | None,
    until: str | None,
    event: str | None,
    strategy: str | None,
    symbol: str | None,
    side: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Time/event-filtered read of ``trade_journal.db::signals`` (the audit
    dual-write). Newest-first by ``logged_at_utc``. Read-only (``mode=ro``).

    Each returned row carries the typed columns merged with the parsed
    ``meta`` payload, so callers see the same ``event`` / ``regime`` /
    ``adx_14`` / ``enforced`` / ``cell`` fields the JSONL row had. Typed
    columns win on key collision (they are the canonical projection).

    Missing DB or absent ``signals`` table → empty result with a flag
    (not a 503), matching ``_journal_select``'s fresh-install tolerance and
    surfacing the "dual-write never ran / disabled" case explicitly.
    """
    result: dict[str, Any] = {
        "table": "signals",
        "filters": {
            "since": since, "until": until, "event": event,
            "strategy": strategy, "symbol": symbol, "side": side,
        },
        "limit": limit,
        "offset": offset,
        "rows": [],
        "count": 0,
        "dual_write_present": False,
    }
    if not _DB_PATH.exists():
        return result
    where: list[str] = []
    params: list[Any] = []
    # logged_at_utc is stored as an ISO-8601 string with a stable +00:00
    # offset (log_signal stamps datetime.now(timezone.utc).isoformat()), so
    # a lexicographic >=/<= compare is also chronological. Bound params.
    if since:
        where.append("logged_at_utc >= ?")
        params.append(_normalize_utc_bound(since))
    if until:
        where.append("logged_at_utc <= ?")
        params.append(_normalize_utc_bound(until))
    if strategy:
        where.append("strategy = ?")
        params.append(strategy)
    if symbol:
        where.append("symbol = ?")
        params.append(symbol)
    if side:
        where.append("side = ?")
        params.append(side)
    if event:
        # event lives in the meta JSON blob, not a typed column. json.dumps
        # renders it as `"event": "<name>"`; match that substring. event is
        # charset-validated at the route layer so no LIKE-wildcard smuggling.
        where.append("meta LIKE ?")
        params.append(f'%"event": "{event}"%')
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT id, logged_at_utc, strategy, symbol, side, qty, status, "
        "reason, meta FROM signals" + clause +
        # logged_at_utc is a fixed +00:00 isoformat string, so a TEXT sort is
        # chronological — and avoids depending on SQLite datetime() T/Z parsing.
        " ORDER BY logged_at_utc DESC, id DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:  # allow-silent: not silent — surfaces no-such-table as an explicit error flag and logs + 503s every other sqlite error (mirrors _journal_select)
        # "no such table: signals" => the dual-write has never run (or was
        # disabled before any write). Surface that explicitly as a non-fatal
        # signal rather than a misleading 503, so the caller learns the
        # table is absent (and can check SIGNAL_DUAL_WRITE_DISABLED).
        if "no such table" in str(exc).lower():
            result["error"] = "signals_table_absent"
            return result
        logger.exception("diag: _signals_query sqlite read failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "signals_query_unavailable",
                "reason": f"sqlite error: {type(exc).__name__}",
            },
        )
    result["dual_write_present"] = True
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        meta_raw = row.pop("meta", None)
        if meta_raw:
            try:
                meta = json.loads(meta_raw)
            except (json.JSONDecodeError, TypeError):
                row["meta_raw"] = meta_raw
            else:
                if isinstance(meta, dict):
                    # Full original payload (event/regime/adx_14/enforced/…)
                    # under the canonical typed columns.
                    row = {**meta, **row}
        out.append(row)
    result["rows"] = out
    result["count"] = len(out)
    return result


def _db_info_payload() -> dict[str, Any]:
    """Return DB metadata for diagnostic cross-referencing of trader vs
    web-api. Resolves the same ``_DB_PATH`` the journal endpoint reads,
    plus inode + size + table list + per-table row count.

    The 2026-05-09 ``order_packages returns []`` mystery surfaced
    because the existing ``journal`` endpoint silently swallowed
    ``sqlite3.Error`` (returns ``[]``) — so a "no such table" or schema
    mismatch was indistinguishable from "table empty". S-067 fixed the
    journal endpoint itself; this endpoint stays as the
    failure-surfacing companion (it returns the per-table error string
    even when the journal endpoint already 503s on the same condition).

    Best-effort: every step is wrapped so a single failure never
    aborts the whole payload. ``error_per_table`` is only populated
    when a SELECT raised; missing keys mean the count succeeded.
    """
    payload: dict[str, Any] = {
        "db_path": str(_DB_PATH),
        "db_path_resolved": None,
        "exists": False,
        "size_bytes": None,
        "inode": None,
        "tables": [],
        "row_counts": {},
        "error_per_table": {},
        "load_error": None,
    }
    try:
        payload["db_path_resolved"] = str(_DB_PATH.resolve())
    except Exception as exc:  # noqa: BLE001
        payload["load_error"] = f"resolve: {type(exc).__name__}: {exc}"
        return payload

    if not _DB_PATH.exists():
        return payload
    payload["exists"] = True
    try:
        st = os.stat(_DB_PATH)
        payload["size_bytes"] = st.st_size
        payload["inode"] = st.st_ino
    except OSError as exc:
        payload["load_error"] = f"stat: {type(exc).__name__}: {exc}"

    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "ORDER BY name"
                ).fetchall()
            ]
            payload["tables"] = tables
            for tbl in tables:
                try:
                    cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}")
                    payload["row_counts"][tbl] = int(cur.fetchone()[0])
                except sqlite3.Error as exc:
                    payload["error_per_table"][tbl] = (
                        f"{type(exc).__name__}: {exc}"
                    )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        payload["load_error"] = f"connect: {type(exc).__name__}: {exc}"

    return payload


# S-067 follow-up #9: vm_health implementation moved to
# src/web/api/_vm_health.py to remove the diag.py / dashboard.py
# fork. Re-exported under the legacy ``_vm_health`` name so
# tests (e.g. tests/test_web_api_diag.py + the monkeypatching in
# the S-067 silent-empty regression tests) keep working without
# modification.
from src.web.api._vm_health import vm_health as _vm_health  # noqa: E402


def _is_active_batch(units: list[str]) -> dict[str, str]:
    if not units:
        return {}
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", *units],
            capture_output=True,
            text=True,
            timeout=_SYSTEMCTL_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {u: "unknown" for u in units}
    states = (proc.stdout or "").splitlines()
    return {
        u: (states[i].strip() if i < len(states) else "unknown")
        for i, u in enumerate(units)
    }


def _normalize_journalctl_timestamp(ts: str) -> str:
    """Convert a validated ISO-8601 string into journalctl's universal form.

    journalctl 245 (Ubuntu 20.04) rejects ISO-8601 with the ``T`` separator
    or trailing ``Z`` — it expects ``YYYY-MM-DD HH:MM:SS`` and optionally a
    timezone word like ``UTC``. journalctl 252+ accepts both forms, but the
    live VM still runs the older binary, so passing ``2026-05-11T15:40:00Z``
    verbatim returns rc=1 with no log lines (issue #930).

    Normalize:
      ``2026-05-11T15:40:00Z``        → ``2026-05-11 15:40:00 UTC``
      ``2026-05-11T15:40:00+00:00``   → ``2026-05-11 15:40:00 UTC``
      ``2026-05-11T15:40:00-05:00``   → ``2026-05-11 15:40:00 -05:00``
      ``2026-05-11T15:40:00``         → ``2026-05-11 15:40:00`` (naive, local)
      ``2026-05-11 15:40:00``         → ``2026-05-11 15:40:00`` (passthrough)

    The input is already validated by _ISO_TIMESTAMP_RE at the route layer,
    so we know it's well-formed.
    """
    # T → space
    out = ts.replace("T", " ", 1)
    # Z (or +00:00 / +0000) → UTC suffix (journalctl-native)
    if out.endswith("Z"):
        out = out[:-1] + " UTC"
    elif out.endswith("+00:00") or out.endswith("+0000"):
        # Strip the offset, replace with the UTC word
        out = out.rsplit("+", 1)[0].rstrip() + " UTC"
    return out


def _journalctl_tail(
    unit: str,
    lines: int,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    canonical = _normalize_unit(unit)
    cmd = [
        "journalctl",
        "-u",
        canonical,
        "-n",
        str(lines),
        "--no-pager",
        "--output=short-iso",
    ]
    # ?since / ?until support — passes through to journalctl's native
    # --since/--until flags. The endpoint route validates the format
    # with _ISO_TIMESTAMP_RE before reaching this helper. Normalize
    # to journalctl's universal "YYYY-MM-DD HH:MM:SS [UTC]" form so
    # older journalctl versions (Ubuntu 20.04 ships 245) also accept it.
    if since:
        cmd.extend(["--since", _normalize_journalctl_timestamp(since)])
    if until:
        cmd.extend(["--until", _normalize_journalctl_timestamp(until)])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_JOURNALCTL_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return {"unit": canonical, "available": False, "reason": "journalctl_not_found", "lines": []}
    except subprocess.TimeoutExpired:
        return {"unit": canonical, "available": False, "reason": "timeout", "lines": []}
    output = proc.stdout or ""
    stderr = (proc.stderr or "").strip()
    out_lines = output.splitlines()[-lines:] if output else []
    # journalctl rc=1 has overloaded semantics:
    #   * rc=0, stdout=N lines      → matches found
    #   * rc=1, stdout="", stderr="" → query valid, just no matching entries
    #   * rc=1, stdout="", stderr=X → real failure (bad args, perm, etc.)
    #   * rc=0, stdout=""           → unit has no entries at all
    # Treat empty-stderr rc=1 as "available, just empty" so a legitimate
    # zero-match window doesn't get misreported as a unit-unavailable
    # failure (issue #930).
    available = proc.returncode == 0 or (proc.returncode == 1 and not stderr)
    result: dict[str, Any] = {
        "unit": canonical,
        "available": available,
        "returncode": proc.returncode,
        "lines": out_lines,
    }
    if not available and stderr:
        result["stderr"] = stderr[:500]  # truncate; defensive
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/snapshot")
def get_snapshot(request: Request, limit: int = _DEFAULT_LIMIT) -> dict[str, Any]:
    _require_diag_token(request)
    n = _clamp(limit, _DEFAULT_LIMIT, _MAX_LIMIT)
    states = _is_active_batch(list(_CANONICAL_UNITS))
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "heartbeat": _heartbeat_snapshot(),
        "status": _status_json_payload(),
        "audit_tail": _audit_tail(n),
        "order_packages": _journal_select("order_packages", n),
        "trades": _journal_select("trades", n),
        "vm_health": _vm_health(),
        "services": [{"unit": u, "state": states.get(u, "unknown")} for u in _CANONICAL_UNITS],
    }


# ---------------------------------------------------------------------------
# BL-20260821-NO-READ-SURFACE-FOR-TIMER-SCHEDULE (workplan 0.6)
#
# Every surface that reports systemd timers reports STATE, never SCHEDULE.
# `/api/diag/services` returns {unit, state, sub_state, active_enter_iso}, so
# `ict-exchange-fills-pull.timer` reads "active" whether it fires HOURLY or
# DAILY -- and the difference between those two was a real, measured defect
# (BL-20260821-ICTSCALP-TP-CROSSED-BOOKED-AS-ESTIMATE: a real-money trade
# crossed its take-profit and was booked at candle_at_close because the fills
# store held nothing recent enough). `systemctl list-timers` is the one command
# that shows the next elapse and it appears in this repo only inside four
# scripts, each scoped to its own single unit, none reachable as a general read.
#
# So a relay-bound session could not answer "how often does this fire" at all,
# and inferred it from unit files that may not match what is installed.
#
# THREE STATES, NEVER COLLAPSED, and the distinction is not academic here:
# MOST of these timers are MONOTONIC (OnBootSec / OnUnitActiveSec), so an empty
# `TimersCalendar` is the CORRECT answer for them, not a failure. Collapsing
# "no calendar" into "could not read" would report two-thirds of the fleet as
# broken; collapsing the other way would report a genuinely unreadable timer as
# scheduleless. Both spellings are therefore read and reported.
_TIMER_PROPS = (
    "TimersCalendar",
    "TimersMonotonic",
    "NextElapseUSecRealtime",
    "NextElapseUSecMonotonic",
    "LastTriggerUSec",
    "ActiveState",
)


def _timer_units() -> list[str]:
    return [u for u in _CANONICAL_UNITS if u.endswith(".timer")]


def _systemctl_show(units: list[str], props: tuple[str, ...]) -> dict[str, dict[str, str]]:
    """`systemctl show -p ... unit...` parsed per unit.

    Returns {} on a missing binary or a timeout -- the CALLER turns that into an
    explicit `could_not_look`, never into an empty schedule. Multiple units emit
    blank-line-separated blocks in the order requested.
    """
    if not units:
        return {}
    args = ["systemctl", "show"]
    for prop in props:
        args += ["-p", prop]
    try:
        proc = subprocess.run(args + units, capture_output=True, text=True,
                              timeout=_SYSTEMCTL_TIMEOUT_S, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    out: dict[str, dict[str, str]] = {}
    blocks = (proc.stdout or "").split("\n\n")
    for i, unit in enumerate(units):
        if i >= len(blocks):
            break
        parsed: dict[str, str] = {}
        for line in blocks[i].splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                parsed[k.strip()] = v.strip()
        if parsed:
            out[unit] = parsed
    return out


@router.get("/timers")
def get_timers(request: Request) -> dict[str, Any]:
    """Per allowlisted timer: its SCHEDULE, not merely its state. Read-only."""
    _require_diag_token(request)
    units = _timer_units()
    shown = _systemctl_show(units, _TIMER_PROPS)
    rows: list[dict[str, Any]] = []
    for unit in units:
        props = shown.get(unit)
        if not props:
            # We could not look. NOT "this timer has no schedule".
            rows.append({
                "unit": unit,
                "read_state": "could_not_look",
                "schedule_state": "unknown",
                "on_calendar": None, "on_monotonic": None,
                "next_elapse_realtime": None, "next_elapse_monotonic": None,
                "last_trigger": None, "state": None,
            })
            continue
        cal = props.get("TimersCalendar") or ""
        mono = props.get("TimersMonotonic") or ""
        if cal:
            schedule_state = "calendar"
        elif mono:
            schedule_state = "monotonic"
        else:
            # Read cleanly and it declares neither -- a real, reportable state.
            schedule_state = "no_schedule"
        rows.append({
            "unit": unit,
            "read_state": "read",
            "schedule_state": schedule_state,
            "on_calendar": cal or None,
            "on_monotonic": mono or None,
            "next_elapse_realtime": props.get("NextElapseUSecRealtime") or None,
            "next_elapse_monotonic": props.get("NextElapseUSecMonotonic") or None,
            "last_trigger": props.get("LastTriggerUSec") or None,
            "state": props.get("ActiveState") or None,
        })
    by_schedule: dict[str, int] = {}
    for r in rows:
        by_schedule[r["schedule_state"]] = by_schedule.get(r["schedule_state"], 0) + 1
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        # Read `read` beside `count`: an all-`could_not_look` payload is a
        # systemctl failure, and must not read as a fleet with no schedules.
        "summary": {"by_schedule_state": by_schedule,
                    "read": sum(1 for r in rows if r["read_state"] == "read"),
                    "could_not_look": sum(1 for r in rows
                                          if r["read_state"] == "could_not_look")},
        "timers": rows,
    }


@router.get("/audit")
def get_audit(request: Request, limit: int = _DEFAULT_LIMIT) -> list[dict[str, Any]]:
    _require_diag_token(request)
    return _audit_tail(_clamp(limit, _DEFAULT_LIMIT, _MAX_LIMIT))


@router.get("/journal")
def get_journal(
    request: Request,
    table: str,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    _require_diag_token(request)
    return _journal_select(table, _clamp(limit, _DEFAULT_LIMIT, _MAX_LIMIT))


@router.get("/audit_query")
def get_audit_query(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    event: str | None = None,
    strategy: str | None = None,
    symbol: str | None = None,
    side: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Historical, time/event-filtered audit read backed by the
    ``trade_journal.db::signals`` dual-write.

    Unlike ``/audit`` and ``/log_file?name=audit`` — which tail only the last
    ``_MAX_LIMIT`` (1000) lines of ``signal_audit.jsonl`` (~15 min on a busy
    day) — this reaches arbitrary history because the full audit stream is
    mirrored to the indexed ``signals`` table. Use it to pull a specific
    window (``since`` / ``until``) or every row of one event type
    (``event=regime_shadow_gate``) without the tail cap.

    Params:
      * ``since`` / ``until`` — ISO-8601 (``2026-06-01T15:00:00Z``); filter on
        ``logged_at_utc``. Validated against ``_ISO_TIMESTAMP_RE``.
      * ``event`` — match the audit ``event`` field (stored in the ``meta``
        JSON), e.g. ``regime_shadow_gate``, ``vwap_eval``. Charset
        ``[A-Za-z0-9_]+``.
      * ``strategy`` / ``symbol`` / ``side`` — exact-match typed columns.
      * ``limit`` (≤ ``_MAX_LIMIT``) + ``offset`` — page back through the
        full table.

    Rows are newest-first and carry the typed columns merged with the parsed
    ``meta`` payload (``regime`` / ``adx_14`` / ``enforced`` / ``cell`` …).
    Empty ``rows`` with ``dual_write_present: false`` / ``error:
    signals_table_absent`` means the dual-write hasn't populated the table
    (check ``SIGNAL_DUAL_WRITE_DISABLED``).
    """
    _require_diag_token(request)
    for label, value in (("since", since), ("until", until)):
        if value is not None and not _ISO_TIMESTAMP_RE.match(value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_timestamp",
                    "param": label,
                    "expected": "ISO-8601 like 2026-06-01T15:00:00Z",
                    "got": value,
                },
            )
    if event is not None and not _EVENT_NAME_RE.match(event):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_event",
                "expected": "identifier matching [A-Za-z0-9_]+",
                "got": event,
            },
        )
    return _signals_query(
        since=since,
        until=until,
        event=event,
        strategy=strategy,
        symbol=symbol,
        side=side,
        limit=_clamp(limit, _DEFAULT_LIMIT, _MAX_LIMIT),
        offset=max(0, offset),
    )


@router.get("/db_info")
def get_db_info(request: Request) -> dict[str, Any]:
    """Diagnostic — resolved DB path, inode, table list, row counts.

    Companion to ``/journal``. Surfaces the per-table error string when
    a SELECT raises (``journal`` swallows it as ``[]``). Trader vs
    web-api inode mismatch on the same logical path is the canonical
    signature for the 2026-05-09 ``order_packages returns []`` mystery.
    """
    _require_diag_token(request)
    return _db_info_payload()


@router.get("/status")
def get_status(request: Request) -> dict[str, Any]:
    _require_diag_token(request)
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "heartbeat": _heartbeat_snapshot(),
        "status": _status_json_payload(),
        "vm_health": _vm_health(),
    }


@router.get("/services")
def get_services(request: Request) -> list[dict[str, str]]:
    _require_diag_token(request)
    states = _is_active_batch(list(_CANONICAL_UNITS))
    return [{"unit": u, "state": states.get(u, "unknown")} for u in _CANONICAL_UNITS]


@router.get("/ib_state")
def get_ib_state(request: Request) -> dict[str, Any]:
    """IB connection-state legibility (BL-20260707-IB-STATE-LEGIBILITY).

    Read-only view of ``runtime_logs/ib_state.json`` — the per-tick snapshot
    the TRADER process writes of each live ``IBClient``'s connection state
    (``src.units.accounts.ib_client.write_ib_state_file``). Answers, at a
    glance, the question that kept being confusing: is IB connected, and is a
    current failure a TRANSITORY circuit-breaker backoff (``state:breaker_open``,
    ``likely_wedged:false`` — auto-recovers) or a REAL wedge
    (``likely_wedged:true`` — needs a look)?

    ``present:false`` when the trader hasn't written the file yet (e.g. it has
    not dispatched to an IB account since the last restart) or the file is
    unreadable. ``age_seconds`` is how stale the snapshot is — a large value
    while the trader is otherwise ticking means the writer hook isn't running
    (deploy check). Never opens a socket; pure file read. Tier 1.
    """
    _require_diag_token(request)
    out: dict[str, Any] = {
        "present": False,
        "path": str(_IB_STATE_JSON),
        "generated_at": None,
        "age_seconds": None,
        "clients": [],
    }
    if not _IB_STATE_JSON.exists():
        return out
    try:
        with _IB_STATE_JSON.open(encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("diag: ib_state read failed: %s: %s", type(exc).__name__, exc)
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["present"] = True
    out["generated_at"] = payload.get("generated_at")
    out["clients"] = payload.get("clients", [])
    # age_seconds is a derived convenience field; a malformed/absent
    # generated_at (ValueError from fromisoformat, TypeError from arithmetic on
    # a non-str) must leave it None, not fail the read. Narrow types only —
    # the payload was already parsed as valid JSON above.
    try:
        gen = payload.get("generated_at")
        if gen:
            out["age_seconds"] = round(
                (datetime.now(timezone.utc) - datetime.fromisoformat(gen)).total_seconds(),
                1,
            )
    except (ValueError, TypeError) as exc:
        logger.warning("diag: ib_state age_seconds compute failed: %s: %s",
                       type(exc).__name__, exc)
    return out


@router.get("/journalctl")
def get_journalctl(
    request: Request,
    unit: str,
    lines: int = _DEFAULT_JOURNAL_LINES,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Tail systemd-journal lines for an allowlisted unit.

    ``since`` / ``until`` accept ISO-8601 timestamps (``2026-05-10T21:13:00Z``
    or ``2026-05-10 21:13:00``) and forward to journalctl's native
    ``--since`` / ``--until`` flags. Format is strictly validated against
    ``_ISO_TIMESTAMP_RE`` before being passed to the subprocess argv —
    arbitrary strings are rejected with HTTP 400. Without these params
    the endpoint preserves the pre-FU-20260511-001 tail-only behaviour
    (max 2000 lines, recent end of the journal). FU-005 / FU-008 style
    historical-window evidence needs ``?since=`` to reach back hours.
    """
    _require_diag_token(request)
    for label, value in (("since", since), ("until", until)):
        if value is not None and not _ISO_TIMESTAMP_RE.match(value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_timestamp",
                    "param": label,
                    "expected": "ISO-8601 like 2026-05-10T21:13:00Z",
                    "got": value,
                },
            )
    return _journalctl_tail(
        unit,
        _clamp(lines, _DEFAULT_JOURNAL_LINES, _MAX_JOURNAL_LINES),
        since=since,
        until=until,
    )


@router.get("/version")
def get_version(request: Request) -> dict[str, Any]:
    """Diagnostic — git SHA + captured timestamp of the running web-api
    process. Used by ``scripts/deploy_pull_restart.sh`` to assert that
    a post-deploy restart actually rolled the running code forward
    (the 2026-05-09 24h-stale-code incident shipped because nothing
    in the deploy chain confirmed the running web-api had rebooted).

    Returns ``git_sha`` resolved by the same helper that powers
    ``runtime_logs/runtime_status.json::git_sha`` so the value is consistent
    between read sources. ``"unknown"`` is a legitimate value on
    sandbox / dev hosts without git available; the deploy script
    treats ``unknown`` as a soft failure.
    """
    _require_diag_token(request)
    on_disk = _resolve_git_sha()
    # Three-way, never collapsed. "unknown" on either side means we could not
    # look, which is NOT the same as "they agree" -- so restart_pending is None
    # rather than False when either sha is unresolvable.
    if _RUNNING_GIT_SHA == "unknown" or on_disk == "unknown":
        restart_pending = None
    else:
        restart_pending = _RUNNING_GIT_SHA != on_disk
    return {
        # The sha the RUNNING process was loaded from -- captured once at
        # import. This is what the field name and this endpoint's whole
        # purpose have always claimed, and what deploy_pull_restart.sh
        # compares against.
        "git_sha": _RUNNING_GIT_SHA,
        # The sha currently checked out in the working tree, resolved live.
        # A pull advances this WITHOUT restarting anything.
        "git_sha_on_disk": on_disk,
        # True when the tree has moved ahead of the running process, i.e. code
        # was pulled and nothing restarted -- the 2026-05-09 incident state.
        # None when either side is unknown (we could not look).
        "restart_pending": restart_pending,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/log_file")
def get_log_file(
    request: Request,
    name: str,
    lines: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    _require_diag_token(request)
    if name not in _LOG_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unknown_log_file", "allowed": sorted(_LOG_FILES.keys())},
        )
    n = _clamp(lines, _DEFAULT_LIMIT, _MAX_LIMIT)
    path = _LOG_FILES[name]
    if not path.exists():
        return {"name": name, "path": str(path), "present": False, "lines": []}
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            content = fh.readlines()
    except OSError as exc:
        return {
            "name": name,
            "path": str(path),
            "present": True,
            "error": str(exc),
            "lines": [],
        }
    return {
        "name": name,
        "path": str(path),
        "present": True,
        "size_bytes": path.stat().st_size,
        "lines": [ln.rstrip("\n") for ln in content[-n:]],
    }


@router.get("/shadow_stats")
def get_shadow_stats(
    request: Request,
    model_id: str | None = None,
    stage: str | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    """Token-gated mirror of GET /api/bot/shadow/stats for diag-relay access.

    FU-20260516-001: /api/bot/shadow/stats is not under /api/diag/ so the
    vm-diag-snapshot relay cannot reach it. This endpoint exposes the same
    aggregate shadow-prediction stats through the authenticated diag surface
    so Layer-2 health reviews can cross-tab audit actionable signals against
    shadow prediction counts without requiring SSH.

    DELEGATES to the public handler rather than re-deriving the rows
    (BL-20260812-DIAG-SHADOW-STATS-MISSING-SOAK-BASIS). This route used to build
    its own row dicts, and when #8774 added the soak-start disclosure
    (`soak_start_basis`) plus the registry-sourced recovery (`soak_started_at` /
    `soak_days`) to the public handler, THIS mirror silently kept serving the
    undisclosed `first_seen` -- on the ONLY surface a relay-bound session can
    reach, which is the surface this endpoint exists for. Measured live on
    2026-08-12 (diag #8800): all 30 models reported `first_seen` inside a
    ~2-minute band at the log's rotation boundary, and BOTH advisory heads had
    been promoted BEFORE it (sol-...-fc-pcv-v2 2026-08-02, btc-...-fc-pcv-v2
    2026-08-04), so their soak read ~6.0 days against a genuinely longer one --
    exactly the `log_censored` case, invisible here.

    A second copy of "what is a soak start" would be free to drift from the one
    the promotion gate reads, so there is now one definition and this is a thin
    auth wrapper around it.
    """
    _require_diag_token(request)
    try:
        from src.web.api.routers.shadow import stats as _shadow_stats
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "shadow_inspector_unavailable", "detail": str(exc)},
        ) from exc
    # The public handler owns `since` parsing (and its 400 on a bad value), the
    # log-path resolution, the log_coverage envelope, and the registry lookup.
    return _shadow_stats(model_id=model_id, stage=stage, since=since)


@router.get("/exchange_positions")
async def get_exchange_positions(
    request: Request,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Read-only **exchange-side** open positions per account — the BROKER's
    truth, not the journal.

    Added 2026-06-19 (BL-20260618-RECONCILE-DUP residual / BL-20260619): a
    web/PM session has no other way to confirm whether a journal orphan
    actually exists on the broker before any cleanup. This mirrors
    ``get_account_balances`` exactly — it opens a brief read-only client per
    account via ``account_open_positions`` (the same primitive the live
    reconciler calls each tick), so it adds no new connection class and places
    NO order. The call is offloaded to the dedicated single-worker
    account-read executor (``src.web.api._account_read_executor``) rather
    than invoked directly — ``account_open_positions``'s IB branch is a
    synchronous, event-loop-driving call that is unsafe to run directly on
    this coroutine's thread (uvicorn's already-running loop); see that
    module's docstring for the full incident writeup
    (BL-20260706-IBCONCURRENCY).

    ``account_id`` filters to one account. Per-account ``positions`` is:
      * ``null``  — could-not-read (logged-out IB gateway / missing creds /
        SDK error). NOT the same as flat.
      * ``[]``    — genuinely flat on the exchange.
      * ``[{symbol, side, size, entry_price, unrealised_pnl}, ...]`` — live.

    Tier 1 — read-only, token-gated, best-effort per account.
    """
    _require_diag_token(request)
    try:
        from src.units.ui.data_loaders import account_open_positions, list_accounts
    except Exception as exc:  # noqa: BLE001  # allow-silent: logged + re-raised as 503 (not swallowed)
        logger.warning("get_exchange_positions: import failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "data_loaders_unavailable", "detail": str(exc)},
        ) from exc

    try:
        accounts = list_accounts() or []
    except Exception as exc:  # noqa: BLE001  # allow-silent: read-only diag; logged, returns empty accounts so the call still answers
        logger.warning("get_exchange_positions: list_accounts failed: %s", exc)
        accounts = []

    out: list[dict[str, Any]] = []
    for acc in accounts:
        aid = (acc or {}).get("account_id")
        if account_id and aid != account_id:
            continue
        positions: Any = None
        err: str | None = None
        try:
            # Offloaded — see the docstring above + _account_read_executor
            # module docstring (BL-20260706-IBCONCURRENCY): a direct call
            # here would drive ib_insync's own event loop on top of
            # uvicorn's already-running one under concurrent requests.
            positions = await run_account_read(account_open_positions, acc)
        except Exception as exc:  # noqa: BLE001  # allow-silent: per-account error surfaced in the row (error + positions=null), logged; one account must not fail the call
            err = f"{type(exc).__name__}: {exc}"
            logger.warning("get_exchange_positions: %s raised %s", aid, exc)
        out.append({
            "account_id": aid,
            "exchange": (acc or {}).get("exchange"),
            # null = could-not-read; [] = flat; list = live positions.
            "positions": positions,
            "count": (len(positions) if isinstance(positions, list) else None),
            "error": err,
        })
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "requested_account_id": account_id,
        "accounts": out,
    }


@router.get("/venue_session")
async def get_venue_session(
    request: Request,
    account_id: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Read-only **IB venue-session verdict + the evidence behind it**.

    Closes BL-20260817-VENUE-SESSION-HAS-NO-READ-SURFACE. The venue gate
    (`src/runtime/ib_trading_hours.py`, shipped #9693) is fail-permissive on
    ``unknown`` — it PLACES and logs a WARNING — and it runs ONLY on a close.
    So on a book that is holding rather than exiting it can be permanently
    unknown while behaving indistinguishably from a working gate on an open
    venue: measured 2026-08-17 as ~24h deployed with zero closes attempted, so
    the question that matters most about the change was unanswerable by waiting.

    **``tz_source`` is the field this route exists for.** ``zoneinfo`` and
    ``pytz`` both yield a working tzinfo, so ``state: "open"`` proves the
    timezone resolved but not THROUGH WHAT. ``US/Eastern`` and ``US/Central``
    are tzdata legacy links absent from slim installs — measured raising in this
    repo's sandbox while ``America/New_York`` resolves — and COMEX/CME report
    precisely those, so on such a host every futures contract rides the ``pytz``
    fallback. That is fine today and one dependency prune from the gate going
    permanently ``unknown``. ``tz_resolved_name`` shows the alias that actually
    worked, so ``US/Eastern`` served as ``America/New_York`` is visible rather
    than assumed.

    ``graded_field`` / ``close_would_send_outside_rth`` report the FUT/STK split
    the close applies (a future is graded on ``tradingHours`` and transmits
    ``outsideRth=True``; an equity is graded on ``liquidHours`` and does not), so
    the verdict can be checked against the flag the order actually carries rather
    than assumed to match.

    Per-account ``session`` is three-state, never collapsed, with ``read_state``
    naming which: ``not_ib`` (nothing to read) · ``could_not_look``
    (``session: null`` — gateway unreachable, dry/shelved, breaker open) ·
    ``session_read`` (a real verdict, itself one of open/closed/unknown).
    A ``could_not_look`` is NOT a closed venue.

    Opens a brief read-only client, places NO order, and cannot refuse a trade.
    Offloaded to the single-worker account-read executor for the same reason
    ``ib_open_orders`` is (BL-20260706-IBCONCURRENCY). Tier 1.
    """
    _require_diag_token(request)
    try:
        from src.units.accounts.clients import account_ib_venue_session
        from src.units.ui.data_loaders import list_accounts
    except Exception as exc:  # noqa: BLE001  # allow-silent: logged + re-raised as 503 (not swallowed)
        logger.warning("get_venue_session: import failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "data_loaders_unavailable", "detail": str(exc)},
        ) from exc

    try:
        accounts = list_accounts() or []
    except Exception as exc:  # noqa: BLE001  # allow-silent: read-only diag; logged, returns empty accounts so the call still answers
        logger.warning("get_venue_session: list_accounts failed: %s", exc)
        accounts = []

    out: list[dict[str, Any]] = []
    for acc in accounts:
        aid = (acc or {}).get("account_id")
        if account_id and aid != account_id:
            continue
        ex = ((acc or {}).get("exchange") or "unknown").lower()
        is_ib = ex in ("interactive_brokers", "ib")
        sess: Any = None
        err: str | None = None
        if is_ib:
            try:
                sess = await run_account_read(account_ib_venue_session, acc, symbol)
            except Exception as exc:  # noqa: BLE001  # allow-silent: per-account error surfaced in the row (error + session=null), logged; one account must not fail the call
                err = f"{type(exc).__name__}: {exc}"
                logger.warning("get_venue_session: %s raised %s", aid, exc)
        out.append({
            "account_id": aid,
            "exchange": (acc or {}).get("exchange"),
            "mode": (acc or {}).get("mode"),
            "read_state": (
                "not_ib" if not is_ib
                else "session_read" if isinstance(sess, dict)
                else "could_not_look"
            ),
            "session": sess,
            "error": err,
        })
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "requested_account_id": account_id,
        "requested_symbol": symbol,
        "count": len(out),
        "accounts": out,
    }


@router.get("/ib_open_orders")
async def get_ib_open_orders(
    request: Request,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Read-only **IB open orders** per account — what the broker is actually
    holding, not a verdict derived from it.

    Closes BL-20260814-NO-IB-OPEN-ORDERS-READ-SURFACE. IB order state had two
    consumers in the codebase and both REDUCE it before anyone sees it:
    ``IBClient.has_protective_orders`` → a boolean, ``protection_coverage`` →
    a covered quantity. Neither can be contradicted from outside, so when the
    MGC take-profit was silently cancelled the coverage read said "covered"
    and no session could ask which orders existed. That stripped take-profit
    sat undetected for seven days. This endpoint reduces nothing.

    Per-account ``orders`` is three-state, never collapsed:

    * ``null``  — **could not look** (non-IB account, gateway unreachable,
      breaker open, ``ib_port`` unset, or a dry/shelved account we never
      dial). NOT the same as "no orders".
    * ``[]``    — a confirmed clean read: the account holds no resting orders.
    * ``[{...}]`` — the rows: ``symbol``/``local_symbol``/``sec_type``,
      ``order_id``/``perm_id``, ``order_type``, ``action``,
      ``total_quantity``, ``aux_price``/``lmt_price``, ``oca_group``, ``tif``,
      ``parent_id``, ``status``, ``filled``/``remaining``.

    ``read_state`` names WHICH of the three a row is (``orders_read`` /
    ``could_not_look`` / ``not_ib``) so a consumer never has to infer it from
    a null, and ``count`` is ``null`` — never ``0`` — when we could not look.

    Reads account-wide via ``reqAllOpenOrders`` (IB order visibility is
    per-client-session, so a readonly client's own ``openTrades()`` would miss
    the trader's brackets entirely). Opens a brief read-only client per
    account and places NO order. Offloaded to the single-worker account-read
    executor for the same reason ``exchange_positions`` is — the IB branch
    drives ib_insync's own event loop and is unsafe on this coroutine's thread
    (BL-20260706-IBCONCURRENCY).

    Tier 1 — read-only, token-gated, best-effort per account.
    """
    _require_diag_token(request)
    try:
        from src.units.accounts.clients import account_ib_open_orders
        from src.units.ui.data_loaders import list_accounts
    except Exception as exc:  # noqa: BLE001  # allow-silent: logged + re-raised as 503 (not swallowed)
        logger.warning("get_ib_open_orders: import failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "data_loaders_unavailable", "detail": str(exc)},
        ) from exc

    try:
        accounts = list_accounts() or []
    except Exception as exc:  # noqa: BLE001  # allow-silent: read-only diag; logged, returns empty accounts so the call still answers
        logger.warning("get_ib_open_orders: list_accounts failed: %s", exc)
        accounts = []

    out: list[dict[str, Any]] = []
    for acc in accounts:
        aid = (acc or {}).get("account_id")
        if account_id and aid != account_id:
            continue
        ex = ((acc or {}).get("exchange") or "unknown").lower()
        is_ib = ex in ("interactive_brokers", "ib")
        orders: Any = None
        err: str | None = None
        if is_ib:
            try:
                orders = await run_account_read(account_ib_open_orders, acc)
            except Exception as exc:  # noqa: BLE001  # allow-silent: per-account error surfaced in the row (error + orders=null), logged; one account must not fail the call
                err = f"{type(exc).__name__}: {exc}"
                logger.warning("get_ib_open_orders: %s raised %s", aid, exc)
        out.append({
            "account_id": aid,
            "exchange": (acc or {}).get("exchange"),
            "mode": (acc or {}).get("mode"),
            # Three states, never collapsed — a null `orders` means we could
            # not look, and `count` stays null rather than reporting 0 orders
            # on an account we never reached.
            "read_state": (
                "not_ib" if not is_ib
                else "orders_read" if isinstance(orders, list)
                else "could_not_look"
            ),
            "orders": orders,
            "count": (len(orders) if isinstance(orders, list) else None),
            "error": err,
        })
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "requested_account_id": account_id,
        "count": len(out),
        "accounts": out,
    }


# ---------------------------------------------------------------------------
# BL-20260821-NO-BYBIT-ACCOUNT-IDENTITY-READ-SURFACE (workplan 0.6)
#
# THE GATING QUESTION for T.2 (pairs hedge mode): `bybit_portfolio` is ALSO
# demo. If it shares a demo UID with `bybit_1`, switching a symbol's position
# mode on `bybit_1` hits BOTH books -- so the hedge-mode design's "scope it
# per-symbol on bybit_1" safety argument would not hold.
#
# config/accounts.yaml cannot settle it. Distinct key ENV VARS prove two key
# pairs exist; they do NOT prove two ACCOUNTS. Two keys can be issued under one
# UID, and a sub-account key carries its own key id while sharing its parent's
# book. So the question was answerable only by asking the venue -- and nothing
# asked it. That is why a prior handoff carried this as "operator-blocked" when
# it is a missing Tier-1 read.
#
# THREE STATES, NEVER COLLAPSED: identity_read / could_not_look / not_bybit.
# And the null discipline is explicit here because Bybit will hand back an
# EMPTY STRING for a field it declines to populate: an empty string or a zero
# is normalised to None and graded could_not_look, never reported as a UID. A
# falsy-but-present UID compared against another falsy-but-present UID would
# read as "these two accounts share an identity", which is the precise wrong
# answer this route exists to prevent.
_IDENTITY_READ = "identity_read"
_IDENTITY_COULD_NOT_LOOK = "could_not_look"
_IDENTITY_NOT_BYBIT = "not_bybit"


def _clean_uid(value: Any) -> str | None:
    """A UID, or None. `""`, `"0"`, `0` and whitespace are NOT identities."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "0":
        return None
    return text


def _bybit_identity(client: Any) -> dict[str, Any]:
    """`userID` + `parentUid` for one bybit account. Read-only; places no order.

    `get_api_key_information` -> /v5/user/query-api. A partial answer (one field
    present, the other blank) is still `identity_read` for what it got, with the
    missing half None -- reporting the whole read as failed would discard a UID
    the venue did give us.
    """
    try:
        resp = client.get_api_key_information()
    except Exception as exc:  # noqa: BLE001  # allow-silent: surfaced in the row
        return {"read_state": _IDENTITY_COULD_NOT_LOOK, "user_id": None,
                "parent_uid": None, "is_sub_account": None,
                "error": f"{type(exc).__name__}: {exc}"}
    result = ((resp or {}).get("result") or {})
    uid = _clean_uid(result.get("userID"))
    parent = _clean_uid(result.get("parentUid"))
    if uid is None and parent is None:
        # The call answered and carried no identity at all -- we still cannot
        # say who this account is, so it is could_not_look, not a clean read.
        return {"read_state": _IDENTITY_COULD_NOT_LOOK, "user_id": None,
                "parent_uid": None, "is_sub_account": None,
                "error": "no_uid_in_response"}
    return {
        "read_state": _IDENTITY_READ,
        "user_id": uid,
        "parent_uid": parent,
        # A sub-account reports a parentUid DIFFERENT from its own userID.
        # None when either half is missing -- never guessed from one.
        "is_sub_account": (parent != uid) if (uid and parent) else None,
        "error": None,
    }


@router.get("/broker_account_status")
def get_broker_account_status(
    request: Request,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Read-only **broker account authorization** flags per account — answers
    *"can this account actually place an order?"*, distinct from whether its
    creds merely authenticate for reads.

    Added 2026-07-01 (BL-20260701-ALPACA-STATUS-VISIBILITY): when an Alpaca
    order returned 401/403 ``unauthorized`` while ``/api/bot/accounts/balances``
    showed ``api_ok:true``, no read path exposed WHY — the balance snapshot only
    proves ``GET /v2/account`` succeeded, NOT that the account is trade-enabled.
    This surfaces the account object's authorization flags so a "reads OK,
    orders blocked" split is one diag call instead of a code trace.

    ``status_flags`` is populated for **Alpaca** accounts (the broker whose
    account object carries these flags): ``status`` (ACTIVE / restricted),
    ``trading_blocked``, ``account_blocked``, ``trade_suspended_by_user``,
    ``transfers_blocked``, ``shorting_enabled``, ``crypto_status``. Other
    exchanges return ``supported:false`` (no analogous per-account flag set).
    Per account:

      * ``status_flags`` ``null`` + ``error`` — could-not-read (creds/host/SDK).
      * ``status_flags`` populated — the broker's live authorization state.

    **Bybit arm (added 2026-08-13, BL-20260701-BYBIT-AVAILABLE-FIELD).** Bybit
    has no analogous flag set, so ``status_flags`` stays null and ``supported``
    stays false — but a bybit row carries ``available_margin``, the answer to a
    question that previously had **no read surface anywhere**: which branch the
    available-margin read took. That value is the input to the sizer's margin
    pre-flight cap, and when it is not the venue's own figure the cap silently
    sizes from total equity — counting the initial margin already pledged to
    open positions as though it were free. Three states, never collapsed:

      * ``read_state: "venue_available"`` (``is_broker_truth``) — the
        account-level ``totalAvailableBalance``. The only measured one.
      * ``read_state: "deprecated_withdrawable"`` (``is_substitute``) — the
        account-level field was absent and the per-coin ``availableToWithdraw``
        was used: a withdrawal-eligibility figure Bybit deprecated for UNIFIED
        accounts on 2025-01-09, standing in for new-order margin.
      * ``read_state: "unavailable"`` (``could_not_look``) — the call raised or
        neither field was present. **Not** "the account has no margin".

    Opens a brief read-only client per account via ``alpaca_client_for`` /
    ``bybit_client_for`` (the same factories the executor uses, so each
    resolves the account's OWN live key+secret pair); places NO order —
    ``get_wallet_balance`` is a read. Tier 1 — read-only, token-gated.
    """
    _require_diag_token(request)
    try:
        from src.units.ui.data_loaders import list_accounts
        from src.units.accounts.clients import alpaca_client_for
    except Exception as exc:  # noqa: BLE001  # allow-silent: logged + re-raised as 503 (not swallowed)
        logger.warning("get_broker_account_status: import failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "loaders_unavailable", "detail": str(exc)},
        ) from exc

    try:
        accounts = list_accounts() or []
    except Exception as exc:  # noqa: BLE001  # allow-silent: read-only diag; logged, empty accounts so the call still answers
        logger.warning("get_broker_account_status: list_accounts failed: %s", exc)
        accounts = []

    out: list[dict[str, Any]] = []
    for acc in accounts:
        aid = (acc or {}).get("account_id")
        if account_id and aid != account_id:
            continue
        exchange = ((acc or {}).get("exchange") or "").lower()
        row: dict[str, Any] = {
            "account_id": aid,
            "exchange": exchange,
            "mode": (acc or {}).get("mode"),
            "account_class": (acc or {}).get("account_class"),
            # NOTE: `supported` means "this exchange exposes an account-
            # authorization FLAG SET", i.e. it qualifies `status_flags` and
            # nothing else. It is deliberately NOT widened to cover the Bybit
            # arm below — a bybit row carries `available_margin` while
            # `status_flags` genuinely stays null, so flipping `supported` true
            # for bybit would make the field describe a payload it is not
            # about (sub-class A of the diagnostic-provenance rule). Read
            # `available_margin is not None` for the bybit half.
            "supported": exchange == "alpaca",
            "status_flags": None,
            "available_margin": None,
            # BL-20260821-NO-BYBIT-ACCOUNT-IDENTITY-READ-SURFACE. `not_bybit`
            # is "there is nothing to read here", which is a different fact
            # from "we tried and failed" -- a non-bybit row must never read as
            # an unreadable bybit one.
            "identity": {"read_state": _IDENTITY_NOT_BYBIT, "user_id": None,
                         "parent_uid": None, "is_sub_account": None,
                         "error": None},
            "error": None,
        }
        if exchange == "alpaca":
            try:
                client = alpaca_client_for(acc)
                if client is None:
                    row["error"] = "not_configured"  # creds env unset
                else:
                    row["status_flags"] = client.account_status()
                    if row["status_flags"] is None:
                        row["error"] = "read_failed"
            except Exception as exc:  # noqa: BLE001  # allow-silent: per-account error surfaced in the row; one account must not fail the call
                row["error"] = f"{type(exc).__name__}: {exc}"
                logger.warning("get_broker_account_status: %s raised %s", aid, exc)
        elif exchange == "bybit":
            # Bybit has no account-authorization flag set like Alpaca's, so
            # `status_flags` stays null here. What it DOES have — and what had
            # no read surface at all until 2026-08-13 — is which branch the
            # available-margin read took. That is the input to the sizer's
            # margin pre-flight cap, and when it is not the venue figure the
            # cap silently sizes from total equity, counting initial margin
            # already pledged to open positions as if it were free.
            #
            # Three states, never collapsed (see execute.read_linear_available_
            # balance): venue_available = broker truth; deprecated_withdrawable
            # = a SUBSTITUTE wearing the label; unavailable = we could not look.
            # A caller reading only `available_usd` cannot tell them apart,
            # which is exactly why establishing what had happened on bybit_2
            # took a proof by contradiction across four diag pulls — see
            # BL-20260813-ICTSCALP-BTC-BYBIT2-BALANCE-REJECTS (whole id on one
            # line: artifact-validity-guard reads a wrapped id as a DIFFERENT,
            # unfiled id, and it is right to).
            #
            # Read-only: get_wallet_balance places no order.
            try:
                from src.units.accounts.clients import bybit_client_for
                from src.units.accounts.execute import (
                    AVAILABLE_STATE_DEPRECATED,
                    AVAILABLE_STATE_UNAVAILABLE,
                    AVAILABLE_STATE_COIN_DERIVED,
                    AVAILABLE_STATE_VENUE,
                    read_linear_available_balance,
                    read_linear_margin_fields,
                )

                client = bybit_client_for(acc)
                if client is None:
                    row["error"] = "not_configured"  # creds env unset
                    row["identity"] = {
                        "read_state": _IDENTITY_COULD_NOT_LOOK,
                        "user_id": None, "parent_uid": None,
                        "is_sub_account": None, "error": "not_configured"}
                else:
                    row["identity"] = _bybit_identity(client)
                    value, read_state, detail = read_linear_available_balance(client)
                    row["available_margin"] = {
                        "read_state": read_state,
                        "available_usd": value,
                        "detail": detail,
                        # Spelled out per state so a reader never has to infer
                        # the semantics from the enum name alone. These four
                        # PARTITION the read states — adding a state without a
                        # flag here would make it read as "none of the above",
                        # which is the collapse this contract exists to stop.
                        "is_broker_truth": read_state == AVAILABLE_STATE_VENUE,
                        "is_coin_derived": read_state == AVAILABLE_STATE_COIN_DERIVED,
                        "is_substitute": read_state == AVAILABLE_STATE_DEPRECATED,
                        "could_not_look": read_state == AVAILABLE_STATE_UNAVAILABLE,
                    }
                    if read_state == AVAILABLE_STATE_UNAVAILABLE:
                        row["error"] = "available_margin_unreadable"
                        # Only when the read FAILED: surface Bybit's own
                        # account-level margin fields verbatim. bybit_2's
                        # response carries totalAvailableBalance PRESENT-but-
                        # EMPTY next to a populated totalEquity and
                        # totalInitialMargin — so the inputs for a derived
                        # available figure are already on the wire. Reported
                        # here so that derivation can be CHECKED against the
                        # independently-reconstructed journal figure before
                        # any code is built on it. Nothing computes from these
                        # yet; interpreting them is an order-path change.
                        fields, ferr = read_linear_margin_fields(client)
                        row["margin_fields"] = fields
                        row["margin_fields_error"] = ferr
            except Exception as exc:  # noqa: BLE001  # allow-silent: per-account error surfaced in the row; one account must not fail the call
                row["error"] = f"{type(exc).__name__}: {exc}"
                logger.warning("get_broker_account_status: %s raised %s", aid, exc)
        out.append(row)
    # T.2 STEP 0 answered by MEASUREMENT rather than inference: group the
    # accounts that were actually read by the UID the venue reported. Only rows
    # with read_state == identity_read participate -- an unreadable account
    # cannot be shown to share OR not share a UID, and lumping it in either
    # direction would manufacture the answer. `unread_bybit_accounts` is the
    # denominator that keeps a partial grouping from reading as complete.
    uid_groups: dict[str, list[str]] = {}
    unread: list[str] = []
    for r in out:
        if r["exchange"] != "bybit":
            continue
        ident = r.get("identity") or {}
        if ident.get("read_state") != _IDENTITY_READ:
            unread.append(r["account_id"])
            continue
        uid = ident.get("user_id")
        if uid:
            uid_groups.setdefault(uid, []).append(r["account_id"])
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "requested_account_id": account_id,
        "accounts": out,
        "bybit_identity_summary": {
            "uid_groups": uid_groups,
            "shared_uid_groups": {u: a for u, a in uid_groups.items() if len(a) > 1},
            "unread_bybit_accounts": unread,
        },
    }


@router.get("/exposure")
def get_exposure(
    request: Request,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Per-account **gross exposure** — the measurement, served from a path
    enforcement never reads.

    Added 2026-08-09. PR #8665 made ``RiskManager.report()["exposure"]`` emit
    ALWAYS (rather than only once a ceiling was declared) so that an operator
    choosing ``max_gross_exposure_pct`` could see the number FIRST. It shipped
    without a read surface: the block reached ``TradingAccount.status()`` via
    ``**risk_report`` and the sole consumer — the Telegram ``/accounts_status``
    renderer — never referenced the key. Written and never read, which is worse
    than absent, because a reviewer sees the field and assumes something acts on
    it (the shape ``provenance-consumer-guard`` exists to catch). This route is
    the missing half.

    Serves the **identical** ``report()["exposure"]`` dict from the real
    ``TradingAccount`` objects — deliberately NOT a reconstruction from balances
    + open rows. A parallel computation would be a second definition of
    "exposure" free to drift from the enforcing one, and would be reported under
    a label describing the code path it is NOT (sub-class B of the
    diagnostic-provenance rule).

    Three states, never collapsed — mirroring
    ``src/units/accounts/exposure.py``:

      * ``policy_declared: false`` — no ceiling declared. There may still be a
        measurement; that is the useful case and the reason this exists.
      * ``measured: false`` — we could not look. ``unmeasured_reason`` names the
        missing input. **Not** the same as flat.
      * ``exposure_multiple: 0.0`` — we looked; the account is flat.

    **Connection-free**: ``observe_exposure()`` reads equity from the balance
    snapshot and notional from the journal, so this opens no broker socket and
    places no order. Cannot refuse a trade — it never consults policy to
    compute, only to report what was declared. Tier 1, token-gated.
    """
    _require_diag_token(request)
    try:
        from src.units.accounts import load_accounts
    except Exception as exc:  # noqa: BLE001  # allow-silent: logged + re-raised as 503 (not swallowed)
        logger.warning("get_exposure: import failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "accounts_unavailable", "detail": str(exc)},
        ) from exc

    try:
        accounts = load_accounts() or []
    except Exception as exc:  # noqa: BLE001  # allow-silent: logged + re-raised as 503 (not swallowed)
        logger.warning("get_exposure: load_accounts failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "load_accounts_failed", "detail": str(exc)},
        ) from exc

    out: list[dict[str, Any]] = []
    for acct in accounts:
        aid = getattr(acct, "name", None) or getattr(acct, "account_id", "?")
        if account_id and aid != account_id:
            continue
        row: dict[str, Any] = {
            "account_id": aid,
            "exchange": getattr(acct, "exchange", None),
            "account_class": getattr(acct, "account_class", None),
            "exposure": None,
            "error": None,
        }
        try:
            rm = getattr(acct, "risk_manager", None)
            if rm is None:
                row["error"] = "no_risk_manager"
            else:
                # The SAME call the enforcing side reports through. If this ever
                # needs to become something else, the fix is in report(), not a
                # second copy here.
                row["exposure"] = rm.report().get("exposure")
        except Exception as exc:  # noqa: BLE001  # allow-silent: per-account error surfaced in the row; one account must not fail the call
            row["error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("get_exposure: %s raised %s", aid, exc)
        out.append(row)

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "requested_account_id": account_id,
        "count": len(out),
        "accounts": out,
    }


@router.get("/position_telemetry")
def get_position_telemetry(
    request: Request,
    limit: int = 500,
    strategy: str | None = None,
) -> dict[str, Any]:
    """M31 **P3** — the read half of the position-telemetry record.

    P2 shipped the writer and NOTHING read it back. That is the
    ``exit_price_source`` shape this repo already paid for (written in 12 files,
    branched on in one, and it produced a "-$6,358 exit leak" that did not
    exist), and §8 of the M31 decisions doc names it as P3's own failure signal:
    *"rows accruing a month with no consumer."* This route is the first
    consumer.

    It is deliberately **not** a second copy of
    ``/api/bot/db/table/position_telemetry`` — that already dumps the rows. It
    adds the three things the TABLE CANNOT SAY, resolved through
    ``src.runtime.position_telemetry`` so this surface and ``/api/bot/positions``
    can never drift into two answers:

    * **``lifecycle``** — is this row FINAL? The table is UPSERT-on-
      ``order_package_id`` with **no status column**: when a trade closes its row
      simply stops being updated, so a closed row is byte-shaped like an open
      one. The only in-table hint is a staler ``updated_at``, which is **not a
      signal** — a quiet leg and a closed leg both go stale. Measured 2026-08-17:
      14 rows, **13 open + 1 closed**, and the closed one (trade 4697,
      ``trend_donchian_sol_4h``) was findable only via this join. Four states,
      never collapsed: ``open`` / ``closed`` / ``unknown_no_trade_id`` (the
      package never filled) / ``unknown_trade_absent`` (a trade id the trades
      table does not have).
    * **``finality_source``** — WHICH evidence decided that, which is a
      different question from the verdict and must not be folded into it.
      ``"stamped"`` = the close path wrote ``terminal_state='final'`` on the row
      itself (the Tier-2 terminal writer, 2026-08-17, closing
      ``PB-20260817-TELEMETRY-HAS-NO-TERMINAL-SNAPSHOT``) · ``"derived_join"`` =
      finality came only from the ``trades`` join, so the row PREDATES the
      writer · ``"not_final"`` = still in flight · ``"unknown"`` = we could not
      look (no trade id, or a trade id ``trades`` does not have).
      **Read ``summary.by_finality_source`` beside ``final_rows``:** a
      ``final_rows`` count that is entirely ``derived_join`` on rows closed
      AFTER the writer deployed means the close hook is not firing — a
      condition the split makes visible and a bare count hides. A consumer
      reading the table DIRECTLY (Data Explorer, an ad-hoc query, a future
      lever) sees only the stamp, never the join, which is exactly why the
      stamp had to exist.
    * **``peak_pct_of_cap``** — how close the trade EVER got to its venue
      ceiling. The stored ``pct_of_cap`` is computed from ``open_r``, i.e. where
      it is NOW. Both are right for what they name; only this one answers "was
      the ceiling ever approached", which is the M31 P4 Check-A quantity.
    * **``arm_reach``** — can this row's declared lever arm be reached under
      this row's own ceiling at all? ``arm_r > cap_r`` means the lever cannot
      fire on this trade however it goes
      (``BL-20260816-TRAIL-DECAY-ARM-R-SITS-ABOVE-THE-VENUE-TP-CAP``). Four
      states: ``reachable`` / ``unreachable`` / ``no_arm_declared`` /
      ``unmeasured``.

    ⚠️ **``peak_r`` is a LOWER BOUND on true MFE, on every row** — the last
    write precedes the close by up to one exit-loop pass, and a bar extreme
    cannot see an intrabar excursion (hence ``peak_provenance: estimated``,
    never ``measured``). ``peak_r_is_lower_bound: true`` is stamped on every row
    so a consumer cannot average or gate on it without meeting that fact.

    Read ``summary.final_rows`` beside any distribution claim — the
    ``max_multiple``/``measured_n`` discipline. A statistic over closed rows is
    not a statistic over the fleet.

    **Observe-only.** Reads one read-only SQLite connection, opens no socket,
    places no order, and cannot refuse a trade. A lever that READS this to
    change an exit is **M31 P5 and Tier-3**. Tier 1, token-gated.
    """
    _require_diag_token(request)
    try:
        from src.runtime.position_telemetry import read_records
    except Exception as exc:  # noqa: BLE001  # allow-silent: logged + re-raised as 503
        logger.warning("get_position_telemetry: import failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "telemetry_unavailable", "detail": str(exc)},
        ) from exc
    return read_records(limit=limit, strategy=strategy)


@router.get("/tick_cost")
def get_tick_cost(request: Request) -> dict[str, Any]:
    """Per-tick wall-clock cost of the trader's hook chain (2026-08-09).

    ``src/main.py``'s tick runs a dozen best-effort hooks — order monitor,
    pairs executor, macro thesis, five prop prompts, two reachability alerts,
    the IB-state dump, the exposure soak. Each is individually bounded and
    documented as cheap; **nothing measured the SUM**, which is the shape of
    both June 2026 wedges (each new component cheap in isolation, the total
    never watched).

    Read ``max_ms`` beside ``ticks_measured``: the max is the statistic that
    matters (a mean that looks fine while the peak freezes the heartbeat is
    exactly the 2026-06-09 incident), and a max over 3 ticks is not the claim a
    max over 3000 is. ``max_at_utc`` dates the peak. Counters are per-PROCESS,
    so a restart resets them — ``process_started_utc`` says from when.

    **Measurement only — no budget is enforced.** A cap with no distribution
    behind it is the exposure-ceiling mistake (`gross-exposure-governance-DESIGN.md`
    § 6): a ceiling below normal operation silently throttles correct work.

    ``present:false`` until the trader has written the file (persisted on the
    ``TICK_COST_WRITE_SECONDS`` cadence, default 300s). A large ``age_seconds``
    while the trader is otherwise ticking means the writer is not running.
    Pure file read — no socket, no order path. Tier 1.
    """
    _require_diag_token(request)
    from src.runtime.tick_cost import read_state
    return read_state()


@router.get("/bybit_open_orders")
async def get_bybit_open_orders(
    request: Request,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Read-only **Bybit resting orders + position-level protection**.

    The Bybit sibling of ``/api/diag/ib_open_orders``, and it exists for the
    same reason one level up: every consumer of Bybit protection state REDUCES
    it before anyone sees it. ``order_monitor._bybit_position_protection``
    returns a covered QUANTITY; its Full-mode branch returns
    ``covered_qty == size`` on any ``pos["stopLoss"]`` that is merely non-empty
    and not ``"0"``. So the *string* is the whole test, the PRICE is never read,
    and a position whose stop sits anywhere at all grades fully covered.

    That is criterion 5 of BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND, which
    was written about IB and explicitly left Bybit unchecked. It is checked now,
    and the blast radius is larger than the row's: the IB instance is
    ``ib_paper``, whereas **``bybit_2`` is mainnet**.

    ⚠️ **BOTH collections are the protection; reading one is reading half.**
    Under ``BYBIT_TPSL_MODE=full`` there is NO resting order -- the stop lives
    on the position row as ``stopLoss``. A surface that dumped only open orders
    would report zero legs for a correctly-protected position and a consumer
    would grade it naked, which would drive a re-arm on a position that is
    already protected. ``positions[]`` carries the Full-mode levels;
    ``orders[]`` carries the Partial-mode legs with their trigger prices.

    Per-account ``result`` is three-state, never collapsed:

    * ``null``  -- **could not look** (non-Bybit account, creds missing, a spot
      account with no derivative position to protect, or an SDK error). NOT the
      same as "nothing is resting".
    * ``{...}`` -- a confirmed clean read; empty lists mean genuinely nothing.

    ``read_state`` names WHICH (``orders_read`` / ``could_not_look`` /
    ``not_bybit``) so a consumer never infers it from a null, and
    ``order_count`` / ``position_count`` stay ``null`` -- never ``0`` -- when we
    could not look. An unset venue price is reported ``null``, never ``0.0``:
    a zero would read as a stop AT zero and compare as hugely divergent from a
    declared level, when the truth is that no stop is set.

    Grades nothing, re-arms nothing, opens no order path, cannot refuse a
    trade. Whether ``_bybit_position_protection`` itself should carry the price
    axis is that row's criterion 4 -- a Tier-2/3 change to the ENFORCING path,
    and deliberately not proposed here.

    Tier 1 -- read-only, token-gated, best-effort per account.
    """
    _require_diag_token(request)
    try:
        from src.units.accounts.clients import account_bybit_open_orders
        from src.units.ui.data_loaders import list_accounts
    except Exception as exc:  # noqa: BLE001  # allow-silent: logged + re-raised as 503 (not swallowed)
        logger.warning("get_bybit_open_orders: import failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "data_loaders_unavailable", "detail": str(exc)},
        ) from exc

    try:
        accounts = list_accounts() or []
    except Exception as exc:  # noqa: BLE001  # allow-silent: read-only diag; logged, returns empty accounts so the call still answers
        logger.warning("get_bybit_open_orders: list_accounts failed: %s", exc)
        accounts = []

    out: list[dict[str, Any]] = []
    for acc in accounts:
        aid = (acc or {}).get("account_id")
        if account_id and aid != account_id:
            continue
        is_bybit = ((acc or {}).get("exchange") or "unknown").lower() == "bybit"
        result: Any = None
        err: str | None = None
        if is_bybit:
            try:
                result = await run_account_read(account_bybit_open_orders, acc)
            except Exception as exc:  # noqa: BLE001  # allow-silent: per-account error surfaced in the row (error + result=null), logged; one account must not fail the call
                err = f"{type(exc).__name__}: {exc}"
                logger.warning("get_bybit_open_orders: %s raised %s", aid, exc)
        ok = isinstance(result, dict)
        out.append({
            "account_id": aid,
            "exchange": (acc or {}).get("exchange"),
            "mode": (acc or {}).get("mode"),
            "account_class": (acc or {}).get("account_class"),
            "read_state": (
                "not_bybit" if not is_bybit
                else "orders_read" if ok
                else "could_not_look"
            ),
            "result": result,
            "position_count": (len(result.get("positions") or []) if ok else None),
            "order_count": (len(result.get("orders") or []) if ok else None),
            "error": err,
        })
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "requested_account_id": account_id,
        "count": len(out),
        "accounts": out,
    }
