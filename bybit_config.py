\"\"\"Legacy compatibility shim.

Do not hardcode Telegram or exchange credentials here.
Set values in .env, Colab userdata, GitHub secrets, or VM environment variables.
\"\"\"
from __future__ import annotations

import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
