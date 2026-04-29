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
