#!/usr/bin/env python3
"""
Decrypt a SOPS-encrypted master secrets file and render a lean .env file
for one runtime profile. Never prints secret values.

Usage:
    python scripts/render_env_from_master.py \
        --master /path/to/master-secrets.sops.yaml \
        --age-key-file /path/to/age-keys.txt \
        --profile live|vwap_btcusd_live \
        --out .env.live \
        --allow-live \
        [--sops-bin sops]

All supported profiles target live trading on real exchange keys. There is no
paper / simulation profile in this repo: we build, test, and deploy live on
small accounts only.
"""
from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

PROFILES = (
    "live",
    "vwap_btcusd_live",
)
LIVE_PROFILES = PROFILES  # every supported profile is live
PLACEHOLDER_PATTERNS = ("REPLACE_ME", "CHANGEME", "YOUR_", "<", ">", "TODO")


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
    rd = data.get("runtime_defaults") or {}
    pairs = []
    mapping = [
        ("SYMBOL", "symbol"),
        ("TIMEFRAME", "timeframe"),
        ("DATA_DIR", "data_dir"),
        ("MODEL_DIR", "model_dir"),
        ("LOG_DIR", "log_dir"),
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


def build_live(data: dict) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = [
        ("ENVIRONMENT", "production"),
        ("EXCHANGE", _get(data, "profiles.live.exchange")),
        ("MODE", "LIVE"),
        ("DRY_RUN", "false"),
        ("ALLOW_LIVE_TRADING", "true"),
        ("TELEGRAM_BOT_TOKEN", _get(data, "telegram.prod.bot_token")),
        ("TELEGRAM_CHAT_ID", _get(data, "telegram.prod.chat_id")),
        ("BYBIT_API_KEY", _get(data, "bybit.live.api_key")),
        ("BYBIT_API_SECRET", _get(data, "bybit.live.api_secret")),
        ("BYBIT_BASE_URL", _get(data, "bybit.live.base_url")),
    ]
    pairs.extend(_runtime_defaults(data))
    pairs.extend(_risk_pairs(data, "live"))
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


def build_vwap_btcusd_live(data: dict) -> list[tuple[str, str]]:
    """Build env pairs for the vwap_btcusd_live profile.

    Uses the bybit.vwap_strategy subaccount keys (live Bybit endpoints) and the
    prod Telegram profile. Always renders MODE=LIVE and DRY_RUN=false; the CLI
    requires --allow-live to render this profile.
    """
    profile_key = "vwap_btcusd_live"
    pairs: list[tuple[str, str]] = [
        ("ENVIRONMENT", _get(data, f"profiles.{profile_key}.environment")),
        ("EXCHANGE", _get(data, f"profiles.{profile_key}.exchange")),
        ("MODE", "LIVE"),
        ("DRY_RUN", "false"),
        ("ALLOW_LIVE_TRADING", "true"),
        ("BYBIT_TESTNET", "false"),
        ("STRATEGY", _get(data, "strategies.vwap_btcusd.strategy_name")),
        ("SYMBOL", _get(data, "strategies.vwap_btcusd.symbol")),
        ("TIMEFRAME", _get(data, "strategies.vwap_btcusd.timeframe")),
        ("BYBIT_API_KEY", _get(data, "bybit.vwap_strategy.api_key")),
        ("BYBIT_API_SECRET", _get(data, "bybit.vwap_strategy.api_secret")),
        ("TELEGRAM_BOT_TOKEN", _get(data, "telegram.prod.bot_token")),
        ("TELEGRAM_CHAT_ID", _get(data, "telegram.prod.chat_id")),
    ]
    pairs.extend(_vwap_risk_pairs(data))
    return pairs


BUILDERS = {
    "live": build_live,
    "vwap_btcusd_live": build_vwap_btcusd_live,
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
    parser.add_argument("--profile", required=True, choices=PROFILES, help="Target runtime profile (all live)")
    parser.add_argument("--out", required=True, help="Output .env file path")
    parser.add_argument("--allow-live", action="store_true", help="Required for every supported profile (all are live)")
    parser.add_argument("--sops-bin", default="sops", help="Path to the sops binary (default: sops)")
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

    if args.profile in LIVE_PROFILES and not args.allow_live:
        sys.exit(
            f"ERROR: Generating the '{args.profile}' profile requires --allow-live.\n"
            "Pass --allow-live to confirm you intentionally want live trading credentials."
        )

    print(f"Profile : {args.profile}")
    print(f"Output  : {out_path}")

    data = decrypt_master(master_path, age_key_file, args.sops_bin)

    builder = BUILDERS[args.profile]
    pairs = builder(data)

    # Drop any pairs where the value resolved to None (optional fields absent)
    pairs = [(k, v) for k, v in pairs if v is not None]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_env_file(out_path, pairs)

    written_keys = [k for k, _ in pairs]
    print(f"Written : {len(written_keys)} variables")
    print("Keys    :", ", ".join(written_keys))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
