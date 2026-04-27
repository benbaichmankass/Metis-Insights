# Deployment ops

## Default stance

Do not deploy, restart, or live-trade unless explicitly asked.

## Before live changes

```bash
git status -sb
python scripts/secret_scan.py
PYTHONPATH=. pytest --collect-only -q tests
```

## Paper to live checklist

- Confirm `MODE`.
- Confirm `DRY_RUN`.
- Confirm `ALLOW_LIVE_TRADING`.
- Confirm exchange keys are environment variables.
- Confirm Telegram emergency stop works.
