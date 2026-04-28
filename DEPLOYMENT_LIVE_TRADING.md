# Live Trading Deployment Guide

**Last Updated**: April 13, 2026  
**Status**: Bot is running on Oracle VM (129.159.83.68) - currently monitoring, not trading

---

## Current Status

✅ **Working**:
- Oracle VM healthy and running
- Telegram bot responding to commands (`/status`, `/price`, `/log`)
- Kill zone detection active (London Open, NY Open)
- Trend analysis working (BULLISH/BEARISH/NEUTRAL detection)
- FVG and Order Block signal generation confirmed
- Alert system sending Telegram notifications

⚠️ **Blockers Fixed (pending deployment)**:
- ✅ `scripts/init_db.py` created - ready to initialize trade journal DB
- ⏳ `/closeall` emergency command - code ready, needs merge
- ⏳ DB auto-initialization - code ready, needs merge

⚠️ **Remaining Before Live**:
- Initialize `trade_journal.db` (fixes `/last5` and `/latest_backtest` errors)
- Verify Bybit LIVE API keys are loaded (not testnet)
- Set `.env` to live trading mode
- Restart both services

---

## Pre-Deployment Checklist

### 1. Database Initialization

**Problem**: `/last5` returns `no such table: trades`  
**Fix**: Run the DB init script

```bash
cd /home/ubuntu/ict-trading-bot
python scripts/init_db.py
```

**Expected output**:
```
Initialising database at: /home/ubuntu/ict-trading-bot/src/bot/trade_journal.db
  [OK] trades table ready.
  [OK] backtest_results table ready.
Database initialisation complete.
```

**Verify**:
```bash
ls -lh src/bot/trade_journal.db
```

You should see the DB file created (~20KB).

---

### 2. Update Code from GitHub

Pull the latest commits with `/closeall` and DB auto-init:

```bash
cd /home/ubuntu/ict-trading-bot
git pull origin main
```

**What this includes**:
- `scripts/init_db.py` - DB initialization
- Updated `telegram_query_bot.py` with:
  - `/closeall` emergency flatten-all command
  - Auto DB initialization on bot startup
  - Improved error handling

---

### 3. Verify Bybit API Keys

**Critical**: Confirm your `.env` uses **LIVE** Bybit keys, not testnet.

```bash
cd /home/ubuntu/ict-trading-bot
grep "BYBIT_API" .env
```

**Check**:
- Do the keys start with a recognizable prefix?
- Did you create them on bybit.com (live) or testnet.bybit.com?

**Test the keys**:
```bash
python -c "from dotenv import load_dotenv; import os; from pybit.unified_trading import HTTP; load_dotenv(); client = HTTP(testnet=False, api_key=os.getenv('BYBIT_API_KEY'), api_secret=os.getenv('BYBIT_API_SECRET')); print(client.get_wallet_balance(accountType='UNIFIED'))"
```

If this returns wallet data (even $0 balance), your keys are valid for LIVE.

---

### 4. Configure Live Trading Mode

Edit `/home/ubuntu/ict-trading-bot/.env`:

```bash
nano /home/ubuntu/ict-trading-bot/.env
```

**Required changes**:
```bash
# Exchange and mode
EXCHANGE=bybit
MODE=live

# Safety interlocks - BOTH must be set to enable live trading
DRY_RUN=false
ALLOW_LIVE_TRADING=true

# Position sizing
SYMBOL=BTC/USDT:USDT
RISK_PER_TRADE=0.01
MAX_QTY=0.001  # Start SMALL - this is ~$70 per trade at current BTC price

# Runtime
TICK_INTERVAL_SECONDS=900  # 15 minutes between strategy ticks
LOOP=true
```

**Save**: `Ctrl+O`, `Enter`, `Ctrl+X`

---

### 5. Restart Services

**Check current status**:
```bash
sudo systemctl status ict-trader
sudo systemctl status ict-telegram-bot
```

**Restart both**:
```bash
sudo systemctl restart ict-trader
sudo systemctl restart ict-telegram-bot
```

**Verify logs**:
```bash
# Trader loop
sudo journalctl -u ict-trader -f --since "1 minute ago"

# Telegram bot
sudo journalctl -u ict-telegram-bot -f --since "1 minute ago"
```

**Expected in trader log**:
```
Startup validation passed. EXCHANGE=bybit MODE=live DRY_RUN=false ALLOW_LIVE_TRADING=true
Kill Zone: London Open 1H Trend: BULLISH Price: $71,500.00
```

---

### 6. Test Telegram Commands

Open Telegram and test all commands:

```
/status    → Should show bot LIVE
/balance   → Should show Bybit wallet balance
/price     → Current BTC/USDT price
/trades    → Open positions (should be empty initially)
/last5     → Should work now (after DB init)
/closeall  → Emergency flatten (should confirm "no positions")
```

---

## Live Trading Monitoring

### First 24 Hours

**Watch for**:
1. Signal generation during kill zones (London Open 08:00-11:00 UTC, NY Open 13:00-16:00 UTC)
2. Order placement confirmation in Telegram
3. Stop-loss and take-profit order creation
4. Position management and exits

**Monitor**:
```bash
# Live tail trader log
tail -f /home/ubuntu/ict-trading-bot/bot.log

# Or via systemd
sudo journalctl -u ict-trader -f
```

**Telegram monitoring**:
- Every signal should trigger a Telegram notification
- Every order should be reported
- Errors should be sent immediately

**Emergency stop**:
```
/closeall  → Immediately flatten all positions
```

Then:
```bash
sudo systemctl stop ict-trader
```

---

## Risk Management

**Position Sizing**:
- `MAX_QTY=0.001` BTC ≈ $70 per trade at current prices
- `RISK_PER_TRADE=0.01` = 1% of account per trade
- Recommended starting balance: $1000+ on Bybit

**Daily Loss Cap** (to add manually):
- Set a personal daily loss limit (e.g., 3% of account)
- Stop trading if reached
- Use `/closeall` + `sudo systemctl stop ict-trader`

**Position Limits**:
- Current code allows 1 position at a time
- No hedging or multiple simultaneous entries

---

## Troubleshooting

### Bot not placing orders

**Check**:
```bash
grep "DRY_RUN\|ALLOW_LIVE" /home/ubuntu/ict-trading-bot/.env
```

Must show:
```
DRY_RUN=false
ALLOW_LIVE_TRADING=true
```

**Check logs**:
```bash
sudo journalctl -u ict-trader | grep -i "dry\|allow\|validation"
```

### `/last5` still broken

**Re-run DB init**:
```bash
python /home/ubuntu/ict-trading-bot/scripts/init_db.py
```

**Check DB exists**:
```bash
ls -lh /home/ubuntu/ict-trading-bot/src/bot/trade_journal.db
```

### `/balance` returns "No balance found"

**Possible causes**:
1. Using testnet keys on live mode
2. Account not funded
3. Wrong account type

**Fix**: Verify keys are for bybit.com (live), not testnet.bybit.com

### Oracle VM "Something went wrong"

This is a temporary Oracle Cloud infrastructure issue. The VM is still running (confirmed by Telegram bot responding). Wait 30 minutes or restart the instance via Oracle Console.

---

## Success Criteria

Before considering the bot "live ready":

- [ ] `python scripts/init_db.py` completes successfully
- [ ] `/last5` and `/latest_backtest` commands work
- [ ] `/balance` shows real Bybit balance (even if $0)
- [ ] `/closeall` command is available
- [ ] `.env` set to `MODE=live`, `DRY_RUN=false`, `ALLOW_LIVE_TRADING=true`
- [ ] Both systemd services running and healthy
- [ ] Trader log shows "ALLOW_LIVE_TRADING=true" on startup
- [ ] Telegram notifications working for every tick
- [ ] At least 1-2 days of dry-run on a small live account observed with no crashes (`DRY_RUN=true`, `ALLOW_LIVE_TRADING=false`; orders logged with status `"dry_run"` and never submitted)

---

## Next Steps After Deployment

1. **Monitor first 24 hours closely**
2. **Log every trade in a spreadsheet** for manual review
3. **Set a daily review time** (e.g., 18:00 UTC after NY close)
4. **Track**:
   - Win rate
   - Average win vs average loss
   - Drawdown
   - Sharpe ratio
5. **Iterate**:
   - Adjust `RISK_PER_TRADE` based on performance
   - Refine entry filters if too many signals
   - Add more kill zones if missing opportunities

---

## Emergency Contacts

- **Telegram Bot**: @bict_trading_bot
- **Bybit Support**: https://www.bybit.com/en/help-center
- **Oracle Support**: Cloud Console → Support

---

**Last commit**: Check `git log -1` for latest changes
