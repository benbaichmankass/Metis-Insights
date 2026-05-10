"""Bybit-V5 off-VM adapter for `market_raw` (S-AI-WS5-B-PART-1).

**Off-VM only.** Refuses to run unless the operator has set
`ICT_OFFVM_BUILD_HOST=1`. The Oracle live VM must NEVER set this
env var; market_raw builds are meant to run on a separate build
host (developer laptop, HF Space, GitHub Actions runner with
operator-supplied creds).

WS9 rule: heavy market-data pulls do not belong on the live
trading VM. This adapter exists so a follow-up sprint can wire
`src/exchange/bybit_connector.py` (read-only API, off-VM only) to
produce historical bars for the regime classifier (WS5-B-PART-2).

This sprint (S-AI-WS5-B-PART-1) ships:
- the adapter class with the env-gate guardrail,
- a clear `NotImplementedError` on the actual fetch path,
- documentation pointing at the wiring task.

The operator wires the connector call when they next run the
build on a non-VM host with read-only credentials.
"""
from __future__ import annotations

import os
from typing import Any, ClassVar, Iterator, Mapping

from .base import MarketRawAdapter

OFFVM_ENV = "ICT_OFFVM_BUILD_HOST"
OFFVM_EXPECTED = "1"


class OffVmGuardrailViolation(RuntimeError):
    """Raised when the off-VM adapter tries to run on (or as if on) the live VM."""


class BybitOffvmMarketRawAdapter(MarketRawAdapter):
    source: ClassVar[str] = "bybit_v5_offvm"

    def iter_bars(
        self,
        *,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        self._enforce_offvm()
        # Wiring task: instantiate src/exchange/bybit_connector.py
        # with read-only credentials and translate its candle/kline
        # response into the canonical row shape. Filed for the
        # operator to land in S-AI-WS5-B-PART-2 alongside the
        # regime classifier baseline.
        raise NotImplementedError(
            "BybitOffvmMarketRawAdapter scaffold landed in S-AI-WS5-B-PART-1; "
            "the actual exchange call is filed for the operator to wire when "
            "they next run the build on a non-VM host. "
            "See docs/ml/market-raw-adapters.md § “Bybit off-VM wiring”."
        )

    @staticmethod
    def _enforce_offvm() -> None:
        if os.environ.get(OFFVM_ENV, "") != OFFVM_EXPECTED:
            raise OffVmGuardrailViolation(
                f"BybitOffvmMarketRawAdapter requires {OFFVM_ENV}={OFFVM_EXPECTED} "
                "to run. This adapter MUST NOT run on the Oracle live VM. "
                "Set the env var only on a build host that is not the live VM."
            )
