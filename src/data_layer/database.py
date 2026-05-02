"""
Database Module
Handles SQLite database operations for storing trades, backtests, and strategy versions
"""

import sqlite3
from pathlib import Path
from datetime import datetime
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


class Database:
    """Manages SQLite database for trade journal and backtest results"""
    
    def __init__(self, db_path='trade_journal.db'):
        """
        Initialize database connection
        
        Args:
            db_path (str): Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.conn = None
        self.create_tables()
    
    def connect(self):
        """Create database connection"""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row  # Allow dict-like access
        return self.conn
    
    def create_tables(self):
        """Create all necessary tables if they don't exist"""
        conn = self.connect()
        cursor = conn.cursor()
        
        # Trades table - stores all executed trades (backtest or live)
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
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Idempotent migrations for pre-existing DBs missing these columns.
        _migrate_add_strategy_name(cursor)
        _migrate_add_account_id(cursor)
        # Index for efficient per-account trade history queries.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_account_created "
            "ON trades (account_id, datetime(created_at) DESC)"
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
                FOREIGN KEY (linked_trade_id) REFERENCES trades(id)
            )
        ''')
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
        
        return trade_id
    
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
                to 'open'), linked_trade_id, close_reason, meta
                (dict — serialised to JSON). created_at / updated_at
                default to UTC now if absent.

        Returns:
            str: The order_package_id.
        """
        from datetime import datetime, timezone
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
        from datetime import datetime, timezone
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

    def get_order_packages_by_strategy(self, strategy_name, *, limit=None,
                                       status=None):
        """Return rows filtered by ``strategy_name`` (Rule 4 — package
        logs are queried *by strategy*).

        Args:
            strategy_name (str): The strategy column to filter on.
            limit (int): Optional row cap.
            status (str): Optional status filter ('open' / 'closed' /
                'rejected').

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
            query += " ORDER BY datetime(updated_at) DESC"
            if limit:
                query += f" LIMIT {int(limit)}"
            cursor.execute(query, params)
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


# Convenience function
def get_db():
    """Get database instance"""
    return Database()
