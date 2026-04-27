# Security and secrets

## Never commit

- Telegram bot tokens.
- Telegram chat IDs if private.
- Bybit/Binance API keys or secrets.
- `.env` files.
- Colab userdata exports.
- SSH keys.

## Required storage

Use `.env`, Colab userdata, GitHub secrets, or VM environment variables.

## If a secret was committed

1. Revoke/rotate it first.
2. Remove or sanitize the current file.
3. Commit the cleanup.
4. Consider history rewrite only with explicit approval because it requires force-push coordination.

## Current known issue

The setup audit found committed Telegram and Bybit testnet credentials. Rotate them before pushing further changes.
