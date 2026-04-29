"""Upload large repo assets to Hugging Face.

Run once after cloning / whenever the local files are refreshed:

    python scripts/hf_upload_large_files.py

Requires:
  - huggingface_hub installed  (pip install huggingface_hub)
  - HF token with write access: huggingface-cli login
    OR set the HF_TOKEN environment variable.

What gets uploaded:
  dataset  bentzbk/ict-trading-bot-btcusdt-1m
    data/bybit_btcusdt_1m.csv          → bybit_btcusdt_1m.csv
    ml/data/raw/btcusdt_1m.csv         → btcusdt_1m.csv

  model    bentzbk/ict-trading-bot-rf-breakout-v1
    ml/models/local/btc_breakout_confirmation_v1.joblib
                                        → btc_breakout_confirmation_v1.joblib

After confirming uploads succeed, the local copies can be removed with:
    git rm data/bybit_btcusdt_1m.csv ml/data/raw/btcusdt_1m.csv \\
           ml/models/local/btc_breakout_confirmation_v1.joblib
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

try:
    from huggingface_hub import HfApi, create_repo
except ImportError:
    print("ERROR: huggingface_hub not installed. Run: pip install huggingface_hub")
    sys.exit(1)

api = HfApi()

UPLOADS = [
    # (local_path, hf_repo_id, repo_type, path_in_repo)
    (
        REPO_ROOT / "data" / "bybit_btcusdt_1m.csv",
        "bentzbk/ict-trading-bot-btcusdt-1m",
        "dataset",
        "bybit_btcusdt_1m.csv",
    ),
    (
        REPO_ROOT / "ml" / "data" / "raw" / "btcusdt_1m.csv",
        "bentzbk/ict-trading-bot-btcusdt-1m",
        "dataset",
        "btcusdt_1m.csv",
    ),
    (
        REPO_ROOT / "ml" / "models" / "local" / "btc_breakout_confirmation_v1.joblib",
        "bentzbk/ict-trading-bot-rf-breakout-v1",
        "model",
        "btc_breakout_confirmation_v1.joblib",
    ),
]


def main() -> None:
    seen_repos: set[tuple[str, str]] = set()
    for local_path, repo_id, repo_type, path_in_repo in UPLOADS:
        if not local_path.exists():
            print(f"SKIP  {local_path} — file not found locally")
            continue

        # Create repo if first time we see it
        if (repo_id, repo_type) not in seen_repos:
            try:
                create_repo(repo_id=repo_id, repo_type=repo_type, exist_ok=True, private=False)
            except Exception as exc:
                print(f"WARN  Could not ensure repo {repo_id} exists: {exc}")
            seen_repos.add((repo_id, repo_type))

        size_mb = local_path.stat().st_size / 1_048_576
        print(f"UP    {local_path.relative_to(REPO_ROOT)}  ({size_mb:.1f} MB)"
              f"  →  {repo_type}/{repo_id}/{path_in_repo}")
        try:
            api.upload_file(
                path_or_fileobj=str(local_path),
                path_in_repo=path_in_repo,
                repo_id=repo_id,
                repo_type=repo_type,
            )
            print(f"OK    {repo_id}/{path_in_repo}")
        except Exception as exc:
            print(f"FAIL  {repo_id}/{path_in_repo}: {exc}")
            sys.exit(1)

    print("\nAll uploads complete.")
    print("Verify on HF, then run:")
    print("  git rm data/bybit_btcusdt_1m.csv ml/data/raw/btcusdt_1m.csv \\")
    print("         ml/models/local/btc_breakout_confirmation_v1.joblib")


if __name__ == "__main__":
    main()
