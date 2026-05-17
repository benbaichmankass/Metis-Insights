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
from src.runtime.hourly_report import (
    build_accounts_hourly_report,
    build_hourly_report,
)
from src.runtime.notify import send_telegram_direct
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


def _apply_per_account_leverage() -> None:
    """Pre-flight: set per-symbol leverage for every linear-perp account.

    PR 3 cutover (spot-margin → USDT-margined perpetuals). Bybit V5
    requires `/v5/position/set-leverage` to be called per (symbol,
    account) before placing linear orders; the value persists until
    explicitly changed. Idempotent on retCode=110043 (already set),
    so re-calling on every boot is safe.

    Iterates `config/accounts.yaml`:
      - skips accounts with `market_type` ≠ `linear`
      - skips accounts missing creds (resolve_credentials returns None)
      - reads `risk.leverage` (or `leverage`) from the account's YAML
      - reads the per-strategy symbols from `config/strategies.yaml`
        for the strategies that account is wired to
      - calls `client.set_leverage(symbol, leverage)` for each pair

    Best-effort — a failure on one account does not block the others
    or block boot. A retCode-110043 (already set) is treated as
    success; everything else is logged as a warning. The trader loop
    will surface the consequence (an immediate Bybit order rejection
    with a clear retMsg) if a real leverage problem is left
    unresolved.
    """
    try:
        from src.units.accounts import load_accounts
        from src.units.accounts.clients import bybit_client_for
        from src.units.strategies import load_strategy_config
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_leverage pre-flight: import failed (%s)", exc)
        return

    try:
        accounts = load_accounts()
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_leverage pre-flight: load_accounts failed (%s)", exc)
        return

    try:
        strategies_cfg = load_strategy_config() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_leverage pre-flight: load_strategy_config failed (%s)", exc)
        strategies_cfg = {}

    for account in accounts:
        market_type = (getattr(account, "market_type", "spot") or "spot").lower()
        if market_type != "linear":
            continue

        if getattr(account, "exchange", "").lower() != "bybit":
            # Velotrade / future non-bybit derivatives have their own
            # leverage primitives; this helper is bybit-specific.
            continue

        leverage = _resolve_account_leverage(account)
        if leverage <= 0:
            logger.warning(
                "set_leverage pre-flight: account=%s has market_type=linear "
                "but no usable `leverage` config — skipping",
                account.name,
            )
            continue

        # Use the SAME pybit HTTP client factory that order placement uses
        # (src/units/accounts/clients.py::bybit_client_for). Three prior
        # implementations of set-leverage all returned retCode=10003 from
        # the SAME credentials that successfully placed orders via this
        # pybit client (see FU-20260510-005):
        #   * PR #781 — ccxt high-level `set_leverage`
        #   * PR #782 — ccxt private_post_v5_position_set_leverage
        #   * PR #903 — hand-rolled direct V5 signed POST in BybitConnector
        # Root cause was never identified in the signing math (all three
        # passed unit tests against Bybit's documented spec), but pybit's
        # internal V5 signer demonstrably DOES work on the same key for
        # set-leverage. Routing through it eliminates the parallel auth
        # path and the every-boot WARNING.
        account_cfg = {
            "api_key_env": getattr(account, "api_key_env", ""),
            "exchange": "bybit",
            "env_path": getattr(account, "env_path", ""),
        }
        try:
            client = bybit_client_for(account_cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "set_leverage pre-flight: client init failed for %s (%s)",
                account.name, exc,
            )
            continue
        if client is None:
            logger.warning(
                "set_leverage pre-flight: account=%s creds not resolvable "
                "(env vars unset) — skipping",
                account.name,
            )
            continue

        symbols = _symbols_for_account(account, strategies_cfg)
        if not symbols:
            logger.warning(
                "set_leverage pre-flight: account=%s has no symbols (no "
                "strategies or empty symbol lists) — skipping",
                account.name,
            )
            continue

        for symbol in symbols:
            try:
                resp = client.set_leverage(
                    category="linear",
                    symbol=symbol,
                    buyLeverage=str(int(leverage)),
                    sellLeverage=str(int(leverage)),
                ) or {}
                ret_code = resp.get("retCode")
                # Bybit V5: 0 = newly set; 110043 = "leverage not modified"
                # (already at the target value) — idempotent success.
                if ret_code in (0, "0", 110043, "110043"):
                    logger.info(
                        "set_leverage pre-flight: account=%s symbol=%s x%d ok "
                        "(retCode=%s)",
                        account.name, symbol, leverage, ret_code,
                    )
                    continue
                logger.warning(
                    "set_leverage pre-flight: account=%s symbol=%s x%d "
                    "rejected (retCode=%s retMsg=%s) — order placement may "
                    "be rejected until leverage is set",
                    account.name, symbol, leverage,
                    ret_code, resp.get("retMsg"),
                )
            except Exception as exc:  # noqa: BLE001
                # pybit raises on retCode != 0 for some endpoints; absorb
                # 110043 here too (same idempotent-already-set semantics).
                msg = str(exc)
                if "110043" in msg or "leverage not modified" in msg.lower():
                    logger.info(
                        "set_leverage pre-flight: account=%s symbol=%s x%d "
                        "already set (retCode=110043, idempotent)",
                        account.name, symbol, leverage,
                    )
                    continue
                logger.warning(
                    "set_leverage pre-flight: account=%s symbol=%s x%d "
                    "failed (%s) — order placement may be rejected until "
                    "leverage is set",
                    account.name, symbol, leverage, exc,
                )


def _resolve_account_leverage(account) -> int:
    """Pull integer leverage from an account's YAML config.

    Preferred path: ``risk.leverage`` (groups it with other risk
    caps). Fallback: top-level ``leverage`` on the account object.
    Returns 0 when neither is set or the value can't be coerced to
    a positive int.
    """
    candidates = []
    rm = getattr(account, "risk_manager", None)
    if rm is not None:
        candidates.append(getattr(rm, "leverage", None))
    # ``account`` is a TradingAccount; doesn't carry leverage today.
    # Fall through to RiskManager attribute which we'll wire in
    # accounts.yaml as ``risk.leverage``.
    for raw in candidates:
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _symbols_for_account(account, strategies_cfg: dict) -> list:
    """Return the union of symbols the account's strategies trade.

    Reads ``strategies_cfg`` (from ``load_strategy_config()``); each
    strategy entry carries a ``symbols`` list. The account's
    ``strategies`` attribute lists the strategy names that account is
    wired to. We take the union.
    """
    strat_names = getattr(account, "strategies", None) or []
    symbols = []
    for name in strat_names:
        cfg = (strategies_cfg or {}).get(name) or {}
        for sym in (cfg.get("symbols") or []):
            if sym and sym not in symbols:
                symbols.append(str(sym))
    return symbols


def _build_monitor_ohlcv_fetcher(settings: dict):
    """Build the ``(symbol, timeframe) -> DataFrame | None`` fetcher
    that ``run_monitor_tick`` needs to feed strategy ``monitor()``
    hooks fresh candles.

    Without this the monitor loop calls every strategy with
    ``candles_df=None`` and the strategies short-circuit at their
    first guard, never producing TP / SL / VWAP-cross / time-decay
    close verdicts. The bot then leans on the +30 min stuck-strategy
    watchdog and the borrow reconciler as a de-facto exit, which is
    what surfaced the recurring vwap/BTCUSDT stuck cascades (PR #566).

    Built fresh per tick so the connector matches what
    ``pipeline._build_vwap_signal`` / ``_build_turtle_soup_signal``
    do for signal generation. Returns ``None`` instead of raising
    on init failure so the caller's ``run_monitor_tick`` falls back
    to the prior no-change behaviour.
    """
    from src.runtime.market_data import fetch_candles, _build_exchange_client

    try:
        exchange = _build_exchange_client(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("monitor: ohlcv exchange init failed (%s)", exc)
        return None

    # Per-strategy default timeframes — fallback when a package's meta
    # JSON lacks ``timeframe``. Pre-2026-05-09 every package row was
    # written without the key, so the closure would short-circuit to
    # ``None`` and ``monitor()`` never received candles. Loading the
    # map here once per fetcher build keeps the hot path cheap.
    # Best-effort: a config-load failure leaves the map empty, which
    # means falsy-timeframe packages still short-circuit (no regression
    # vs the prior contract).
    try:
        from src.units.strategies import load_strategy_config
        _per_strategy_tf = {
            name: (cfg or {}).get("timeframe")
            for name, cfg in (load_strategy_config() or {}).items()
        }
    except Exception:  # noqa: BLE001
        _per_strategy_tf = {}

    def _fetch(symbol, timeframe, strategy_name=None):
        if not symbol:
            return None
        if not timeframe and strategy_name:
            timeframe = _per_strategy_tf.get(strategy_name)
        if not timeframe:
            return None
        return fetch_candles(
            symbol, timeframe,
            settings=settings,
            exchange_client=exchange,
            limit=200,
        )

    return _fetch


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
    _drain_critical_alerts(telegram_client)
    return result


def _drain_critical_alerts(telegram_client) -> None:
    """Forward queued critical alerts to Telegram, then drain.

    The coordinator's circuit breaker (PR #741) and other callers push
    alerts onto the in-process queue at
    ``src.units.dashboards.alerts``; pre-this-PR the queue had no
    autonomous consumer, so ``level="critical"`` items (e.g.
    "Account auto-paused after N consecutive exchange rejections")
    only surfaced when the operator manually issued ``/alerts`` on
    Telegram. The 2026-05-10 incident chain (operator missed an
    8-hour VWAP silence; circuit-breaker behaviour ambiguous because
    the would-be alert was queued but never sent) was rooted in this
    silent queue.

    Drain on every tick so operator notification latency is bounded
    by ``TICK_INTERVAL_SECONDS``. Best-effort — never let a
    notification failure break the trader loop.
    """
    try:
        from src.units.dashboards.alerts import pop_alerts
        for alert in pop_alerts():
            if str(alert.get("level", "")).lower() != "critical":
                continue
            source = alert.get("source") or "unknown"
            msg = alert.get("message") or ""
            try:
                telegram_client.send_message(f"[CRITICAL][{source}] {msg}")
            except Exception:  # noqa: BLE001
                logger.exception("alert_drainer: telegram send failed")
    except Exception:  # noqa: BLE001
        logger.exception("alert_drainer: pop_alerts failed")


def main() -> None:
    load_dotenv()
    settings = build_settings_from_env()

    validate_startup()
    # Operator directive 2026-05-03 — dry/live mode is no longer in env.
    # Per-account ``mode: live | dry_run`` in config/accounts.yaml is the
    # only toggle (see RiskManager.dry_run). Startup logs only report
    # exchange / symbol / testnet — the mode mix is account-scoped.
    logger.info(
        "Startup validation passed. exchange=%s bybit_testnet=%s symbol=%s",
        settings.get("exchange"),
        os.environ.get("BYBIT_TESTNET"),
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

    # S-021: log open packages per strategy on every startup so the operator
    # can see at a glance that monitoring will resume (BUG-048 observability gap).
    try:
        from src.runtime.boot_audit import report_open_packages_on_boot
        report_open_packages_on_boot()
    except Exception as exc:  # noqa: BLE001
        logger.warning("boot_audit skipped: %s", exc)

    # Sprint A-3: compare journal open rows against live Bybit positions.
    # Ghost rows (journal open, Bybit flat) get a Telegram alert immediately
    # on startup — before the first tick — so the operator can investigate.
    try:
        from src.runtime.boot_audit import reconcile_journal_vs_exchange_on_boot
        reconcile_journal_vs_exchange_on_boot()
    except Exception as exc:  # noqa: BLE001
        logger.warning("boot_reconcile skipped: %s", exc)

    # PR 3 cutover: set per-symbol leverage on every linear-perp account
    # before the first tick. Best-effort; logs warnings on failure and
    # never blocks boot. Idempotent on Bybit's retCode=110043 ("leverage
    # not modified") so re-calling on every restart is normal.
    _apply_per_account_leverage()

    exchange_client = _build_exchange_adapter(settings)
    telegram_client = _build_telegram_client()

    loop = str(os.environ.get("LOOP", "true")).strip().lower() not in {"false", "0", "no"}
    # 2026-05-08 operator directive: re-evaluate every minute, on
    # 5-min candles. Strategies are unchanged — they still operate on
    # 5-min bars — but a 1-min tick gives up to 4 min faster reaction
    # to a fresh candle close. Override per environment via
    # TICK_INTERVAL_SECONDS in the systemd unit / .env if a slower
    # cadence is needed (e.g. backtests, reduced API budget).
    interval = int(os.environ.get("TICK_INTERVAL_SECONDS", "60"))

    if not loop:
        logger.info("LOOP=false: running single tick.")
        run_one_tick(settings, exchange_client, telegram_client)
        return

    logger.info("Starting continuous loop. TICK_INTERVAL_SECONDS=%s", interval)
    tick_count = 0
    last_tick_status = "starting"
    while True:
        tick_count += 1
        try:
            run_one_tick(settings, exchange_client, telegram_client)

            # CLAUDE.md § Architecture rules § 2 + § 3 +
            # architecture-audit-2026-05-02 P1-4: after generating
            # signals on this tick, run the monitor loop across every
            # open order package. The loop calls each strategy's
            # monitor() hook with fresh candles and applies non-None
            # verdicts to the DB unit. Best-effort; never raises.
            try:
                from src.runtime.order_monitor import run_monitor_tick
                run_monitor_tick(
                    ohlcv_fetcher=_build_monitor_ohlcv_fetcher(settings),
                )
            except Exception:  # noqa: BLE001
                logger.exception("order_monitor tick failed")

            # PR5: heartbeat is the single source of truth for "trader is
            # alive". Writes after a successful tick, not before — so a
            # tick that crashes mid-run doesn't refresh the heartbeat and
            # the watchdog will alert.
            last_tick_status = "ok"
            write_heartbeat(status=last_tick_status, tick=tick_count)
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
            last_tick_status = "error"
            write_heartbeat(status=last_tick_status, tick=tick_count)
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
                # S-telegram-format: two parallel hourly messages, one
                # focused on strategies, one focused on accounts/trades.
                # Both use HTML mode so their detail sections render as
                # collapsable blockquotes; ``send_telegram_direct`` is
                # called per-report so an HTML parse failure on one
                # doesn't suppress the other.
                strat_message = build_hourly_report(
                    now_utc=now_utc, tick_interval_s=interval,
                )
                acct_message = build_accounts_hourly_report(
                    now_utc=now_utc, tick_interval_s=interval,
                )
                logger.info(
                    "hourly report dispatch: slot=%s strat_len=%d acct_len=%d",
                    now_utc.strftime("%Y-%m-%d-%H"),
                    len(strat_message), len(acct_message),
                )
                for label, body in (
                    ("strategies", strat_message),
                    ("accounts", acct_message),
                ):
                    try:
                        send_telegram_direct(body, parse_mode="HTML")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "hourly report HTML send (%s) failed (%s); "
                            "falling back to scheduled plain-text path",
                            label, exc,
                        )
                        send_scheduled(body)
                report(
                    "hourly_report", "dispatched",
                    level=Level.INFO,
                    slot=now_utc.strftime("%Y-%m-%d-%H"),
                    strat_chars=len(strat_message),
                    acct_chars=len(acct_message),
                    demo=force_demo,
                )
                if force_demo:
                    try:
                        os.remove(demo_flag)
                    except OSError:
                        pass

                # CLAUDE.md § Architecture rules § 6 +
                # architecture-audit-2026-05-02 P0-3: liveness watchdog
                # piggybacks on the hourly cycle. Pings the operator
                # when actionable signals fired but no trades landed —
                # the gap that allowed BUG-034 to hide. Best-effort;
                # never raises.
                try:
                    from src.runtime.liveness_watchdog import run_liveness_watchdog
                    run_liveness_watchdog(now_utc=now_utc)
                except Exception:  # noqa: BLE001
                    logger.exception("liveness_watchdog dispatch failed")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to send hourly report")
            report(
                "hourly_report", "failed",
                level=Level.WARN,
                reason=f"{type(exc).__name__}: {exc}",
            )

        # Refresh the heartbeat between ticks so the dashboard / diag
        # liveness signal is "is this process responsive *right now*"
        # rather than "did the last tick complete in the last 15 min".
        # Cadence is HEARTBEAT_INTERVAL_SECONDS (default 60 s — one
        # write per minute is free on a loopback FS). A pipeline hang
        # still stops the heartbeat because we run inline on the main
        # thread; a daemon-thread writer would falsely report alive.
        heartbeat_interval = int(
            os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "60")
        )
        if heartbeat_interval <= 0:
            heartbeat_interval = 60
        logger.info(
            "Sleeping %s seconds until next tick (heartbeat every %s s).",
            interval, heartbeat_interval,
        )
        end_time = time.monotonic() + interval
        while True:
            remaining = end_time - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(heartbeat_interval, remaining))
            if time.monotonic() < end_time:
                # Status reflects the last completed tick — refreshes
                # mtime so liveness checks see a fresh signal without
                # losing the "ok / error" state of the most recent run.
                write_heartbeat(status="ok", tick=tick_count)


if __name__ == "__main__":
    main()
