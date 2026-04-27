# Colab workflows

Use Colab for heavy analysis, backtests, and training.

## Standard notebook sections

1. Install dependencies.
2. Clone/update repo.
3. Load secrets from Colab userdata or prompts.
4. Run exactly one task.
5. Save outputs to Drive or Hugging Face.

## Secrets

Prefer:

```python
from google.colab import userdata
api_key = userdata.get("BYBIT_API_KEY")
```

Never hardcode keys in cells.
