"""S-012 PR D2: single-process consolidation regression test.

Locks the post-S-012 architecture in place: every strategy in the active
roster runs inside ``ict-trader-live.service``; per-strategy systemd
units do not exist and must not be re-introduced without an explicit
sprint.

This test catches the failure mode that triggered S-012 — configs
declaring per-strategy services that have no matching ``.service`` file
in ``deploy/``.

PM § 8 #1 (b) confirmed.
"""
from __future__ import annotations

import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEPLOY_DIR = os.path.join(REPO_ROOT, "deploy")

# Canonical set of systemd units in deploy/ post-S-012. Any change to
# this set is an architectural decision and must be reflected in
# docs/claude/deployment-ops.md plus an explicit sprint.
EXPECTED_SERVICES = {
    "ict-env-check.service",
    "ict-git-sync.service",
    "ict-heartbeat.service",
    "ict-telegram-bot.service",
    "ict-trader-live.service",
    # S-013 M2 PR #1: read-only dashboard API.
    "ict-web-api.service",
    # 2026-05-11: external liveness watchdog dead-man switch.
    "ict-liveness-watchdog.service",
    # Hourly snapshot collector.
    "ict-hourly-snapshot.service",
    # Claude bridge bot (ict-claude-bridge.service manages it separately).
    "ict-claude-bridge.service",
    # Shadow log rotation for the shadow-predictions audit log.
    "ict-shadow-log-rotate.service",
    # One-shot smoke check run on deploy.
    "ict-smoke-once.service",
    # Claude VM runner template unit.
    "claude-vm-runner@.service",
    # M13 S1/S2 (2026-05-26): AI Analyst generator (fast tier:
    # globals every 15 min) + per-strategy slow tier (every 60 min).
    "ict-insights-generator.service",
    "ict-insights-generator-strategies.service",
    # 2026-05-28: IB Gateway auto-heal watchdog (MES session recovery —
    # BL-20260527-003). Timer-fired oneshot; pairs with
    # ict-ib-gateway-watchdog.timer.
    "ict-ib-gateway-watchdog.service",
    # 2026-06-10: IB Gateway daily reset (gateway-isolation redesign). One
    # deterministic `docker restart ib-gateway`/day on the dedicated gateway
    # VM (gated by ConditionPathExists=/etc/ict/ib-gateway-docker.env);
    # replaces the reactive 5-min restart loop. Pairs with
    # ict-ib-gateway-reset.timer.
    "ict-ib-gateway-reset.service",
    # 2026-06-04: health-snapshot writer (BL-20260529-005). Timer-fired
    # oneshot (ict-health-snapshot.timer, every 15 min) that writes
    # artifacts/health/{latest,health_check_*}.json — revives the JSON
    # producer the 2026-05-12 health-snapshot.yml refactor deleted, so
    # /api/bot/health/* + the M13 health card stop serving a frozen
    # 2026-05-11 snapshot.
    "ict-health-snapshot.service",
    # 2026-06-04: web-api self-heal watchdog (BL-20260604-003). Timer-fired
    # oneshot (ict-web-api-watchdog.timer, every 2 min) that probes
    # 127.0.0.1:8001/api/health and restarts ict-web-api on a sustained
    # wedge — VM-side recovery for the read surface, independent of the
    # webhook-flaky GitHub vm-web-api-recover dispatch.
    "ict-web-api-watchdog.service",
    # 2026-06-15: /dev/null guard (devnull-guard runbook). Timer-fired oneshot
    # (ict-devnull-guard.timer, every 60 s) that re-asserts /dev/null is the
    # 1:3 char device with mode 0666 — an OCI host agent intermittently chmods
    # it to 0444, which EACCESes every non-root `>/dev/null` and silently
    # wedged ict-git-sync auto-deploy for ~16h on 2026-06-15.
    "ict-devnull-guard.service",
    # 2026-06-17: DB-integrity checker (dashboard-truth Phase 4). Timer-fired
    # oneshot (ict-db-integrity.timer, hourly) that runs
    # scripts/check_db_integrity.py over trade_journal.db and Telegrams a
    # [WARN]/[CRITICAL] when intake breaks (orphan trades, NULL pnl past the
    # bounded window, account_class gaps, closed_at gaps) — the DB tells us
    # when persistence drifts instead of the dashboard silently mis-rendering.
    "ict-db-integrity.service",
    # 2026-06-30: MES IBKR deep-history pull scheduler (BL-20260626-MES-BASE-STALE).
    # Timer-fired oneshot (ict-mes-ibkr-pull.timer, daily 23:30 UTC) that runs
    # scripts/ops/pull_mes_ibkr_history.sh on the live-trader box so the trainer's
    # MES regime base stays current instead of freezing at a one-shot snapshot
    # (the pull had been manual-only and stopped 2026-06-14). Live-trader-box only
    # via install_systemd_units.sh auto-enable; heartbeat-guarded, secondary-priority.
    "ict-mes-ibkr-pull.service",
}

# Trader-side units (i.e. units that run trading-strategy code). Used to
# pin the single-process architecture.
TRADER_UNITS = {"ict-trader-live.service"}


# ---------------------------------------------------------------------------
# deploy/ shape
# ---------------------------------------------------------------------------


def test_deploy_dir_exists():
    assert os.path.isdir(DEPLOY_DIR), f"deploy/ missing at {DEPLOY_DIR}"


def test_deploy_service_set_matches_canonical():
    """Exactly the canonical 5 .service files exist; no more, no less."""
    actual = {
        name for name in os.listdir(DEPLOY_DIR)
        if name.endswith(".service")
    }
    assert actual == EXPECTED_SERVICES, (
        f"deploy/ .service set drifted from the canonical S-012 architecture.\n"
        f"  expected: {sorted(EXPECTED_SERVICES)}\n"
        f"  actual:   {sorted(actual)}\n"
        f"  missing:  {sorted(EXPECTED_SERVICES - actual)}\n"
        f"  extra:    {sorted(actual - EXPECTED_SERVICES)}\n"
        "Adding or removing a unit is a sprint-level decision; update "
        "EXPECTED_SERVICES here, deployment-ops.md, and the sprint summary "
        "in the same PR."
    )


def test_only_one_trader_side_unit():
    """Single-process architecture: exactly one trader-side .service."""
    trader_units = {
        name for name in os.listdir(DEPLOY_DIR)
        if name.startswith("ict-trader-") and name.endswith(".service")
    }
    assert trader_units == TRADER_UNITS, (
        f"Expected exactly one trader-side service ({sorted(TRADER_UNITS)}); "
        f"got {sorted(trader_units)}. Per-strategy services were the failure "
        "that triggered S-012; do not re-introduce them without a dedicated "
        "sprint."
    )


# ---------------------------------------------------------------------------
# Configs reference no service that lacks a .service file
# ---------------------------------------------------------------------------


def _service_files_in_deploy() -> set:
    return {
        name for name in os.listdir(DEPLOY_DIR)
        if name.endswith(".service")
    }


def test_strategy_registry_service_names_have_unit_files():
    """Every service_name() returned by the registry is a real unit file."""
    from src.strategy_registry import load_strategies

    units_on_disk = {
        name[: -len(".service")] for name in _service_files_in_deploy()
    }
    for s in load_strategies():
        svc = s["service"]
        assert svc in units_on_disk, (
            f"strategy '{s['name']}' references service '{svc}', "
            f"but deploy/{svc}.service does not exist. "
            "Either author the unit file or remove the service field."
        )


def test_data_loaders_list_trader_services_returns_real_units():
    """list_trader_services() must return only services whose unit files exist."""
    from src.bot import data_loaders as dl

    services = dl.list_trader_services()
    units_on_disk = {
        name[: -len(".service")] for name in _service_files_in_deploy()
    }
    for svc in services:
        assert svc in units_on_disk, (
            f"list_trader_services() returned '{svc}' but no matching "
            f"unit file exists in deploy/."
        )


def test_data_loaders_list_trader_services_is_single_process():
    """S-012 single-process: exactly one trader-side service in the registry."""
    from src.bot import data_loaders as dl

    services = dl.list_trader_services()
    assert services == ["ict-trader-live"], (
        f"Expected single-process architecture (services == ['ict-trader-live']); "
        f"got {services}."
    )


def test_account_services_have_unit_files_or_are_default():
    """Each account in accounts.yaml routes to a real systemd unit.

    Either the account has no explicit ``service`` (defaults to
    ict-trader-live in src/bot/data_loaders) or its declared service
    matches a unit file in deploy/.
    """
    from src.bot import data_loaders as dl

    accounts_yaml = os.path.join(REPO_ROOT, "config", "accounts.yaml")
    if not os.path.exists(accounts_yaml):
        return  # accounts.yaml absent in this checkout

    original = dl.ACCOUNTS_YAML_PATH
    dl.ACCOUNTS_YAML_PATH = accounts_yaml
    try:
        accounts = dl._load_yaml_accounts()
    finally:
        dl.ACCOUNTS_YAML_PATH = original

    units_on_disk = {
        name[: -len(".service")] for name in _service_files_in_deploy()
    }
    for acc in accounts:
        svc = acc.get("service")
        if not svc:
            continue
        # data_loaders may auto-fill `ict-trader-<account_id>` when the
        # YAML omits the field. The post-S-012 contract is that account
        # services must still exist in deploy/. The default is the
        # legacy live unit, which always exists.
        assert svc in units_on_disk or svc == "ict-trader-live", (
            f"Account '{acc.get('account_id')}' references service '{svc}', "
            f"but deploy/{svc}.service does not exist."
        )
