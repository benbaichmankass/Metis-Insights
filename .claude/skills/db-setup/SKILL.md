---
name: db-setup
description: Set up, locate, and verify the ICT bot's canonical SQLite stores — trade_journal.db (the money DB the live trader produces) and trainer_store.db (the read-mostly trainer/ML sidecar). Covers the single path resolver (src.utils.paths + scripts/ops/_lib.sh), how tables get created (lazy on first access by src/units/db/database.py + WAL via src/utils/db_init.py), the OCI data-dir migration (scripts/migrate_journal_db.sh), and the canonical-db-resolver CI guard. Use when setting up a fresh environment, when "the DB is in the wrong place" / "there's a duplicate journal", when adding a new DB path read/write, or when verifying storage on the VM. Composes with db-wiring (integrity) and diag-data (reads).
---

# /db-setup — locate, create, and verify the canonical SQLite stores

There is **one** canonical store, federated across two SQLite files on
the OCI block volume `/data/bot-data` (S-PERSIST-CANON):

- **`trade_journal.db`** — everything the LIVE trader produces: `trades`,
  `order_packages`, `signals`, `backtest_results`, `daily_risk_state`,
  `strategy_versions`.
- **`trainer_store.db`** — read-mostly sidecar: trainer/ML lifecycle data
  ingested from `runtime_logs/trainer_mirror/`. Kept separate so ingest
  never contends with the money DB.

The single most important rule: **never construct a DB path by hand.**
The stray-duplicate-journal bug came from CWD-relative fallbacks; the
resolvers below are the only sanctioned way to find the path.

## The one path resolver (use it, don't reinvent it)

**Python** — `src/utils/paths.py`:

- `trade_journal_db_path()` resolves in order: `TRADE_JOURNAL_DB` env →
  `$DATA_DIR/trade_journal.db` → `<repo_root>/trade_journal.db`. **Never**
  a CWD-relative basename.
- `trainer_store_db_path()` — same order, basename `trainer_store.db`
  (env `TRAINER_STORE_DB`).
- `runtime_logs_dir()`, `data_dir()`, `repo_root()` — sibling resolvers
  for the other runtime roots.

`src/utils/db_init.py::journal_db_path()` just delegates to
`trade_journal_db_path()` — don't add a second resolver.

**Shell** — `scripts/ops/_lib.sh`:

- `load_runtime_env` populates `DATA_DIR` / `TRADE_JOURNAL_DB` (+ friends)
  from the systemd drop-in, whitelist-gated.
- `runtime_db_path` prints `${TRADE_JOURNAL_DB:-${REPO_DIR}/trade_journal.db}`
  — the shell mirror of the Python resolver. Source `_lib.sh` and call it;
  never inline `${TRADE_JOURNAL_DB:-trade_journal.db}`.

> **CI guard:** `canonical-db-resolver` forbids the CWD-relative fallback
> and inline `os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"`
> reads in both Python and shell. If you add a new DB consumer, route it
> through the resolver or the guard fails the PR. This is the mechanism
> that keeps "one source of truth" true.

## How tables get created (no migration tool needed)

Schema is **lazy, idempotent, on first access** — there is no separate
"create the DB" step:

- `src/units/db/database.py::Database.__init__` runs `CREATE TABLE IF NOT
  EXISTS` for every trade-journal table on construction. Instantiating
  `Database()` against a fresh path creates a fully-formed empty journal.
- `src/utils/db_init.py::enable_wal_mode()` flips the DB to WAL on boot
  (idempotent, persistent, never raises). WAL lets the pipeline,
  order_monitor, dashboard API, and diag relay touch the journal
  concurrently without `database is locked`.
- `trainer_store.db` is rebuilt lazily from the trainer mirror by
  `src/units/db/trainer_store.py` (mtime-gated) on read — no manual build.

So "set up a fresh DB" = point the resolver at a path (env/`DATA_DIR`) and
let the first `Database()` call create it. Don't write DDL by hand.

## Fresh-environment checklist

1. Set the data root: `DATA_DIR=/data/bot-data` (live VM) via the systemd
   drop-in `deploy/dropins/data-dir.conf`, or leave unset for a
   repo-root dev DB.
2. Boot the trader (or instantiate `Database()`); tables + WAL appear.
3. Verify path + schema (sandbox, via diag relay):
   `GET /api/bot/db/tables` lists every table in BOTH federated DBs with
   row counts and a `db` field. `GET /api/diag/journal?table=trades&limit=1`
   confirms the live journal is the one being written.

## Migrating the journal to the OCI data-dir

`scripts/migrate_journal_db.sh` moves `trade_journal.db` (+ `-wal`/`-shm`)
from the repo root to `/data/bot-data`. The SQLite journal **cannot** be
copied with the trader running, so the script: stops the trader services,
copies preserving mode/mtime, chowns to ubuntu, runs `PRAGMA
integrity_check`, and leaves services stopped on success (the caller
installs the `data-dir.conf` drop-in and `systemctl restart`). On
integrity failure it restarts on the OLD path so the trader stays alive.
Source files are never deleted — rollback = remove the drop-in line.

```bash
sudo ./scripts/migrate_journal_db.sh --dry-run   # show the plan
sudo ./scripts/migrate_journal_db.sh             # execute
```

This is **Tier-2** (it stops live services). Run it via a `system-action`
/ `pull-and-deploy`, not a hand-SSH, and verify the post-state.

## Verify storage on the VM

`scripts/verify_storage_setup.sh` checks the data-dir mount/fstab and
permissions. The diag surface can't run it (fixed-curl only), so the
`health-snapshot.yml` cron is what executes it on the VM; from a sandbox,
grade disk pressure from `GET /api/diag/status` → `vm_health.disk`.

## When you hit a duplicate / stray journal

A second `trade_journal.db` under a process's CWD means something
bypassed the resolver. Find the offending path read (it'll be a bare
`"trade_journal.db"` or CWD-relative open), route it through
`trade_journal_db_path()` / `runtime_db_path`, and confirm the
`canonical-db-resolver` guard passes. Then reconcile data: the
`/data/bot-data` copy is canonical; the stray is the bug. Use the
`db-wiring` skill to confirm every writer lands in the canonical store.
