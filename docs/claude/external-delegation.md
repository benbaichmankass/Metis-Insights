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
| LLM delegation (Gemini) | `tools/gemini_delegate.ipynb` in Colab |

## Gemini delegation workflow

Use `tools/gemini_delegate.ipynb` when a task requires Gemini's context window or multimodal capabilities:

1. Open the notebook in Colab.
2. Set the `GEMINI_API_KEY` Colab secret (key icon → Secrets).
3. Run Cell 2 (install + configure).
4. Paste the prompt into Cell 4 and run it.
5. Copy `/content/gemini_response.txt` back into the Claude Code session.

Secret key name: `GEMINI_API_KEY` (Colab userdata — never hardcode).

## Output convention

For delegated work, Claude should produce:

1. One script/notebook.
2. Input requirements.
3. Expected outputs.
4. Save location.
5. Copy-ready run commands.
