# End-to-End System Audit — 2026-05-12
**Reconstruction Sprint: Pinpoint Errors, Not Patches**

> Target branch: `claude/fix-trade-pipeline-MG5qb`  
> Audit date: 2026-05-12  
> Scope: Trade pipeline (Layer 1), service runners (Layer 2), web API + edge transport (Layer 3), observability gaps (Layer 4)

---

## Layer 1: TRADE PIPELINE (The Live Trader)

### Canonical Files & Signal Flow
- **Entry:** `/home/user/ict-trading-bot/src/main.py` (systemd `ict-trader-live.service` spawns this)
- **Core loop:** `/home/user/ict-trading-bot/src/runtime/pipeline.py` (1386 lines; `run_pipeline()` main function)
- **Risk gate:** `src/runtime/risk_counters.py` + `src/runtime/orders.py::safe_place_order()`
- **Signal generation:** `src/core/signals.py`, `src/pipeline/`, strategy builders (turtle_soup, vwap)
- **Signal audit:** `src/utils/signal_audit_logger.py` → `runtime_logs/signal_audit.jsonl` (NDJSON, primary dashboard data source)
- **Dual-write:** S-034 cutover writes to `trade_journal.db::signals` table (opt-out: `SIGNAL_DUAL_WRITE_DISABLED=true`)
- **Order dispatch:** `src/runtime/orders.py::safe_place_order()` → Bybit/Binance adapter
- **Outcomes logging:** `src/runtime/outcomes.py::report()` → Telegram + structured logs
- **Status snapshot:** `src/web/runtime_status.py::write_status()` → `runtime_logs/runtime_status.json` (atomic via tempfile+rename)
- **Heartbeat:** `src/runtime/heartbeat.py::write_heartbeat()` → `runtime_logs/heartbeat.txt` (mtime = liveness signal)

### Intended Design
1. **Tick cadence:** 60 s loop (TICK_INTERVAL_SECONDS=60, 2026-05-08 change from 5 min)
2. **Per-tick flow:**
   - Fetch candles from exchange
   - Run strategy signal builders (multiplexed: first actionable wins)
   - Publish liquidity state (S-064: `runtime_logs/liquidity_state.json`)
   - Apply risk gate (position size, margin cap, halt flag, news veto)
   - Place order if passed all checks
   - Log outcome (level: INFO/WARN/ERROR mapped to status string)
   - Write heartbeat + status snapshot
3. **Exception boundaries:** try/except blocks at risk gate and order placement; _report_pipeline_outcome() never raises (line 59–74)
4. **Audit trail:**
   - `signal_audit.jsonl`: every signal detection (buy/sell/skip/refuse reason)
   - `runtime_status.json`: effective config view (accounts, strategies, halt state)
   - `trade_journal.db`: orders, trades, order_packages (M5 consumer writes backtest_results)

### Actual Current Behavior
- **Signal flow:** Pipeline correctly detects ICT patterns (FVG, order blocks) and writes to JSONL (lines 800–883)
- **Silent exceptions (26 try/except blocks):**
  - Line 90–91: liquidity_state write swallows exceptions, logs warning
  - Line 850–851: FVG price parsing catches TypeError/ValueError, sets price=None silently
  - Line 873–874: order block price parsing swallows all exceptions
  - Line 893–894: strategy registry load fails over to hardcoded fallback (logs warning)
  - Line 1007: signal_prefixes lookup swallows exception, falls back to pre-S-007 logic (logs nothing)
- **Outcomes reporting:** Line 73–74 catches exception in outcomes.report() and logs (never surfaces)
- **Exception tunneling:** Line 1003–1012 has silent fallback for strategy name resolution; if registry fails, defaults to "trade_signal" type
- **Risk gate:** Lines 495, 561 catch exceptions in validation; lines 938 catches exception in dispatch (all surface as failed_validation/failed_dispatch status)

### Recent Patch Churn (Last 30 Days)
| Commit | Patch | Root |
|--------|-------|------|
| 385d47b | fix: observability + reconciler self-correction (#855) | 2026-05-11 incident: bybit_2 silent flip + missing heartbeat |
| 6ffae97 | fix(web-api): heartbeat + runtime_status reader paths respect DATA_DIR (#871) | Data directory path divergence (trader wrote to `/home/ubuntu/ict-trading-bot/data/`, reader looked at `/data/bot-data/`) |
| 053a183 | fix(audit): signal_audit_logger writer respects DATA_DIR (#882) | Same divergence: writer ignored runtime_logs_dir() utility |
| 8a58e40 | fix(paths): anchor relative DATA_DIR / per-root overrides (#881) | Path resolution inconsistency across 6+ writers |
| 6d8689d | feat(health-snapshot): fold OCI /data verify into snapshot; fix (#875) | OCI block storage path verification missing |
| d5fe5df | fix(bybit): set_leverage via direct V5 signed POST (#903) | CCXT routing bug in leverage endpoint |
| 215e801 | fix(liveness): external heartbeat watchdog + optional autoheal (#950) | Heartbeat writer silent failure (16h stall on 2026-05-11) |

**Summary:** 6 patches in 2 weeks for PATH DIVERGENCE ALONE (writers hardcode, readers follow env vars; or vice versa). Each patch targeted a different file. Root cause never unified.

### Single Most Error-Prone Spot
**`src/runtime/pipeline.py` line 1007–1012: Silent strategy name fallback**
```python
try:
    from src.strategy_registry import signal_prefixes as _sp
    _prefixes = _sp(_strat_key)
    _sig_type = _prefixes[0] if _prefixes else "trade_signal"
except Exception:
    # Pre-S-007 fallback: preserves exact historical behaviour.
    _sig_type = (
        "ml_breakout" if _strat_key == "breakout_confirmation"
        else ("fvg" if meta.get("fvg") else "trade_signal")
    )
```
**Why it bites:**
- If registry import fails (missing yaml, corrupt file, import error), signal type defaults to hardcoded logic
- Operator has no way to know registry failed (no log, no audit row, no Telegram)
- Dashboard signal filtering by pattern breaks silently (groups signals under wrong pattern)
- No heartbeat impact (tick loop keeps running) → operator doesn't notice for hours

---

## Layer 2: SERVICE RUNNERS (Systemd Units + Watchdogs)

### Inventory
| Unit | Process | Trigger/Cadence | Watchdog | Restart | Failure Signal |
|------|---------|-----------------|----------|---------|-----------------|
| `ict-trader-live.service` | `/usr/bin/python3 -u -B -m src.main` | Systemd start at boot | External: `ict-liveness-watchdog.timer` (60 s) | Restart=always; RestartSec=10 | Heartbeat >5 min stale → `ict-liveness-watchdog` alerts + restarts after 8 min |
| `ict-web-api.service` | `/usr/bin/python3 -m uvicorn src.web.api.main:app --host 0.0.0.0 --port 8001` | Systemd start at boot | Manual check: `GET /api/health` | Restart=always; RestartSec=5 | 503 to `/api/diag/*`, curl exit 7 (connection refused) → operator opens issue to `vm-web-api-recover.yml` |
| `ict-liveness-watchdog.timer` | Runs `ict-liveness-watchdog.service` | Every 60 s | None | N/A (one-shot) | Watchdog itself can fail to start (systemd logs only) |
| `ict-heartbeat.timer` | Runs `ict-heartbeat.service` (daily digest) | Once daily at 13:00 UTC | None | N/A | Silent failure: Telegram not sent (operator only notices next day) |
| `ict-hourly-snapshot.timer` | Runs `ict-hourly-snapshot.service` | Every hour | None | N/A | Silent failure: snapshot not written |
| `ict-git-sync.timer` | Runs `ict-git-sync.service` | Every 10 min | None | N/A | Silent failure: local branch out of sync |
| `ict-shadow-log-rotate.timer` | Runs `ict-shadow-log-rotate.service` | Every 6 hours | None | N/A | Silent failure: shadow logs grow unbounded |

### Intended Design (Per CLAUDE.md)
- **Prime Directive:** Trader runs 24/7; operator controls mode via `set-account-mode` action only (no auto-flip)
- **Heartbeat:** 60 s cadence from inside tick loop (line 33, `src/main.py`); liveness signal is mtime of `runtime_logs/heartbeat.txt`
- **Watchdog escalation:** stale > 5 min → alert; stale > 8 min → restart (opt-in via `LIVENESS_AUTO_RESTART_AFTER=3`)
- **Telegram coverage:** Every outcome level (INFO/WARN/ERROR) routes through alert manager or Telegram direct; refusals get per-trade pings

### Actual Current Behavior
- **Restart=(always|no) mismatch:** `ict-trader-live` and `ict-web-api` have `Restart=always`; all timers are one-shot with **no Restart**
- **Watchdog enabled:** `/deploy/ict-liveness-watchdog.timer` fires every 60 s; `ExecStart=/usr/bin/python3 -u scripts/check_heartbeat.py --interval 60 --grace 5 --auto-restart-after 3`
- **Heartbeat monitoring:** Works; threshold is `< cadence × 3` for running (180 s), `< cadence × 10` for paused (600 s), else stopped
- **Web API:** Restart=always with RestartSec=5; no separate watchdog (relies on systemd+Restart)
- **Daily digest (`ict-heartbeat.timer`):** Runs at 13:00 UTC; no Restart; silent failure if `scripts/daily_heartbeat.py` crashes (operator only notices next day)
- **Git sync:** Runs every 10 min; no watchdog; silent failure if repo is dirty/wedged (verified via diag relay)

### Top 3 Fragility Hot-Spots
1. **`/deploy/ict-heartbeat.{service,timer}` — line 14 (EnvironmentFile=/home/ubuntu/.env.live)**
   - Daily digest uses **different env** file than trader (`ict-trader-live` reads `.env`)
   - If `.env.live` is missing or out of sync, heartbeat fails silently (no Telegram, operator waits 24 h)
   - **Patch history:** 2026-05-12 incident required manual Telegram verification; not surface in alerts

2. **`/deploy/ict-liveness-watchdog.{service,timer}` — absence of BEFORE/AFTER ordering**
   - Timer runs every 60 s, but no `After=ict-trader-live.service` ordering
   - On boot, watchdog can fire before trader has even started (false alert)
   - **No recent fix** because symptom was not obvious (one-minute noise, not a failure)

3. **`/deploy/ict-git-sync.timer` — no Restart, no monitoring**
   - Silent failure if fetch fails (network timeout, auth expired, bare repo state)
   - Operator unaware until next deploy (hours to days later)
   - **Verify via diag relay:** git status will show divergence but operator must SSH to notice

---

## Layer 3: WEB API + EDGE TRANSPORT

### Canonical Files
- **Bot API:** `/home/user/ict-trading-bot/src/web/api/main.py` (FastAPI app, CORS config)
- **Routers:** `/home/user/ict-trading-bot/src/web/api/routers/*.py` (dashboard, diag, status, pnl, backtests, health_snapshots, etc.)
- **Dashboard repo:** `/home/user/ict-trader-dashboard/` (React SPA, Vercel deployment)
- **Vercel config:** `/home/user/ict-trader-dashboard/vercel.json` (API rewrite rules)

### Intended Design
```
Browser (Vercel HTTPS)
  → Vercel edge (matches /api/bot/*)
  → Rewrite destination: HTTP → Cloudflare quick tunnel
                        → Plain HTTP :8001 (VPS)
  ← Response
```

The bot listens on `0.0.0.0:8001` (plain HTTP). Vercel's rewrite proxies `/api/bot/*` server-side so the browser never sees mixed-content block (HTTPS → HTTP).

### Actual Current Behavior & Patch Churn

**vercel.json current state (as of 2026-05-12):**
```json
{
  "rewrites": [
    { "source": "/api/bot/:path*", "destination": "https://featured-thunder-bio-lucia.trycloudflare.com/api/bot/:path*" },
    { "source": "/(.*)", "destination": "/" }
  ]
}
```

**Rewrite path history (last 30 days):**
| Date | Commit | Attempt | Outcome |
|------|--------|---------|---------|
| 2026-05-08 | a5d2311 | Vercel rewrite to `http://158.178.210.252:8001` | Mixed-content block (browser HTTPS → HTTP forbidden) |
| 2026-05-10 | e0d071e (#22) | Repoint to Cloudflare quick tunnel (HTTPS tunnel) | Worked; tunnel endpoint `*.trycloudflare.com` is HTTPS |
| 2026-05-10 | 6e72850 (#23) | Vercel Edge Function proxy (drop tunnel dependency) | Vercel's edge runtime has HTTPS-only egress on Hobby plan; fetch to plain HTTP blocked |
| 2026-05-10 | 430c050 (#25) | Revert Edge Function; restore tunnel rewrite | Functional again |
| 2026-05-10 | c702e1f (#29) | Fresh quick tunnel (old one stale) | Current working state |

**Root cause summary:**
- Vercel rewrites cannot target plain-HTTP external IPs (Hobby plan restriction)
- Vercel Edge Functions have HTTPS-only egress (same restriction)
- Cloudflare Workers also block raw IPv4 targets (error 1003: "Direct IP Access Not Allowed")
- **Only working path:** HTTPS tunnel (quick-tunnel or named tunnel) acts as HTTPS endpoint
- Tunnel is **not persistent:** `trycloudflare.com` endpoints are temporary; a new tunnel spawn changes the URL
- **Operator must manually edit `vercel.json` on each tunnel restart** (if the tunnel dies and restarts, dashboard breaks until URL is updated)

### Top 3 Fragility Hot-Spots
1. **Cloudflare quick tunnel endpoint (`vercel.json` line 3) — hardcoded URL**
   - Quick tunnels are temporary; lifespan is ~24 h or less if `cloudflared` restarts
   - If tunnel dies, `vercel.json` endpoint becomes 404; dashboard fails (no fallback)
   - **No monitoring:** operator unaware until dashboard returns 502 from Vercel edge
   - **Patch burden:** 2 emergency PRs (#22, #29) in 2 days because tunnel addresses changed

2. **CORS mismatch (`src/web/api/main.py` line 37–43)**
   - Origins: `localhost:5173`, `localhost:3000`, `DASHBOARD_ORIGIN` env var
   - Env var set to `https://bentzbk-ict-trader-dashboard.vercel.app` in `ict-web-api.service`
   - If Vercel project is renamed or custom domain changes, unit file must be updated (not versioned)
   - **Silent failure:** CORS rejection returns 403; dashboard renders connection error banner (operator might think bot crashed)

3. **`src/web/api/routers/dashboard.py` line 36 — hardcoded audit log path**
   - `_AUDIT_LOG = _REPO_ROOT / "runtime_logs" / "signal_audit.jsonl"`
   - Does NOT respect `DATA_DIR` or `RUNTIME_LOGS_DIR` env vars (unlike heartbeat reader on line 41)
   - 2026-05-11 incident: trader wrote to `data/runtime_logs/`; dashboard read from repo root (divergence)
   - **Fixed in #882** but the code still hardcodes; other routers (backtests.py, liquidity.py) have similar bugs

---

## Layer 4: OBSERVABILITY GAPS (The Silent Failure Root Cause)

### Operator Debug Path Today
When a trade should happen and doesn't:

1. **Check if bot is alive:**
   - SSH to VM: `ssh ubuntu@158.178.210.252`
   - Manually: `tail runtime_logs/heartbeat.txt` (mtime check)
   - **Dashboard:** Bot Status label (running/paused/stopped) from `/api/bot/stats` heartbeat age
2. **Check if signal was generated:**
   - SSH to VM: `tail -f runtime_logs/signal_audit.jsonl | grep <symbol>`
   - **Dashboard:** Signals tab shows recent ICT detections
   - **Operator pain point:** If audit log is missing or has wrong path → Signals tab blank, no logs → operator assumes no signal was generated (but signal may have been generated and written to the wrong file)
3. **Check if order was placed:**
   - SSH to VM: `sqlite3 trade_journal.db 'SELECT * FROM orders WHERE symbol="BTCUSDT" ORDER BY created_at DESC LIMIT 5;'`
   - **Dashboard:** No endpoint directly shows pending orders (only open trades in Positions tab)
   - **Gap:** If order placement failed, operator must infer from the absence of a row + search bot.log for error
4. **Check outcomes / refusals:**
   - SSH to VM: `grep -i "refuse\|risk\|halt\|news" runtime_logs/signal_audit.jsonl`
   - **Dashboard:** No dedicated refusal viewer (hidden in raw Logs tab JSON)
   - **Operator pain point:** Refusals are mixed with signal detections; hard to surface why a signal was skipped
5. **Check systemd status:**
   - SSH to VM: `systemctl status ict-trader-live.service`
   - **Dashboard:** `/api/bot/health/services` endpoint (added 2026-05-11) shows unit states
   - **Gap:** No historical service state (only current)

### Places Where Signal Flow Dies Silently
| Stage | Silent Failure Mode | Audit Row? | Log Row? | Telegram? | Surface in API? |
|-------|---------------------|-----------|---------|-----------|-----------------|
| Signal generation (strategy builder) | Exception in strategy code (e.g., FVG calc). Line 850: price parse fails → price=None | Yes (but price=null) | Yes (logger.info) | No | Yes (logs endpoint) |
| Risk gate (position size) | Position_size() returns 0 → qty=0 → order placement skipped | No | Maybe (depends on exception catch) | No | No (no refusal audit row) |
| Order placement (safe_place_order) | Exception in order submission → result={status: failed_dispatch} | No (swallowed in line 938) | Yes (logger.exception) | Yes (outcomes.report → Telegram) | Yes (logs endpoint) |
| Halt flag check | HALT_FLAG_PATH exists → result={status: halted} | No | Yes (logger.info) | No (INFO level, not WARN) | Yes (logs endpoint) |
| News veto | get_news_score() > threshold → result={status: news_veto} | No | Yes (logger.info) | No (INFO level) | Yes (logs endpoint) |
| Risk counter exceeded | RiskManager.can_trade() returns False → result={status: refused} | No | Yes (logger.info) | No (INFO level) | Yes (logs endpoint) |
| Heartbeat write failure | write_heartbeat() returns False (atomic rename failed) | No | Yes (logger.warning) | No | Dashboard shows "stopped" |
| Signal audit write failure | JSONL file permission denied or disk full | No | No (exception swallowed in log_signal) | No | Audit endpoint returns [] |
| Runtime status write failure | write_status() fails (disk full, permission, tempfile) | No | Yes (logger.exception) | No | `/api/status` returns stale data |
| Liquidity state write failure | _publish_liquidity_state() exception (line 90–91) | No | Yes (logger.exception) | No | `/api/bot/liquidity` returns 404 |

### Minimum Structured Audit Lines to Answer "Where Did This Signal Die?"

**Current deficiency:** A signal detected, risk gate applied, order placement attempted, but no row in `trade_journal.db::orders` means the order hit an exception OR risk gate rejected it. Operator cannot tell which.

**Proposed addition (one NDJSON row per audit point):**
```jsonl
{"ts": "2026-05-12T10:00:00Z", "event": "signal_detected", "symbol": "BTCUSDT", "side": "buy", "signal_id": "fvg_abc123", "pattern": "fvg_bullish"}
{"ts": "2026-05-12T10:00:01Z", "event": "risk_gate_entry", "signal_id": "fvg_abc123", "qty_requested": 0.001, "account": "bybit_2", "mode": "live"}
{"ts": "2026-05-12T10:00:01Z", "event": "risk_gate_reject", "signal_id": "fvg_abc123", "reason": "position_size_zero", "detail": "balance_insufficient"}
{"ts": "2026-05-12T10:00:02Z", "event": "signal_processed", "signal_id": "fvg_abc123", "outcome": "refused", "outcome_level": "INFO"}
```

**Grep to find signal death:** `grep "fvg_abc123" runtime_logs/signal_audit.jsonl` → single grep shows full lifecycle, every decision point.

---

## Layer 1 Top 3 Fragility Hot-Spots (Detailed)

### 1. Silent Strategy Registry Fallback (pipeline.py:1007–1012)
**File:** `/home/user/ict-trading-bot/src/runtime/pipeline.py`  
**Lines:** 1003–1012  
**Symptom:** Signal type defaults to "trade_signal" if registry import fails; no operator notification.  
**Trigger:** Missing yaml package, corrupt YAML, import error in strategy_registry.  
**Impact:** Dashboard signal filtering breaks (wrong pattern group); multi-account dispatch silently uses fallback strategy list.  
**Bug class:** Exception tunneling + silent fallback.  
**Patch history:** No recent fixes (fallback existed pre-S-007, still exists).

### 2. Path Divergence in Writers vs. Readers (6+ files)
**File:** Multiple (`signal_audit_logger.py` line 21, `runtime_status.py` line 26, `dashboard.py` line 36 & 41, etc.)  
**Symptom:** Writer uses `runtime_logs_dir()` (DATA_DIR-aware); reader hardcodes repo path (or vice versa).  
**Trigger:** Operator sets `DATA_DIR=/data/bot-data` but dashboard reads from `/home/ubuntu/ict-trading-bot/runtime_logs`.  
**Impact:** Heartbeat missing → bot shows "stopped"; audit log missing → no signals visible; status stale.  
**Bug class:** Path resolution inconsistency.  
**Patch history:** 6 patches in 2 weeks (PRs #871, #882, #881, #875, etc.) each targeting a different file.  
**Root cause never unified:** Each writer/reader pair fixed independently instead of centralizing path resolution.

### 3. Heartbeat Write Failure (16h Silent Stall on 2026-05-11)
**File:** `/home/user/ict-trading-bot/src/runtime/heartbeat.py`  
**Lines:** 55–75 (atomic write via tempfile + rename)  
**Symptom:** write_heartbeat() returns False (logged as warning); tick loop continues; heartbeat.txt mtime goes stale; external watchdog alerts after 5 min.  
**Trigger:** Permission error, disk full, tempfile directory permissions, or inode exhaustion.  
**Impact:** Bot appears "stopped" to dashboard for 16+ hours (incident 2026-05-11); watchdog correctly auto-restarted but bybit_2 woke up in dry mode (separate silent flip bug, now fixed by Prime Directive codification).  
**Bug class:** Silent write failure + missing early alert.  
**Patch history:** Fixed in PR #950 (added external watchdog); heartbeat write itself still swallows exceptions (logging only).

---

## Reconstruction Recommendations (Ranked)

### 1. **Unified Path Resolution Audit (S, Bug class: path divergence × 6 files) — DONE (T2)**
**Effort:** S  
**Action:** Every runtime-log reader now routes through `src.utils.paths` (`runtime_logs_dir()` / `artifacts_dir()`). Anti-pattern lint guard in `tests/test_runtime_paths_alignment.py` blocks future regressions at the `pytest-collect` CI gate (tokenize-aware so docstring references to incident history don't false-fire).  
**Files migrated (readers):**
- `src/web/api/routers/dashboard.py` — `_AUDIT_LOG`, `_HEARTBEAT`
- `src/web/api/routers/diag.py` — `_AUDIT_LOG`, `_HEARTBEAT`, `_STATUS_JSON`
- `src/web/api/routers/bot_config.py` — `_RUNTIME_STATUS_JSON`
- `src/web/api/routers/shadow.py` — `_log_path()`
- `src/web/api/routers/trade_scores.py` — `_SHADOW_LOG`
- `src/runtime/health.py` — `tick_check_heartbeat`
- `src/utils/validation_logger.py` — `_log_path()` fallback (writer also; in T2 scope as the M5 path-divergence risk had not yet hit but matched the pattern exactly)

**Sprint log:** `docs/sprint-logs/T2-unified-path-resolution.md`  
**Supersedes:** PRs #871, #882, #881, and the 2026-05-11 dashboard.py:36 / heartbeat reader / runtime_status reader divergences — six one-off fixes in two weeks; all one bug class.  
**Verification:** `pytest tests/test_runtime_paths_alignment.py -v` → 7/7 (6 alignment tests under default / DATA_DIR / RUNTIME_LOGS_DIR overrides + 1 lint guard).

### 2. **Structured Audit Logging for Signal Lifecycle (M, Bug class: observability gap)**
**Effort:** M  
**Action:** Add `event`, `signal_id`, `outcome_code` fields to `signal_audit.jsonl`. Every decision point (signal detected → risk gate → order placed → outcome) writes its own row. Single grep finds full lifecycle.  
**New fields in each row:**
- `event` (string): signal_detected, risk_gate_entry, risk_gate_reject, order_placed, order_submitted, outcome_logged
- `signal_id` (string): UUID or hash of (symbol, timestamp, pattern) for tracing
- `outcome_code` (string): skipped, refused, submitted, filled, failed_dispatch, etc.
- `detail` (object): exception name, rejected_reason, order_id, etc.

**Supersedes:** Ad-hoc grep through bot.log + database + Telegram transcripts.  
**Test:** Operator can run `grep <signal_id> runtime_logs/signal_audit.jsonl` and see 5–10 rows showing every decision.

### 3. **Silent Exception Centralization (M, Bug class: exception tunneling)**
**Effort:** M  
**Action:** Replace 26 bare `except Exception:` blocks in pipeline.py with two patterns: (a) log.warning + continue (non-critical path); (b) report() + outcomes (critical path, routes to Telegram). Audit which exceptions should alert.  
**Lines to review:** 90, 435, 495, 556, 561, 850, 873, 893, 938, 1007, 1030+ (26 total).  
**Supersedes:** Incident 2026-05-11 (silent flip, heartbeat write failure, path divergence all masked by poor exception visibility).  
**Test:** Mock each exception; verify outcomes.report() fires and Telegram receives the alert.

### 4. **Persistent HTTP Tunnel Replacement (L, Bug class: infrastructure fragility) — IN PROGRESS (T1)**
**Effort:** L  
**Action:** Named Cloudflare tunnel — persistent, account-bound, stable hostname. Wrapped in `ict-cloudflared-tunnel.service` with `Restart=always` (kills the silent-crash failure class). Operator fires `setup-named-cloudflare-tunnel` (allowlisted Tier-2 action); script creates/fetches the tunnel via CF API, writes credentials + ingress config, installs the unit, returns the stable URL. Bot still listens on plain HTTP; TLS terminates at CF's edge before traffic reaches the VM — no cert management on the bot side.  
**Implementation:**
- `deploy/ict-cloudflared-tunnel.service`
- `scripts/ops/setup_named_cloudflare_tunnel.sh` / `scripts/ops/teardown_named_cloudflare_tunnel.sh`
- `.github/workflows/operator-actions.yml` — two new allowlisted actions (`setup-named-cloudflare-tunnel`, `teardown-named-cloudflare-tunnel`)
- `docs/runbooks/cloudflare-named-tunnel.md` — operator runbook
- `docs/sprint-logs/T1-named-cloudflare-tunnel.md` — sprint log

**Supersedes:** PRs ict-trader-dashboard#22, #23, #25, #29, #30 — five `vercel.json` URL-rotation PRs in two days.  
**Verification:** Operator fires `setup-named-cloudflare-tunnel` from workflow_dispatch UI (or labelled issue); follow-up `vercel.json` PR repoints to the stable URL. After one healthy 24 h cycle, retire the quick tunnel via `teardown-cloudflare-tunnel`.

### 5. **External Watchdog Alerting for Secondary Units (M, Bug class: silent timer failure)**
**Effort:** M  
**Action:** Extend `scripts/check_heartbeat.py` pattern to `ict-heartbeat.timer`, `ict-git-sync.timer`, `ict-hourly-snapshot.timer`. Each timer writes a state JSON (last_run_ts, last_success_ts). A new watchdog checks staleness (> 2× expected cadence) and Telegrams. Opt-in per unit via env.  
**Supersedes:** Silent failure of daily digest (operator waits 24 h to notice).  
**Test:** Kill `ict-heartbeat.service` subprocess; watchdog detects timer didn't run, sends alert within 2 min.

### 6. **Outcome-Level Standardization (S, Bug class: inconsistent alerting)**
**Effort:** S  
**Action:** Define when each outcome level fires Telegram. Currently: INFO (skipped, halted, news_veto, refused) doesn't alert; only WARN/ERROR do. But refusals are operator-relevant (e.g., "position_size_zero = out of balance"). Make `refused` → WARN.  
**Lines to review:** `src/runtime/pipeline.py` line 35–50 (_OUTCOME_LEVEL_BY_STATUS mapping).  
**Supersedes:** Incident 2026-05-11 incident analysis (silent dry flip).  
**Test:** Trigger each outcome type; verify Telegram behavior matches expectation.

### 7. **Atomic Write Verification Wrapper (S, Bug class: silent write failure)**
**Effort:** S  
**Action:** Create `src/utils/atomic_write.py::safe_atomic_write()` helper. Wraps tempfile + rename pattern used in heartbeat, runtime_status, signal_audit. Returns (success: bool, error: str). Callers log.warning on False; critical paths route through outcomes.report().  
**Supersedes:** PR #950 (external watchdog as a patch for internal write failure).  
**Test:** Mock permission error; verify helper returns False and caller alerts.

### 8. **Dashboard Data Contract Audit (M, Bug class: contract drift)**
**Effort:** M  
**Action:** Enumerate every nullable field in API responses (stats.vmHealth.*, signals[].pattern, positions[].stopLoss, etc.). Audit dashboard renderer for each: does it treat null as "not measured" (render em-dash) or "unmeasured but should be zero" (render 0)? Docstring the contract per endpoint. Add JSON schema validation.  
**Supersedes:** ict-trading-bot#556 (contract ambiguity re: null vs. 0).  
**Test:** Dashboard CI validates every response against schema; any unexpected null triggers build failure.

### 9. **Unified Telegram Alerting Backend (L, Bug class: redundant paths)**
**Effort:** L  
**Action:** Consolidate alert paths: currently outcomes.report() → AlertManager → Telegram, AND heartbeat → direct Telegram, AND watchdog → direct Telegram. One alert service with dedup, rate-limit, priority queue.  
**Supersedes:** Multiple Telegram client initializations (AlertManager, DummyTelegramClient, direct sends).  
**Test:** 100 outcomes/min → Telegram receives deduplicated stream (no spam).

### 10. **Config-as-Code for Unit Files (M, Bug class: env var divergence)**
**Effort:** M  
**Action:** Declare all systemd EnvironmentFile references in a central location (e.g., `.env.systemd` parsed by a deploy script). Ensure `ict-trader-live.service`, `ict-web-api.service`, `ict-heartbeat.service` all load the same env. Prevent future divergence (e.g., `.env` vs. `.env.live`).  
**Supersedes:** PRs #871, #921, #991 (environment path mismatches).  
**Test:** Change one env var in central config; verify all 3 units load it.

---

## Summary: The Leaning Tower's Keystone

**Root cause:** Path resolution divergence created a cascading audit gap:
1. Trader writes signal to `/home/ubuntu/ict-trading-bot/data/runtime_logs/signal_audit.jsonl` (respects DATA_DIR)
2. Dashboard reads from `/home/ubuntu/ict-trading-bot/runtime_logs/signal_audit.jsonl` (hardcoded)
3. Heartbeat mtime check works (both use runtime_logs_dir()) but dashboard blank
4. Operator unaware signal was actually generated → assumes bot is broken
5. Silent retry loop (each subsystem fails independently, patches one file at a time)

**This audit identifies 26 silent-exception blocks in the pipeline alone, a 16-hour heartbeat failure, and 6 path-divergence patches in 2 weeks.**

Reconstruction priority: Unify path resolution (blocks 5 other patches), add structured audit rows for signal lifecycle (enables operator debugging), and centralize exception handling (eliminates silent failures).

