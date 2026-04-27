# Security and secrets

## Never commit

- Telegram bot tokens.
- Telegram chat IDs if private.
- Bybit/Binance API keys or secrets.
- Bybit subaccount API keys (e.g. `vwap_strategy`, `current_account`) — these live
  only in the encrypted master secrets file and are never to be committed in plaintext.
- `.env` files.
- Colab userdata exports.
- SSH keys.
- `master-secrets.yaml` (plaintext master secrets file).
- `master-secrets.sops.yaml` (encrypted master secrets — contains ciphertext but keep it in Drive only).
- `age-keys.txt` (age private key — keep only in Drive and a password manager).
- Any generated `.env.*` files — these are runtime artifacts, not source files.

## Required storage

Use `.env`, Colab userdata, GitHub secrets, or VM environment variables.

For encrypted-at-rest master secrets, use the Google Drive SOPS workflow:
see `docs/claude/google-drive-master-secrets.md`.

## Claude must not

- Read or print decrypted secret values.
- Log, echo, or expose the contents of any `.env*` file.
- Print the output of `sops --decrypt` beyond confirming success/failure.
- Commit generated `.env.*` files — they are runtime-only artifacts.

## Generated env files

Files produced by `scripts/render_env_from_master.py` are:

- Written with `chmod 0600` (owner read/write only).
- Listed in `.gitignore` — never tracked.
- Deleted from Colab after copying to the target host.

## If a secret was committed

1. Revoke/rotate it first.
2. Remove or sanitize the current file.
3. Commit the cleanup.
4. Consider history rewrite only with explicit approval because it requires force-push coordination.

## Current known issue

The setup audit found committed Telegram and Bybit testnet credentials. Rotate them before pushing further changes.
