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
    "strategy-performance-audit": "strategy_performance_audit_action.sh",
    "monitor-miss-analysis": "monitor_miss_analysis_action.sh",
    "vwap-backtest-sweep": "vwap_backtest_sweep_action.sh",
    # Tier 2 — mutating / restart / derived-artifact writes
    "pull-and-deploy": "pull_and_deploy.sh",
    "restart-bot-service": "restart_bot.sh",
    "reboot-vm": "reboot_vm.sh",
    "enable-closed-flat-invariant": "enable_closed_flat_invariant.sh",
    "disable-closed-flat-invariant": "disable_closed_flat_invariant.sh",
    "enable-m5-consumer": "enable_m5_consumer.sh",
    "disable-m5-consumer": "disable_m5_consumer.sh",
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
    # 2026-06-22 — normalise existing epoch-ms trades.closed_at rows to ISO
    # (BL-20260620-RECONCILER-CLOSEDAT-MS); distinct from backfill-closed-at
    # (which fills NULLs). Wraps migrate_closed_at_to_iso.py.
    "migrate-closed-at-iso": "migrate_closed_at_to_iso_action.sh",
    "pull-exchange-fills": "pull_exchange_fills_action.sh",
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
    # 2026-06-19 — one-shot guarded flatten of a single IB exchange position
    # (BL-20260618-RECONCILE-DUP residual: the stranded ib_paper MGC short).
    "flatten-ib-position": "flatten_ib_position_action.sh",
    # 2026-06-29 — Bybit sibling of flatten-ib-position: one-shot guarded
    # reduce-only flatten of a single Bybit exchange position (close an
    # account before a different-account key rotation).
    "flatten-bybit-position": "flatten_bybit_position_action.sh",
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
}

TIER_2_ACTIONS = {
    "pull-and-deploy",
    "restart-bot-service",
    "reboot-vm",
    "enable-closed-flat-invariant",
    "disable-closed-flat-invariant",
    "enable-m5-consumer",
    "disable-m5-consumer",
    "enable-signal-dual-write",
    "disable-signal-dual-write",
    "backfill-pnl-nulls",
    "backfill-orphan-pnl",
    "backfill-closed-null-pnl",
    "backfill-monitor-closed-pnl",
    "revert-backfill-monitor-closed-pnl",
    "mark-reconciler-incomplete",
    "rebuild-pnl-from-bybit",
    "backfill-shadow-predictions",
    "backfill-account-class",
    "backfill-closed-at",
    "backfill-trade-costs",
    "backfill-broker-order-id",
    "backfill-broker-truth-costs",
    "migrate-closed-at-iso",
    "pull-exchange-fills",
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
    "flatten-ib-position",
    "flatten-bybit-position",
    "flatten-alpaca-position",
    "close-stranded-journal-row",
    "reconcile-orphan-history",
    "supersede-options-adoption-artifacts",
    "supersede-reset-orphan-artifacts",
    "supersede-intent-reduce-phantom-pnl",
    "fix-prop-mislinked-close",
    "reset-daily-risk-state",
    "repair-malformed-notes",
    "repair-netted-rows",
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
