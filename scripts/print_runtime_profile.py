import os

from dotenv import load_dotenv

from src.runtime.validation import build_settings_from_env, validate_startup


def main():
    # Load .env if present
    load_dotenv()

    # Build settings dict from environment
    settings = build_settings_from_env(os.environ)

    # Run startup validation; will raise RuntimeError if invalid
    validate_startup(settings)

    # Print a simple human-readable summary.
    # BUG-054: MODE / DRY_RUN / ALLOW_LIVE_TRADING removed — the single
    # dry/live toggle is per-account `mode:` in config/accounts.yaml.
    items = [
        f"EXCHANGE={os.environ.get('EXCHANGE', 'bybit')}",
        f"SYMBOL={settings.get('SYMBOL')}",
    ]
    print(" | ".join(items))


if __name__ == "__main__":
    main()
