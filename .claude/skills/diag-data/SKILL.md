---
name: diag-data
description: Retrieve live runtime state from the production VMs (signals, orders, trades, journal tables, service/heartbeat status, journalctl) without asking the operator. Use whenever you need real runtime data — "what is the bot doing", "pull the recent trades", "is the trader alive", "check the audit log", debugging live behavior. Read-only. Composes with vm-ops, db-wiring, and the health-review skill.
---

# /diag-data — pull live runtime state yourself

You have autonomous **read** access to both VMs. Never ask the operator to
SSH in or paste a snapshot — fetch what you need through the diag surface.
This skill is the read primitive other skills build on.

The authoritative contract (paths, failure modes, token management) is
`docs/claude/diag-relay.md` — read it if anything here is ambiguous. This
skill is the fast how-to.

## Two transports, identical JSON — try direct, fall back to the relay

**Transport A — direct HTTP (when the session is configured for it).**
```
scripts/ops/diag_fetch.sh '<path>'
```
Resolves `$DIAG_BASE_URL/api/diag/<path>` with the bearer in a 0600 curl
config. Exit `0` → JSON on stdout. Exit `3` → not configured / egress blocked
/ web-api down → fall back to Transport B. Live VM only.

**Transport B — GitHub-issue relay (always available).** Open a labelled
issue; the workflow SSHes, runs the read, comments the JSON back, closes the
issue. Poll `mcp__github__issue_read` (`get_comments`) for the
`github-actions[bot]` reply (~30–60 s).

| Target | Label | Issue shape |
|---|---|---|
| **Live VM** (`vm-diag-snapshot.yml`) | `vm-diag-request` | **title** = the path: `[diag-request] <path>` (body ignored) |
| **Trainer VM** (`trainer-vm-diag.yml`) | `trainer-vm-diag-request` | **body** = `cmd: <bash>` or a `cmd: |` block (arbitrary bash) |

Common live-VM `<path>` values (full list in `docs/claude/diag-relay.md`):
`snapshot?limit=5` (packages/trades/health — keep limit small; GitHub
truncates comments ~55 kB), `audit?limit=600` (signal_audit tail),
`journal?table=order_packages&limit=100`, `journal?table=trades&limit=100`,
`status` (heartbeat + status.json + vm_health), `services`,
`journalctl?unit=ict-trader-live&lines=200`,
`log_file?name=heartbeat&lines=5`.

## Batch your reads — default to ONE issue per session, not one per path

**This is the default recommended pattern, not an edge case.** Every relay
issue is its own separately-billed GitHub Actions job. This repo hit its
Actions free-tier cap (2,000 min/month) on 2026-07-06; in just the first 5.5
days of that billing cycle this repo opened **427 issues**, 90% of them
single-path `vm-diag-request`/`trainer-vm-diag-request`/`system-action`
relay calls — one `/system-review` session alone opened 33 separate
diag-request issues for what could have been a handful of batched reads
(MB-20260706-CI-MINUTES). Two concrete fixes, both live now:

1. **Prefer the bundled endpoint over its constituent parts.**
   `snapshot?limit=N` already bundles heartbeat + status + audit tail +
   order_packages + trades + vm_health + service states in ONE path. If
   what you need is covered by `snapshot`, request `snapshot` — don't
   separately request `status`, `services`, and `journal?table=trades` as
   three issues (or even three paths) when one `snapshot?limit=5` covers
   all three.
2. **Batch multiple paths into ONE `vm-diag-snapshot` issue** (added
   2026-07-06). The issue **title** still carries a single path exactly as
   before (backward-compatible fallback) — but you can instead put a list
   of paths in the issue **body**, either a JSON array:
   ```json
   ["snapshot?limit=5", "audit?limit=200", "journalctl?unit=ict-trader-live&lines=100"]
   ```
   or one path per line (plain or `-`/`*` bulleted):
   ```
   snapshot?limit=5
   audit?limit=200
   journalctl?unit=ict-trader-live&lines=100
   ```
   The workflow fetches all of them over **one ssh session** (the
   reconnect, not the curl, is the expensive/billed part) and posts **one**
   combined comment, `## <path>` per result. Capped at 15 paths per issue;
   each path gets its own truncation marker (never silently dropped) if the
   combined output would exceed the ~55 KB comment budget. Every path is
   still individually validated against the same allowlist/regex as
   before — nothing about the trust contract changed, only the transport.

   **`trainer-vm-diag`'s `cmd:` block already supports multiple chained
   commands** in one issue (no workflow change needed there) — combine
   several commands into one `cmd:` block instead of opening N issues.

Rule of thumb before opening a diag-relay issue: **"could this be one
`snapshot` call, or one multi-path body, instead of N single-path issues?"**
If yes, batch it.

## Pitfalls (from `docs/claude/diag-relay.md` + debug-memory)

- Live VM: the **title is the path**; the body is ignored. `cmd:` in the body
  is for the **trainer** relay only.
- Use `limit=5` for packages/trades; `snapshot?limit=200` (~665 kB) gets
  truncated to just the audit tail.
- **Bursts are safe since 2026-07-04** (BL-20260611-002 fix): the relay's
  concurrency group is keyed per issue number, so parallel diag-requests run
  CONCURRENTLY and cannot cancel each other. (History: with the old shared
  group, GitHub kept at most ONE pending run, so bursts silently dropped —
  verified 2026-06-11 and again 2026-07-03. If a run ever shows `cancelled`
  again, suspect a regression of the per-issue group.)
- curl exit 7 (`Failed to connect to 127.0.0.1`) = `ict-web-api.service` is
  down → fire `vm-web-api-recover` (label `vm-web-api-recover`) and retry once.
- The live relay is fixed-curl only: it **cannot** run `sqlite3 PRAGMA` or
  arbitrary bash. For DB integrity / arbitrary shell on the live VM there is
  no read path; on the **trainer** VM you have arbitrary bash via its relay.

## Honesty

Report only what a pull actually returned. If a relay failed, say so and which
one — don't infer the state you couldn't read. "audit pull failed, graded from
status only" is a valid, honest result.
