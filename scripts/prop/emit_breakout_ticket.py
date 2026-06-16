#!/usr/bin/env python3
"""Render a sample Breakout "trade setup" ticket (POC, Tier-1).

Demo/CLI for ``src/prop/breakout_ticket.py``. With ``--demo`` it renders a
hard-coded sample signal so we can eyeball the ticket format end-to-end. It
places NO order and is not wired into the live path.

Usage:
    python scripts/prop/emit_breakout_ticket.py --demo
    python scripts/prop/emit_breakout_ticket.py --demo --risk-pct 0.6
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.prop.breakout_ticket import BreakoutSignal, TicketConfig, build_ticket, render_ticket  # noqa: E402

_ROUTING = _REPO_ROOT / "config" / "prop_rulesets" / "breakout_routing.yaml"


def _load_cfg(risk_pct: float | None) -> TicketConfig:
    raw = {}
    if _ROUTING.exists():
        raw = yaml.safe_load(_ROUTING.read_text()) or {}
    sym_map = raw.get("symbol_map") or {}
    return TicketConfig(
        account_size_usd=float(raw.get("account_size_usd", 5000.0)),
        risk_pct=float(risk_pct if risk_pct is not None else raw.get("risk_pct", 0.6)),
        dxtrade_symbol=sym_map.get("BTCUSDT"),
        contract_value_usd_per_point=float(raw.get("contract_value_usd_per_point", 1.0)),
        entry_band_frac=float(raw.get("entry_band_frac", 0.25)),
        ttl_bars=float(raw.get("ttl_bars", 1.0)),
    )


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Render a Breakout trade-setup ticket (POC).")
    p.add_argument("--demo", action="store_true", help="Render a sample signal.")
    p.add_argument("--risk-pct", type=float, default=None)
    args = p.parse_args(argv)

    if not args.demo:
        p.error("only --demo is supported in the POC; pass --demo")

    cfg = _load_cfg(args.risk_pct)
    sample = BreakoutSignal(
        strategy="squeeze_breakout_4h",
        symbol="BTCUSDT",
        direction="long",
        entry=60000.0,
        sl=58800.0,
        tp=63600.0,
        timeframe="4h",
        signal_time=datetime.now(timezone.utc),
    )
    ticket = build_ticket(sample, cfg)
    print(render_ticket(ticket))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
