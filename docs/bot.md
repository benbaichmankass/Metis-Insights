# Trading Bot Command Tree

## Current Command Structure

### Trading Commands
- `/start` - Start the bot
- `/stop` - Stop the bot
- `/status` - Show current status
- `/signals` - Show latest signals
- `/positions` - Show open positions
- `/balance` - Show account balance

### Strategy Commands
- `/ict` - ICT strategy controls
- `/vwap` - VWAP strategy controls
- `/backtest` - Run backtest
- `/optimize` - Optimize parameters

### System Commands
- `/deploy` - Deploy to production
- `/logs` - Show recent logs
- `/health` - System health check
- `/config` - View/edit config

## Target Command Structure

> This bot trades live on real exchange accounts. There is no paper-trading
> mode. The trading commands below operate on the single live trader.

### Live Trading Mode
- `/live_start` - Start live trading
- `/live_stop` - Stop live trading
- `/live_report` - Live trading report

### Multi-Strategy Support
- `/strategy list` - List all strategies
- `/strategy enable <name>` - Enable strategy
- `/strategy disable <name>` - Disable strategy
- `/strategy params <name>` - View parameters

### Risk Management
- `/risk set <value>` - Set risk per trade
- `/risk daily_limit <value>` - Set daily loss limit
- `/risk show` - Show risk settings

## Implementation Status
- [x] Basic command structure defined
- [x] Telegram bot integration
- [ ] Multi-strategy routing
- [ ] Risk management commands


## Operational visibility

Three channels give the operator passive and active observability without SSH:

### 1. `/status` slash command
Telegram slash command handled by `src/bot/telegram_query_bot.py`.
Returns current bot status on demand.

### 2. Daily heartbeat (automated)
`scripts/daily_heartbeat.py` posts once daily at **13:00 UTC** (16:00 IDT —
post NY open) via the `ict-heartbeat.timer` systemd unit.

Message format:
```
📊 Daily heartbeat — YYYY-MM-DD
🚦 Kill-switch: 🟢 RUNNING | 🔴 HALTED
📂 Open positions: N
💰 Today's PnL: $±X.XX
📰 News layer: disabled | enabled-no-key | enabled-active
🕐 Last tick: <ts>  (HH:MM ago)
```

**Install on the VM** (run once via SSH):
```bash
sudo cp deploy/ict-heartbeat.service /etc/systemd/system/
sudo cp deploy/ict-heartbeat.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ict-heartbeat.timer
# Verify:
sudo systemctl list-timers ict-heartbeat.timer
```

### 3. News-veto notifications (event-driven)
When the news veto gate fires, `run_pipeline` sends an immediate Telegram
push (`feat/news-veto-telegram-notify`, PR #68):
```
🚫 News veto: <reason>
Symbol: <sym> | Side: <side> | Qty: <qty>
Adj: <adjustment> | Items: <item_count>
```
This fires in addition to the regular pipeline-result notification.
