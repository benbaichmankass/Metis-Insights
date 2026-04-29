# Colab workflows

Use Colab for heavy analysis, backtests, and training.

## Standard notebook sections

1. Install dependencies.
2. Clone/update repo.
3. Load secrets from Colab userdata or prompts.
4. Run exactly one task.
5. Save outputs to Drive or Hugging Face.

## Market data in Colab

Do **not** pull candles from Binance in a Colab notebook. Binance endpoints
are blocked or key-gated from Colab IPs and have caused repeated test
failures. Use, in order:

1. Hand-crafted DataFrames or repo fixtures.
2. Open keyless sources: Bybit public REST, Coinbase public, Kraken public,
   CryptoCompare, yfinance.
3. Pre-mirrored datasets on our Hugging Face org.

See [`testing-policy.md`](testing-policy.md#test-data-sources-read-first) for
the full rules.

## Secrets

Prefer:

```python
from google.colab import userdata
api_key = userdata.get("BYBIT_API_KEY")
```

Never hardcode keys in cells.

## Gemini delegate notebook

`tools/gemini_delegate.ipynb` — wraps `google-generativeai` for prompt delegation.

- Secret name: `GEMINI_API_KEY` (Colab userdata).
- Model: `gemini-2.0-pro-exp` (update cell if model changes).
- Output: `/content/gemini_response.txt` — copy back to Claude Code session.
