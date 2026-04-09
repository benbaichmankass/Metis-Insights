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

    # Print a simple human-readable summary
    items = [
        f"EXCHANGE={os.environ.get('EXCHANGE', 'bybit')}",
        f"MODE={settings.get('MODE')}",
        f"DRY_RUN={settings.get('DRY_RUN')}",
        f"ALLOW_LIVE_TRADING={os.environ.get('ALLOW_LIVE_TRADING', '')}",
        f"SYMBOL={settings.get('SYMBOL')}",
    ]
    print(" | ".join(items))


if __name__ == "__main__":
    main()
