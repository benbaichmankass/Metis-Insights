from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

from src.runtime.validation import build_settings_from_env, validate_startup


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("src.main")


def run_bot(settings: dict) -> None:
    """
    Temporary Thread 2 runner.
    Replace internals later with the repo's real analysis -> signal -> order flow.
    """
    logger.info(
        "Bot runtime ready | mode=%s symbol=%s timeframe=%s dry_run=%s",
        settings.get("MODE"),
        settings.get("SYMBOL"),
        settings.get("TIMEFRAME"),
        settings.get("DRY_RUN"),
    )
    logger.info("Next step: wire this entrypoint into the actual bot modules.")


def main() -> None:
    load_dotenv()
    settings = build_settings_from_env(os.environ)
    validate_startup(settings)
    logger.info("Startup validation passed.")
    run_bot(settings)


if __name__ == "__main__":
    main()
