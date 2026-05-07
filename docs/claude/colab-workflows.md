# Colab workflows

Use Colab for heavy analysis, backtests, and training.

## Operator VM steps — Colab notebooks are the default channel (MANDATORY)

When Claude needs the operator to perform a manual action on the trading VM
(set an env var, restart a service, rotate a key, flip a flag, edit a config
file), the **default deliverable is a one-click Colab notebook** under
`notebooks/operator/`, NOT a markdown checklist of CLI commands.

Why: the operator's stable interaction surface with the VM is Colab — Drive
holds the SSH key, Colab Secrets hold connection details, and a single
*Runtime → Run all* delivers reproducible, auditable steps. Text instructions
shift the burden of correctness to the operator and don't leave a re-runnable
artifact when the same action needs to repeat (rollback, second VM, recovery).

### Rules

1. Each operator-VM action gets its own notebook in `notebooks/operator/`.
   Name it `<verb>_<thing>.ipynb` (e.g. `enable_comms_channel.ipynb`,
   `rotate_api_keys.ipynb`).
2. Follow the structure already in `rotate_api_keys.ipynb`:
   - **Cell 1 (markdown)** — what it does, required Colab Secrets,
     SSH key location, security note.
   - **Cell 2** — mount Drive (one-click *Allow* dialog).
   - **Cell 3** — locate the SSH key (Drive → file-picker fallback).
   - **Cell 4** — load + validate Colab Secrets.
   - **Cell 5 (optional)** — a markdown header + a single small "configure"
     cell with toggles (e.g. `ENABLE_X = True`). Operator edits this if they
     want to flip behaviour.
   - **Cell 6** — apply: SSH in, do the change idempotently (read → patch →
     atomic write back), restart the affected service, verify `is-active`,
     wipe the tempdir-copied SSH key in a `finally` block.
   - **Cell 7 (optional)** — smoke test that exercises the change end-to-end.
   - **Cell 8 (markdown)** — verification steps, rollback instructions,
     when to re-run.
3. **Idempotent** — re-running must be safe. Patch single lines in
   config files; do not overwrite full files unless that's the
   notebook's whole job (key rotation does, because every key changes).
4. **Never print, log, or commit secret values.** A `_redact()` helper that
   strips known secret strings from stderr is the standard guard. The SSH
   key gets `0600` perms in a tempdir for the duration of the SSH call only.
5. **Default Colab Secrets** that every operator-VM notebook should rely on:
   - `VM_SSH_HOST`, `VM_SSH_USER` — connection details.
   - `SSH_KEY_FILE` (optional) — override for non-default key filename.
   Per-task secrets (API keys, tokens) get added on top.
6. **Commit message convention.** When Claude opens the PR introducing a
   new operator notebook, the title prefix is `feat(ops):` so the
   notify pipeline doesn't mistake it for a code change.
7. **Colab open link is part of every delivery (MANDATORY).** Any time
   Claude *delivers* an operator notebook to the operator — in a chat
   reply, Telegram ping, PR body, checkpoint handoff, or sprint summary —
   the message **must include the Colab open URL**, not just the GitHub
   URL. The operator clicks that link and the notebook opens in Colab
   ready to *Runtime → Run all*; pasting only a GitHub blob/raw URL
   forces a manual download-and-upload every time and is a workplan
   violation. Format:

   ```
   https://colab.research.google.com/github/<owner>/<repo>/blob/<branch>/<path>
   ```

   For this repo with a notebook on `main`:

   ```
   https://colab.research.google.com/github/the-lizardking/ict-trading-bot/blob/main/notebooks/operator/<file>.ipynb
   ```

   For a notebook on a feature branch (during PR review), substitute the
   branch name for `main`. The "Existing operator notebooks" table below
   carries the canonical link for every notebook on `main`; copy from
   there rather than re-deriving the URL by hand.

### Anti-patterns (don't do these)

- ❌ "Run these commands on the VM:" followed by a code block. The
  operator has to copy-paste, the SSH connection details are implicit,
  and the action isn't reproducible.
- ❌ Linking only the GitHub blob URL of an operator notebook. The
  operator has to find the *Open in Colab* button (which doesn't always
  exist on a draft-PR branch). Always include the
  `colab.research.google.com/github/...` URL — see Rule 7.
- ❌ A notebook that overwrites the entire `.env`. That clobbers
  values rotated by other notebooks. Patch the lines you own.
- ❌ A notebook that requires the operator to type values into prompts
  during the run. Configuration goes in a single editable cell at the
  top — *Runtime → Run all* must be the only required action.
- ❌ Hardcoding the VM hostname / SSH user / key path in the notebook
  source. Always read them from Colab Secrets / Drive.

### Existing operator notebooks

Each row links to the **Colab open URL on `main`** — the link the operator
should click. Switch the branch in the URL when the notebook is still on a
feature branch under review.

| Notebook | What it does | Colab open link |
|---|---|---|
| `rotate_api_keys.ipynb` | Generate a fresh `.env` + `.env.live` from Colab Secrets, push to VM, restart trader + bot | [open](https://colab.research.google.com/github/the-lizardking/ict-trading-bot/blob/main/notebooks/operator/rotate_api_keys.ipynb) |
| `enable_comms_channel.ipynb` | Idempotently flip `COMMS_PUSH_ENABLED` in the bot service `.env`, restart `ict-telegram-bot`, optional smoke test | [open](https://colab.research.google.com/github/the-lizardking/ict-trading-bot/blob/main/notebooks/operator/enable_comms_channel.ipynb) |
| `cleanup_ghost_trades.ipynb` | One-shot manual remediation for DB-open trades whose exchange position is flat (BUG-041 family) | [open](https://colab.research.google.com/github/the-lizardking/ict-trading-bot/blob/main/notebooks/operator/cleanup_ghost_trades.ipynb) |
| `sweep_unlinked_packages.ipynb` | Orphan `order_packages` rows with `linked_trade_id IS NULL` to unblock the BUG-046 gate (BUG-049 hotfix) | [open](https://colab.research.google.com/github/the-lizardking/ict-trading-bot/blob/main/notebooks/operator/sweep_unlinked_packages.ipynb) |
| `diagnose_live_trading.ipynb` | Pull a structured live-trading health snapshot from the VM | [open](https://colab.research.google.com/github/the-lizardking/ict-trading-bot/blob/main/notebooks/operator/diagnose_live_trading.ipynb) |
| `deploy_latest_main.ipynb` | One-click `git pull` on the VM and restart of every `ict-*` service | [open](https://colab.research.google.com/github/the-lizardking/ict-trading-bot/blob/main/notebooks/operator/deploy_latest_main.ipynb) |
| `push_notebook_to_repo.ipynb` | Stage + commit + push a notebook from Colab back to the repo | [open](https://colab.research.google.com/github/the-lizardking/ict-trading-bot/blob/main/notebooks/operator/push_notebook_to_repo.ipynb) |
| `update_branch_protection.ipynb` | Sync GitHub branch-protection rules from the repo's canonical config | [open](https://colab.research.google.com/github/the-lizardking/ict-trading-bot/blob/main/notebooks/operator/update_branch_protection.ipynb) |
| `debug_vwap_bybit2.ipynb` | Diagnose VWAP→bybit_2 silencing across the 13 documented gates; gated remediation for the strategy-monocle stuck-state | [open](https://colab.research.google.com/github/the-lizardking/ict-trading-bot/blob/main/notebooks/operator/debug_vwap_bybit2.ipynb) |

Add a row here (with the Colab open link) when you add a notebook.

---

## Standard notebook sections (analysis / backtests / training)

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
