# Backtest Trigger workflow (S-009 PR #1)

## How it works

```
Telegram /backtest <strategy>
         ↓
  Coordinator.trigger_backtest(strategy)
         ↓
  validator.trigger_backtest() writes JSON line to BACKTEST_QUEUE_PATH
  (default: /tmp/backtest-queue.json)
         ↓
  Alert pushed → dashboards queue (source="trading_school")
         ↓
  Colab / VM cron polls queue file → runs backtest → saves results
```

## Triggering from Python

```python
from src.core.coordinator import Coordinator

coord = Coordinator()
result = coord.trigger_backtest(
    "vwap",
    config={"symbol": "ETHUSDT", "timeframe": "4h", "start_date": "2026-01-01"},
)
# result = {"queued": True, "strategy": "vwap", "queue_path": "/tmp/backtest-queue.json", ...}
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKTEST_QUEUE_PATH` | `/tmp/backtest-queue.json` | Queue file path |
| `VM_USER` | `ubuntu` | VM SSH user |
| `VM_HOST` | *(empty)* | VM IP address |
| `REPO_DIR` | `/home/ubuntu/ict-trading-bot` | Repo path on VM |
| `SSH_KEY_FILE` | `ict-bot-ovm-private.key` | SSH key filename |

## Running in Colab (PM copy-paste instructions)

1. Open `notebooks/templates/triggered-backtest.ipynb` in Google Colab
2. Upload your SSH key file (`ict-bot-ovm-private.key`) to the Colab session
3. Verify Cell 1 config values (VM_HOST etc.)
4. Run Cell 2 to pull the current queue from the VM
5. Run Cell 3 to execute the most recent backtest job
6. Run Cell 4 to clear the queue after success

## Queue file format

Each line is a JSON object:
```json
{
  "strategy": "vwap",
  "symbol": "BTCUSDT",
  "timeframe": "1h",
  "start_date": "2026-01-01",
  "end_date": null,
  "queued_at": "2026-04-29T12:00:00+00:00",
  "vm_user": "ubuntu",
  "vm_host": "...",
  "repo_dir": "/home/ubuntu/ict-trading-bot",
  "ssh_key": "ict-bot-ovm-private.key"
}
```

## Rules
- Never block on backtest completion — the trigger is fire-and-forget
- Queue file is append-only; Colab/cron clears it after processing
- All backtest results must go to `backtest_results` DB table (not live DB)
