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

## VM reset / redeploy

> **WARNING:** A successful env render is *not* a green light to reset or redeploy the VM.
> A VM reset/redeploy requires a **separate audit** — running processes, in-flight orders,
> log/state retention, deployment artifacts, and on-host config must all be reviewed
> manually first. Do **not** assume any tool, notebook, or script has performed that audit
> on your behalf. There is no auto-reset path.

The `notebooks/setup/test_vwap_env_and_vm_readiness.ipynb` notebook only checks that
the VWAP env renders and that safety flags are correct. It does not call Bybit, place
orders, SSH, or restart the VM.

## VWAP BTCUSD profiles

Two profiles target the Bybit `vwap_strategy` subaccount:

- `vwap_btcusd_dry_run` — `MODE=PAPER`, `DRY_RUN=true`, `ALLOW_LIVE_TRADING=false`,
  uses live Bybit endpoint keys but never places orders. Default for VM dry-runs.
- `vwap_btcusd_live` — `MODE=LIVE`, `DRY_RUN=false`, `ALLOW_LIVE_TRADING=true`.
  Requires `--allow-live` on the renderer CLI.

Both pull credentials from `bybit.vwap_strategy.api_key` / `api_secret` in the master
secrets file. The strategy itself (`STRATEGY=vwap`) must be implemented and wired
into the runtime loop before it is meaningful at runtime — rendering the env does
not make the strategy executable.
