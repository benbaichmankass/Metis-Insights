# OCI Block Storage Architecture

**Audience:** PM, operator, anyone trying to understand where the bot keeps its data.
**Status:** Adopted 2026-05-11. Live VM migration is an explicit operator step (see [`docs/runbooks/mounted-storage.md`](../runbooks/mounted-storage.md)).

## Why we did this

The trader used to keep its trades database, logs, runtime state, and health snapshots **inside the git repo working tree** (`/home/ubuntu/ict-trading-bot/runtime_logs/` etc.). That's convenient but creates three problems:

- Every `git pull` deploy is a stress test on whether we remembered to keep these dirs out of commits.
- A repo reclone or `git clean` would wipe live trading state.
- The root filesystem is small and shared with the OS; the bot's growing log + sqlite footprint competes with system journals.

The fix is to put **code** in the repo and **data** on a separate, mountable Oracle Cloud Infrastructure (OCI) block volume.

## What this means in practice

```
VM compute (Always Free, instance-20260414-1555)
│
├── /home/ubuntu/ict-trading-bot/        ← code (git)
│   ├── src/
│   ├── scripts/
│   ├── tests/
│   ├── deploy/
│   └── docs/
│
└── /data/bot-data/                      ← data (OCI block volume)
    ├── data/                            (trades.db, candle CSVs)
    ├── runtime_logs/                    (heartbeat, jsonl audits, status)
    ├── runtime_state/                   (exchange_fills.sqlite, prop_state.json)
    └── artifacts/                       (health snapshots)
```

The repo holds **source-controlled things only**: Python modules, configs, tests, docs, migrations.
The mount holds **operational state**: anything written at runtime.

## The opt-in switch

A single environment variable controls everything:

```ini
DATA_DIR=/data/bot-data
```

- **Unset** (today's live VM) → trader writes to `<repo>/runtime_logs/` etc. (unchanged from before this work).
- **Set to a mounted path** → trader writes to `<DATA_DIR>/<subdir>/` for every logical root.

Per-root overrides are also supported (`RUNTIME_LOGS_DIR`, `RUNTIME_STATE_DIR`, `ARTIFACTS_DIR`) for the rare case of splitting hot logs onto a different volume than cold artifacts. See `src/utils/paths.py` for the resolution rules.

## How the safeguards work

Three layers protect the live trader from a misconfigured mount:

1. **Code layer.** `src/utils/paths.py` resolves paths fresh on every call. If `DATA_DIR` is unset, it falls back to the repo subdir — there is no "config file" that could rot.

2. **systemd layer** ([`deploy/dropins/data-dir.conf`](../../deploy/dropins/data-dir.conf)).
   - `RequiresMountsFor=/data/bot-data` — service won't start until the volume is mounted.
   - `ExecStartPre=scripts/check_data_dir.sh` — preflight runs before every (re)start; if the mount disappears or the subdirs aren't writable, the service refuses to start instead of falling through to the wrong filesystem.

3. **Migration layer** ([`scripts/migrate_to_data_dir.sh`](../../scripts/migrate_to_data_dir.sh)).
   - rsync-based, **dry-run by default**.
   - **Does not delete source files** — rollback is "unset `DATA_DIR`, remove the drop-in, restart" and the trader is back where it started.

## What's NOT externalized

| Thing | Location | Why |
|---|---|---|
| `trade_journal.db` | repo root | Has its own `TRADE_JOURNAL_DB` env var since long before this work; can be redirected independently. |
| `.env` and master-secrets | repo root (gitignored) | Secrets stay alongside the code that reads them; the block volume isn't encrypted differently from the OS disk. |
| ML training datasets | `ml/`, `experiments/` | Already use Hugging Face / OCI Object Storage offload (see `scripts/hf_upload_large_files.py`). |
| Vendored configs | `config/`, `comms/` | These are source of truth, not runtime state. |

## Why a separate volume, not just a directory

OCI block volumes give us three things a regular directory can't:

- **Independent lifecycle.** Detach + reattach to a replacement VM during a host failure; the trading data follows the volume, not the compute.
- **Snapshots.** Point-in-time backups of the volume without quiescing the bot.
- **Capacity.** Always-Free tier includes 200 GiB of block storage we weren't using.

The trade-off is one extra `mount` step on every VM provision and one extra failure mode (mount didn't come up). Both are handled by the systemd `RequiresMountsFor` directive.

## Where to read next

- [`docs/runbooks/mounted-storage.md`](../runbooks/mounted-storage.md) — verify, migrate, monitor, roll back.
- [`docs/security/permissions-tiers.md`](../security/permissions-tiers.md) — who can create/attach/write to the volume.
- [`docs/operator/github-actions-oci-secrets.md`](../operator/github-actions-oci-secrets.md) — how CI/CD reaches OCI without exposing keys.
