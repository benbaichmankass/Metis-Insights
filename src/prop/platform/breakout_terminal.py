"""Breakout's proprietary terminal (dashboard → "Open Terminal") — SCOPED ONLY.

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 2.4.

No login has ever been made against this platform and nothing here is built:
breakout_1 is a DXtrade account, so there is no proprietary-terminal login to
measure. What first-party material says (spec § Sources S3/S4/S6/S7/S23/S24):
it is reached from the dashboard at portal.breakoutprop.com by clicking
"Open Terminal"; it is a separate platform with its own mobile app; DXtrade
accounts do not migrate to it; it is the only platform for new purchases and
for non-crypto instruments. The one login-flow signal we have (a third-party
review describing an emailed number-match at sign-in) would make it
infeasible if it holds on every login.

Every method raises, including the read path, so selecting
``platform: breakout_terminal`` fails loudly instead of doing something.
"""
from __future__ import annotations

from typing import Any, List

from src.prop.platform.base import AccountSnapshot, Position, PropPlatformAdapter, WorkingOrder

_SCOPED = ("breakout_terminal adapter is scoped only (spec § 2.4); it needs a "
           "proprietary-terminal account to measure its login and DOM")


class BreakoutTerminalAdapter(PropPlatformAdapter):
    platform = "breakout_terminal"

    def login(self, page: Any, login_url: str, username: str, password: str) -> None:
        raise NotImplementedError(_SCOPED)

    def read_account(self, page: Any) -> AccountSnapshot:
        raise NotImplementedError(_SCOPED)

    def read_positions(self, page: Any) -> List[Position]:
        raise NotImplementedError(_SCOPED)

    def read_orders(self, page: Any) -> List[WorkingOrder]:
        raise NotImplementedError(_SCOPED)
