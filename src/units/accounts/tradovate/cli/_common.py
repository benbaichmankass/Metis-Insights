"""Shared CLI plumbing — env override + adapter construction."""
from __future__ import annotations

import argparse
import os

from ..adapter import TradovateAdapter
from ..config import TradovateConfig


def add_env_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env",
        choices=("demo", "live"),
        default=None,
        help="Override TRADOVATE_ENV for this run (default: demo, or whatever env says)",
    )


def build_adapter(args: argparse.Namespace, *, attach_ws: bool = False) -> TradovateAdapter:
    if args.env:
        os.environ["TRADOVATE_ENV"] = args.env
    cfg = TradovateConfig.load()
    return TradovateAdapter.build(cfg, attach_ws=attach_ws)
