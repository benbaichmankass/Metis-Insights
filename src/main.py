from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv

from src.exchange.bybit_connector import BybitConnector
from src.runtime.heartbeat import write_heartbeat
from src.runtime.outcomes import Level, report
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
            # Future non-bybit derivatives have their own leverage
            # primitives; this helper is bybit-specific.
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
            "demo": getattr(account, "demo", False),
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

    Only source today: ``risk.leverage`` on the account's RiskManager
    (groups it with other risk caps). The ``TradingAccount`` object
    itself carries no leverage field, so there is no separate
    top-level fallback. Returns 0 when unset or the value can't be
    coerced to a positive int.
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
    from src.runtime.market_data import fetch_candles, connector_for_symbol

    # Per-symbol connector cache. The monitor must route each symbol to the
    # SAME exchange the signal builders use (BTCUSDT → Bybit; MES/MGC/MHG →
    # IBKR, per config/instruments.yaml). A single default client asked Bybit
    # for the IB futures ("bybit does not have market symbol MHG"), so open
    # IB-futures positions got candles=None and the strategy monitor()
    # short-circuited — bot-side TP/SL/time-decay exits never ran (the
    # broker-side IBKR bracket still held the position). connector_for_symbol
    # falls back to the default EXCHANGE for unprofiled symbols, so BTCUSDT
    # routing is unchanged. Cached so the (possibly IBKR) client is built at
    # most once per symbol per fetcher build.
    _connector_cache: dict = {}

    def _connector_for(symbol):
        if symbol in _connector_cache:
            return _connector_cache[symbol]
        try:
            client = connector_for_symbol(symbol, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("monitor: connector init failed for %s (%s)", symbol, exc)
            client = None
        _connector_cache[symbol] = client
        return client

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
        client = _connector_for(symbol)
        if client is None:
            return None
        return fetch_candles(
            symbol, timeframe,
            settings=settings,
            exchange_client=client,
            limit=200,
        )

    return _fetch


def _run_symbol_tick(settings: dict, exchange_client, telegram_client) -> dict:
    """Run the pipeline for a single symbol (the original run_one_tick body)."""
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


# Per-exchange default instrument when a configured account omits the
# ``symbols`` field in accounts.yaml. Keeps an account trading its natural
# instrument rather than nothing.
_EXCHANGE_DEFAULT_SYMBOL = {
    "bybit": "BTCUSDT",
    "interactive_brokers": "MES",
}


def _resolve_tick_symbols(settings: dict) -> list:
    """Symbols to run this tick — derived from configured accounts.

    ``config/accounts.yaml`` is the single source of truth: the tick loop
    trades the union of every *configured* account's ``symbols`` (falling
    back to the per-exchange default when an account omits the field),
    restricted to accounts that actually trade (an explicit
    ``strategies: []`` opts an account out; ``None`` / non-empty are
    included). So one process trades BTCUSDT (Bybit) and MES (IB) whenever
    those accounts are configured.

    There is intentionally **no enable flag**. Per the "one switch per
    account" rule, ``mode: live|dry_run`` is the only runtime gate — a
    ``dry_run`` account still generates signals (logged, never executed),
    and the symbol set never depends on a separate on/off env. The
    previous ``MULTI_SYMBOL_ENABLED`` env was a forbidden second gate and
    has been removed.

    Best-effort: the primary ``SYMBOL`` is always included, and any
    account-load failure falls back to ``[primary]`` so a config error can
    never empty the tick (defence-in-depth; preserves single-symbol
    behaviour).
    """
    primary = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))
    try:
        from src.units.accounts import load_accounts

        seen: set = set()
        out: list = []
        if primary:
            seen.add(primary)
            out.append(primary)
        for acct in load_accounts():
            if not getattr(acct, "configured", True):
                continue
            strategies = getattr(acct, "strategies", None)
            if strategies is not None and len(strategies) == 0:
                continue  # explicit opt-out — account trades nothing
            syms = list(getattr(acct, "symbols", None) or [])
            if not syms:
                default = _EXCHANGE_DEFAULT_SYMBOL.get(
                    str(getattr(acct, "exchange", "") or "").lower()
                )
                syms = [default] if default else []
            for s in syms:
                s = str(s).strip()
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
        return out or [primary]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_resolve_tick_symbols: account-derived symbols failed (%s); "
            "single-symbol fallback to %s", exc, primary,
        )
        return [primary]


def _exchange_for_symbol(symbol: str):
    """Return the instrument's exchange (BTCUSDT→bybit, MES→IB) or None."""
    try:
        from src.core.coordinator import _instrument_exchange_for
        return _instrument_exchange_for(symbol)
    except Exception:  # noqa: BLE001
        return None


def _per_symbol_client(symbol: str, settings: dict):
    """Build the right market-data connector for *symbol* (None on failure)."""
    try:
        from src.runtime.market_data import connector_for_symbol
        return connector_for_symbol(symbol, settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_one_tick: connector build failed for %s: %s", symbol, exc)
        return None


def run_one_tick(settings: dict, exchange_client, telegram_client) -> dict:
    """Run a single pipeline tick across the configured symbol(s).

    Single-symbol (default) is byte-identical to the legacy behaviour.
    With multi-symbol enabled, each symbol runs its own pipeline pass with
    a per-symbol exchange + connector; a failure on one symbol (e.g. the IB
    Gateway being down for MES) is isolated and never aborts the others, so
    the live crypto path keeps trading regardless of the MES side.
    """
    symbols = _resolve_tick_symbols(settings)
    if len(symbols) <= 1:
        return _run_symbol_tick(settings, exchange_client, telegram_client)

    primary = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))
    results = {}
    for sym in symbols:
        per = dict(settings)
        per["SYMBOL"] = sym
        ex = _exchange_for_symbol(sym)
        if ex:
            per["EXCHANGE"] = ex
        client = exchange_client if sym == primary else _per_symbol_client(sym, per)
        try:
            results[sym] = _run_symbol_tick(per, client, telegram_client)
        except Exception:  # noqa: BLE001
            logger.exception("run_one_tick: symbol %s tick failed (isolated)", sym)
            results[sym] = {"error": "tick_failed", "symbol": sym}
    return {"multi_symbol": True, "results": results}


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

    # D-3: enable WAL on the trade journal so the pipeline writer, order
    # monitor reader, dashboard API, and diag relay can run concurrently
    # without "database is locked" contention. Persistent at the file
    # level — idempotent on every boot. Best-effort; never blocks start.
    try:
        from src.utils.db_init import enable_wal_mode
        enable_wal_mode()
    except Exception as exc:  # noqa: BLE001
        logger.warning("WAL enable skipped: %s", exc)

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

    # S-PERSIST-CANON: snapshot the active strategies.yaml into the
    # (previously dead) trade_journal.db::strategy_versions table so the
    # Data Explorer carries an in-DB strategy-config version history.
    try:
        from src.runtime.boot_audit import snapshot_strategy_versions_on_boot
        snapshot_strategy_versions_on_boot()
    except Exception as exc:  # noqa: BLE001
        logger.warning("strategy_version snapshot skipped: %s", exc)

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
        # Refresh the heartbeat at tick-START, before any work. The IBKR
        # restart-loop incident (2026-06-05) showed why "write only after
        # a successful tick" can starve liveness: a slow tick (e.g. a
        # logged-out IB Gateway making a request hang) holds the loop past
        # the watchdog's stale threshold, the watchdog autoheals (kills)
        # the trader before it ever reaches the post-tick write, and the
        # process never gets to refresh the heartbeat → a perpetual
        # restart loop. Stamping the heartbeat first means a tick that is
        # merely *slow* (now bounded — every IB call has a hard timeout)
        # still proves liveness; a genuine hang that outlives even this is
        # the only thing that can now stall the beat. The post-tick write
        # below still records the "ok"/"error" outcome.
        write_heartbeat(status="tick_start", tick=tick_count)
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

            # Market-neutral pairs sleeve (M22 D2): an ISOLATED 2-leg executor
            # that does NOT fit the single-symbol intent model, so it runs as
            # its own once-per-tick hook (prop-bridge pattern) rather than
            # through multi_account_execute. For each configured pair it
            # reconstructs open-state from the journal, decides on fresh 1h
            # candles, and (only for an `execution: live` pair) places/closes
            # the two legs; an `execution: shadow` pair computes + logs the
            # would-be trade but places nothing. Inert until config/pairs.yaml
            # is authored; best-effort (never raises into the loop).
            try:
                from src.units.strategies.pairs_executor import run_pairs_tick
                run_pairs_tick(settings)
            except Exception:  # noqa: BLE001
                logger.exception("pairs_tick failed")

            # Macro/value thesis sleeve (M28 P3): an ISOLATED, slow-cadence,
            # OBSERVE-ONLY scanner that reads the point-in-time valuation
            # snapshots, forms weeks-horizon value theses (the S1 rule-based
            # former), and logs the would-be theses to a soak. It places NOTHING
            # — the defined-risk options executor is P5, so no order path exists
            # here regardless of config/macro_theses.yaml's `execution` gate.
            # Cadence-gated (hourly by default) + best-effort (never raises into
            # the loop, never blocks a trade). Inert until valuation snapshots
            # accrue.
            try:
                from src.units.strategies.macro_thesis.thesis_tick import run_macro_thesis_tick
                run_macro_thesis_tick(settings)
            except Exception:  # noqa: BLE001
                logger.exception("macro_thesis_tick failed")

            # Prop trades are a manual bridge (no broker feed), so the
            # order_monitor above never sees them. Emit a periodic
            # "still monitoring" pulse per open prop position instead so
            # the operator knows the system is actively tracking it
            # between report-backs. Internally rate-limited to
            # PROP_MONITOR_PULSE_SECONDS (default 15 min); best-effort.
            try:
                from src.prop.prop_monitor_pulse import run_prop_monitor_pulse
                run_prop_monitor_pulse()
            except Exception:  # noqa: BLE001
                logger.exception("prop_monitor_pulse tick failed")

            # A prop ticket that passed its validity window with no report-back
            # is silent drift — the bot can't tell whether it was
            # placed-and-unreported or skipped. Ask the operator with a Yes/No
            # prop-bot prompt: No → log it expired; Yes → send the report prompt
            # to collect the fill details. Once per tick; internally idempotent
            # (each ticket is prompted exactly once via its status flip).
            try:
                from src.prop.prop_expiry_prompt import run_prop_expiry_prompts
                run_prop_expiry_prompts()
            except Exception:  # noqa: BLE001
                logger.exception("prop_expiry_prompt tick failed")

            # The rule-distance guard is only as fresh as the last
            # account-status report-back. While a prop position is open and
            # the latest prop_account_status snapshot is absent/stale, ask
            # the operator for one on the prop bot — with the paste-ready
            # reply formats (`bal ...` / JSON) baked into the message.
            # Internally rate-limited (PROP_STATUS_REQUEST_MAX_AGE_HOURS /
            # PROP_STATUS_REQUEST_COOLDOWN_HOURS); best-effort.
            try:
                from src.prop.prop_status_request import run_prop_status_request
                run_prop_status_request()
            except Exception:  # noqa: BLE001
                logger.exception("prop_status_request tick failed")

            # When an open prop trade's current price crosses its SL or TP
            # level, fire a one-shot Telegram + FCM alert prompting the
            # operator to check whether the trade closed and report back.
            # One alert per level per open-position lifetime; best-effort.
            try:
                from src.prop.prop_sl_tp_alert import run_prop_sl_tp_alert
                run_prop_sl_tp_alert()
            except Exception:  # noqa: BLE001
                logger.exception("prop_sl_tp_alert tick failed")

            # While the bot is still waiting for the operator's place-decision on
            # a freshly-emitted prop ticket, price can move beyond the ticket's
            # brackets — the setup is no longer placeable. Proactively warn ("do
            # NOT place it if you haven't") and re-ask the Yes/No, before the
            # slower valid_until timeout would. Once per tick; internally
            # idempotent (prompted exactly once via its status flip); best-effort.
            try:
                from src.prop.prop_invalidation_prompt import run_prop_invalidation_prompts
                run_prop_invalidation_prompts()
            except Exception:  # noqa: BLE001
                logger.exception("prop_invalidation_prompt tick failed")

            # A supposed-to-be-live broker account reading unreachable (IB
            # gateway logged out, exchange API 401-ing, creds rotated out)
            # is a money-at-risk condition that must surface LOUDLY, not sit
            # quietly in a report body — the IB gateway was dark across
            # reviews and went unflagged. Latched per-account: one DOWN ping
            # on a confirmed cross-into-down (>= N consecutive checks), one
            # OK ping on recovery. Internally cadence-gated
            # (ACCOUNT_REACHABILITY_CHECK_SECONDS, default 10 min); reuses
            # the reconciler's reachability primitive; best-effort.
            try:
                from src.runtime.account_reachability_alert import (
                    run_account_reachability_check,
                )
                run_account_reachability_check()
            except Exception:  # noqa: BLE001
                logger.exception("account_reachability_check tick failed")

            # Trainer-VM-down alert (operator-requested 2026-07-08): the trainer
            # VM can go SSH-dead / OOM-hung and nothing fires a loud alert. The
            # trainer rsyncs trainer_status.json into the mirror every ~2 min, so
            # a mirror stale beyond TRAINER_DOWN_STALE_SECONDS (default 20 min) is
            # a confirmed DOWN. Latched: one 🔴 DOWN ping (Telegram + WARNING FCM)
            # + surfaced on /api/bot/notifications for the app banners, one 🟢 OK
            # on recovery. Internally cadence-gated (5 min); best-effort.
            try:
                from src.runtime.trainer_reachability_alert import (
                    run_trainer_reachability_check,
                )
                run_trainer_reachability_check()
            except Exception:  # noqa: BLE001
                logger.exception("trainer_reachability_check tick failed")

            # IB connection-state legibility (BL-20260707-IB-STATE-LEGIBILITY):
            # dump each live IBClient's non-blocking connection_state() to
            # runtime_logs/ib_state.json so the SEPARATE web-api process can
            # surface "connected vs down, transitory backoff vs real wedge" via
            # /api/diag/ib_state. Pure observability, best-effort — never
            # touches the socket, never gates a trade.
            try:
                from src.units.accounts.ib_client import write_ib_state_file
                write_ib_state_file()
            except Exception:  # noqa: BLE001
                logger.debug("write_ib_state_file tick hook skipped", exc_info=True)

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
        # Hourly report + the liveness-watchdog piggyback were moved OUT
        # of the trader loop to the single flock-guarded producer
        # (scripts/send_hourly_now.py via ict-hourly-snapshot.timer) so
        # the operator gets EXACTLY ONE dispatch per hour. The old
        # in-loop should_send_summary path double-fired alongside the
        # timer ("hourly coming too often"); see TELEGRAM-SPEC.md § 4.1.
        # Running the watchdog from the timer is strictly better — it
        # fires on the wall-clock hour even if a tick is wedged.

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
                write_heartbeat(status=last_tick_status, tick=tick_count)


if __name__ == "__main__":
    main()
