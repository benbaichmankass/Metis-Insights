# Silent-empty audit — reporting layer (2026-05-10)

S-067 follow-up #8. Same shape as
`docs/audits/silent-empty-2026-05-10.md` but scoped to the
reporting-layer files explicitly deferred from the original audit:

* `src/runtime/hourly_report.py` (817 lines) — Telegram hourly
  report fanout. Runs from `ict-trader-live` every hour.
* `src/runtime/boot_audit.py` (104 lines) — open-package
  observability ping fired once per trader restart.

These files share a defining contract with the web-api read-path
files audited in S-067: **they MUST NEVER raise**. A crash here
silences the operator's only window into bot state — the hourly
report is the canonical "is the bot alive and what is it doing"
channel. So a broad `except Exception:` is intentional defensive
coding, not laziness.

That contract is not the same as "silent on failure", though. The
S-067 audit's classification still applies:

* **Trust-corroding** — broad except + sentinel return, no log.
  Caller can't distinguish "no data" from "broken source".
* **Borderline** — broad except + sentinel return, *with* a log
  line. Caller still can't distinguish, but the next debugging
  session sees the failure shape.
* **Legitimate** — broad except + a narrowed-type fallback, or
  the failure is surfaced into the payload itself (`[WARN] Hourly
  Report — assembly failed: <exc>` is the canonical form here).

## Findings

### `src/runtime/boot_audit.py`

| Line | Body | Classification |
|---|---|---|
| 46 | `_load_strategy_names` — log warning, return `[]` | **borderline**. Already logs at `warning`. The empty list collapses "no strategies configured" with "import failed" but the boot-time fanout downstream tolerates either. Could be narrowed to `(ImportError, AttributeError)`. |
| 59 | DB unavailable — log warning, return `{}` | **legitimate**. Boot-time DB read; the never-raise contract takes precedence. The warning is the operator-visible signal. |
| 72 | per-strategy query — log warning, record `0` | **borderline-bordering-trust-corroding**. Recording `0` open packages on a query failure is exactly the silent-empty class; an operator who restarts during a real query failure would see "no open packages" and assume nothing to monitor. The warning log mitigates this — operator is supposed to grep `bot.log` on every restart anyway — but ideally this should record `None` (unknown) and the report builder should pass that through to the wire shape. **Filed as deferred fix in § Phase-2.** |
| 102 | Telegram ping failure — log warning | **legitimate**. Best-effort by design; a broken Telegram channel must not block boot. |

### `src/runtime/hourly_report.py`

| Line | Body | Classification |
|---|---|---|
| 244 | `data_loaders` import failure — log warning, return `[]` | **legitimate**. Optional dependency; the alternative is a hard crash on every hourly tick. The warning is enough to surface the issue. Narrowing to `ImportError` would be a small win. |
| 250 | `list_accounts` failure — log warning, return `[]` | **borderline**. Returning `[]` collapses "no accounts configured" with "load failed". Hourly report builder reads this as "nothing to report"; operator sees a silent empty section. Should at least be narrowed to `(OSError, RuntimeError)` and the report-build path should annotate "accounts data unavailable" rather than silently omit the section. **Filed as deferred fix in § Phase-2.** |
| 263 | `account_balance(acc)` per-account failure — log warning, set `bal = None` | **legitimate**. Mirror of S-061's vm_health post-fix — `None` per field signals "measurement unavailable" to the wire-shape consumer (vs a real `0.0` reading). |
| 285 | `account_open_positions(acc)` per-account failure — log warning, set `positions = None` | **legitimate**. Same as line 263. |
| 312 | `strategy_dashboard_data` failure — log warning, return `[]` | **borderline**. Same shape as line 250 — empty section in the report is silently identical to "no strategies have data". The narrow-type fix is the same recommendation. |
| 409 | `run_all_checks` failure — log warning, set `health_checks = []` | **borderline**. Empty health-check list reads as "all checks healthy" in the downstream assembly. The bug class is exactly the silent-empty original — the report says "no critical / no warn" when actually "we don't know". **Filed as deferred fix in § Phase-2.** |
| 780 | top-level `build_hourly_report` failure — log exception, return `[WARN] Hourly Report — assembly failed: <exc>` | **legitimate / canonical**. This is the textbook pattern: surface the failure into the payload itself (`[WARN]` prefix + exception message). The operator sees the failure as the report; they can't miss it. |
| 808 | top-level `build_accounts_hourly_report` failure | **legitimate / canonical**. Same as line 780. |

## Summary

* **Trust-corroding sites: 0.** Nothing in either file silently
  swallows a failure — every site logs.
* **Borderline sites: 5.** Three in `hourly_report.py` (lines 250,
  312, 409 — all return `[]`) plus two in `boot_audit.py` (line
  46 returns `[]`, line 72 records `0`). All five log; the issue
  is that the empty/zero sentinel collapses with the legitimate
  "no data" path on the wire. Phase-2 fix below.
* **Legitimate sites: 7.** Including the two canonical
  surface-failure-into-payload patterns at lines 780 + 808 — the
  audit doc lifts these as exemplars alongside `_db_info_payload`.

## Phase-1 fix (this PR)

Mechanical: extend the `silent-empty-guard` lint script's
`_PROTECTED_FILES` to include `src/runtime/hourly_report.py` and
`src/runtime/boot_audit.py`. Any new broad-except handler in
either file now requires either a narrow type or
`# allow-silent: <reason>`. The lint guard is the contract; the
audit doc is the precedent for which existing sites are
considered acceptable under the contract.

No code changes to either file in this PR — the existing broad
excepts pre-date the lint guard's protection set, and converting
them is filed as Phase-2 below.

## Phase-2 (deferred)

Three borderline sites (and one borderline-bordering-trust-
corroding site in boot_audit.py:72) should be converted in a
follow-up:

1. `boot_audit.py:72` — replace `counts[strategy] = 0` on
   exception with `counts[strategy] = None`; have the boot ping's
   message handler render `None` as "(query failed)" rather than
   `0`.
2. `hourly_report.py:250` (list_accounts) — narrow except to
   `(OSError, RuntimeError, AttributeError)`; have the assembly
   path surface "accounts data unavailable" in the report body
   rather than silently omit the accounts section.
3. `hourly_report.py:312` (strategy_dashboard_data) — same shape
   as #2.
4. `hourly_report.py:409` (run_all_checks) — same shape; the
   downstream `checks_critical = any(...)` aggregation should
   tolerate a sentinel "unknown" health-check entry.

Each conversion needs a regression test that asserts:
* The wire shape exposes the "unavailable" signal (not a
  fabricated empty/zero).
* The report body still renders without crashing.
* The downstream Telegram fanout still fires.

These are Tier 1 / infra (reporting-layer files; not in the
live-order path per `docs/sprints/sprint-067-prompt.md` § 7
hard-guardrails). Each fix is one PR.

## Cross-references

* `docs/audits/silent-empty-2026-05-10.md` — original S-067 audit
  scope.
* `docs/sprint-summaries/sprint-067-summary.md` § Hand-off — this
  is item #8 of the queued S-067 follow-ups.
* `scripts/check_silent_empty_in_diff.py` — `_PROTECTED_FILES`
  now includes both audited files (this PR).
