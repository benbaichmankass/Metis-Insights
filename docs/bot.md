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

### Paper Trading Mode
- `/paper_start` - Start paper trading
- `/paper_stop` - Stop paper trading
- `/paper_report` - Paper trading report

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
- [ ] Paper/live mode separation
- [ ] Risk management commands
