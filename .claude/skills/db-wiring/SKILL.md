---
name: db-wiring
description: Verify every part of the system that produces data is wired into the canonical store so there is one uncompromised single source of truth. Use when adding a writer/logger, when adding a strategy or account, when data "isn't showing up" in the dashboard/journal, when you suspect a stray/duplicate DB, or as a periodic integrity check. Composes with diag-data and the health-review skill.
---

# /db-wiring — keep one uncompromised single source of truth

The whole point of the data layer is that everything the system generates lands
in **one** queryable place. This skill checks that no writer has drifted off to
a stray path and that every producer is wired into the canonical store.

Authoritative model: `docs/ARCHITECTURE-CANONICAL.md` (persistence) and
`CLAUDE.md` § "Canonical persistence model". This skill is the verification
procedure.

## The canonical store (federated, two SQLite files on the OCI volume)

- **`trade_journal.db`** — everything the LIVE trader produces (trades,
  order_packages, signals, backtest_results, daily_risk_state,
  strategy_versions). Every Python caller resolves its path through the single
  resolver `src.utils.paths.trade_journal_db_path()`; shell uses
  `scripts/ops/_lib.sh::runtime_db_path`.
- **`trainer_store.db`** — everything the TRAINER produces, ingested from the
  file-based trainer mirror (`runtime_logs/trainer_mirror/`) by
  `src/units/db/trainer_store.py` (idempotent, lazy, mtime-gated). Kept
  separate so ingest never contends with the money DB.
- Both are browsable together in the dashboard **Data Explorer**
  (`/api/bot/db/tables`, `/api/bot/db/table/{name}?db=`).

## The invariant (and the CI guard that enforces it)

There is **one** resolver per side; the CWD-relative fallback (and inline
`os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"` reads) are
**forbidden** — that fallback is what seeded stray duplicate journals under
each process's working directory. The `canonical-db-resolver` CI guard
(`.github/workflows/canonical-db-resolver.yml` + `scripts/check_canonical_db_resolver.py`)
blocks re-introducing it in both Python and shell. Never bypass it.

## Procedure — auditing wiring

1. **New writer/logger?** It must resolve its DB path via
   `trade_journal_db_path()` (Python) or `runtime_db_path` (shell) — never a
   bare `"trade_journal.db"` or a CWD-relative path. Run the guard locally:
   `python scripts/check_canonical_db_resolver.py`.
2. **New data the system generates?** Confirm there is a writer landing it in
   the canonical store AND a read path (an `/api/bot/*` endpoint and/or a Data
   Explorer table). Data with no canonical writer is data we're losing.
3. **Trainer-produced data?** It lands in the file mirror
   (`runtime_logs/trainer_mirror/`) and is ingested into `trainer_store.db` by
   `src/units/db/trainer_store.py`. If you add a trainer artifact, add its
   mirror + ingest mapping.
4. **Detect stray/duplicate DBs (live VM).** Use `diag-data`/`vm-ops`: a
   trainer-relay `cmd:` of `find /home/ubuntu/ict-trading-bot /data -name '*.db' -printf '%p %s %TY-%Tm-%Td\n'`
   on the relevant host — there should be exactly the canonical files under
   `/data/bot-data`. Anything under a process CWD is a stray (the resolver-bypass
   symptom); root-cause the writer, don't just delete.
5. **Freshness/consistency.** Newest `trades` / `order_packages` row age should
   track the live session; `<table>_total` counts non-decreasing run-over-run; a
   large `-wal` with a small main DB suggests a stuck checkpoint.

## Honesty

If you can't reach a DB check (the live relay can't run `sqlite3 PRAGMA`), say
so and grade from what's reachable (row recency via `journal?table=...`), noting
the limitation — don't claim integrity you didn't verify.
