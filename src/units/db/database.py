"""
Database Module
Handles SQLite database operations for storing trades, backtests, and strategy versions
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone
import json


def _migrate_add_strategy_name(cursor: sqlite3.Cursor) -> bool:
    """Add ``strategy_name`` column to ``trades`` table if absent.

    Idempotent: returns True only on the run that actually adds the column.
    Mirrors the helper in ``scripts/init_db.py`` (kept private here to
    avoid an import cycle between scripts/ and src/).
    """
    cursor.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    if "strategy_name" in columns:
        return False
    cursor.execute("ALTER TABLE trades ADD COLUMN strategy_name TEXT")
    return True


def _migrate_add_is_demo(cursor: sqlite3.Cursor) -> bool:
    """Add ``is_demo`` column to ``trades`` table if absent.

    Demo trades (from accounts with ``demo: true`` in accounts.yaml) carry
    is_demo=1 so PnL/stats queries can exclude them from live-account
    aggregations. Idempotent: returns True only on the run that adds the column.
    """
    cursor.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    if "is_demo" in columns:
        return False
    cursor.execute("ALTER TABLE trades ADD COLUMN is_demo BOOLEAN DEFAULT 0")
    return True


def _migrate_add_account_class(cursor: sqlite3.Cursor) -> bool:
    """Add ``account_class`` column to ``trades`` table if absent.

    The paper-vs-real-money funding category (``'paper'`` / ``'real_money'``)
    mirrored from ``config/accounts.yaml::account_class``. This is the
    canonical paper/real reporting axis; ``is_demo`` is kept in sync for
    back-compat but ``account_class`` is authoritative. Default NULL keeps
    pre-existing rows un-stamped — readers fall back to ``is_demo`` for
    NULL rows (see ``_row_is_paper`` and the API "not paper" predicates)
    until the backfill (``scripts/ops/backfill_account_class.py``) runs.
    Idempotent: returns True only on the run that adds the column.
    """
    cursor.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    if "account_class" in columns:
        return False
    cursor.execute("ALTER TABLE trades ADD COLUMN account_class TEXT")
    return True


def _row_is_paper(row: sqlite3.Row) -> bool:
    """Return True when a ``trades`` row is a PAPER-money trade.

    Authoritative axis is ``account_class`` (``'paper'`` / ``'real_money'``).
    When the column is present and non-NULL, use it. When the column is
    absent (old DB) or NULL (un-backfilled row), fall back to the legacy
    ``is_demo`` boolean. Used by the notify-skip paths so paper trades
    never fire a real-money phone notification.
    """
    try:
        keys = row.keys()
    except AttributeError:
        keys = ()
    if "account_class" in keys and row["account_class"] is not None:
        return str(row["account_class"]).strip().lower() == "paper"
    return bool(row["is_demo"])


def _migrate_add_account_id(cursor: sqlite3.Cursor) -> bool:
    """Add ``account_id`` column to ``trades`` table if absent.

    Default ``'live'`` keeps pre-existing rows attributed to the legacy live
    account. Idempotent: returns True only on the run that adds the column.
    Mirrors ``migrate_add_account_id`` in ``scripts/init_db.py``.
    """
    cursor.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    if "account_id" in columns:
        return False
    cursor.execute("ALTER TABLE trades ADD COLUMN account_id TEXT NOT NULL DEFAULT 'live'")
    return True


def _migrate_add_order_package_id(cursor: sqlite3.Cursor) -> bool:
    """Add ``order_package_id`` column to ``trades`` table if absent.

    Many-to-one back-reference from a trade row to the
    ``order_packages`` decision that produced it. Before this column
    the only link was ``order_packages.linked_trade_id`` (one slot per
    package) — so when a single decision fanned out into multiple
    trade rows (real-money entry + demo mirror + intent_reduce flip
    leg + multi-account fanout) only the **last** writer's
    ``update_order_package(linked_trade_id=...)`` survived. The
    others showed up as ``(unlinked)`` in the reconciler's orphan-
    sweep notification and could not be cascaded by
    ``_resolve_linked_package_id``. With this column every trade row
    carries the package id directly; the legacy ``linked_trade_id``
    keeps its "primary entry trade" semantics for back-compat. Pre-
    existing rows are left at NULL — the reconciler falls back to
    the legacy lookup for them.

    Idempotent: returns True only on the run that adds the column.
    """
    cursor.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    if "order_package_id" in columns:
        return False
    cursor.execute("ALTER TABLE trades ADD COLUMN order_package_id TEXT")
    return True


def _migrate_add_closed_at(cursor: sqlite3.Cursor) -> bool:
    """Add ``closed_at`` column to ``trades`` table if absent.

    The **canonical close timestamp** of a trade — written as a real column on
    every close path (P1-B), replacing the read-time derivation chain
    (``order_packages.updated_at`` → parse ``notes.closed_at`` JSON → fall back
    to the open time) that the dashboard/Android/API endpoints carry today.
    See ``docs/audits/dashboard-truth-and-persistence-2026-06-16.md`` (defect
    S2). NULL until a close path stamps it; the open-row state and terminal
    rows that never opened a position (``rejected``/``exchange_rejected``)
    legitimately leave it NULL. Pre-existing closed rows are backfilled by the
    P1-E repair pass (``scripts/ops/backfill_closed_at.py``).

    Idempotent: returns True only on the run that actually adds the column.
    """
    cursor.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    if "closed_at" in columns:
        return False
    cursor.execute("ALTER TABLE trades ADD COLUMN closed_at TEXT")
    return True


def _migrate_add_reconcile_status(cursor: sqlite3.Cursor) -> bool:
    """Add ``reconcile_status`` column to ``trades`` if absent.

    Makes ORPHAN an explicit, queryable terminal state rather than something a
    reader must INFER from ``setup_type='adopted_orphan'`` / ``strategy_name=
    'orphan_adopt'`` / ``status='orphaned'`` (operator directive 2026-06-24:
    orphan is a problem to RESOLVE, never a silent resting status). Values:

    * ``NULL``           — unspecified (the normal case for a non-orphan trade,
                           and pre-migration rows).
    * ``'unreconciled'`` — an orphan row that has NOT been tied back to a real
                           trade / order package: the red-flag state to resolve.
    * ``'reconciled'``   — an orphan reconciled to its originating order package
                           (e.g. a reverse-reconciler adoption that recovered the
                           real strategy + package).
    * ``'superseded'``   — a phantom flap duplicate void-flagged in favour of the
                           single canonical row (written by the historical
                           reconciliation pass; excluded from analytics).

    Idempotent: returns True only on the run that actually adds the column.
    """
    cursor.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    if "reconcile_status" in columns:
        return False
    cursor.execute("ALTER TABLE trades ADD COLUMN reconcile_status TEXT")
    return True


def _migrate_add_trade_costs(cursor: sqlite3.Cursor) -> bool:
    """Add the per-trade transaction-cost columns to ``trades`` if absent (M18 P0a).

    Captures what the per-cell live path never recorded: the trade's transaction
    cost, so the M18 capital allocator's EV scorer can learn cost as a feature and
    a future learned ranker gets an unbiased net-R label (the #1 data gap — see
    ``docs/research/capital-allocation-ai-DESIGN.md`` § 4).

    * ``fee_taker_usd``     — taker fees paid over the round trip (USD).
    * ``fee_maker_usd``     — maker fees (USD); NULL until a broker-truth writer
                              splits maker/taker.
    * ``funding_paid_usd``  — cumulative perp funding / prop swap (USD); NULL until
                              the broker-truth + hold-time writer lands.
    * ``cost_source``       — ``'broker'`` (exchange-reported) / ``'estimate'``
                              (fixed round-trip model) / NULL (uncosted).

    The close path stamps a fixed-model ``estimate`` today; a broker-truth writer
    (where the integration exposes per-fill fees + funding) is the follow-up that
    upgrades the source to ``'broker'``. Pre-existing + backtest rows stay NULL.

    Idempotent: returns True only on the run that actually adds the columns.
    """
    cursor.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    if "cost_source" in columns:
        return False
    cursor.execute("ALTER TABLE trades ADD COLUMN fee_taker_usd REAL")
    cursor.execute("ALTER TABLE trades ADD COLUMN fee_maker_usd REAL")
    cursor.execute("ALTER TABLE trades ADD COLUMN funding_paid_usd REAL")
    cursor.execute("ALTER TABLE trades ADD COLUMN cost_source TEXT")
    return True


def _migrate_add_broker_order_id(cursor: sqlite3.Cursor) -> bool:
    """Add the ``broker_order_id`` join key to ``trades`` if absent (Slice B / B0).

    The broker's *entry* order id — the exchange ``orderId`` the place call
    returned at open — is already captured, but only inside the ``notes`` JSON
    blob (``notes.trade_id``, written by ``execute._log_trade_to_journal``). That
    is not a first-class, indexable column, so tying a trade back to its
    per-fill rows in the exchange-fills store
    (``runtime_state/exchange_fills.sqlite``, whose ``exchange_fills.order_id`` is
    the Bybit ``orderId``) would otherwise be a JSON-extract or a heuristic
    ``(account, symbol, side, qty, time-window)`` match that risks
    double-counting fills across overlapping same-symbol trades.

    Promoting the entry order id to a real column makes the eventual
    broker-truth cost sweep (B2 — ``fee_taker_usd``/``fee_maker_usd`` +
    ``funding_paid_usd``, ``cost_source`` ``estimate``→``broker``) an EXACT
    indexed join instead of a fuzzy one. Pure observability — never read on the
    order path, never gates a trade. Forward rows get it at open;
    ``scripts/ops/backfill_broker_order_id.py`` populates it from
    ``notes.trade_id`` for the historical book.

    Idempotent: returns True only on the run that actually adds the column.
    """
    cursor.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    if "broker_order_id" in columns:
        return False
    cursor.execute("ALTER TABLE trades ADD COLUMN broker_order_id TEXT")
    return True


def _migrate_add_tpsl_leg_ids(cursor: sqlite3.Cursor) -> bool:
    """Add ``sl_order_id`` / ``tp_order_id`` to ``trades`` if absent (BL-20260721-BYBIT2-XRP-TPSL-LEGCAP).

    Under ``BYBIT_TPSL_MODE=partial`` each trade gets its own qty-scoped
    Partial SL/TP leg on Bybit — but nothing tracked which broker ``orderId``
    belonged to which trade, so every trailing-stop tick had no target to
    amend and fell back to ``set_trading_stop``'s ADD-a-new-leg behaviour
    (Bybit's own V5 docs: Partial mode "can only add partial position TP/SL
    orders", unlike Full mode's in-place modify). Legs piled up unbounded
    until Bybit's 20-combined-leg-per-symbol cap silently blocked further
    amends (23 stranded legs on one bybit_2 XRPUSDT position, live-confirmed
    2026-07-21).

    These columns are the fix's foundation: ``execute._log_trade_to_journal``
    now persists the entry-time leg id(s) (captured via a before/after
    snapshot diff around order placement, since Bybit's inline-SL/TP place
    response never returns the leg's own orderId), so
    ``execute.modify_open_order`` can target Bybit's ``amend_order`` at that
    SPECIFIC leg instead of re-adding, and the close path can cancel it
    explicitly. ``NULL`` on any pre-migration / non-Bybit / non-partial-mode
    row — those fall back to the legacy add-a-leg behaviour, logged loudly.

    Idempotent: returns True only on the run that actually adds the columns.
    """
    cursor.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    if "sl_order_id" in columns:
        return False
    cursor.execute("ALTER TABLE trades ADD COLUMN sl_order_id TEXT")
    cursor.execute("ALTER TABLE trades ADD COLUMN tp_order_id TEXT")
    return True


def _migrate_add_order_package_model_scores(cursor: sqlite3.Cursor) -> bool:
    """Add ``model_scores`` column to ``order_packages`` if absent.

    Persists the per-model ML decision scores (shadow/advisory predictions)
    that were part of the trade, as a JSON object ``{model_id: {stage, score}}``
    — so consumers read them with a cheap SELECT instead of recompiling
    per-trade aggregates from ``runtime_logs/shadow_predictions.jsonl`` on
    every request. Observe-only metadata. Pre-existing rows stay NULL.

    Idempotent: returns True only on the run that adds the column.
    """
    cursor.execute("PRAGMA table_info(order_packages)")
    columns = {row[1] for row in cursor.fetchall()}
    if "model_scores" in columns:
        return False
    cursor.execute("ALTER TABLE order_packages ADD COLUMN model_scores TEXT")
    return True


def _migrate_add_order_package_exit_plan(cursor: sqlite3.Cursor) -> bool:
    """Add ``exit_plan`` + ``exit_plan_state`` columns to ``order_packages``.

    ``exit_plan`` is the strategy-declared (or legacy-derived) ExitPlan JSON
    captured at signal time — the static description of the whole intended exit
    (ladder rungs + final target + stop + trailing rule; see
    ``src/runtime/exit_plan.py``). ``exit_plan_state`` is the evolving state the
    materializer/monitor write as the plan is rested and re-materialised (rungs
    filled, current materialised SL/TP, prop-update count, and the
    ``materializations[]`` audit list — the SL/TP-modification history the
    2026-06-16 contract noted was missing). Both are observe-only JSON metadata;
    pre-existing rows stay NULL.

    Idempotent: returns True only on the run that actually adds the columns.
    """
    cursor.execute("PRAGMA table_info(order_packages)")
    columns = {row[1] for row in cursor.fetchall()}
    if "exit_plan" in columns and "exit_plan_state" in columns:
        return False
    if "exit_plan" not in columns:
        cursor.execute("ALTER TABLE order_packages ADD COLUMN exit_plan TEXT")
    if "exit_plan_state" not in columns:
        cursor.execute("ALTER TABLE order_packages ADD COLUMN exit_plan_state TEXT")
    return True


class Database:
    """Manages SQLite database for trade journal and backtest results"""
    
    def __init__(self, db_path=None):
        """
        Initialize database connection

        Args:
            db_path (str | None): Path to SQLite database file. When None
                (the default), resolves the canonical trade-journal path via
                ``src.utils.paths.trade_journal_db_path()`` — the env-first,
                CWD-independent resolver. Passing ``None`` is the correct way
                to get the live/canonical DB; an explicit path is for tests
                and one-off tooling only. The historical default of the bare
                relative ``"trade_journal.db"`` is what seeded the stray
                duplicate journals on the live VM and must not return.
        """
        if db_path is None:
            from src.utils.paths import trade_journal_db_path
            db_path = trade_journal_db_path()
        self.db_path = Path(db_path)
        self.conn = None
        self.create_tables()
    
    def connect(self):
        """Create database connection"""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row  # Allow dict-like access
        # Wait up to 3s for a lock rather than raising "database is locked"
        # immediately. The journal is shared (live trader + web-api + sidecars);
        # a brief writer lock should degrade to a short wait, not a hard error
        # (RISK-3, BL-20260707-HEALTHAPI-ACCTBAL-BLOCKING-DB).
        self.conn.execute("PRAGMA busy_timeout=3000")
        return self.conn
    
    def create_tables(self):
        """Create all necessary tables if they don't exist"""
        conn = self.connect()
        cursor = conn.cursor()
        
        # Trades table - stores all executed trades (backtest or live)
        #
        # TODO(WC-6 follow-up): once the P1-E backfill has cleaned the
        # historical rows (legacy direction='buy' on is_backtest=1 rows +
        # any non-vocabulary status), add CHECK constraints
        #   direction IN ('long','short')
        #   status IN (<documented status vocabulary>)
        # via an EXPLICIT rebuild migration, NOT here. A CHECK added only
        # to this `CREATE TABLE IF NOT EXISTS` is inert for the existing
        # live DB (the table already exists) and would only constrain
        # fresh/test DBs — breaking tests that seed non-conforming fixture
        # rows. Adding a CHECK to the live table requires a full
        # table-rebuild that would FAIL on the legacy 'buy'/'sell' rows.
        # Ship the constraint as a separate change after the backfill.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                stop_loss REAL,
                take_profit_1 REAL,
                take_profit_2 REAL,
                take_profit_3 REAL,
                position_size REAL NOT NULL,
                setup_type TEXT,
                killzone TEXT,
                bias TEXT,
                entry_reason TEXT,
                exit_reason TEXT,
                pnl REAL,
                pnl_percent REAL,
                status TEXT DEFAULT 'open',
                notes TEXT,
                is_backtest BOOLEAN DEFAULT 1,
                strategy_name TEXT,
                account_id TEXT NOT NULL DEFAULT 'live',
                is_demo BOOLEAN DEFAULT 0,
                account_class TEXT,
                order_package_id TEXT,
                closed_at TEXT,
                fee_taker_usd REAL,
                fee_maker_usd REAL,
                funding_paid_usd REAL,
                cost_source TEXT,
                broker_order_id TEXT,
                sl_order_id TEXT,
                tp_order_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Idempotent migrations for pre-existing DBs missing these columns.
        _migrate_add_strategy_name(cursor)
        _migrate_add_account_id(cursor)
        _migrate_add_is_demo(cursor)
        _migrate_add_account_class(cursor)
        _migrate_add_order_package_id(cursor)
        _migrate_add_closed_at(cursor)
        _migrate_add_reconcile_status(cursor)
        _migrate_add_trade_costs(cursor)
        _migrate_add_broker_order_id(cursor)
        _migrate_add_tpsl_leg_ids(cursor)
        # Index for efficient per-account trade history queries.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_account_created "
            "ON trades (account_id, datetime(created_at) DESC)"
        )
        # Index for the reconciler's reverse lookup
        # (``_resolve_linked_package_id`` in src/runtime/order_monitor.py).
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_order_package_id "
            "ON trades (order_package_id)"
        )
        # Index for close-time-ordered queries (the canonical `closed_at`
        # column replaces the read-time COALESCE(op.updated_at, …) ordering
        # key — see dashboard-truth-and-persistence-2026-06-16.md S2).
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_closed_at "
            "ON trades (datetime(closed_at) DESC)"
        )
        # Index for the Slice-B broker-truth cost sweep's exact
        # trades→exchange_fills join key (Bybit entry orderId).
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_broker_order_id "
            "ON trades (broker_order_id)"
        )

        # Backtest results table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                strategy_version TEXT,
                start_date TEXT,
                end_date TEXT,
                total_trades INTEGER,
                winning_trades INTEGER,
                losing_trades INTEGER,
                win_rate REAL,
                profit_factor REAL,
                expectancy REAL,
                max_drawdown REAL,
                max_drawdown_pct REAL,
                sharpe_ratio REAL,
                total_pnl REAL,
                total_pnl_pct REAL,
                avg_win REAL,
                avg_loss REAL,
                largest_win REAL,
                largest_loss REAL,
                config JSON,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Strategy versions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version_name TEXT UNIQUE NOT NULL,
                description TEXT,
                config JSON NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 0
            )
        ''')

        # Order packages table (S-030 PR1, architecture-audit-2026-05-02
        # P1-5). Each row records the lifecycle of an OrderPackage from
        # generation through dispatch, monitor updates, and close. Per
        # CLAUDE.md § Architecture rules § 2 + § 4 the DB unit owns
        # three logs: signals (file-based today), order packages
        # (this table), and trades (the table above). The strategy
        # unit writes the open row when the package is dispatched and
        # updates the row from its monitor() loop; the row links to
        # the trades table via ``linked_trade_id`` once the account
        # unit places the order.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_packages (
                order_package_id TEXT PRIMARY KEY,
                strategy_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                confidence REAL,
                signal_logic TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                linked_trade_id INTEGER,
                close_reason TEXT,
                meta TEXT,
                model_scores TEXT,
                exit_plan TEXT,
                exit_plan_state TEXT,
                FOREIGN KEY (linked_trade_id) REFERENCES trades(id)
            )
        ''')
        # Idempotent migrations for pre-existing DBs missing these columns.
        _migrate_add_order_package_model_scores(cursor)
        _migrate_add_order_package_exit_plan(cursor)
        # Indexes — hourly report + UI helpers query by strategy and
        # by status; the per-strategy view is the primary access path
        # per Rule 4 ("order package logs per strategy").
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_packages_strategy_created "
            "ON order_packages (strategy_name, datetime(created_at) DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_packages_status "
            "ON order_packages (status, datetime(updated_at) DESC)"
        )

        # Signals table (S-034, architecture-audit-2026-05-02 P2-9).
        # Per CLAUDE.md § Architecture rules § 4 the DB unit owns three
        # logs side-by-side: signals (this table), order_packages
        # (above), trades (above). Pre-S-034 signals lived in two
        # places: ``runtime_logs/signal_audit.jsonl`` (file) and
        # ``data/trades.db::signals`` (legacy SQL). The transition
        # window flow is:
        #   1. JSONL writer dual-writes to this table.
        #   2. Readers (processor.get_recent_signals,
        #      liveness_watchdog._count_actionable_signals) flip to
        #      SQL when stable.
        #   3. JSONL writer + legacy data/trades.db::signals deleted
        #      after one full operator-confirmed day.
        # The schema mirrors what the JSONL writer already records
        # (``log_signal({…})``) so the dual-writer can map fields 1:1.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at_utc TEXT NOT NULL,
                strategy TEXT,
                symbol TEXT,
                side TEXT,
                qty REAL,
                status TEXT,
                reason TEXT,
                meta TEXT
            )
        ''')
        # Per Rule 4 the primary access path is "signals log per
        # strategy" (mirrors the order-packages indexing scheme).
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_strategy_logged "
            "ON signals (strategy, datetime(logged_at_utc) DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_logged_at "
            "ON signals (datetime(logged_at_utc) DESC)"
        )

        # Device tokens table (M12 S1). Stores per-device FCM registration
        # so the mobile_push notifier can fan out to the operator's
        # phone(s). Operator-only data; no PII beyond the device label
        # the operator chose. ``subscriptions`` is a JSON column —
        # null/empty means "subscribed to everything" (default-permissive,
        # matches the bot's no-third-gate principle).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS device_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                platform TEXT NOT NULL DEFAULT 'android',
                label TEXT,
                subscriptions TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_device_tokens_platform "
            "ON device_tokens (platform)"
        )

        # Learning-center progress (dashboard Learning tab, 2026-07-14).
        # One row per curriculum resource the operator has marked; the
        # dashboard Learning tab reads/writes it via /api/bot/learning/progress
        # so progress is durable + cross-device (not browser-local) and ready
        # to mirror to the Android app. resource_id is the stable slug from
        # comms/learning/curriculum.json. Operator-only observability data —
        # no trading impact, no order path. Browseable in the Data Explorer.
        # data-wiring: trade_journal.db IS the source of truth for learning
        # progress — this is operator-authored UI state, NOT a projection of
        # any other table, so there is no upstream to backfill from and history
        # begins at first write. Sole writer: POST /api/bot/learning/progress
        # (src/web/api/routers/learning.py); sole reader: the dashboard
        # Learning tab via GET /api/bot/learning/progress.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS learning_progress (
                resource_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'not_started',
                note TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # AI Analyst — per-run history (M13 S1).
        # Every generator cycle appends one row per refreshed endpoint
        # so the dashboard can render "what did the analyst say
        # yesterday / two hours ago." The cache files under
        # runtime_logs/insights/ are the live view; this table is the
        # durable history. Browseable in the Data Explorer alongside
        # trades / order_packages. Read-only of the live order path.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS insights_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                strategy_name TEXT,
                model_id TEXT,
                grade TEXT,
                summary_md TEXT NOT NULL,
                signals_json TEXT,
                data_window_json TEXT,
                row_counts_json TEXT,
                payload_json TEXT NOT NULL
            )
        ''')
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_insights_history_endpoint_ts "
            "ON insights_history (endpoint, datetime(generated_at) DESC)"
        )

        # AI Analyst — per-call token + cost log (M13 S1).
        # The generator writes one row per Anthropic call (ok), per
        # budget-skip (budget_skipped), and per API error (error). The
        # monthly budget gate sums estimated_cost_usd over the current
        # calendar month against INSIGHTS_MONTHLY_BUDGET_USD before
        # each call — keeps the analyst inside Anthropic monthly
        # included usage rather than spilling into pay-as-you-go.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS insights_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                model_id TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'ok'
            )
        ''')
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_insights_usage_ts "
            "ON insights_usage (datetime(ts) DESC)"
        )

        # Account balance snapshots (WC-5, dashboard-truth 2026-06-16).
        # Append-only history of the per-account tracked balance the
        # hourly-report writer already computes. The JSON file
        # runtime_logs/balance_snapshots.json holds only the LATEST reading
        # per account (overwritten each cycle), so there was no DB home for
        # balances and no balance-over-time history — the audit's "balances
        # have no DB table" gap. This table is the canonical source: the
        # writer appends one row per (account, cycle); /api/bot/accounts/balances
        # reads the latest row per account here (JSON = degraded fallback).
        # api_ok=0 rows record a failed reading (balance NULL) so a gap is
        # visible rather than silently dropped. Read-only of the order path.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS balance_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                balance REAL,
                delta_1h REAL,
                open_positions INTEGER,
                api_ok INTEGER NOT NULL DEFAULT 1,
                ts TEXT NOT NULL
            )
        ''')
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_balance_snapshots_account_ts "
            "ON balance_snapshots (account_id, datetime(ts) DESC)"
        )

        conn.commit()
        conn.close()

        print("✓ Database tables created/verified")
    
    def insert_trade(self, trade_data):
        """
        Insert a new trade record

        Args:
            trade_data (dict): Trade information

        Returns:
            int: ID of inserted trade
        """
        # Ensure every row carries an account identifier. Callers that have an
        # account dict should pass account_id explicitly; legacy/backtest callers
        # that don't will be attributed to the 'live' legacy account.
        if "account_id" not in trade_data:
            trade_data = {**trade_data, "account_id": "live"}

        conn = self.connect()
        cursor = conn.cursor()

        columns = ', '.join(trade_data.keys())
        placeholders = ', '.join(['?' for _ in trade_data])
        query = f"INSERT INTO trades ({columns}) VALUES ({placeholders})"
        
        cursor.execute(query, list(trade_data.values()))
        trade_id = cursor.lastrowid

        conn.commit()
        conn.close()

        # M12 follow-up — fire trade_opened notification for new trades
        # (real + paper). Best-effort + unconditional (FCM has no enable
        # flag — inert when unconfigured); any failure is swallowed so the
        # insert path stays intact.
        try:
            _is_paper = (
                str(trade_data.get("account_class")).strip().lower() == "paper"
                if trade_data.get("account_class") is not None
                else bool(trade_data.get("is_demo"))
            )
            if (
                str(trade_data.get("status", "open")).lower() == "open"
                and not trade_data.get("is_backtest")
                and not _is_paper
            ):
                self._fire_trade_opened_event(int(trade_id))
        except Exception:  # noqa: BLE001  # allow-silent: notifier hook must never propagate into the insert path
            pass

        return trade_id

    def update_trade(self, trade_id, updates):
        """Update a row in the ``trades`` table by primary key.

        S-030 PR3 (architecture-audit-2026-05-02 P1-4) — close path.
        The S-029 PR2 writer creates the row at ``status='open'``;
        the monitor loop updates it on close (status, exit_price,
        exit_reason, pnl, pnl_percent). Caller controls the field
        set; this method mirrors ``update_order_package`` semantics.

        Args:
            trade_id (int): The trades.id value (from insert_trade).
            updates (dict): Column → new value.

        Returns:
            int: Rows affected (0 if the id was not found).
        """
        if trade_id is None:
            raise ValueError("update_trade requires trade_id")
        row = dict(updates or {})
        if not row:
            return 0

        conn = self.connect()
        cursor = conn.cursor()
        try:
            assignments = ", ".join(f"{k} = ?" for k in row.keys())
            cursor.execute(
                f"UPDATE trades SET {assignments} WHERE id = ?",
                list(row.values()) + [int(trade_id)],
            )
            conn.commit()
            rowcount = cursor.rowcount
        finally:
            conn.close()

        # M12 S1 — mobile-push observer hook. When the update transitions
        # a row to ``status='closed'`` (or moves SL/TP on a still-open row),
        # fan out a trade notification to subscribed devices AND the
        # operator's Telegram for any non-backtest trade — paper included
        # (the operator wants paper open/close/update pings too). The FCM
        # publish is unconditional + best-effort (no enable flag — inert
        # when FCM unconfigured); the Telegram line runs off-thread. Both
        # swallow every exception so a notification failure can never
        # propagate into the trader's close path.
        # The whole block is also wrapped here for defense-in-depth, so
        # a malformed import or a row-lookup glitch can't break the
        # close even if mobile_push itself has a bug.
        if rowcount > 0:
            status_str = str(row.get("status", "")).lower()
            try:
                if status_str == "closed":
                    self._fire_trade_closed_event(int(trade_id))
                elif ("sl" in row or "tp" in row) and status_str != "closed":
                    # Monitor-driven SL/TP move on an still-open trade —
                    # fire trade_updated so the operator's phone shows
                    # the trail / BE flip without waiting for close.
                    self._fire_trade_updated_event(int(trade_id))
            except Exception:  # noqa: BLE001  # allow-silent: M12 S1 observer hook — notifier failure must never propagate into trader close path
                pass
            # M18 P0a — observe-only cost capture. On a close, stamp a fixed-model
            # round-trip fee estimate (trades.fee_taker_usd / cost_source) so the
            # capital allocator's EV scorer has a cost feature + a future learned
            # ranker gets net-R labels. Its OWN guard so a cost-write failure can
            # never propagate into (or be skipped by) the close path / notifier.
            if status_str == "closed":
                try:
                    self._record_trade_cost_estimate(int(trade_id))
                except Exception:  # noqa: BLE001  # allow-silent: observe-only cost capture must never propagate into trader close path
                    pass
        return rowcount

    def _record_trade_cost_estimate(self, trade_id: int) -> None:
        """Stamp a fixed-model round-trip fee estimate on a just-closed trade (M18 P0a).

        Observe-only substrate for the capital allocator's EV scorer + a future
        learned ranker's net-R label. **Best-effort — never propagates** into the
        trader's close path. Skips:

        * **backtest rows** (``is_backtest`` truthy) — costs are modelled in the
          harness, not the live journal;
        * rows that already carry a cost (``cost_source`` set / ``fee_taker_usd``
          present) — **never overwrites broker truth** or a prior estimate.

        Resolves the symbol's USD-per-point multiplier via the canonical
        ``contract_value_usd_for`` (futures-aware, fail-safe to 1.0) so a futures
        notional is costed correctly. Writes ``fee_taker_usd`` +
        ``cost_source='estimate'`` in its own connection (the update's conn is
        already closed by here, mirroring ``_fire_trade_closed_event``).
        """
        from src.runtime.local_pnl import contract_value_usd_for
        from src.runtime.trade_costs import estimate_roundtrip_fee_usd

        conn = self.connect()
        try:
            cur = conn.execute(
                "SELECT entry_price, position_size, symbol, is_backtest, "
                "fee_taker_usd, cost_source FROM trades WHERE id = ?",
                (int(trade_id),),
            )
            r = cur.fetchone()
            if r is None:
                return
            entry, qty, symbol, is_backtest, fee_existing, cost_src = r
            if is_backtest:
                return
            if cost_src is not None or fee_existing is not None:
                return  # never overwrite broker truth / a prior estimate
            fee = estimate_roundtrip_fee_usd(
                entry_price=entry,
                qty=qty,
                contract_value_usd=contract_value_usd_for(symbol),
            )
            if fee is None:
                return
            conn.execute(
                "UPDATE trades SET fee_taker_usd = ?, cost_source = ? WHERE id = ?",
                (round(float(fee), 8), "estimate", int(trade_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def _fire_trade_closed_event(self, trade_id: int) -> None:
        """Read the just-closed row and fire the mobile-push observer.

        Separated from ``update_trade`` so the observer's row-lookup
        doesn't sit in the update's connection scope, and so tests can
        stub it cleanly. The publish itself is best-effort — see
        ``src.runtime.mobile_push.publish_event``.
        """
        from src.runtime.mobile_push.event_kinds import TRADE_CLOSED
        from src.runtime.mobile_push.trade_events import notify_trade_event

        conn = self.connect()
        try:
            cur = conn.execute(
                "SELECT symbol, direction, pnl, pnl_percent, exit_reason, "
                "strategy_name, account_id, is_backtest, is_demo, account_class "
                "FROM trades WHERE id = ?",
                (trade_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return
        # Skip backtest replays only — they're not real events. Paper
        # trades DO notify now (operator wants paper open/close/update
        # pings too); the funding class rides in the payload so the
        # consumer can tag the message [paper] / [live].
        if row["is_backtest"]:
            return
        notify_trade_event(
            TRADE_CLOSED,
            {
                "trade_id": trade_id,
                "symbol": row["symbol"],
                "direction": row["direction"],
                "pnl": row["pnl"],
                "pnl_percent": row["pnl_percent"],
                "exit_reason": row["exit_reason"],
                "strategy": row["strategy_name"],
                "account": row["account_id"],
                "account_class": row["account_class"],
                "is_paper": _row_is_paper(row),
            },
        )

    def _fire_trade_opened_event(self, trade_id: int) -> None:
        """Read the just-inserted row and fire the trade_opened observer.

        BL-20260722-XRP-SLSPAM (part 3): this SELECT previously named
        ``qty``/``sl``/``tp`` — none of which exist on ``trades`` (the real
        columns are ``position_size``/``stop_loss``/``take_profit_1``; see
        the ``trades`` schema declared above). ``insert_trade`` calls this
        inside a bare ``except Exception: pass``, so the resulting
        ``sqlite3.OperationalError`` was silently swallowed on every single
        real-money/paper trade open — the TRADE_OPENED push/Telegram
        notification has never actually fired. Mapped onto the correct
        columns here; the outbound payload keys are unchanged (``qty``/
        ``sl``/``tp``) since ``format_trade_event_message`` reads those.
        """
        from src.runtime.mobile_push.event_kinds import TRADE_OPENED
        from src.runtime.mobile_push.trade_events import notify_trade_event

        conn = self.connect()
        try:
            cur = conn.execute(
                "SELECT symbol, direction, position_size, entry_price, "
                "stop_loss, take_profit_1, "
                "strategy_name, account_id, is_backtest, is_demo, account_class "
                "FROM trades WHERE id = ?",
                (trade_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        # Backtest replays don't notify; paper opens DO (see _fire_trade_closed_event).
        if row is None or row["is_backtest"]:
            return
        notify_trade_event(
            TRADE_OPENED,
            {
                "trade_id": trade_id,
                "symbol": row["symbol"],
                "direction": row["direction"],
                "qty": row["position_size"],
                "entry_price": row["entry_price"],
                "sl": row["stop_loss"],
                "tp": row["take_profit_1"],
                "strategy": row["strategy_name"],
                "account": row["account_id"],
                "account_class": row["account_class"],
                "is_paper": _row_is_paper(row),
            },
        )

    def _fire_trade_updated_event(self, trade_id: int) -> None:
        """Read the just-updated open row and fire the trade_updated observer.

        Fires on SL/TP moves while the row is still open. Skips backtest
        replays only; paper trades notify too (see ``_fire_trade_closed_event``).

        BL-20260722-XRP-SLSPAM (part 3): same ``qty``/``sl``/``tp`` →
        non-existent-column bug as ``_fire_trade_opened_event`` — fixed here
        for consistency. Note this observer's caller-side gate
        (``"sl" in row or "tp" in row`` in ``update_trade``, above) checks
        for those exact literal keys, which no real caller passes (the real
        columns are ``stop_loss``/``take_profit_1``) — so this path is
        effectively unreachable today regardless of this SQL fix. Left
        unreachable deliberately: wiring the gate to the real column names
        would make this fire a SECOND Telegram/FCM ping alongside
        ``execution_diagnostics.enqueue_trade_update`` (order_monitor.py's
        modify branch) for the same event, which needs its own dedup design
        rather than a rushed fix here. Logged to the health-review backlog.
        """
        from src.runtime.mobile_push.event_kinds import TRADE_UPDATED
        from src.runtime.mobile_push.trade_events import notify_trade_event

        conn = self.connect()
        try:
            cur = conn.execute(
                "SELECT symbol, direction, position_size, entry_price, "
                "stop_loss, take_profit_1, status, "
                "strategy_name, account_id, is_backtest, is_demo, account_class "
                "FROM trades WHERE id = ?",
                (trade_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        # Backtest replays don't notify; paper updates DO (see _fire_trade_closed_event).
        if row is None or row["is_backtest"]:
            return
        if str(row["status"]).lower() == "closed":
            return
        notify_trade_event(
            TRADE_UPDATED,
            {
                "trade_id": trade_id,
                "symbol": row["symbol"],
                "direction": row["direction"],
                "qty": row["position_size"],
                "entry_price": row["entry_price"],
                "sl": row["stop_loss"],
                "tp": row["take_profit_1"],
                "strategy": row["strategy_name"],
                "account": row["account_id"],
                "account_class": row["account_class"],
                "is_paper": _row_is_paper(row),
            },
        )

    def get_trades(self, filters=None, limit=None):
        """
        Retrieve trades from database
        
        Args:
            filters (dict): Optional filters (e.g., {'symbol': 'BTCUSDT'})
            limit (int): Maximum number of trades to return
            
        Returns:
            list: List of trade records as dictionaries
        """
        conn = self.connect()
        cursor = conn.cursor()
        
        query = "SELECT * FROM trades"
        params = []
        
        if filters:
            conditions = [f"{k} = ?" for k in filters.keys()]
            query += " WHERE " + " AND ".join(conditions)
            params = list(filters.values())
        
        query += " ORDER BY timestamp DESC"

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query, params)
        trades = [dict(row) for row in cursor.fetchall()]

        conn.close()
        return trades

    # ------------------------------------------------------------------
    # Order packages log (S-030 PR1, architecture-audit-2026-05-02 P1-5)
    # ------------------------------------------------------------------

    def insert_order_package(self, package_data):
        """Insert a fresh OrderPackage row.

        Args:
            package_data (dict): Must contain order_package_id (TEXT),
                strategy_name, symbol, direction, entry, sl, tp.
                Optional: confidence, signal_logic, status (defaults
                to 'open'), linked_trade_id, close_reason, meta,
                model_scores, exit_plan, exit_plan_state (dicts/lists
                — serialised to JSON). created_at / updated_at default
                to UTC now if absent.

        Returns:
            str: The order_package_id.
        """
        from datetime import timezone
        import json as _json

        row = dict(package_data)
        if "order_package_id" not in row or not row["order_package_id"]:
            raise ValueError("insert_order_package requires order_package_id")
        now_iso = datetime.now(timezone.utc).isoformat()
        row.setdefault("created_at", now_iso)
        row.setdefault("updated_at", now_iso)
        row.setdefault("status", "open")
        if isinstance(row.get("meta"), dict):
            row["meta"] = _json.dumps(row["meta"], default=str)
        if isinstance(row.get("model_scores"), (dict, list)):
            row["model_scores"] = _json.dumps(row["model_scores"], default=str)
        if isinstance(row.get("exit_plan"), (dict, list)):
            row["exit_plan"] = _json.dumps(row["exit_plan"], default=str)
        if isinstance(row.get("exit_plan_state"), (dict, list)):
            row["exit_plan_state"] = _json.dumps(row["exit_plan_state"], default=str)

        conn = self.connect()
        cursor = conn.cursor()
        try:
            columns = ", ".join(row.keys())
            placeholders = ", ".join(["?" for _ in row])
            cursor.execute(
                f"INSERT INTO order_packages ({columns}) VALUES ({placeholders})",
                list(row.values()),
            )
            conn.commit()
        finally:
            conn.close()
        return row["order_package_id"]

    def update_order_package(self, order_package_id, updates):
        """Update a row by ``order_package_id``.

        Used by the strategy's monitor loop (entry/sl/tp updates) and
        by the account unit's close path (status, close_reason,
        linked_trade_id). ``updated_at`` is bumped automatically.

        Args:
            order_package_id (str): Primary key.
            updates (dict): Column → new value.

        Returns:
            int: Rows affected (0 if the id was not found).
        """
        from datetime import timezone
        import json as _json

        if not order_package_id:
            raise ValueError("update_order_package requires order_package_id")
        row = dict(updates or {})
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        if isinstance(row.get("meta"), dict):
            row["meta"] = _json.dumps(row["meta"], default=str)

        conn = self.connect()
        cursor = conn.cursor()
        try:
            assignments = ", ".join(f"{k} = ?" for k in row.keys())
            cursor.execute(
                f"UPDATE order_packages SET {assignments} "
                "WHERE order_package_id = ?",
                list(row.values()) + [order_package_id],
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def insert_signal(self, signal_data):
        """Insert a row into the signals table.

        S-034 (architecture-audit-2026-05-02 P2-9). The DB unit owns the
        signals log per CLAUDE.md § Architecture rules § 4. The JSONL
        writer (``src/utils/signal_audit_logger.py::log_signal``) calls
        this during the dual-write transition window so both stores
        carry the same data; readers will flip to SQL once the
        operator confirms one full day of clean dual-writes.

        Args:
            signal_data (dict): Pipeline event with optional fields
                ``logged_at_utc``, ``strategy``, ``symbol``, ``side``,
                ``qty``, ``status``, ``reason``, plus any extra
                metadata fields (folded into ``meta`` as JSON).

        Returns:
            int: The new row's primary key.
        """
        import json
        from datetime import timezone

        row = dict(signal_data or {})
        logged_at = row.pop("logged_at_utc", None) or \
            datetime.now(timezone.utc).isoformat()
        strategy = row.pop("strategy", None)
        symbol = row.pop("symbol", None)
        side = row.pop("side", None)
        qty = row.pop("qty", None)
        status = row.pop("status", None)
        reason = row.pop("reason", None)
        # Anything left over rides in the meta JSON blob — keeps the
        # write lossless even when the schema lags behind a new
        # pipeline-event field.
        meta = json.dumps(row, default=str) if row else None

        conn = self.connect()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO signals "
                "(logged_at_utc, strategy, symbol, side, qty, status, "
                "reason, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (logged_at, strategy, symbol, side, qty, status, reason, meta),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_recent_signals(self, *, limit=10, strategy=None):
        """Return the most-recent signals rows.

        S-034 reader for the SQL signals log. Mirrors
        ``processor.get_recent_signals`` shape so the JSONL → SQL
        cutover is a one-line swap on the reader side.

        Args:
            limit (int): Cap (default 10, max 200).
            strategy (str): Optional case-insensitive filter.

        Returns:
            list[dict]: Newest-first by ``logged_at_utc``. Each dict
                contains the ``meta`` JSON expanded back into the
                top-level dict so downstream renderers see the same
                shape as a JSONL record.
        """
        import json

        try:
            limit = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            limit = 10

        conn = self.connect()
        cursor = conn.cursor()
        try:
            params = []
            sql = (
                "SELECT logged_at_utc, strategy, symbol, side, qty, "
                "status, reason, meta FROM signals"
            )
            if strategy is not None:
                sql += " WHERE LOWER(strategy) = ?"
                params.append(str(strategy).lower())
            sql += " ORDER BY datetime(logged_at_utc) DESC LIMIT ?"
            params.append(int(limit))
            cursor.execute(sql, params)
            rows = []
            # Newest-first from SQL → reverse to match JSONL "tail" order
            # (oldest-first within the window) so existing renderers see
            # the same sequence.
            for r in reversed(cursor.fetchall()):
                d = dict(r)
                meta_blob = d.pop("meta", None)
                if meta_blob:
                    try:
                        extra = json.loads(meta_blob)
                        if isinstance(extra, dict):
                            for k, v in extra.items():
                                d.setdefault(k, v)
                    except (json.JSONDecodeError, TypeError):
                        pass
                rows.append(d)
            return rows
        finally:
            conn.close()

    def get_order_packages_by_strategy(self, strategy_name, *, limit=None,
                                       status=None, linked_only=False,
                                       symbol=None):
        """Return rows filtered by ``strategy_name`` (Rule 4 — package
        logs are queried *by strategy*).

        Args:
            strategy_name (str): The strategy column to filter on.
            limit (int): Optional row cap.
            status (str): Optional status filter ('open' / 'closed' /
                'rejected').
            linked_only (bool): When True, only return rows that have a
                non-null ``linked_trade_id`` (i.e. a trade was actually
                placed at the broker). Used by the BUG-046 gate so that
                packages which were logged but never executed do not
                block new signals.
            symbol (str): Optional symbol filter. Required for correct
                multi-symbol behaviour — the strategy-monocle gate must
                scope "one open package per strategy" to a single
                instrument, otherwise an open BTCUSDT package would
                suppress an MES entry for the same strategy (and vice
                versa). Omitting it preserves the legacy strategy-global
                scope for single-symbol callers.

        Returns:
            list[dict]: Newest-first by ``updated_at``.
        """
        conn = self.connect()
        cursor = conn.cursor()
        try:
            query = "SELECT * FROM order_packages WHERE strategy_name = ?"
            params = [strategy_name]
            if status is not None:
                query += " AND status = ?"
                params.append(status)
            if symbol is not None:
                query += " AND symbol = ?"
                params.append(symbol)
            if linked_only:
                query += " AND linked_trade_id IS NOT NULL"
            query += " ORDER BY datetime(updated_at) DESC"
            if limit:
                query += f" LIMIT {int(limit)}"
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_recent_order_packages_for_symbol(self, symbol, *, limit=30):
        """Newest-first order packages for ``symbol`` (any strategy/status).

        Used by the reverse reconciler to recover the originating strategy
        (and its stored SL/TP) of an exchange orphan, so the position can be
        re-attached to that strategy's monitoring instead of left as a bare
        ``orphan_adopt`` row. Ordered by ``created_at`` DESC.
        """
        conn = self.connect()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM order_packages WHERE symbol = ? "
                "ORDER BY datetime(created_at) DESC LIMIT ?",
                [symbol, int(limit)],
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def save_backtest_results(self, results):
        """
        Save backtest results
        
        Args:
            results (dict): Backtest metrics and metadata
            
        Returns:
            int: ID of inserted backtest record
        """
        conn = self.connect()
        cursor = conn.cursor()
        
        # Convert config dict to JSON string if present
        if 'config' in results and isinstance(results['config'], dict):
            results['config'] = json.dumps(results['config'])
        
        columns = ', '.join(results.keys())
        placeholders = ', '.join(['?' for _ in results])
        query = f"INSERT INTO backtest_results ({columns}) VALUES ({placeholders})"
        
        cursor.execute(query, list(results.values()))
        backtest_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return backtest_id
    
    def save_strategy_version(self, version_name, config, description=''):
        """
        Save a strategy configuration version
        
        Args:
            version_name (str): Unique version identifier
            config (dict): Strategy configuration parameters
            description (str): Optional description
            
        Returns:
            int: ID of inserted version
        """
        conn = self.connect()
        cursor = conn.cursor()
        
        config_json = json.dumps(config)
        
        cursor.execute('''
            INSERT INTO strategy_versions (version_name, description, config)
            VALUES (?, ?, ?)
        ''', (version_name, description, config_json))
        
        version_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return version_id
    
    def get_strategy_version(self, version_name):
        """
        Retrieve a strategy version
        
        Args:
            version_name (str): Version identifier
            
        Returns:
            dict: Strategy version data including config
        """
        conn = self.connect()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM strategy_versions WHERE version_name = ?
        ''', (version_name,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            version = dict(row)
            version['config'] = json.loads(version['config'])
            return version
        return None

    def insert_balance_snapshot(
        self,
        account_id,
        *,
        balance=None,
        delta_1h=None,
        open_positions=None,
        api_ok=True,
        ts=None,
    ):
        """Append one account balance reading to ``balance_snapshots`` (WC-5).

        Canonical writer for the append-only balance history. Called by the
        hourly-report ``account_snapshots()`` once per (account, cycle). A
        failed reading is recorded with ``api_ok=False`` + ``balance=None`` so
        a gap is visible in history rather than silently dropped.

        ``ts`` defaults to now (UTC ISO-8601). Returns the new row id.
        """
        if ts is None:
            ts = datetime.now(timezone.utc).isoformat()
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO balance_snapshots
                (account_id, balance, delta_1h, open_positions, api_ok, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (
                str(account_id),
                None if balance is None else float(balance),
                None if delta_1h is None else float(delta_1h),
                None if open_positions is None else int(open_positions),
                1 if api_ok else 0,
                ts,
            ),
        )
        row_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return row_id

    def get_latest_balance_snapshots(self):
        """Return the latest ``balance_snapshots`` row per account (WC-5).

        Powers ``/api/bot/accounts/balances`` (DB-authoritative). Returns a
        dict ``{account_id: {balance, delta_1h, open_positions, api_ok, ts}}``
        — the newest row per account by ``ts``. Empty dict when the table has
        no rows (the reader then falls back to the JSON snapshot).
        """
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT bs.account_id, bs.balance, bs.delta_1h, bs.open_positions,
                   bs.api_ok, bs.ts
            FROM balance_snapshots bs
            JOIN (
                SELECT account_id, MAX(datetime(ts)) AS mx
                FROM balance_snapshots
                GROUP BY account_id
            ) latest
              ON latest.account_id = bs.account_id
             AND datetime(bs.ts) = latest.mx
            '''
        )
        rows = cursor.fetchall()
        conn.close()
        out = {}
        for row in rows:
            out[row["account_id"]] = {
                "balance": row["balance"],
                "delta_1h": row["delta_1h"],
                "open_positions": row["open_positions"],
                "api_ok": bool(row["api_ok"]),
                "ts": row["ts"],
            }
        return out


# Convenience function
def get_db():
    """Get database instance"""
    return Database()
