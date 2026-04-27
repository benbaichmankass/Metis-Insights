# External delegation

Claude Code should orchestrate heavy work, not perform it locally.

## Decision table

| Work | Best place |
|---|---|
| Small code edits | Local repo |
| Unit tests / collection | Local repo |
| Full backtests | Colab or Oracle VM |
| ML training | Colab GPU/CPU or Hugging Face |
| Dataset storage | Hugging Face datasets or Drive |
| Model registry | Hugging Face models |
| Live bot runtime | Oracle VM |
| Exploratory data analysis | Colab / Google AI Studio |

## Output convention

For delegated work, Claude should produce:

1. One script/notebook.
2. Input requirements.
3. Expected outputs.
4. Save location.
5. Copy-ready run commands.
