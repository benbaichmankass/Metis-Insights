r"""CI guard: every account in config/accounts.yaml carries a valid
``account_class``, consistent with the Bybit-only ``demo`` transport flag.

The ``account_class`` field (``paper`` | ``real_money``) is the single
source of truth for the paper-vs-real-money reporting axis — it cascades
into ``trades.account_class`` and the dashboard/Android ``accountClass``
field, and drives every "real-money only" aggregate. Because that axis is
load-bearing for what the operator sees as live-money PnL, a missing or
inconsistent value is a correctness bug, not a style nit — so this guard
freezes the invariant in CI.

Checks (any failure → exit 1, with a clear per-account message):

  1. Every account declares ``account_class`` ∈ {``paper``, ``real_money``}.
  2. A Bybit account with ``demo: true`` must be ``account_class: paper``
     — the demo endpoint trades paper money, so a real-money stamp would
     be a contradiction.
  3. No account may have ``demo: true`` AND ``account_class: real_money``
     (the same contradiction, stated independently of exchange — ``demo``
     is a paper venue everywhere it's honoured).

``demo`` itself stays a Bybit-ONLY transport flag (selects
api-demo.bybit.com); this guard does not require non-Bybit accounts to
carry it, and never adds it.

Usage
-----
::

    python scripts/check_account_class.py          # CI-style scan
    python scripts/check_account_class.py --list    # print OK summary too

Exit 0 → clean. Exit 1 → at least one violation (each printed to stderr).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ACCOUNTS_YAML = _REPO_ROOT / "config" / "accounts.yaml"

# `prop` (2026-06-17): third funding category for prop-firm eval/funded accounts
# (Breakout). Tracked separately from real_money + paper in the API aggregates.
_VALID_CLASSES = frozenset({"paper", "real_money", "prop"})


def _is_truthy_demo(value: Any) -> bool:
    """Match the Bybit transport-flag parse in clients.py / account loader."""
    return str(value).strip().lower() in ("true", "1", "yes")


def check_accounts(accounts: Dict[str, Dict[str, Any]]) -> List[str]:
    """Return a list of violation strings (empty == clean)."""
    violations: List[str] = []
    for name, cfg in accounts.items():
        if not isinstance(cfg, dict):
            violations.append(f"{name}: account entry is not a mapping")
            continue
        exchange = str(cfg.get("exchange") or "").strip().lower()
        raw_class = cfg.get("account_class")
        account_class = (
            str(raw_class).strip().lower() if raw_class is not None else None
        )
        demo = _is_truthy_demo(cfg.get("demo", False))

        # 1. account_class present + valid.
        if account_class is None:
            violations.append(
                f"{name}: missing required `account_class` "
                f"(must be one of {sorted(_VALID_CLASSES)})"
            )
        elif account_class not in _VALID_CLASSES:
            violations.append(
                f"{name}: invalid `account_class={raw_class!r}` "
                f"(must be one of {sorted(_VALID_CLASSES)})"
            )

        # 2. Bybit + demo:true must be paper.
        if demo and exchange == "bybit" and account_class == "real_money":
            violations.append(
                f"{name}: Bybit account has `demo: true` (paper endpoint) but "
                f"`account_class: real_money` — the demo venue is paper money."
            )

        # 3. demo:true + real_money is contradictory on any exchange.
        if demo and account_class == "real_money":
            violations.append(
                f"{name}: has `demo: true` AND `account_class: real_money` — "
                f"`demo` selects a paper venue, so the category must be `paper`."
            )

    return violations


def _load_accounts(path: Path) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Return ``(accounts_dict, errors)``. errors non-empty == hard failure."""
    import yaml

    if not path.exists():
        return {}, [f"accounts file not found: {path}"]
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        return {}, [f"failed to parse {path}: {type(exc).__name__}: {exc}"]
    raw = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}, [f"{path}: top-level `accounts:` block missing or not a mapping"]
    return {str(k): v for k, v in raw.items()}, []


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--accounts", default=str(_DEFAULT_ACCOUNTS_YAML),
        help="Path to accounts.yaml (default: config/accounts.yaml).",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Print an OK summary on clean runs.",
    )
    args = parser.parse_args(argv)

    accounts, load_errors = _load_accounts(Path(args.accounts))
    if load_errors:
        for err in load_errors:
            print(f"account-class guard: {err}", file=sys.stderr)
        return 1

    violations = check_accounts(accounts)
    if violations:
        print(
            "account-class guard: account_class violation(s) in "
            f"{args.accounts}:",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print(
            "\nEvery account needs `account_class: paper | real_money` "
            "(the paper/real funding category). `demo: true` is the "
            "Bybit-only transport flag and implies paper.",
            file=sys.stderr,
        )
        return 1

    if args.list:
        print(
            f"account-class guard: clean. {len(accounts)} account(s), "
            "all carry a valid account_class consistent with demo."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
