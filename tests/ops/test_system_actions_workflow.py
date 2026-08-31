"""Tests for the system-actions GitHub workflow + wrapper scripts.

These tests are static — they parse YAML and read shell scripts; they
do NOT execute the workflow or SSH anywhere. They guard the contract
documented in `docs/claude/system-actions.md`:

* The action allowlist is a single source of truth across the
  workflow, the wrappers, and the doc.
* No freeform / arbitrary-command input ever sneaks into the workflow.
* Every wrapper script exists, parses with `bash -n`, uses
  `set -euo pipefail`, and sources `_lib.sh`.

Note on the exec bit: wrappers are invoked via `bash <path>` from
`system-actions.yml` (see REMOTE_CMD in the Execute step), so the
+x bit on disk is not load-bearing for the workflow path. Older
wrappers were committed exec; newer ones added through the GitHub
Contents API land as 100644. We don't enforce +x in tests for that
reason — `bash -n` and the workflow's explicit `bash <path>` give
us the coverage that matters.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "system-actions.yml"
OPS_DIR = REPO_ROOT / "scripts" / "ops"
DOC = REPO_ROOT / "docs" / "claude" / "system-actions.md"

# Single source of truth for the allowlist as expected by every layer.
# Single source of truth for the system-action allowlist. Must stay in
# lockstep with .github/workflows/system-actions.yml (the `action` choice
# options, the Tier classification case, and the SCRIPT-name case) and with
# docs/claude/system-actions.md. The guard tests below assert all three
# agree. Previously this map listed only 14 of the live actions while the
# workflow had grown to 30; the drift went unnoticed because CI runs
# pytest-collect (import only), not the test bodies.
EXPECTED_ACTIONS = {
    # Tier 1 — read-only / analysis
    "status-check": "status_check.sh",
    # Read-only listening-port + host-firewall inventory (security audit 2026-06-28).
    "list-listening-ports": "list_listening_ports.sh",
    "pull-latest-logs": "pull_logs.sh",
    # PR #1698: read-only IB Gateway container status + recent logs.
    "gateway-logs": "gateway_logs.sh",
    "inspect-closed-pnl": "inspect_closed_pnl_action.sh",
    "bybit-account-audit": "bybit_account_audit_action.sh",
    # Read-only broker-truth audit of Bybit protective-bracket COVERAGE
    # (SL-covered qty vs position size, per-trade leg liveness) + the
    # three-source effective BYBIT_TPSL_MODE read. Places/cancels nothing.
    "bybit-bracket-audit": "bybit_bracket_audit_action.sh",
    "strategy-performance-audit": "strategy_performance_audit_action.sh",
    "monitor-miss-analysis": "monitor_miss_analysis_action.sh",
    "vwap-backtest-sweep": "vwap_backtest_sweep_action.sh",
    # Tier 2 — mutating / restart / derived-artifact writes
    "pull-and-deploy": "pull_and_deploy.sh",
    "restart-bot-service": "restart_bot.sh",
    # The lifecycle pair. stop/start are SEPARATE actions, not a restart with a
    # timeout: a stop whose start is bundled into it cannot be held open for the
    # work the stop was taken for (an IB cancel needs the trader's clientId free).
    "stop-bot-service": "stop_bot.sh",
    "start-bot-service": "start_bot.sh",
    "reboot-vm": "reboot_vm.sh",
    "enable-closed-flat-invariant": "enable_closed_flat_invariant.sh",
    "disable-closed-flat-invariant": "disable_closed_flat_invariant.sh",
    "enable-signal-dual-write": "enable_signal_dual_write.sh",
    "disable-signal-dual-write": "disable_signal_dual_write.sh",
    "set-mobile-push-secrets": "set_mobile_push_secrets.sh",
    "enable-insights-generator": "enable_insights_generator.sh",
    "disable-insights-generator": "disable_insights_generator.sh",
    "inspect-insights": "inspect_insights.sh",
    "kick-insights": "kick_insights.sh",
    "backfill-pnl-nulls": "backfill_pnl_nulls_action.sh",
    "backfill-orphan-pnl": "backfill_orphan_pnl_action.sh",
    "backfill-closed-null-pnl": "backfill_closed_null_pnl_action.sh",
    "backfill-monitor-closed-pnl": "backfill_monitor_closed_pnl_action.sh",
    "revert-backfill-monitor-closed-pnl": "revert_backfill_monitor_closed_pnl_action.sh",
    "mark-reconciler-incomplete": "mark_reconciler_incomplete_action.sh",
    "mark-operator-flattened": "mark_operator_flattened_action.sh",
    "rebuild-pnl-from-bybit": "rebuild_pnl_from_bybit_action.sh",
    "backfill-shadow-predictions": "backfill_shadow_predictions_action.sh",
    # 2026-06-15 — retro-stamp trades.account_class from accounts.yaml,
    # correcting historical ib_paper rows (paper trades that were is_demo=0).
    "backfill-account-class": "backfill_account_class_action.sh",
    # 2026-06-17 — retro-fill trades.closed_at (single source of truth for the
    # close timestamp, P1-B) on historical rows; runs --also-account-class so the
    # same audited pass also closes any remaining account_class gap (P1-E).
    "backfill-closed-at": "backfill_closed_at_action.sh",
    # 2026-07-17 — one-shot backfill of the fixed-model round-trip cost ESTIMATE
    # onto uncosted historical closed trades (MB-20260629-ALLOC-COSTCAP). Writes
    # only fee_taker_usd + cost_source='estimate'; never pnl / order path.
    "backfill-trade-costs": "backfill_trade_cost_estimates_action.sh",
    # Slice B / B0 — promote the entry orderId from notes.trade_id to the
    # first-class trades.broker_order_id join key (MB-20260629-ALLOC-COSTCAP).
    "backfill-broker-order-id": "backfill_broker_order_id_action.sh",
    # Slice B / B2 — FIFO-attribute broker-truth round-trip fees (join by
    # broker_order_id + fills store) onto cleanly-attributable closed trades.
    "backfill-broker-truth-costs": "backfill_broker_truth_costs_action.sh",
    # 2026-07-31 — re-derive FABRICATED exit prices from the broker fills
    # already on disk in exchange_fills.sqlite
    # (BL-20260730-BROKER-TRUTH-COLLECTED-NEVER-READ). Dry-run by default;
    # apply:1 makes it a Tier-2 money-DB write.
    "backfill-fabricated-exits": "backfill_fabricated_exits_action.sh",
    # 2026-08-23 — relabel trades.exit_reason on rows PRICED after they were
    # closed, whose label stayed frozen at the reconciler's generic fallback
    # (BL-20260823-EXIT-LABEL-FROZEN-ON-THE-ANCHORED-PRICE-PATH). The price's
    # provenance gates the label; a FABRICATED price is REFUSED and the refusal
    # is stamped. Touches no monetary field. Dry-run by default; apply:1 makes
    # it a Tier-2 money-DB write.
    "backfill-exit-labels": "backfill_exit_labels_action.sh",
    # 2026-06-22 — normalise existing epoch-ms trades.closed_at rows to ISO
    # (BL-20260620-RECONCILER-CLOSEDAT-MS); distinct from backfill-closed-at
    # (which fills NULLs). Wraps migrate_closed_at_to_iso.py.
    "migrate-closed-at-iso": "migrate_closed_at_to_iso_action.sh",
    "pull-exchange-fills": "pull_exchange_fills_action.sh",
    # Bybit's OWN wallet ledger (/v5/account/transaction-log) into the
    # venue-truth store. The on-demand sibling of the hourly 7-day timer:
    # only this path can ask for the deep ACTION_DAYS window a HISTORICAL
    # gap needs, and the deep window is WALKED in <=7-day chunks because
    # Bybit caps the queryable RANGE, not the retention.
    "pull-bybit-transaction-log": "pull_bybit_transaction_log_action.sh",
    # Slice B / B1 — pull perp funding into the exchange_funding store so the
    # broker-truth sweep can attribute funding_paid_usd.
    "pull-exchange-funding": "pull_exchange_funding_action.sh",
    # M24 P2 — read-only net-R re-grade scorecard (net-of-cost R per strategy /
    # cell + sign-flip flag). Opens trade_journal.db mode=ro; no write.
    "net-r-regrade": "net_r_regrade_action.sh",
    # 2026-05-28 — paced IBKR MES historical pull on the live VM (MB-20260528-002).
    "pull-mes-ibkr-history": "pull_mes_ibkr_history.sh",
    # 2026-06-01 — same wrapper baked to a DAILY multi-year pull (native MES 1d
    # back to ~2019) for validating mes_trend_long_1d on real MES vs SPX proxy.
    "pull-mes-ibkr-history-daily": "pull_mes_ibkr_history.sh",
    # 2026-07-07 — generalized symbol-parameterized sibling so MGC/MHG (metals
    # sleeve) can be backfilled from native IBKR history, not just MES (#5851).
    "pull-ibkr-history": "pull_mes_ibkr_history.sh",
    "set-account-mode": "set_account_mode.sh",
    # enable-mes / disable-mes removed 2026-05-22 — they flipped a
    # forbidden second gate (MULTI_SYMBOL_ENABLED). The traded-symbol set
    # is now derived from accounts.yaml; MES gating is the account `mode:`.
    "fix-data-dir": "fix_data_dir.sh",
    "rotate-account-keys": "rotate_account_keys.sh",
    "init-diag-token": "init_diag_token.sh",
    # 2026-05-24 bots overhaul — autonomous Claude infra.
    "send-ping": "send_ping_action.sh",   # Tier 1: immediate ping, no restart
    # 2026-06-17 — fire one TEST prop ticket through the real prop_signal path
    # (FCM + prop Telegram bot). Tier 1: notify-only, nothing journaled.
    "send-prop-test-ping": "send_prop_test_ping_action.sh",
    # M7 — autonomous strategy-review-packet generator (Tier 1: read-only
    # SQL + write to runtime_logs/strategy_reviews/).
    "generate-strategy-review-packets": "generate_strategy_review_packets_action.sh",
    # 2026-07-06 (MB-20260706-GRADING-DELTA) — Tier 1: read-only rubric scoring
    # against the live trade_journal.db, emits ONLY the ungraded delta as NDJSON
    # (never writes comms/claude_strategy_scores.jsonl on the VM).
    "grade-closed-trades": "grade_closed_trades_action.sh",
    "set-env": "set_env.sh",              # Tier 2: .env upsert + service restart
    # 2026-08-10 (BL-20260810-CONVICTION-SIZING-APPLY-LIVE-VS-DOC) — the READ
    # half of set-env. Tier 1: reports /proc/<MainPID>/environ + the unit's
    # declared EnvironmentFiles for an allowlisted key, flagging a mismatch as a
    # pending restart. No write, no restart, no socket; secret-NAMED keys are
    # fingerprinted, never printed (this action's stdout lands on a public issue).
    "get-env": "get_env_action.sh",
    # 2026-05-27 — strips systemd-EnvironmentFile-noncompliant lines from .env
    # (the orphan FCM-JSON-blob case that bled a PEM private key into the
    # journalctl tail on issue #2157). Tier 2: .env mutation + service restart.
    "scrub-env-noncompliant": "scrub_env_noncompliant.sh",
    # 2026-06-05 restart-loop incident — pause/resume the liveness-watchdog
    # autoheal loop (ict-liveness-watchdog.timer) so a trader stuck in a
    # watchdog-restart loop (first tick slower than the autoheal window) can
    # complete a tick + write a heartbeat. Symmetric pair.
    "pause-autoheal": "pause_autoheal.sh",
    "resume-autoheal": "resume_autoheal.sh",
    # 2026-06-05 incident — diagnose + correct VM clock drift (NTP).
    "sync-clock": "sync_clock.sh",
    # 2026-06-10 — purge the retired Cloudflare tunnel unit from the live VM
    # (the repo cleanup #3233 removed the unit file from source control but
    # install_systemd_units.sh is install-only, so an already-installed
    # ict-cloudflared-tunnel.service kept running). Idempotent no-op if absent.
    "purge-cloudflared": "purge_cloudflared.sh",
    "purge-vm-runner": "purge_vm_runner.sh",
    # 2026-06-19 — one-shot guarded flatten of a single IB exchange position
    # (BL-20260618-RECONCILE-DUP residual: the stranded ib_paper MGC short).
    "flatten-ib-position": "flatten_ib_position_action.sh",
    # 2026-08-16 — cancel ONE resting IB order by id. NOT a flatten: it places
    # nothing. Closes BL-20260816-NO-PER-ORDER-IB-CANCEL, where a stranded
    # ib_paper/MGC market sell was unreachable because the only two options
    # were flatten-ib-position (which PLACES another order) and
    # reqGlobalCancel (which strips every protective stop on the account).
    # IB binds an order to its submitting clientId, so the script connects AS
    # the owning client; it refuses a protective leg and a trader-band
    # clientId unless explicitly forced.
    "cancel-ib-order": "cancel_ib_order_action.sh",
    # 2026-08-16 — attach the DECLARED take-profit to a target-naked IB
    # position by joining the stop's existing OCA group, so the stop is
    # cancelled by IBKR when the target fills instead of surviving onto a
    # flat book. Cancels nothing; refuses on a stray order, 2+ stop groups,
    # or a qty mismatch (BL-20260816-COVERAGE-IS-ONE-SIDED).
    "attach-ib-target": "attach_ib_target_action.sh",
    # 2026-06-29 — Bybit sibling of flatten-ib-position: one-shot guarded
    # reduce-only flatten of a single Bybit exchange position (close an
    # account before a different-account key rotation).
    "flatten-bybit-position": "flatten_bybit_position_action.sh",
    "switch-bybit-position-mode": "bybit_switch_position_mode_action.sh",
    # 2026-07-15 — Alpaca sibling of flatten-bybit-position: one-shot guarded
    # native flatten of a single Alpaca position. AlpacaClient.close cancels the
    # reserving protective bracket (held_for_orders) then market-closes — the
    # on-demand fix for BL-20260708-ALPACA-CLOSE-QTY-AVAILABLE.
    "flatten-alpaca-position": "flatten_alpaca_position_action.sh",
    # 2026-07-15 — JOURNAL-side companion to flatten-alpaca-position: close a
    # stranded open journal row whose broker position is already flat (the
    # shelved-dry_run-account gap where the reconciler can't close-on-disappear).
    # Mode-agnostic broker-flat read is a hard gate; DRY-RUN by default.
    "close-stranded-journal-row": "close_stranded_journal_row_action.sh",
    # 2026-06-24 — orphan-flap hardening #5: collapse historical phantom
    # orphan-flap duplicates so each physical position is ONE reconciled row
    # (void-flag dups as reconcile_status='superseded'). DRY-RUN by default;
    # apply is gated + takes a DB backup. Pure journal hygiene.
    "reconcile-orphan-history": "reconcile_orphan_history_action.sh",
    # 2026-06-28 — one-shot cleanup of the pre-fix options-account
    # orphan-adoption artifacts (root cause fixed in #4858 + #4867):
    # void-flag the historical phantom paper rows that the equity-pricing
    # sweep fabricated. DRY-RUN by default; apply gated + DB backup.
    "supersede-options-adoption-artifacts": "supersede_options_adoption_artifacts_action.sh",
    # 2026-07-07 — one-shot cleanup of the alpaca_paper external-reset
    # orphan-adoption artifacts (BL-20260707-ALPACA-RESET; live-path fix in
    # #5951): void-flag the historical BARE phantom paper rows
    # (strategy_name='orphan_adopt' + NULL order_package_id) the equity-pricing
    # sweep fabricated. DRY-RUN by default; apply gated + DB backup; optional
    # ids: allowlist. A genuinely-reattached orphan is categorically excluded.
    "supersede-reset-orphan-artifacts": "supersede_reset_orphan_artifacts_action.sh",
    # 2026-07-19 — one-shot void-flag of the historical INTENT-REDUCE phantom-pnl
    # rows (BL-20260711; write-path fix in #6926): a closed intent_reduce
    # bookkeeping leg carrying a non-NULL pnl (the parent's close attributed onto
    # it with an entry==exit signature). DRY-RUN by default; apply gated + DB
    # backup; optional ids: / equal_only: narrowing. Void-flags ONLY the reduce
    # leg, never the parent close.
    "supersede-intent-reduce-phantom-pnl": "supersede_intent_reduce_phantom_pnl_action.sh",
    # 2026-07-06 — one-shot repair of the mis-linked ETH prop close
    # (BL-20260706-PROP-CLOSE-MISLINK; root cause fixed in #5744): relink the
    # close fill to the real position ticket, close it, restore the phantom.
    # DRY-RUN by default; apply gated + DB backup; guarded + idempotent.
    "fix-prop-mislinked-close": "prop_fix_mislinked_close_action.sh",
    # 2026-08-20 — prop-journal hygiene for fills admitted with NO direction
    # (BL-20260820-PROP-FILL-DIRECTION-ADMISSION-GAP). _position_key needs
    # (account, symbol, direction) but ingest_report only validates the first
    # two, so a directionless fill keys apart from its own close and reads
    # OPEN for ever. Resolves the field from the linked ticket through the
    # canonical mapper. Unlike its three predecessors this one is a PREDICATE,
    # not a row id. DRY-RUN by default; apply gated + DB backup; guarded +
    # idempotent; touches prop_fills only.
    "repair-prop-fill-direction": "repair_prop_fill_direction_action.sh",
    # 2026-06-30 — clear the daily_risk_state row for one account so
    # INTRADAY_DRAWDOWN counters reset without a full service restart.
    "reset-daily-risk-state": "reset_daily_risk_state.sh",
    # 2026-07-09 — one-shot repair of legacy malformed-JSON blobs in
    # trade_journal.db (BL-20260618 / BL-20260709; write-path fixed in RISK-1
    # Task 2 #6037). DRY-RUN by default; apply gated; idempotent by construction.
    "repair-malformed-notes": "repair_malformed_notes_action.sh",
    # 2026-07-20 — one-shot honest-null repair of the Jun-2026 netted-position
    # misattribution rows (BL-20260720-ICTSCALP-PASTSTOP-EXITS). DRY-RUN by
    # default; apply gated; signature-verified so it is idempotent.
    "repair-netted-rows": "repair_netted_rows_action.sh",
    # 2026-08-01 — W1 reconciliation (BL-20260731-W1-JOURNAL-EXCHANGE-
    # DIVERGENCE-MAP): closes the 4 bybit_1 netting phantom-open ict_scalp
    # rows whose exchange share was flattened by position-level exits.
    # DRY-RUN by default; apply gated; signature-pinned so it is idempotent.
    "reconcile-netting-phantom-rows": "reconcile_netting_phantom_rows_action.sh",
    # 2026-08-02 — GENERAL same-moment netting partial-close reconcile
    # (BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED, option (c)+(b)):
    # the generalization of reconcile-netting-phantom-rows from a signature-
    # pinned one-shot to a cadence-safe on-demand job. Reads a LIVE same-moment
    # exchange snapshot (netting_reconcile_snapshot.py) + closes the surplus
    # open rows so each netted group's journal sum matches the broker
    # (reconcile_netting_rows.py). pnl left UNMEASURED; pairs excluded;
    # unreadable accounts skipped. DRY-RUN by default; apply gated; idempotent.
    "reconcile-netting-rows": "reconcile_netting_rows_action.sh",
    # 2026-08-06 — BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS. Marks rows
    # carrying a DUPLICATED netted broker pnl as FABRICATED
    # (exit_price_source='netted_duplicate_unattributed'), preserving the
    # original under pre_remediation_exit_price_source. Never rewrites pnl —
    # there is no defensible per-row value; the magnitude belongs to the netted
    # POSITION. Biased toward under-marking (qty-spread + $1.00 floor).
    # DRY-RUN by default (opens the DB mode=ro), apply gated, idempotent.
    "mark-netted-duplicate-pnl": "mark_netted_duplicate_pnl_action.sh",
    # 2026-07-20 — venue validation for BYBIT_TPSL_MODE=partial (qty-scoped
    # brackets, Fix 2 of BL-20260720-ICTSCALP-PASTSTOP-EXITS). Demo-locked
    # to bybit_1; places + cleans up two tiny netted orders.
    "validate-partial-tpsl": "validate_partial_tpsl_action.sh",
    "validate-bybit-naked-rearm": "validate_bybit_naked_rearm_action.sh",
    # 2026-07-21 — stopgap cleanup for BL-20260721-BYBIT2-XRP-TPSL-LEGCAP:
    # cancels stale/duplicate Partial-tpsl legs accumulated on one symbol
    # (keeps the most-recent SL/TP), relieving Bybit's 20-leg cap so
    # legitimate stop amends stop silently failing. DRY-RUN by default;
    # apply gated; refuses on a flat position or zero SL legs found.
    "cancel-stale-tpsl-legs": "cancel_stale_tpsl_legs_action.sh",
    # 2026-07-21 — structural-fix completion for BL-20260721-BYBIT2-XRP-TPSL-LEGCAP:
    # backfills trades.sl_order_id/tp_order_id for a Bybit partial-tpsl
    # position that was already open when PR #7321's entry-time capture
    # deployed (those rows would otherwise fall back to the legacy
    # add-a-leg path forever). DRY-RUN by default; apply gated; refuses on
    # a flat position, ambiguous (>1) live legs of one type, or more than
    # one open untracked trade row.
    "backfill-tpsl-leg-ids": "backfill_tpsl_leg_ids_action.sh",
}

TIER_2_ACTIONS = {
    "pull-and-deploy",
    "restart-bot-service",
    "stop-bot-service",
    "start-bot-service",
    "reboot-vm",
    "enable-closed-flat-invariant",
    "disable-closed-flat-invariant",
    "enable-signal-dual-write",
    "disable-signal-dual-write",
    "backfill-pnl-nulls",
    "backfill-orphan-pnl",
    "backfill-closed-null-pnl",
    "backfill-monitor-closed-pnl",
    "revert-backfill-monitor-closed-pnl",
    "mark-reconciler-incomplete",
    "mark-operator-flattened",
    "rebuild-pnl-from-bybit",
    "backfill-shadow-predictions",
    "backfill-account-class",
    "backfill-closed-at",
    "backfill-trade-costs",
    "backfill-broker-order-id",
    "backfill-broker-truth-costs",
    "backfill-fabricated-exits",
    "backfill-exit-labels",
    "migrate-closed-at-iso",
    "pull-exchange-fills",
    "pull-bybit-transaction-log",
    "pull-exchange-funding",
    "pull-mes-ibkr-history",
    "pull-mes-ibkr-history-daily",
    "pull-ibkr-history",
    "set-account-mode",
    "fix-data-dir",
    "rotate-account-keys",
    "init-diag-token",
    "set-env",
    "scrub-env-noncompliant",
    "pause-autoheal",
    "resume-autoheal",
    "sync-clock",
    "purge-cloudflared",
    "purge-vm-runner",
    "flatten-ib-position",
    "cancel-ib-order",
    "attach-ib-target",
    "flatten-bybit-position",
    "switch-bybit-position-mode",
    "flatten-alpaca-position",
    "close-stranded-journal-row",
    "reconcile-orphan-history",
    "supersede-options-adoption-artifacts",
    "supersede-reset-orphan-artifacts",
    "supersede-intent-reduce-phantom-pnl",
    "fix-prop-mislinked-close",
    "repair-prop-fill-direction",
    "reset-daily-risk-state",
    "repair-malformed-notes",
    "repair-netted-rows",
    "reconcile-netting-phantom-rows",
    "reconcile-netting-rows",
    "mark-netted-duplicate-pnl",
    "validate-partial-tpsl",
    "validate-bybit-naked-rearm",
    "cancel-stale-tpsl-legs",
    "backfill-tpsl-leg-ids",
}


@pytest.fixture(scope="module")
def workflow_dict() -> dict:
    """Parse the workflow YAML.

    PyYAML 5.x+ parses bare `on:` as the boolean `True` (YAML 1.1
    legacy). We treat either key as equivalent.
    """
    if yaml is None:
        pytest.skip("PyYAML not available in this env.")
    with WORKFLOW.open() as f:
        d = yaml.safe_load(f)
    if "on" not in d and True in d:
        d["on"] = d.pop(True)
    return d


def test_workflow_file_exists() -> None:
    assert WORKFLOW.exists(), f"Missing workflow: {WORKFLOW}"


def test_only_two_dispatch_paths(workflow_dict: dict) -> None:
    on = workflow_dict["on"]
    assert isinstance(on, dict)
    assert set(on.keys()) == {"workflow_dispatch", "issues"}, (
        f"system-actions allows exactly workflow_dispatch + issues; "
        f"got triggers: {list(on)}"
    )


def test_issues_trigger_is_opened_or_labeled(workflow_dict: dict) -> None:
    # The workflow fires on issue `opened` only (the body carries `action:`),
    # gated by the label check in the job-level `if:` (see
    # test_issue_dispatch_is_label_filtered). `labeled` was REMOVED 2026-06-10:
    # a create-with-label dispatch fires BOTH `opened` and `labeled`, and the
    # old two-branch `if:` ran the action twice (two pull-and-deploys from one
    # request). `opened` alone fires exactly once. No other issue event
    # (edited/closed/…) may trigger a dispatch.
    issues_trigger = workflow_dict["on"]["issues"]
    assert isinstance(issues_trigger, dict)
    assert issues_trigger.get("types") == ["opened"], (
        f"issues trigger must be types: [opened]; got: {issues_trigger}"
    )


def test_issue_dispatch_is_label_filtered() -> None:
    raw = WORKFLOW.read_text()
    assert (
        "github.event_name == 'issues'" in raw
        and "contains(github.event.issue.labels.*.name, 'system-action')" in raw
    ), (
        "system-actions.yml must gate issue-driven dispatch behind "
        "the label `system-action`. Update bootstrap-labels.yml + the "
        "job-level `if:` if you intend to change this."
    )


def test_issue_body_uses_env_not_inline_interpolation() -> None:
    raw = WORKFLOW.read_text()
    assert "ISSUE_BODY: ${{ github.event.issue.body }}" in raw, (
        "Expected ISSUE_BODY to ride through env, not inline ${{ }}."
    )
    inline_unsafe = re.search(
        r"<<['\"]?BODY['\"]?\n[^A-Z]*\$\{\{\s*github\.event\.issue\.body\s*\}\}",
        raw,
    )
    assert inline_unsafe is None, (
        "Detected unsafe inline interpolation of github.event.issue.body "
        "inside a shell heredoc. Pass the body through env: ISSUE_BODY instead."
    )


def test_action_input_is_choice_with_full_allowlist(workflow_dict: dict) -> None:
    inputs = workflow_dict["on"]["workflow_dispatch"]["inputs"]
    assert "action" in inputs
    action = inputs["action"]
    assert action.get("required") is True
    assert action.get("type") == "choice"
    assert set(action.get("options", [])) == set(EXPECTED_ACTIONS), (
        "Workflow `action` choice options drift from EXPECTED_ACTIONS — "
        "update both the workflow and docs/claude/system-actions.md."
    )


def test_no_freeform_command_input(workflow_dict: dict) -> None:
    inputs = workflow_dict["on"]["workflow_dispatch"]["inputs"]
    forbidden = {"command", "cmd", "script", "shell", "exec", "run"}
    bad = forbidden & set(inputs.keys())
    assert not bad, f"Forbidden freeform-command inputs present: {bad}"


def test_no_freeform_command_input_regex_fallback() -> None:
    text = WORKFLOW.read_text()
    assert not re.search(r"^\s+command:\s*$", text, re.MULTILINE), (
        "Found a `command:` input — system-actions allows no freeform shell."
    )


def test_workflow_maps_each_action_to_a_wrapper_script() -> None:
    text = WORKFLOW.read_text()
    for action, script in EXPECTED_ACTIONS.items():
        pattern = rf'{re.escape(action)}\)\s*SCRIPT="{re.escape(script)}"'
        assert re.search(pattern, text), (
            f"Workflow does not map action '{action}' to wrapper '{script}'. "
            f"Both must be updated together."
        )


def test_workflow_validates_action_choice_explicitly() -> None:
    text = WORKFLOW.read_text()
    assert re.search(r"\*\)\s*\n\s*echo \"::error::Unknown action", text), (
        "Validate step must reject unknown actions explicitly with `*) … exit 2`."
    )


def test_workflow_requires_reason_for_tier2_actions() -> None:
    text = WORKFLOW.read_text()
    for action in TIER_2_ACTIONS:
        assert action in text, f"Tier-2 action '{action}' missing from workflow"
    assert "Tier-2 action" in text and "non-empty 'reason'" in text, (
        "Workflow must enforce non-empty reason input for Tier-2 actions."
    )


def test_validate_step_classifies_every_action_into_a_tier() -> None:
    """Every allowlisted action must be enumerated in the Validate step's
    tier case (tier-1 OR tier-2 alternation), not merely present somewhere
    in the file.

    Regression guard (2026-06-15): backfill-account-class was added to the
    choice options + the SCRIPT-name case but NOT to the Validate step's
    tier alternation, so it fell through to `*) Unknown action; exit 2` —
    which aborts the run BEFORE the "Set up SSH key" step, surfacing as a
    confusing `Permission denied (publickey)` rather than an allowlist
    error. The older `action in text` checks passed because the name DID
    appear elsewhere in the file.
    """
    text = WORKFLOW.read_text()
    marker = "Validate action and tier policy"
    assert marker in text, "Validate step renamed? Update this guard."
    seg = text.split(marker, 1)[1].split("- name:", 1)[0]
    for action in EXPECTED_ACTIONS:
        # Must appear as a `case` alternative: bounded by ( | or whitespace
        # on the left and | or ) on the right.
        assert re.search(rf'[(|\s]{re.escape(action)}[|)]', seg), (
            f"Action '{action}' is not classified into a tier in the "
            f"Validate step — it would hit the unknown-action branch and "
            f"abort before SSH. Add it to the tier-1 or tier-2 alternation."
        )


def test_no_appleboy_or_other_third_party_ssh_action() -> None:
    text = WORKFLOW.read_text()
    assert "appleboy/ssh-action" not in text
    for forbidden in ("garygrossgarten/github-action-ssh", "shimataro/ssh-key-action"):
        assert forbidden not in text


@pytest.mark.parametrize("action,script", list(EXPECTED_ACTIONS.items()))
def test_each_wrapper_exists(action: str, script: str) -> None:
    path = OPS_DIR / script
    assert path.exists(), f"Missing wrapper for action '{action}': {path}"


@pytest.mark.parametrize("script", list(EXPECTED_ACTIONS.values()) + ["_lib.sh"])
def test_wrapper_uses_strict_mode_and_sources_lib(script: str) -> None:
    text = (OPS_DIR / script).read_text()
    assert "set -euo pipefail" in text, f"{script} must use `set -euo pipefail`."
    if script != "_lib.sh":
        assert "_lib.sh" in text, f"{script} must source the shared _lib.sh."


@pytest.mark.parametrize(
    "script", list(EXPECTED_ACTIONS.values()) + ["_lib.sh", "notify_run.sh"]
)
def test_wrapper_parses_with_bash_n(script: str) -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash not available in this test env")
    result = subprocess.run(
        ["bash", "-n", str(OPS_DIR / script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"{script} failed `bash -n` syntax check:\n{result.stderr}"
    )


@pytest.mark.parametrize("action", list(EXPECTED_ACTIONS))
def test_doc_lists_every_action(action: str) -> None:
    text = DOC.read_text()
    assert action in text, (
        f"docs/claude/system-actions.md must mention every action in the "
        f"allowlist; '{action}' is missing."
    )


def test_doc_calls_out_docker_omission() -> None:
    text = DOC.read_text()
    assert "Docker is intentionally absent" in text or "Docker is not canonical" in text


def test_doc_includes_dispatcher_trust_contract() -> None:
    text = DOC.read_text()
    assert "Dispatcher trust contract" in text, (
        "system-actions.md must keep § 3.5 'Dispatcher trust contract'."
    )
    for dispatcher in ("Operator", "Perplexity", "PM-side Claude"):
        assert dispatcher in text, (
            f"Dispatcher '{dispatcher}' must appear in the trust-contract table."
        )


def test_doc_includes_transparency_rule() -> None:
    text = DOC.read_text()
    assert "Transparency rule" in text, (
        "system-actions.md must keep § 5.5 'Transparency rule (always-notify)'."
    )
    collapsed = re.sub(r"\s+", " ", text.lower())
    assert "autonomy is complemented by full transparency" in collapsed, (
        "The transparency principle must be quoted verbatim."
    )


def test_notify_run_script_exists() -> None:
    path = OPS_DIR / "notify_run.sh"
    assert path.exists(), f"Missing notify wrapper: {path}"


def test_notify_run_uses_send_ping_with_claude_target() -> None:
    text = (OPS_DIR / "notify_run.sh").read_text()
    assert "send_ping.py" in text or "send_ping" in text, (
        "notify_run.sh must call the canonical scripts/send_ping.py producer."
    )
    assert "--target" in text and "claude" in text, (
        "notify_run.sh must route to the Claude bot channel "
        "(--target claude), not the trader bot."
    )


def test_notify_run_handles_every_allowlisted_action() -> None:
    text = (OPS_DIR / "notify_run.sh").read_text()
    for action in EXPECTED_ACTIONS:
        assert re.search(rf'\b{re.escape(action)}\b', text), (
            f"notify_run.sh must explicitly map action '{action}' to "
            f"a priority. Update the case statement when extending the "
            f"allowlist."
        )


def test_workflow_invokes_notify_step() -> None:
    text = WORKFLOW.read_text()
    assert "Notify operator via Claude bot channel" in text, (
        "system-actions.yml must include the transparency-rule "
        "notify step (see docs/claude/system-actions.md § 5.5)."
    )
    assert "notify_run.sh" in text, (
        "Notify step must invoke scripts/ops/notify_run.sh."
    )
    notify_block = text.split("Notify operator via Claude bot channel", 1)[1]
    notify_block = notify_block.split("- name:", 1)[0]
    assert "if: always()" in notify_block, (
        "Notify step must run with `if: always()` so failures notify too."
    )
    assert "continue-on-error: true" in notify_block, (
        "Notify step must `continue-on-error: true` so a notify failure "
        "doesn't flip an otherwise-successful action."
    )


def test_no_action_exists_in_the_workflow_that_is_absent_from_EXPECTED_ACTIONS() -> None:
    """The REVERSE direction — and the gap that let a half-registration ship.

    Every other guard in this file iterates ``EXPECTED_ACTIONS``, so they check
    that each action *this test file knows about* is wired everywhere. None of
    them can see an action added to the WORKFLOW but never added here: it is
    absent from the dict, so every loop simply skips it and the whole suite
    passes on a broken registration.

    That is exactly what happened with ``backfill-fabricated-exits`` (PR #8149,
    2026-07-31): added to the SCRIPT-name case, absent from the tier
    alternation, the choice options, ``notify_run.sh`` and the docs. It went
    green, then died at dispatch with ``Permission denied (publickey)`` because
    the Validate step hit ``*) exit 2`` before the SSH key was installed.

    Note the sting: ``test_validate_step_classifies_every_action_into_a_tier``
    was written for this precise incident (``backfill-account-class``,
    2026-06-15) and could not catch the recurrence, because it too loops over
    ``EXPECTED_ACTIONS``. A registry-keyed guard cannot police entries missing
    from the registry — only the reverse check can, which is why it exists.
    """
    text = WORKFLOW.read_text()
    in_workflow = set(re.findall(r'^\s*([a-z0-9][a-z0-9-]*)\)\s*SCRIPT="',
                                 text, re.MULTILINE))
    assert in_workflow, "found no `<action>) SCRIPT=\"…\"` mappings — did the dispatch shape change?"
    unknown = in_workflow - set(EXPECTED_ACTIONS)
    assert not unknown, (
        f"Action(s) {sorted(unknown)} are mapped to a wrapper script in "
        f"system-actions.yml but are MISSING from EXPECTED_ACTIONS. Every "
        f"other guard here loops over that dict, so they are invisible to all "
        f"of them — the action will pass CI and then abort at dispatch before "
        f"the SSH key step. Add them to EXPECTED_ACTIONS (and TIER_2_ACTIONS "
        f"if mutating)."
    )


def test_every_action_requiring_env_key_also_forwards_it_to_the_vm():
    """An action VALIDATED as needing ``env_key`` must also FORWARD it.

    The two live in different steps: the Validate step rejects a blank
    ``env_key``, and a much later step prepends ``ENV_KEY=…`` onto ``REMOTE_CMD``
    inside a per-action ``if``. Passing the first proves nothing about the
    second, and the failure between them is silent in the worst way — the
    wrapper RUNS on the VM, sees an unset variable, and exits on its own
    "requires env_key" error, which reads as a caller mistake rather than a
    workflow wiring gap.

    That is exactly what `get-env` hit on its first real dispatch (issue #8753,
    2026-08-10): allowlisted, tier-classified, script-mapped, registered in
    EXPECTED_ACTIONS and notify_run.sh — and the parameter still never crossed
    the SSH boundary, because the forwarding branch was written for `set-env`
    only. Every existing guard here checks that an action is DECLARED; this one
    checks that its inputs actually ARRIVE.
    """
    text = WORKFLOW.read_text()

    # Actions the Validate step demands an env_key from.
    validated = set()
    for m in re.finditer(r'\[\s*"\$\{ACTION\}"\s*=\s*"([a-z0-9-]+)"\s*\]', text):
        action = m.group(1)
        window = text[m.end():m.end() + 600]
        if "requires 'env_key'" in window or 'requires ${ACTION} env_key' in window:
            validated.add(action)
    assert validated, (
        "found no action validating 'env_key' — did the Validate step change? "
        "A guard that silently matches nothing is worse than no guard."
    )

    # Actions that actually prepend ENV_KEY onto the remote command.
    forwards = set()
    for m in re.finditer(r'\[\s*"\$\{ACTION\}"\s*=\s*"([a-z0-9-]+)"\s*\]', text):
        action = m.group(1)
        window = text[m.end():m.end() + 2500]
        if 'REMOTE_CMD="ENV_KEY=' in window:
            forwards.add(action)

    missing = validated - forwards
    assert not missing, (
        f"Action(s) {sorted(missing)} require 'env_key' at validation but never "
        f"forward ENV_KEY to the VM in any `REMOTE_CMD=\"ENV_KEY=…\"` branch. "
        f"The dispatch will succeed, the wrapper will run on the VM with the "
        f"variable UNSET, and it will fail with its own 'requires env_key' "
        f"message — which looks like a bad request rather than a missing wire. "
        f"Add a forwarding branch for it (see the get-env / set-env blocks)."
    )


def _required_env_of_wrapper(script: str) -> set[str]:
    """Env vars a wrapper declares MANDATORY via ``${VAR:?...}``.

    The wrapper is the ground truth for what the remote shell must receive:
    ``:?`` is bash's own "unset here is fatal" marker, so a var written that way
    is, by construction, one the action cannot run without. ``${VAR:-}`` is
    deliberately NOT matched — an optional var that never arrives degrades, it
    does not abort.
    """
    text = (OPS_DIR / script).read_text()
    return set(re.findall(r'\$\{([A-Z_][A-Z0-9_]*):\?', text))


def _forwarded_env_by_action() -> dict[str, set[str]]:
    """Vars each action actually prepends onto ``REMOTE_CMD``.

    Parsed from the dispatch step's per-action ``if`` blocks: the condition line
    names the action(s), the body's ``REMOTE_CMD="VAR=… ${REMOTE_CMD}"``
    assignments name the vars that cross the SSH boundary.
    """
    lines = WORKFLOW.read_text().splitlines()
    forwarded: dict[str, set[str]] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if "${ACTION}" in line and re.match(r"\s*(if|elif)\b", line):
            actions = set(re.findall(r'\$\{ACTION\}"?\s*=\s*"([a-z0-9-]+)"', line))
            indent = len(line) - len(line.lstrip())
            j = i + 1
            while j < len(lines):
                body = lines[j]
                if body.strip() == "fi" and (len(body) - len(body.lstrip())) <= indent:
                    break
                for m in re.finditer(r'REMOTE_CMD="(.*?)\$\{REMOTE_CMD\}"', body):
                    for var in re.findall(r"\b([A-Z][A-Z0-9_]*)=", m.group(1)):
                        for action in actions:
                            forwarded.setdefault(action, set()).add(var)
                j += 1
            i = j
        i += 1
    return forwarded


def test_every_wrapper_required_env_var_is_forwarded_to_the_vm() -> None:
    """The GENERAL form of the guard above — derived, not enumerated.

    ``test_every_action_requiring_env_key_also_forwards_it_to_the_vm`` checks
    exactly one variable name, ``ENV_KEY``, because it was written for the
    `get-env` incident. That scoping is why it could not catch the recurrence:
    `cancel-ib-order` (2026-08-16) was allowlisted, tier-classified,
    script-mapped, validated, registered in EXPECTED_ACTIONS, in notify_run.sh
    and in the docs — 353 guards green — and its first real dispatch died with
    ``ACCOUNT_ID: ACCOUNT_ID required``, because no forwarding branch was ever
    written. Same class, one incident later, one variable name to the left.

    So this asks the WRAPPER what it needs rather than hardcoding a name: any
    future action whose script declares ``${VAR:?…}`` is covered the moment it
    is added, with no edit here. The failure it prevents is the nastiest kind of
    green — the wrapper runs on the VM and aborts with its own usage error,
    which reads as a caller mistake rather than a wiring gap.
    """
    forwarded = _forwarded_env_by_action()
    assert forwarded, (
        "parsed no per-action REMOTE_CMD forwarding blocks — did the dispatch "
        "step change shape? A guard that silently matches nothing is worse "
        "than no guard."
    )

    # Positive control: the parse must find a KNOWN-good wiring, so a regex that
    # quietly matches nothing can never read as 'every action is wired'.
    assert "ACCOUNT_ID" in forwarded.get("flatten-ib-position", set()), (
        "parser failed to see flatten-ib-position forwarding ACCOUNT_ID — the "
        "block-parse is broken, so every 'missing' below would be a false "
        "negative rather than evidence."
    )

    checked, missing = 0, {}
    for action, script in sorted(EXPECTED_ACTIONS.items()):
        required = _required_env_of_wrapper(script)
        if not required:
            continue
        checked += 1
        gap = required - forwarded.get(action, set())
        if gap:
            missing[action] = sorted(gap)

    assert checked, (
        "no wrapper declares a `${VAR:?...}` required var — the probe cannot "
        "find a positive, so its silence proves nothing."
    )
    assert not missing, (
        f"Action(s) declare required env vars their workflow branch never "
        f"forwards: {missing}. The dispatch will succeed and the wrapper will "
        f"abort on the VM with its own 'X required' message. Add the var to "
        f"that action's `REMOTE_CMD=\"…\"` branch in system-actions.yml."
    )


# ---------------------------------------------------------------------------
# cancel-ib-order guard overrides (2026-08-17).
#
# The python script documents two overrides and the wrapper passed NEITHER, so
# the action could not cancel the one class of order it exists for: a stranded
# PROTECTIVE, TRADER-OWNED stop trips both guards at once. Live-confirmed on a
# duplicate MES stop (perm 166865400, clientId 597) -> `action: refused`, with
# no reachable way forward.
#
# These tests EXECUTE the wrapper's flag logic rather than grepping for the
# strings. A grep passes just as happily on a flag appended unconditionally,
# which would silently disarm both guards for every invocation -- the exact
# failure this change must not introduce.
# ---------------------------------------------------------------------------

CANCEL_WRAPPER = OPS_DIR / "cancel_ib_order_action.sh"


def _wrapper_force_args(force_protective: str, force_client_id: str) -> list:
    """Run ONLY the wrapper's two force `case` blocks and return the args.

    Extracted by slicing the real file between its own markers, so the test
    reads what ships rather than a copy that can drift. The values are passed
    through the ENVIRONMENT rather than interpolated into the script text --
    interpolating them was this helper's own first bug, and it would also stop
    the test from covering values containing quotes or spaces.
    """
    src = CANCEL_WRAPPER.read_text()
    start = src.index('case "${ACTION_FORCE_PROTECTIVE}"')
    end = src.index('exec "${PY}"')
    block = src[start:end]

    script = "set -u\nARGS=()\n" + block + '\nprintf "%s\\n" "${ARGS[@]:-}"\n'
    out = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "ACTION_FORCE_PROTECTIVE": force_protective,
            "ACTION_FORCE_CLIENT_ID": force_client_id,
        },
    )
    assert out.returncode == 0, out.stderr
    return [ln for ln in out.stdout.splitlines() if ln.startswith("--")]


def test_force_flags_absent_by_default_so_guards_still_refuse():
    """The whole point of the guards. Absent env => neither flag passed."""
    assert _wrapper_force_args("", "") == []


def test_force_flags_are_independent_not_one_switch():
    """Two guards answer different questions -- 'strip this exit' vs 'connect
    as a trader-band id'. One key enabling both would waive a refusal nobody
    asked to waive."""
    assert _wrapper_force_args("true", "") == ["--force-protective"]
    assert _wrapper_force_args("", "true") == ["--force-client-id"]


def test_force_flags_both_settable_for_the_motivating_case():
    both = _wrapper_force_args("true", "true")
    assert sorted(both) == ["--force-client-id", "--force-protective"]


def test_unrecognised_force_value_does_not_silently_force():
    """A typo must not read as true. It resolves to off here; the workflow's
    validation rejects it outright before we ever reach the wrapper."""
    for bogus in ("yes", "1", "TRUE ", "on", "false"):
        assert _wrapper_force_args(bogus, bogus) == [], bogus


def test_workflow_parses_and_forwards_both_force_keys():
    """Parsed + exported + FORWARDED OVER SSH. The third is the one that bites:
    a var parsed and validated but missing from REMOTE_CMD is dropped silently
    at the SSH boundary, and the run reports a refusal for a forced request."""
    wf = WORKFLOW.read_text()
    for key, env in (
        ("force_protective", "ACTION_FORCE_PROTECTIVE"),
        ("force_client_id", "ACTION_FORCE_CLIENT_ID"),
    ):
        assert f"'^[[:space:]]*{key}[[:space:]]*:'" in wf, f"{key} not parsed"
        assert f'echo "{env}=' in wf, f"{env} not exported to GITHUB_ENV"
        assert f"{env}=$(printf '%q'" in wf, f"{env} not forwarded in REMOTE_CMD"
