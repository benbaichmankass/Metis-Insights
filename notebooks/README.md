# Notebook templates

Use notebooks for delegated work that should run in Colab or Hugging Face instead of Claude Code.

Rules:

- One notebook = one task.
- Never hardcode secrets.
- Save outputs to Drive or Hugging Face.
- Commit templates, not generated outputs.
- **Never pull market data from Binance or any other key-gated exchange in a
  notebook.** Use hand-crafted DataFrames, repo fixtures, or open keyless
  sources (Bybit public REST, Coinbase public, Kraken public, CryptoCompare,
  yfinance, or our Hugging Face datasets). See
  [`docs/claude/testing-policy.md`](../docs/claude/testing-policy.md#test-data-sources-read-first)
  for the full policy.

## Templates

- `templates/colab_template.ipynb` — minimal generic one-task scaffold.
- `templates/hf_dataset_push.ipynb` — push a dataset to Hugging Face.
- `templates/triggered-backtest.ipynb` — process a backtest job from the VM queue.

**Note:** Training/improvement runs no longer use Colab (free Colab
disconnects after ~90 min idle, breaking "fire and forget"). They run
via GitHub Actions instead — see
[`docs/claude/training-improvement-workflow.md`](../docs/claude/training-improvement-workflow.md)
and `.github/workflows/training-run.yml`. Hypotheses are committed as
`experiments/<run-id>/hypotheses.py`, not as notebooks.
