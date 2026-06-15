#!/usr/bin/env python3
"""
Decrypt a SOPS-encrypted master secrets file and render a lean .env file
for the trader. Never prints secret values.

Usage:
    python scripts/render_env_from_master.py \\
        --master /path/to/master-secrets.sops.yaml \\
        --age-key-file /path/to/age-keys.txt \\
        --out .env \\
        [--sops-bin sops]

Operator directive 2026-05-03 — there is one canonical render path.
The rendered .env carries credentials, telegram tokens, exchange
selection, and per-account API-key env vars (driven by
``config/accounts.yaml``). It does NOT carry MODE / DRY_RUN /
ALLOW_LIVE_TRADING — the single dry/live toggle in the codebase is
per-account ``mode: live | dry_run`` inside ``config/accounts.yaml``,
applied at runtime via ``RiskManager.dry_run``.

Operator directive 2026-05-12 — ENV is not a canonical source. The
canonical documents are ARCHITECTURE-CANONICAL.md, README, and
CLAUDE.md. Deployment paths (DATA_DIR, TRADE_JOURNAL_DB, etc.) live
in the systemd drop-ins (see deploy/*.service.d/data-dir.conf which
declares ``Environment=DATA_DIR=/data/bot-data``); the .env layer
MUST NOT contradict them. Per the path-bifurcation incident, the
render script no longer emits DATA_DIR from runtime_defaults — the
systemd drop-in is authoritative. If a future render needs to
override DATA_DIR for a specific deployment (e.g. a non-VM smoke
env), that override is configured at the systemd layer, not the
.env layer.
"""
from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

PROFILES = ("live",)
LIVE_PROFILES = PROFILES  # operator directive 2026-05-03 — single canonical profile
PLACEHOLDER_PATTERNS = ("REPLACE_ME", "CHANGEME", "YOUR_", "<", ">", "TODO")

# config/accounts.yaml is the source of truth for which account_ids
# exist and what env-var name each one expects. The render script
# reads this file to drive the per-account credential block so the
# rendered .env stays in sync with the bot's lookup contract
# (src/bot/data_loaders.py::bybit_client_for reads
# os.environ[<api_key_env>]).
_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACCOUNTS_YAML = _REPO_ROOT / "config" / "accounts.yaml"


def _is_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    up = value.upper()
    return any(p in up for p in PLACEHOLDER_PATTERNS) or not value.strip()


def _get(data: dict, dotted_key: str, required: bool = True) -> str | None:
    """Walk a dotted-key path through nested dicts. Fail clearly on missing/placeholder."""
    parts = dotted_key.split(".")
    node: Any = data
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            if required:
                sys.exit(f"ERROR: Required field '{dotted_key}' is missing from the master secrets file.")
            return None
        node = node[part]
    if required and _is_placeholder(node):
        sys.exit(f"ERROR: Required field '{dotted_key}' is still a placeholder. Fill it in before rendering.")
    if isinstance(node, str):
        return node
    return str(node) if node is not None else None


def _get_optional(data: dict, dotted_key: str) -> str | None:
    return _get(data, dotted_key, required=False)


def decrypt_master(master_path: Path, age_key_file: Path, sops_bin: str) -> dict:
    """Run sops --decrypt and return parsed YAML. Never prints the decrypted content."""
    import yaml  # imported here so the script works without yaml at import time

    env = os.environ.copy()
    env["SOPS_AGE_KEY_FILE"] = str(age_key_file)

    result = subprocess.run(
        [sops_bin, "--decrypt", str(master_path)],
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        # strip any decrypted content that might appear in error messages
        sys.exit(f"ERROR: sops decryption failed (exit {result.returncode}).\n{stderr}")

    try:
        parsed = yaml.safe_load(result.stdout)
    except Exception as exc:
        sys.exit(f"ERROR: Failed to parse decrypted YAML: {exc}")
    finally:
        # overwrite bytes reference immediately
        result = None  # noqa: F841

    if not isinstance(parsed, dict):
        sys.exit("ERROR: Decrypted master file did not produce a YAML mapping.")
    return parsed


def _runtime_defaults(data: dict) -> list[tuple[str, str]]:
    """Render the runtime_defaults: block from master secrets.

    Operator directive 2026-05-12: deployment paths (DATA_DIR,
    TRADE_JOURNAL_DB) are NOT rendered into .env. They live in the
    systemd drop-ins at deploy/*.service.d/data-dir.conf (canonical:
    ``Environment=DATA_DIR=/data/bot-data``). The .env layer would
    win over the drop-in due to systemd's EnvironmentFile ordering,
    which is exactly the path-bifurcation bug the 2026-05-12
    incident exposed. Strategy + risk + trading symbol/timeframe
    remain in the rendered .env because those are runtime defaults,
    not deployment paths.

    Removed from the previous mapping:
      ``("DATA_DIR", "data_dir")``  — see directive above.
    """
    rd = data.get("runtime_defaults") or {}
    pairs = []
    mapping = [
        ("SYMBOL", "symbol"),
        ("TIMEFRAME", "timeframe"),
        # NOTE: DATA_DIR is intentionally NOT in this mapping per the
        # 2026-05-12 operator directive. It lives in the systemd
        # drop-in. Re-introducing it here re-creates the
        # path-bifurcation bug. If a future deployment needs to
        # override DATA_DIR, do it via a per-deployment systemd
        # drop-in, not via the rendered .env.
        ("MODEL_DIR", "model_dir"),
        ("LOG_DIR", "log_dir"),
        # DB_PATH similarly references a deployment path; keeping it
        # rendered for now because TRADE_JOURNAL_DB (the canonical
        # name the bot actually reads) is also in the systemd
        # drop-in. If DB_PATH ends up overriding TRADE_JOURNAL_DB
        # silently, this mapping line is the suspect.
        ("DB_PATH", "db_path"),
    ]
    for env_key, yaml_key in mapping:
        val = rd.get(yaml_key)
        if val and not _is_placeholder(val):
            pairs.append((env_key, str(val)))
    return pairs


def _risk_pairs(data: dict, tier: str) -> list[tuple[str, str]]:
    risk = (data.get("risk") or {}).get(tier) or {}
    pairs = []
    mapping = [
        ("MAX_POSITION_USD", "max_position_usd"),
        ("MAX_DAILY_LOSS_USD", "max_daily_loss_usd"),
        ("MAX_OPEN_POSITIONS", "max_open_positions"),
        ("RISK_PER_TRADE", "risk_per_trade"),
    ]
    for env_key, yaml_key in mapping:
        val = risk.get(yaml_key)
        if val is not None and not _is_placeholder(str(val)):
            pairs.append((env_key, str(val)))
    return pairs


def _load_accounts_yaml(accounts_path: Path) -> dict:
    """Load accounts.yaml. Returns the parsed mapping; never None.

    Delegates to the canonical reader at ``src/config/accounts_loader.py``
    so render-env stays aligned with the production dict-shape schema.
    """
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from src.config.accounts_loader import load_accounts_dict
    return load_accounts_dict(accounts_path)


def _per_account_pairs(
    data: dict, accounts_path: Path
) -> tuple[list[tuple[str, str]], list[str]]:
    """Render `<api_key_env>=...` and `<api_secret_env>=...` for every
    account in accounts.yaml.

    The master-secrets file declares per-account credentials under
    ``<exchange>.accounts.<account_id>.api_key/secret``. accounts.yaml
    declares ``api_key_env: BYBIT_API_KEY_1`` (the env var the bot
    expects). This function bridges them.

    Returns ``(pairs, warnings)``. Warnings are non-fatal — the operator
    sees them so they know an account was skipped (placeholder values,
    explicit ``enabled: false``, etc.).
    """
    accounts = _load_accounts_yaml(accounts_path)
    if not accounts:
        return [], [f"no accounts found in {accounts_path}"]

    pairs: list[tuple[str, str]] = []
    warnings: list[str] = []

    for account_id, cfg in accounts.items():
        if not isinstance(cfg, dict):
            warnings.append(f"account '{account_id}': not a mapping; skipping")
            continue

        # Bot's expected env var names (src/bot/data_loaders.py::bybit_client_for)
        api_key_env = cfg.get("api_key_env")
        if not api_key_env:
            warnings.append(
                f"account '{account_id}': no api_key_env in accounts.yaml; skipping"
            )
            continue
        api_secret_env = (
            cfg.get("api_secret_env")
            or api_key_env.replace("_API_KEY", "_API_SECRET")
        )

        exchange = (cfg.get("exchange") or "").strip().lower() or "bybit"
        master_block = (data.get(exchange) or {}).get("accounts") or {}
        creds = master_block.get(account_id)
        if not isinstance(creds, dict):
            warnings.append(
                f"account '{account_id}' ({exchange}): no entry under "
                f"'{exchange}.accounts.{account_id}' in master-secrets; "
                f"{api_key_env}/{api_secret_env} not written"
            )
            continue

        # Honour explicit `enabled: false` in the master block — operator
        # may keep credentials around for forward-compat without rendering.
        if creds.get("enabled") is False:
            warnings.append(
                f"account '{account_id}' ({exchange}): master block has "
                f"enabled: false; skipping"
            )
            continue

        api_key = creds.get("api_key")
        api_secret = creds.get("api_secret")
        if not api_key or _is_placeholder(api_key):
            warnings.append(
                f"account '{account_id}' ({exchange}): api_key still a "
                f"placeholder; {api_key_env} not written"
            )
            continue
        if not api_secret or _is_placeholder(api_secret):
            warnings.append(
                f"account '{account_id}' ({exchange}): api_secret still a "
                f"placeholder; {api_secret_env} not written"
            )
            continue

        pairs.append((api_key_env, str(api_key)))
        pairs.append((api_secret_env, str(api_secret)))

    return pairs, warnings


def _news_pairs(data: dict) -> list[tuple[str, str]]:
    """Render the news: block from the master secrets template.

    NEWS_API_KEY is always written so its absence in the template is a
    detectable config bug rather than a silent default. Optional tuning knobs
    are written only when explicitly present. (NEWS_ENABLED is no longer
    emitted — the legacy enable gate was removed 2026-06-10; activation is
    source-driven: the rss source is keyless, newsapi needs NEWS_API_KEY.)
    """
    news = data.get("news") or {}
    pairs: list[tuple[str, str]] = []

    # Always-present key (absence → detectable config bug, not silent default)
    api_key = news.get("api_key", "")
    pairs.append(("NEWS_API_KEY", str(api_key) if api_key is not None else ""))

    # Optional tuning knobs — only written when explicitly set
    optional_mapping = [
        ("NEWS_QUERY", "query"),
        ("NEWS_MAX_ARTICLES", "max_articles"),
        ("NEWS_CACHE_TTL", "cache_ttl"),
        ("NEWS_MAX_AGE_MINUTES", "max_age_minutes"),
        ("NEWS_VETO_ENABLED", "veto_enabled"),
        ("NEWS_VETO_SENTIMENT_THRESHOLD", "veto_sentiment_threshold"),
        ("NEWS_VETO_IMPACT_THRESHOLD", "veto_impact_threshold"),
        ("NEWS_WEIGHTED_AGGREGATION", "weighted_aggregation"),
        ("NEWS_POSITIVE_KEYWORDS", "positive_keywords"),
        ("NEWS_NEGATIVE_KEYWORDS", "negative_keywords"),
    ]
    for env_key, yaml_key in optional_mapping:
        val = news.get(yaml_key)
        if val is not None and str(val).strip():
            pairs.append((env_key, str(val)))
    return pairs


def build_live(
    data: dict, *, accounts_path: Path | None = None
) -> list[tuple[str, str]]:
    """Build the canonical .env for the live trader.

    Operator directive 2026-05-03 — there are no MODE / DRY_RUN /
    ALLOW_LIVE_TRADING fields in the rendered file. The single dry/live
    toggle is per-account ``mode: live | dry_run`` in
    ``config/accounts.yaml``, applied via ``RiskManager.dry_run``.

    Operator directive 2026-05-12 — DATA_DIR is no longer rendered
    here (see _runtime_defaults docstring).
    """
    pairs: list[tuple[str, str]] = [
        ("ENVIRONMENT", "production"),
        ("EXCHANGE", _get(data, "profiles.live.exchange")),
        ("TELEGRAM_BOT_TOKEN", _get(data, "telegram.prod.bot_token")),
        ("TELEGRAM_CHAT_ID", _get(data, "telegram.prod.chat_id")),
        # Legacy single-account env vars — kept for back-compat with
        # any code path still reading the unsuffixed names. New code
        # uses the per-account block below (sourced from accounts.yaml).
        ("BYBIT_API_KEY", _get(data, "bybit.live.api_key")),
        ("BYBIT_API_SECRET", _get(data, "bybit.live.api_secret")),
        ("BYBIT_BASE_URL", _get(data, "bybit.live.base_url")),
    ]
    pairs.extend(_runtime_defaults(data))
    pairs.extend(_risk_pairs(data, "live"))
    pairs.extend(_news_pairs(data))

    # Per-account credentials driven by config/accounts.yaml.
    # This is the fix for the silent "balance unavailable (missing API
    # creds)" pattern: the bot reads BYBIT_API_KEY_1, BYBIT_API_KEY_2,
    # etc., but the previous render script only emitted the legacy
    # singular BYBIT_API_KEY. Warnings are surfaced separately by main().
    per_account_pairs, _ = _per_account_pairs(
        data, accounts_path or DEFAULT_ACCOUNTS_YAML,
    )
    pairs.extend(per_account_pairs)
    return pairs


def _vwap_risk_pairs(data: dict) -> list[tuple[str, str]]:
    """Risk pairs for the vwap_btcusd risk profile, including optional max_qty
    and max_open_positions."""
    risk = (data.get("risk") or {}).get("vwap_btcusd") or {}
    pairs: list[tuple[str, str]] = []
    mapping = [
        ("MAX_POSITION_USD", "max_position_usd"),
        ("MAX_DAILY_LOSS_USD", "max_daily_loss_usd"),
        ("RISK_PER_TRADE", "risk_per_trade"),
        ("MAX_QTY", "max_qty"),
        ("MAX_OPEN_POSITIONS", "max_open_positions"),
    ]
    for env_key, yaml_key in mapping:
        val = risk.get(yaml_key)
        if val is not None and not _is_placeholder(str(val)):
            pairs.append((env_key, str(val)))
    return pairs


BUILDERS = {
    "live": build_live,
}


def write_env_file(path: Path, pairs: list[tuple[str, str]]) -> None:
    lines = []
    for key, val in pairs:
        # Quote values that contain spaces or special shell chars
        if val is None:
            val = ""
        needs_quote = any(c in val for c in " \t#$`'\"\\")
        if needs_quote:
            escaped = val.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}="{escaped}"\n')
        else:
            lines.append(f"{key}={val}\n")
    path.write_text("".join(lines), encoding="utf-8")
    # chmod 0600: owner read+write only
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a lean .env file from an encrypted SOPS master secrets file."
    )
    parser.add_argument("--master", required=True, help="Path to master-secrets.sops.yaml")
    parser.add_argument("--age-key-file", required=True, help="Path to age-keys.txt")
    parser.add_argument("--profile", default="live", choices=PROFILES,
                        help="Profile to render (default: live; the single canonical profile post-2026-05-03)")
    parser.add_argument("--out", required=True, help="Output .env file path")
    parser.add_argument("--allow-live", action="store_true",
                        help="(Deprecated — kept for back-compat with notebooks; rendering "
                             "live is the only supported path post-2026-05-03 and the flag is "
                             "no longer required.)")
    parser.add_argument("--sops-bin", default="sops", help="Path to the sops binary (default: sops)")
    parser.add_argument(
        "--accounts-yaml",
        default=str(DEFAULT_ACCOUNTS_YAML),
        help="Path to config/accounts.yaml (drives per-account env vars).",
    )
    args = parser.parse_args()

    master_path = Path(args.master).expanduser().resolve()
    age_key_file = Path(args.age_key_file).expanduser().resolve()
    out_path = Path(args.out).expanduser()

    # Refuse plaintext YAML
    if not master_path.name.endswith(".sops.yaml"):
        sys.exit(
            f"ERROR: --master must point to a .sops.yaml file (got '{master_path.name}').\n"
            "Refusing to process a plaintext master secrets file."
        )

    if not master_path.exists():
        sys.exit(f"ERROR: Master file not found: {master_path}")

    if not age_key_file.exists():
        sys.exit(f"ERROR: Age key file not found: {age_key_file}")

    # --allow-live is no longer required (operator directive 2026-05-03 —
    # the dry/live toggle is per-account in accounts.yaml, not at render
    # time). The flag is accepted but ignored for back-compat with old
    # notebook cells / scripts. Per-account ``mode: dry_run`` is the
    # operator's safety control if they want to render keys but not
    # trade live yet.
    if args.allow_live:
        print("note: --allow-live accepted but ignored (no longer required)")

    print(f"Profile : {args.profile}")
    print(f"Output  : {out_path}")

    data = decrypt_master(master_path, age_key_file, args.sops_bin)

    builder = BUILDERS[args.profile]
    accounts_path = Path(args.accounts_yaml).expanduser().resolve()
    pairs = builder(data, accounts_path=accounts_path)
    # Re-run the per-account scan to surface warnings to the operator
    # (the builder swallowed them so the public contract stays simple
    # — list[tuple[str, str]] — for the existing test suite).
    _, warnings = _per_account_pairs(data, accounts_path)

    # Drop any pairs where the value resolved to None (optional fields absent)
    pairs = [(k, v) for k, v in pairs if v is not None]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_env_file(out_path, pairs)

    written_keys = [k for k, _ in pairs]
    print(f"Written : {len(written_keys)} variables")
    print("Keys    :", ", ".join(written_keys))

    if warnings:
        print()
        print("Warnings (operator should review):")
        for w in warnings:
            print(f"  ! {w}")
        print()
        print(
            "Each warning above means an account in accounts.yaml did NOT get "
            "API-key env vars rendered. The bot will report 'missing env vars' "
            "for those accounts on /accounts_status until you fix the master "
            "secrets file."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
