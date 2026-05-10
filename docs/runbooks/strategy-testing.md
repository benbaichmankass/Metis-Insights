# Strategy Testing Workflow — Runbook

**Owner:** auto-claude (M5).
**Status:** ✅ Live as of 2026-05-09.
**Code map:** consumer in `src/bot/test_strategy_consumer.py`; subprocess
runner in `src/backtest/run_backtest_m5.py`; validation log writer in
`src/utils/validation_logger.py`; dispatch handler at
`src/bot/telegram_query_bot.py::cmd_test_strategy`; comms wiring in
`src/bot/comms_handler.py::install_comms_handlers`.

This runbook is the operator-facing TL;DR for the closed-loop M5
strategy-testing flow: an operator types `/test <strategy>` in Telegram
and a backtest result lands back in the same Telegram thread within
one comms-poll cycle (default 60 s + the backtest's wall-clock).

---

## TL;DR

```
Operator (Telegram)  ──/test vwap──►  cmd_test_strategy
                                       │  (registry-validate, mint
                                       │   REQ-…-tsvwap.json,
                                       │   git push)
                                       ▼
                                 comms/requests/REQ-…-tsvwap.json
                                       │
                                       ▼ (next CommsPoller tick)
                                 BacktestConsumer.scan_and_run
                                       │
                                       ├─► subprocess: run_backtest_m5
                                       │     → load CSV, run ICTBacktester,
                                       │       persist row to backtest_results,
                                       │       JSON envelope on stdout
                                       │
                                       ├─► apply_answer
                                       │     → REQ-…-tsvwap.json
                                       │       transitions PENDING→SENT→ANSWERED,
                                       │       git push (response writeback)
                                       │
                                       └─► runtime_logs/validation.jsonl
                                             → one NDJSON row per run
```

The artifact reaches `answered` on **every** path — happy, timeout,
subprocess failure, missing-data, registry-miss. Operators always see a
result in Telegram and a row in the validation log.

---

## Wire-up & env-var matrix

The consumer is **off by default** in every environment. Set
`M5_CONSUMER_ENABLED=1` on the VM systemd unit (and only there) to
auto-install the consumer pass into `CommsPoller.poll_once`.

| Env var | Default | Purpose |
|---|---|---|
| `M5_CONSUMER_ENABLED` | unset (`0`) | Auto-install the consumer when the bot boots. Set to `1`/`true`/`yes`/`on` to enable. **Kill switch:** unset on the VM and restart the trader-bot service. |
| `M5_BACKTEST_TIMEOUT_S` | `120` | Wall-clock cap per subprocess run, in seconds. A multi-MB CSV that exceeds this surfaces as `outcome=timeout`. |
| `BACKTEST_DATA_PATH` | `data/backtest_candles.csv` (or `data/candles.csv`) | Override the candle CSV the runner reads. |
| `TRADE_JOURNAL_DB` | `trade_journal.db` | Override the SQLite path; the run row lands in `backtest_results`. |
| `VALIDATION_LOG_PATH` | `runtime_logs/validation.jsonl` | Override the NDJSON validation-log path. |
| `COMMS_PUSH_ENABLED` | unset (`0`) | Inherited from the comms layer. Must be `1` on the VM so the response-writeback push fires; otherwise the answer stays local. |

The dispatch handler (`cmd_test_strategy`) does **not** depend on the
env gate — it always validates the strategy name against the registry
and queues an artifact. Disabling the consumer just means the artifact
sits in `comms/requests/` until somebody flips the gate or answers
manually.

---

## Validation log

`runtime_logs/validation.jsonl` is the canonical audit trail. One
NDJSON row per backtest run, append-only, no rotation (matches
`signal_audit.jsonl`). Schema:

| Key | Type | Notes |
|---|---|---|
| `event` | `"backtest_run"` | Constant for now; reserved for future M5 events. |
| `request_id` | `REQ-…` | Comms artifact this run was triggered by. |
| `strategy` | string | From the registry (`vwap`, `turtle_soup`, …). |
| `outcome` | `"ok" \| "timeout" \| "subprocess_failure" \| "error"` | Canonical label; `"error"` is the catch-all. |
| `started_at_utc` | ISO 8601 | When the consumer claimed the artifact. |
| `completed_at_utc` | ISO 8601 | When `apply_answer` returned. |
| `db_row_id` | int | `backtest_results.id` (only on `outcome=ok`). |
| `summary` | dict | Headline metrics subset (only on `outcome=ok`). |
| `error` | string | `"<ClassName>: <msg>"` (only on non-`ok` outcomes). |
| `exit_code` | int | Subprocess exit code (only on `outcome=subprocess_failure`). |
| `logged_at_utc` | ISO 8601 | Set by the writer; safe to use for time-ordering. |

### Tail it

```bash
ssh ict-trader-vm 'tail -n 20 /home/ubuntu/ict-trading-bot/runtime_logs/validation.jsonl' | jq .
```

Or via the diag relay (Tier-1 read):

```bash
gh issue create -R benbaichmankass/ict-trading-bot \
  --title '[diag-request] log_file?name=audit&lines=100' \
  --label vm-diag-request
```

(The `audit` log_file alias still points at `signal_audit.jsonl`; the
relay's allowlist gets a `validation` alias when the dashboard tab in
P4 lands. Until then, SSH or the underlying file_tail is the path.)

### Pull a row by id

```bash
ssh ict-trader-vm 'sqlite3 /home/ubuntu/ict-trading-bot/trade_journal.db \
  "SELECT * FROM backtest_results WHERE id = 42"'
```

---

## Failure modes

The consumer guarantees **the artifact reaches `answered` on every
path**, so a failed run never strands a `pending` request. Each mode
below lists the symptom (what the operator sees in Telegram) and the
audit trail (what lands in the validation log).

### 1. Unknown strategy (rejected at dispatch)

  - **Trigger:** `/test vwapp` (typo) or `/test turtle-soup` (wrong slug).
  - **Telegram reply:** `⚠️ Unknown strategy <name>. Registered: <roster>`.
  - **Artifact:** none — `cmd_test_strategy` short-circuits before
    minting the comms request.
  - **Validation log:** no row. The dispatch handler is the gate.
  - **Fix:** re-issue with a roster-listed name. Roster lives in
    `config/strategies.yaml` (currently `turtle_soup`, `vwap`).

### 2. Timeout

  - **Trigger:** the backtest subprocess exceeds `M5_BACKTEST_TIMEOUT_S`.
  - **Telegram reply:** `M5 backtest failed — <strategy>` … `error:
    BacktestTimeout: backtest exceeded <N>s …`.
  - **Validation log:** `outcome=timeout`, no `exit_code`.
  - **Fix:** raise `M5_BACKTEST_TIMEOUT_S` on the systemd unit and
    restart, OR shrink the input CSV.

### 3. Subprocess non-zero exit (data, code, env)

  - **Trigger:** `run_backtest_m5` raised — most commonly a missing
    `data/backtest_candles.csv`, a corrupt CSV (missing
    `timestamp`/`ohlcv` columns), or a runtime error inside
    `ICTBacktester.run`.
  - **Telegram reply:** structured error with truncated stderr
    (≤ 800 chars) so it renders in the bubble.
  - **Validation log:** `outcome=subprocess_failure`, `exit_code: <int>`,
    full stderr in the `error` field (capped by the runner's
    `_STDERR_TRUNCATE_CHARS`).
  - **Fix:** re-run the script manually on the VM to reproduce —

    ```bash
    ssh ict-trader-vm 'cd /home/ubuntu/ict-trading-bot && \
      M5_BACKTEST_TIMEOUT_S=300 \
      python -m src.backtest.run_backtest_m5 vwap'
    ```

### 4. Subprocess produced no JSON envelope

  - **Trigger:** the subprocess exited 0 but stdout was empty / not
    JSON / missing the `summary` dict (almost always a code regression
    in `run_backtest_m5`).
  - **Telegram reply:** structured `BacktestSubprocessFailure` with the
    parse error.
  - **Validation log:** `outcome=subprocess_failure`, no `exit_code`.
  - **Fix:** open a bug — the runner must always either exit non-zero
    or emit one JSON line as the last stdout line.

### 5. apply_answer push failure

  - **Trigger:** the comms `GitPusher` failed (network, rebase
    conflict, permission). The artifact still transitions to
    `answered` locally.
  - **Telegram reply:** sent normally (the local artifact is the
    source of truth; push is propagation).
  - **Validation log:** the run row lands; the push failure logs as a
    separate `error` event in `comms/log.ndjson`.
  - **Fix:** retry by editing the artifact's `status` back to
    `pending` won't help — the answer is already attached. Push will
    catch up on the next git-sync cycle automatically.

### 6. Stuck artifact (consumer disabled or down)

  - **Trigger:** `M5_CONSUMER_ENABLED` got unset, or the bot service
    is down. The artifact sits in `pending` past
    `stuck_alert_threshold` (default 24 h).
  - **Telegram reply:** none initially; the comms stuck-alert fires
    after the threshold ("`Comms request <id> is stuck …`").
  - **Validation log:** no row.
  - **Fix:** verify the env gate (`systemctl show
    ict-trader-telegram-bot | grep M5_CONSUMER_ENABLED`), restart the
    bot, OR cancel the artifact: edit the JSON, set
    `status: cancelled`, commit, push.

---

## Kill switch

```bash
# 1. Unset the gate.
ssh ict-trader-vm 'sudo systemctl edit --full ict-trader-telegram-bot.service'
# Remove `Environment=M5_CONSUMER_ENABLED=1` from the unit; save.

# 2. Restart so the consumer un-installs.
ssh ict-trader-vm 'sudo systemctl restart ict-trader-telegram-bot.service'

# 3. Verify.
ssh ict-trader-vm 'sudo systemctl show ict-trader-telegram-bot.service \
  | grep -i M5_CONSUMER_ENABLED'   # should be empty
```

After the kill switch, any subsequent `/test <strategy>` still mints
an artifact in `comms/requests/` (the dispatch path is independent of
the consumer). The artifact will sit pending until the gate is
re-enabled or it expires per its TTL.

---

## Smoke test (read-only)

Verify the closed loop without queueing real work:

```bash
ssh ict-trader-vm 'cd /home/ubuntu/ict-trading-bot && \
  python -c "
import json
from src.bot.test_strategy_consumer import default_run_backtest
result = default_run_backtest(\"vwap\")
print(json.dumps({\"db_row_id\": result.db_row_id, \"summary\": result.summary}, indent=2))
"'
```

Lands a row in `backtest_results`, prints the JSON envelope, no comms
or Telegram side-effects. The validation log is **not** written by
this path — only the consumer wraps a run with a log entry.

---

## Operator quick-reference

| Want to … | Do this |
|---|---|
| Run a backtest from Telegram | `/test <strategy>` (must be in `config/strategies.yaml`) |
| See the registered strategies | Type `/test bogus` — the rejection lists them |
| Read the latest run row | `sqlite3 trade_journal.db 'SELECT * FROM backtest_results ORDER BY id DESC LIMIT 1'` |
| Read the last 10 audit rows | `tail -n 10 runtime_logs/validation.jsonl \| jq .` |
| Disable the consumer | unset `M5_CONSUMER_ENABLED`, restart bot |
| Raise the timeout | bump `M5_BACKTEST_TIMEOUT_S` on the unit, restart |
| Cancel a pending request | edit `comms/requests/REQ-….json`, set `status: cancelled`, commit, push |

---

## See also

- [`comms/README.md`](../../comms/README.md) — operator-facing comms TL;DR (auto-consumer table at the top).
- [`docs/claude/comms-architecture.md`](../claude/comms-architecture.md) § 11 — the artifact contract.
- [`docs/runbooks/live-smoke-test.md`](live-smoke-test.md) — analogous shape for the live-trade smoke pipeline.
- M5 PRs: #637 (P1 closed loop), #639 (P2 hardening), this PR (P3 docs), and the queued P4 dashboard surface.
