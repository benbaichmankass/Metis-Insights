#!/usr/bin/env python3
"""news-feed-coverage-guard: every traded symbol must resolve to a DECLARED
news-feed disposition — never a silent fallthrough to macro-only.

Why this exists (``BL-20260807-NEWS-FEED-SYMBOL-COVERAGE-5-OF-24``)
------------------------------------------------------------------
The news layer used to select feeds from a hand-maintained per-symbol map in
``config/news_feeds.yaml`` running parallel to ``config/instruments.yaml``. It
drifted to cover **5 of 24** traded bases. The other 19 — including every
non-BTC/ETH crypto — silently fell through to the shared ``global`` macro
feeds, so an XRP scalp was scored against Fed and Social Security headlines
while ``NEWS_VETO_ENABLED`` was armed and able to block real-money trades.

The defect was invisible for one specific reason, and this guard is built
around it: **falling through to ``global`` looks exactly like being correctly
assigned to ``global``.** Both produce a working fetch and a plausible score.
Counting feeds, or asserting "every symbol gets at least one feed", would have
passed the whole time.

So the invariant is not "has feeds" — it is **"its disposition was declared"**:

  1. every traded symbol classifies to something other than ``unknown``; and
  2. that class either maps to a feed group in ``asset_class_groups``, or is
     explicitly named in ``macro_only_classes``.

A class that is macro-only ON PURPOSE (``bond`` — rates/Fed news IS the macro
feed) passes. A class that is macro-only because nobody noticed fails.

Usage:  python3 scripts/ci/check_news_feed_coverage.py
Exit 0 = every traded symbol declared. Exit 1 = at least one undeclared.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Set

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _collect_symbols(node, found: Set[str]) -> None:
    """Recursively harvest every ``symbols:`` list into *found*."""
    if isinstance(node, dict):
        for key, val in node.items():
            if key == "symbols" and isinstance(val, list):
                found.update(str(x).strip().upper() for x in val if str(x).strip())
            else:
                _collect_symbols(val, found)
    elif isinstance(node, list):
        for item in node:
            _collect_symbols(item, found)


def _load_yaml(path: Path) -> dict:
    """Read one YAML file. Sole ``yaml.safe_load`` site in this module.

    Kept as its own function with no config-filename literal in it, so the
    account registry is only ever reached through the canonical loader and
    `canonical-config-loaders` can see that plainly. (That guard caught this
    file on its first run — a guard written to stop a SECOND registry drifting
    was itself hand-rolling a reader for the FIRST one.)
    """
    import yaml

    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _traded_symbols() -> Set[str]:
    """Every symbol any account or strategy declares it trades.

    Union of the ``symbols:`` lists across both registries — the same basis
    the runtime uses to decide what to evaluate, so the guard's population
    matches the trader's. Accounts come from the canonical loader.
    """
    from src.config.accounts_loader import load_accounts_dict

    found: Set[str] = set()
    _collect_symbols(load_accounts_dict(), found)
    _collect_symbols(_load_yaml(REPO / "config" / "strategies.yaml"), found)
    return found


def main() -> int:
    from src.core.instrument_class import (
        UNKNOWN,
        asset_class_for_symbol,
        news_group_for_symbol,
    )
    from src.news.news_feeds import load_feeds_config, reload_feeds_config

    reload_feeds_config()
    cfg = load_feeds_config()
    class_groups: Dict[str, List[str]] = cfg.get("asset_class_groups") or {}

    # macro_only_classes is read straight from the YAML: load_feeds_config()
    # normalises only the keys it knows about, and this guard must not depend
    # on that loader growing a new field to stay honest.
    raw = _load_yaml(REPO / "config" / "news_feeds.yaml")
    macro_only = {
        str(x).strip().lower() for x in (raw.get("macro_only_classes") or []) if str(x).strip()
    }

    symbols = _traded_symbols()
    if not symbols:
        print("::error::news-feed-coverage: found NO traded symbols in "
              "accounts.yaml/strategies.yaml — the guard cannot have checked "
              "anything. Refusing to report a pass on an empty population.")
        return 1

    failures: List[str] = []
    declared_group = 0
    declared_macro = 0

    for sym in sorted(symbols):
        cls = asset_class_for_symbol(sym)
        token = news_group_for_symbol(sym)

        if cls == UNKNOWN or not token:
            failures.append(
                f"{sym}: classifies as '{cls}' — add an `asset_class` (or a "
                f"`news_group`) to its config/instruments.yaml entry. An "
                f"unclassified symbol silently reads macro-only headlines."
            )
            continue

        key = str(token).lower()
        if class_groups.get(key):
            declared_group += 1
        elif key in macro_only:
            declared_macro += 1
        else:
            failures.append(
                f"{sym}: resolves to '{key}', which has NO entry in "
                f"news_feeds.yaml::asset_class_groups and is not listed in "
                f"macro_only_classes. Either map it to a feed group, or name "
                f"it in macro_only_classes to declare macro-only ON PURPOSE."
            )

    total = len(symbols)
    if failures:
        for line in failures:
            print(f"::error::news-feed-coverage: {line}")
        print(
            f"news-feed-coverage: {total} traded symbol(s) checked — "
            f"{len(failures)} undeclared, {declared_group} mapped to a feed "
            f"group, {declared_macro} declared macro-only."
        )
        return 1

    print(
        f"news-feed-coverage OK: {total} traded symbol(s) — "
        f"{declared_group} mapped to a class feed group, "
        f"{declared_macro} declared macro-only ({', '.join(sorted(macro_only))}). "
        f"0 silent fallthroughs."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
