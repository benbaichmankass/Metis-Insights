# Silent‑empty error path audit — 2026-05-10

> **Sprint:** S-067 — silent-empty error path audit & hardening (CP-1 / T0).
> **Trigger:** PR #627 (`/positions` returned `[]` for endpoint lifetime due to swallowed `OperationalError`) and PR #629 (`/signals` dropped `price` because writer aliases weren't covered) both surfaced the same root-cause class. Plus PR #624's docstring on `/api/diag/db_info` explicitly names the same pattern in `_journal_select`.
> **In-scope dirs:** `src/web/api/`, `src/web/runtime_status.py`, `src/units/db/`, plus the read-path slice of `src/runtime/` (config readers, state writers, health checks, reporters).
> **Path note:** the sprint prompt referenced `src/web_api/`; the actual layout is `src/web/api/`. No semantic change.

## Method

Read every Python file in the in-scope dirs and located every `except` block. Each was classified as one of:

| Class | Definition |
|-------|------------|
| **trust-corroding** | Returns a value that the caller cannot distinguish from "no data" (`[]`, `{}`, `0`, `0.0`, fabricated default). The error is invisible to operators, dashboards, and tests. **Fix in this sprint.** |
| **borderline** | Returns a sensible "missing" sentinel (`None`, `[]` on a clearly-optional source) but **doesn't log**. Hard to debug when it bites. **Fix in a secondary PR — log loudly, keep behavior.** |
| **legitimate** | Either (a) explicit never-raise contract (tick-loop best-effort, health-check containers, reporting paths) or (b) returns a typed sentinel that the caller branches on. Already correct. |

`tests/` and live-order-path files are **out of scope** for this sprint per sprint-067-prompt § 7. Live-order-path files (`src/runtime/orders.py`, `src/runtime/pipeline.py`, `src/runtime/risk_counters.py`, `src/runtime/order_monitor.py`, `src/main.py`) are flagged here but defer to a Tier-2 follow-up.

---

## 1. Trust‑corroding sites — fix in S-067

| # | File | Function | Lines (approx) | Pattern | Why trust-corroding | Proposed fix |
|---|------|----------|----------------|---------|---------------------|--------------|
| 1 | `src/web/api/routers/dashboard.py` | `_pnl_stats` | 80–110 | `except Exception: return 0.0, 0.0, 0, 0.0` | Schema mismatch / locked DB / corrupt file all render as "no P&L today, no open trades, 0% winrate". Same root-cause class as PR #627. The `pnl24h` shown on the dashboard header strip is **not provably correct** under any DB error. | Convert to `except sqlite3.Error: logger.exception(...); return None` and have `get_stats()` either omit the field or return 503 when the helper returned `None`. Regression test: materialise a real-schema DB, drop one column, assert `/api/bot/stats` doesn't render fabricated zeroes. |
| 2 | `src/web/api/routers/diag.py` | `_journal_select` | 175–205 | `except sqlite3.Error: return []` | The `/api/diag/db_info` docstring names this exact bug: "the existing journal endpoint silently swallows sqlite3.Error (returns []) — so a 'no such table' or schema mismatch is indistinguishable from 'table empty'". `/api/diag/db_info` was added as a *workaround*; this is the actual fix. | Convert to `except sqlite3.Error: logger.exception(...); raise HTTPException(status_code=503, detail={"error": "journal_unavailable", "table": table, "exc": type(exc).__name__})`. Keep the `_DB_PATH.exists()` early-return path returning `[]` (that's a true "DB hasn't been created" signal, not a swallow). |
| 3 | `src/web/api/routers/diag.py` | `_vm_health` | 270–283 | `except Exception: return {"cpu": 0.0, "memory": 0.0, "disk": 0.0}` | Returns *real-looking* zero readings on psutil failure. Worse than `dashboard.py::_vm_health` (which returns `None` so the dashboard renders `—`). An operator looking at `/api/diag/snapshot` cannot tell "0% disk" from "psutil sample failed". | Mirror the dashboard.py pattern: `logger.warning(...)` + return `{"cpu": None, "memory": None, "disk": None}`. Update the typed return to `Dict[str, float | None]`. |
| 4 | `src/web/api/routers/bot_config.py` | `_read_yaml` | 90–104 | `except Exception: return {}` | A malformed `strategies.yaml` or `accounts.yaml` silently surfaces as "no strategies / no accounts configured" on the Settings tab. The inline comment explicitly says "must never 500 on a missing config — the operator needs visibility precisely when something is off" — the *intent* is right, but the implementation drops the failure invisibly. | Three options: (a) raise; (b) log + return `{}` and surface a top-level `config_load_errors: [...]` in `build_config`'s payload; (c) accept the loss and just log. **Recommend (b)** — keeps the endpoint always-200 (per the comment's intent) but the dashboard can surface a "config corrupt" badge. |
| 5 | `src/web/runtime_status.py` | `build_status` (the `dry_run_overrides` block) | 121–125 | `except Exception: dry_run_overrides = {}` | If `src.units.accounts.get_dry_run_overrides()` raises, every account is reported `live` regardless of Telegram-driven overrides. The runtime-status file then misreports the trader's actual dry/live state — same class as PR #630 (`MONITOR_APPLY_TO_EXCHANGE` survivor) where a process-wide gate silently lost money. | Use the existing `_swallow_runtime_status("dry_run_overrides_read_failed", exc, ...)` helper that the surrounding code uses for accounts.yaml / strategies.yaml read failures. Bonus: the operator gets a deduplicated Telegram alert via `outcomes.report`. |

**Out of these five:** items #1 and #5 directly affect dashboard P&L / trading-mode display (highest priority for the 2026-05-10 trade-performance review trigger). Items #2 and #3 are diag-surface (lower-stakes but flagged in #624 already). Item #4 is config-tab-only.

## 2. Borderline sites — fix in a secondary PR (log loudly, keep behavior)

These return a sensible "missing" sentinel but don't log. The caller branches on the sentinel correctly today, so behavior change isn't required — but the next debugging session is poorer for the silence.

| # | File | Function | Pattern | Recommended log shape |
|---|------|----------|---------|----------------------|
| 6 | `src/web/api/routers/dashboard.py` | `_tail_jsonl` | `except OSError: return []` | `logger.warning("dashboard: tail_jsonl(%s) failed: %s", path, exc)` |
| 7 | `src/web/api/routers/dashboard.py` | `_tail_plain_log` | `except OSError: return []` | same shape |
| 8 | `src/web/api/routers/diag.py` | `_status_json_payload` | `except (OSError, json.JSONDecodeError): return None` | `logger.warning("diag: status_json read failed: %s", exc)` |
| 9 | `src/web/api/routers/diag.py` | `_audit_tail` | `except OSError: return []` | `logger.warning("diag: audit_tail read failed: %s", exc)` |
| 10 | `src/web/api/routers/bot_config.py` | `_read_runtime_live_state` | `except (OSError, json.JSONDecodeError): return {}` | `logger.warning("bot_config: runtime_status read failed: %s", exc)` |
| 11 | `src/web/api/routers/pnl.py` | `_load_account_ids` | `except Exception: outcomes.report(...); return []` (already logs!) | **Already loud** — this is borderline-legitimate. The inner `except Exception: pass` for the report call itself is a defensible last-resort. **No fix needed.** |
| 12 | `src/web/runtime_status.py` | `_resolve_git_sha` | `except Exception: pass; return env or "unknown"` | `logger.debug("runtime_status: git_sha resolution failed: %s", exc)` — debug not warning, this one is noisy in test envs without git |
| 13 | `src/runtime/liquidity_state.py` | `_load_all` | `except (OSError, json.JSONDecodeError): return {}` | `logger.warning("liquidity_state: load_all read failed: %s", exc)` |

Bundle 6–10 + 12–13 into one PR after the trust-corroding fixes land.

## 3. Legitimate sites — no fix needed

These are either explicit never-raise contracts or already do the right thing:

- **`src/web/api/routers/status.py::_load_status`** — `except (json.JSONDecodeError, OSError): raise HTTPException(503)`. ✅ Converts to 503 cleanly.
- **`src/web/api/routers/pnl.py::build_pnl`** — `except sqlite3.Error: raise HTTPException(503)`. ✅ Same pattern.
- **`src/web/api/routers/pnl_history.py::build_pnl_history`** — same. ✅
- **`src/web/api/routers/pnl_fragment.py::pnl_fragment`** — `except HTTPException: return TemplateResponse(503)`. ✅ Converts upstream 503 to HTML stub.
- **`src/web/api/routers/status_fragment.py::status_fragment`** — same shape. ✅
- **`src/web/api/routers/dashboard.py::_vm_health`** — `except Exception: logger.warning(...); return {"cpu": None, "memory": None, "disk": None}`. ✅ Logs + uses `None` sentinels (the dashboard renders `—`).
- **`src/web/api/routers/dashboard.py::get_positions`** — `except sqlite3.Error: logger.exception(...); return []`. ✅ Fixed by PR #627. The `[]` return is a documented trade-off (PositionsPanel keeps rendering); could escalate to 503 in a follow-up but isn't required.
- **`src/web/api/routers/trades_closed.py::get_closed_trades`** — `except sqlite3.Error: logger.exception(...); return []` + `except Exception: logger.exception(...); return []`. ✅ Same trade-off as `/positions`.
- **`src/web/api/routers/diag.py::_db_info_payload`** — three nested `except` blocks all surface the failure into the payload (`load_error` / `error_per_table[tbl]`). ✅ Failure is the *purpose* of the endpoint.
- **`src/web/api/routers/diag.py::_is_active_batch`** — `except (FileNotFoundError, subprocess.TimeoutExpired): return {u: "unknown"}`. ✅ Explicit "unknown" sentinel.
- **`src/web/api/auth.py::decode_token`** — `except jwt.PyJWTError: return None`. ✅ Returning `None` is the documented contract; caller raises 401.
- **`src/web/runtime_status.py::write_status`** — `except Exception: logger.exception(...)`. ✅ Tick-loop never-raise contract.
- **`src/runtime/liquidity_state.py::write_state`** — `except Exception: logger.exception(...)`. ✅ Same tick-loop contract.
- **`src/runtime/health.py`** — every check function has `except …: return _warn(...)` per the explicit "every check function MUST NEVER raise" contract; failures surface as `HealthCheck(status="warn", detail=...)` with the full reason. ✅ Textbook pattern.
- **`src/runtime/api_reporting.py::report_api_failure`** — `except Exception: logger.exception(...)`. ✅ Explicit "reporting must NEVER itself raise" contract.

## 4. Out of scope for S-067 — flagged for follow-ups

### 4a. Live-order path (Tier-2 follow-up)

Per sprint-067-prompt § 7 hard guardrails, no edits in this sprint:

- `src/runtime/orders.py`
- `src/runtime/pipeline.py`
- `src/runtime/risk_counters.py`
- `src/runtime/order_monitor.py` (101 KB — large surface; almost certainly contains analogous patterns)
- `src/main.py`

A separate Tier-2 sprint should audit these with operator ack on each fix. The `closed → exchange-flat` invariant sprint (already in sprint-067-prompt § 8 hand-off) is the natural home for the `order_monitor.py` patterns.

### 4b. Adjacent reporting / boot files — not strictly in-scope but matching pattern

Search hits for `except sqlite3.Error` / `except Exception` + `return []` show matching patterns in:

- `src/runtime/hourly_report.py` — once-per-hour reporter; report-side errors should be loud. Not a tick-loop best-effort path. **Recommend a follow-up audit.**
- `src/runtime/boot_audit.py` — boot-time audit; same logic.

Filed as the third item in this audit's hand-off below.

### 4c. Comms / strategies / accounts / units — not in-scope

`src/units/strategies/`, `src/units/accounts/`, `src/units/dashboards/`, `src/comms/`, `src/bot/`, `src/exchange/`, `src/ict_detection/`, `src/news/`, `src/units/trading_school/`, `src/units/ui/` — sprint prompt § 4b declares these untouched. Their internal exception handling is its own audit topic.

## 5. Notable discoveries during the audit

1. **`/api/bot/trades/closed` already exists** (`src/web/api/routers/trades_closed.py`) — ict-trading-bot#557 was at least partially shipped. The dashboard's `deriveClosedTradesFromLogs` regex fallback in `ict-trader-dashboard/src/services/api.ts` may already be inert in production. Sprint-067 hand-off § 8 should be updated: the closed-trades-endpoint follow-up is now smaller (verify, don't build).
2. **The `_vm_health` pattern is forked.** Two different `_vm_health` helpers exist (`dashboard.py` and `diag.py`) with **different failure semantics** — dashboard returns `None` per field, diag returns `0.0` per field. Item #3 fix unifies them; consider extracting to a shared helper in a follow-up. Filed as cleanup.
3. **`runtime_status.py` already has the right helper** (`_swallow_runtime_status`) that logs via `outcomes.report` with per-fingerprint dedup. Item #5 is just plumbing the existing helper into one more call site.
4. **PR #624's `_db_info_payload` is itself a textbook example of the right pattern** — when the *purpose* of an endpoint is to report failures, every catch surfaces the failure into the payload rather than swallowing it. Worth lifting up in `docs/claude/testing-policy.md` as the canonical example.

## 6. Plan for next checkpoints

| Checkpoint | Sites covered | PR boundary |
|------------|---------------|-------------|
| **CP-2 (T1) — first fix batch** | #1, #2, #3 (all dashboard + diag, no runtime_status) | One PR. Each conversion gets a regression test using PR #627's real-schema fixture pattern locally. |
| **CP-2 (T1) cont'd — second fix batch** | #4, #5 | One PR. Include the `_swallow_runtime_status` plumbing + a regression test that monkeypatches `get_dry_run_overrides` to raise. |
| **CP-3 (T1) — borderline batch** | #6–#10, #12, #13 | One PR. Pure log-call additions; no behavior change; smallest possible diff per site. |
| **CP-4 (T3) — CI guard** | All of `src/web/api/` and `src/units/db/` | `scripts/lint/check_silent_empty.py` AST check + unit test on the lint script. Wire into the existing lint workflow. |
| **CP-5 (T4) — docs + close** | `bug-log.md` (silent-empty class entry), `testing-policy.md` (endpoint error-path checklist + the `_db_info_payload` canonical example), `sprint-067-summary.md`, `milestone-state.md`, `CHECKPOINT_LOG.md`. | One PR (docs only, self-merge). |

## 7. Hand-off

After S-067 ships, the natural follow-ups (in priority order):

1. **Closed → exchange-flat invariant reconciler** (Tier 2, separate sprint) — covers `order_monitor.py` and `pipeline.py` silent-empty patterns naturally as part of a focused live-order-path review.
2. **`hourly_report.py` + `boot_audit.py` audit** — same exercise, scoped to the reporting layer. Tier 1.
3. **Cleanup: unify the two `_vm_health` helpers** (dashboard.py + diag.py) under one shared module. Tier 1.
4. **Verify `/api/bot/trades/closed` end-to-end** and retire the dashboard's `deriveClosedTradesFromLogs` fallback. Update the sprint-067-prompt § 8 hand-off entry. Tier 1.
5. **`_db_info_payload`-style "failure surfacing" pattern** documented in `docs/claude/testing-policy.md` as the canonical "endpoint whose job is reporting failures" example.

— end audit —
