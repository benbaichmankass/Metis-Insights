from __future__ import annotations
from datetime import datetime, timezone
from src.utils.signal_audit_logger import should_send_summary

import logging
import os
import time

from dotenv import load_dotenv

from src.exchange.binance_connector import BinanceConnector
from src.exchange.bybit_connector import BybitConnector
from src.runtime.heartbeat import write_heartbeat
from src.runtime.hourly_report import build_hourly_report
from src.runtime.outcomes import Level, report, send_scheduled
from src.runtime.pipeline import run_pipeline
from src.runtime.validation import build_settings_from_env, validate_startup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

from src.utils.log_redact import install_redacting_filter, suppress_httpx_logging  # noqa: E402
install_redacting_filter()   # redact tokens from every log record
suppress_httpx_logging()     # prevent httpx from emitting full Telegram URLs at INFO

logger = logging.getLogger("src.main")


class BybitExchangeAdapter:
    """Thin adapter so BybitConnector works with safe_place_order."""
    def __init__(self, connector: BybitConnector, symbol: str):
        self._connector = connector
        self._symbol = symbol

    def place_order(self, **order):
        side = order.get("side")
        qty = float(order.get("qty", 0))
        symbol = order.get("symbol", self._symbol)
        logger.info("BybitExchangeAdapter.place_order: %s %s %s", symbol, side, qty)
        if qty <= 0:
            raise ValueError(f"Invalid qty for adapter: {qty}")
        return self._connector.place_market_order(symbol, side, qty)


class BinanceExchangeAdapter:
    """Thin adapter so BinanceConnector works with safe_place_order."""
    def __init__(self, connector, symbol: str):
        self._connector = connector
        self._symbol = symbol

    def place_order(self, **order):
        side = order.get("side")
        qty = float(order.get("qty", 0))
        symbol = order.get("symbol", self._symbol)
        logger.info("BinanceExchangeAdapter.place_order: %s %s %s", symbol, side, qty)
        if qty <= 0:
            raise ValueError(f"Invalid qty for adapter: {qty}")
        return self._connector.place_market_order(symbol, side, qty)


class DummyTelegramClient:
    def send_message(self, message: str):
        logger.info("DummyTelegramClient.send_message: %s", message)


class _AlertManagerAdapter:
    """Wraps AlertManager.send_alert() as send_message() for pipeline compatibility."""
    def __init__(self, alert_manager):
        self._am = alert_manager

    def send_message(self, message: str):
        self._am.send_alert(message)


def _build_telegram_client():
    """Use real Telegram client if credentials are present, else fall back to dummy."""
    try:
        from src.bot.alert_manager import AlertManager
        am = AlertManager()
        if am.enabled:
            return _AlertManagerAdapter(am)
    except Exception as exc:
        logger.warning("Could not initialise real Telegram client: %s", exc)
    return DummyTelegramClient()


def _build_exchange_adapter(settings: dict):
    exchange_name = settings.get("EXCHANGE", "bybit").lower()
    symbol = settings.get("SYMBOL", "BTCUSDT")

    # FIXED: read BYBIT_TESTNET directly; do not rely on MODE
    bybit_testnet_raw = str(os.environ.get("BYBIT_TESTNET", "true")).strip().lower()
    testnet = bybit_testnet_raw not in {"false", "0", "no"}

    logger.info("Exchange mode: exchange=%s testnet=%s symbol=%s", exchange_name, testnet, symbol)

    if exchange_name == "binance":
        connector = BinanceConnector(
            api_key=settings.get("BINANCE_API_KEY"),
            api_secret=settings.get("BINANCE_API_SECRET"),
            testnet=testnet,
        )
        return BinanceExchangeAdapter(connector, symbol)

    connector = BybitConnector(
        api_key=settings.get("BYBIT_API_KEY"),
        api_secret=settings.get("BYBIT_API_SECRET"),
        testnet=testnet,
    )
    return BybitExchangeAdapter(connector, symbol)


def run_one_tick(settings: dict, exchange_client, telegram_client) -> dict:
    """Run a single pipeline tick and return the result."""
    result = run_pipeline(
        settings=settings,
        exchange_client=exchange_client,
        telegram_client=telegram_client,
    )
    logger.info("Tick result: %s", result)
    order_result = (result or {}).get("order_result") or {}
    status = order_result.get("status", "unknown")
    report(
        "pipeline_tick",
        status,
        level=Level.INFO,
        symbol=(result or {}).get("signal", {}).get("symbol"),
    )
    return result


def main() -> None:
    load_dotenv()
    settings = build_settings_from_env()
    # S-012 hotfix: build_settings_from_env now emits both lowercase
    # ("dry_run", "allow_live_trading") and uppercase ("DRY_RUN",
    # "ALLOW_LIVE_TRADING") keys. The post-call patch that previously
    # backfilled DRY_RUN here is no longer needed and is removed —
    # ALLOW_LIVE_TRADING was never backfilled, which produced the
    # "ALLOW_LIVE_TRADING=true is required for live submission"
    # rejection on every live signal.

    validate_startup()
    logger.info(
        "Startup validation passed. exchange=%s dry_run=%s allow_live_trading=%s bybit_testnet=%s mode=%s symbol=%s",
        settings.get("exchange"),
        settings.get("DRY_RUN", settings.get("dry_run")),
        settings.get("ALLOW_LIVE_TRADING", settings.get("allow_live_trading")),
        os.environ.get("BYBIT_TESTNET"),
        settings.get("mode"),
        settings.get("symbol"),
    )

    # BUG-033: ping the operator on duplicate per-account API keys. Doesn't
    # block startup — per CLAUDE.md the trader runs autonomously and the
    # per-account risk caps bound the blast radius.
    try:
        from src.units.accounts import load_accounts
        from src.units.accounts.dup_key_check import warn_on_duplicate_keys
        warn_on_duplicate_keys(load_accounts())
    except Exception as exc:  # noqa: BLE001
        logger.warning("dup-key check skipped: %s", exc)

    exchange_client = _build_exchange_adapter(settings)
    telegram_client = _build_telegram_client()

    loop = str(os.environ.get("LOOP", "true")).strip().lower() not in {"false", "0", "no"}
    interval = int(os.environ.get("TICK_INTERVAL_SECONDS", "900"))

    if not loop:
        logger.info("LOOP=false: running single tick.")
        run_one_tick(settings, exchange_client, telegram_client)
        return

    logger.info("Starting continuous loop. TICK_INTERVAL_SECONDS=%s", interval)
    tick_count = 0
    while True:
        tick_count += 1
        try:
            run_one_tick(settings, exchange_client, telegram_client)
            # PR5: heartbeat is the single source of truth for "trader is
            # alive". Writes after a successful tick, not before — so a
            # tick that crashes mid-run doesn't refresh the heartbeat and
            # the watchdog will alert.
            write_heartbeat(status="ok", tick=tick_count)
        except Exception as exc:
            logger.exception("Tick failed with unhandled exception: %s", exc)
            report(
                "pipeline_tick",
                "exception",
                level=Level.CRITICAL,
                reason=f"{type(exc).__name__}: {exc}",
            )
            # Heartbeat marker still gets written so monitors can
            # distinguish "process is running but ticks failing" from
            # "process is dead". The 'error' status is what the watchdog
            # surfaces.
            write_heartbeat(status="error", tick=tick_count)
        now_utc = datetime.now(timezone.utc)
        # BUG-032: a one-shot demo flag the operator drops on the VM after
        # deploy (`touch runtime_flags/send_hourly_demo`). When present, the
        # dedup marker is bypassed for one tick so the operator sees a fresh
        # summary in Telegram and confirms the fix is live. The flag is
        # consumed (deleted) after a successful build to prevent loops.
        demo_flag = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "runtime_flags", "send_hourly_demo",
        )
        force_demo = os.path.exists(demo_flag)
        try:
            if force_demo or should_send_summary(now_utc):
                # S-022 PR2: replace the one-line "service is alive" blurb
                # with the structured hourly report. Sent via the
                # scheduled-message path so it bypasses the per-fingerprint
                # rate limit and the hourly cap on ERROR/CRITICAL alerts.
                #
                # BUG-032: explicit INFO log on every attempted send so the
                # operator can grep `journalctl -u ict-trader-live` for
                # "hourly report" when summaries stop arriving. Without this
                # the silent-failure mode (e.g. send_via_alert_manager raising
                # and being swallowed by send_scheduled) is invisible.
                message = build_hourly_report(
                    now_utc=now_utc, tick_interval_s=interval,
                )
                logger.info(
                    "hourly report dispatch: slot=%s len=%d",
                    now_utc.strftime("%Y-%m-%d-%H"), len(message),
                )
                send_scheduled(message)
                report(
                    "hourly_report", "dispatched",
                    level=Level.INFO,
                    slot=now_utc.strftime("%Y-%m-%d-%H"),
                    chars=len(message),
                    demo=force_demo,
                )
                if force_demo:
                    try:
                        os.remove(demo_flag)
                    except OSError:
                        pass
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to send hourly report")
            report(
                "hourly_report", "failed",
                level=Level.WARN,
                reason=f"{type(exc).__name__}: {exc}",
            )

        logger.info("Sleeping %s seconds until next tick.", interval)
        time.sleep(interval)


if __name__ == "__main__":
    main()
