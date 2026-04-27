# ICT Trading Bot - Project Overview

## Architecture Summary
- Python-based cryptocurrency trading bot
- ICT (Inner Circle Trader) methodology strategies
- VWAP mean reversion strategies
- ML model integration for signal generation
- Telegram bot for monitoring and alerts
- Deployed on Oracle Cloud Infrastructure

## Repository Structure
```
ict-trading-bot/
  config/       - Configuration files
  data/         - Historical and live market data
  deploy/       - Deployment scripts and configs
  docs/         - Documentation
  logs/         - Runtime logs
  ml/           - Machine learning models
  runtime_logs/ - Live trading logs
  scripts/      - Utility scripts
  src/          - Main source code
  strategies/   - Trading strategy implementations
  tests/        - Test suite
```

## Key Commands
- `python src/main.py` - Run the bot
- `python scripts/backtest.py` - Run backtests
- `./run_telegram_bot.sh` - Start Telegram monitoring
- `./run_trader.sh` - Start live trading

## Environment
- Python 3.x
- Bybit API (primary exchange)
- Oracle Cloud VM deployment
- Docker support available


## Claude Code Issue: Stream Timeout During Git Push

### Problem Description
When using Claude Code to push files to GitHub, the connection times out with `API Error: Stream idle timeout - partial response received`. The MCP (Model Context Protocol) git provider experiences repeated stream timeouts, preventing commits from being pushed to the remote repository.

### Root Cause
Claude Code's git operations are performed through MCP integration. Large file operations or slow network conditions can cause the stream to idle. The MCP stream timeout is hardcoded and occurs before git operations complete. The web interface cannot reliably display git operation progress or error recovery.

### Error Signatures
- `API Error: Stream idle timeout - partial response received`
- `fatal: unable to access 'https://github.com/...': The requested URL returned error: 403`
- Git operations hang indefinitely without clear completion status

### Solution
**Bypass Claude Code's git limitations by using GitHub's web editor directly:**

1. Create the branch on GitHub first using the branch dropdown
2. Create files via GitHub web interface with paths like `docs/strategies/ict.md`
3. Use GitHub's "Compare & pull request" button for PR creation
4. Merge directly through GitHub's web interface

This avoids MCP stream timeouts entirely and provides immediate feedback on success.
