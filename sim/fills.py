"""SIM fill model (Phase 1).

Resolves an open simulated position against subsequent bars into a realized
R-multiple, net of fees. Deliberately the SAME fill convention the standalone
backtests use (TP/SL intrabar touch, round-trip fee in bps) so SIM portfolio
results are directly comparable to the per-strategy harnesses
(``scripts/backtest_*.py``). Higher-fidelity fills (intrabar path order,
partials, funding) are a Phase-4 follow-up per the design doc § 8.

Conventions:
  * R is measured against the entry->SL distance the strategy chose
    (``risk_per_unit``), so a strategy that sets a tight stop books more R
    for the same price move — exactly as live.
  * **Conservative tie-break:** if a single bar's range spans BOTH the SL and
    the TP, we assume the **SL** filled first (we cannot know intrabar order
    from OHLC, so we take the adverse outcome — never optimistic).
  * Fees: ``fee_bps_roundtrip`` is charged once per round trip, expressed in R
    by dividing the fee fraction by the risk fraction (risk-per-unit / entry).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class BarFillModel:
    """TP/SL-touch fill model, net-of-fee, SL-priority on an ambiguous bar."""

    fee_bps_roundtrip: float = 7.5   # Bybit linear-perp taker round trip ~7.5 bps
    timeout_bars: int = 0            # 0 = no timeout; else close at bar N with current R

    def _fee_r(self, entry: float, risk_per_unit: float) -> float:
        """Round-trip fee expressed in R units.

        fee fraction of notional = fee_bps/1e4. risk fraction = risk_per_unit/entry.
        fee_in_R = fee_fraction / risk_fraction.
        """
        if entry <= 0 or risk_per_unit <= 0:
            return 0.0
        fee_frac = self.fee_bps_roundtrip / 1e4
        risk_frac = risk_per_unit / entry
        return fee_frac / risk_frac if risk_frac > 0 else 0.0

    def resolve(
        self,
        *,
        direction: str,
        entry: float,
        sl: float,
        tp: float,
        future_bars: list[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """Resolve a position against ``future_bars`` (each {ts,open,high,low,close}).

        Returns ``{exit_ts, exit, exit_reason, r_multiple}`` or ``None`` if the
        position never resolved within the available bars (caller marks it as
        still-open at end of data).
        """
        risk_per_unit = abs(entry - sl)
        if risk_per_unit <= 0:
            return None
        fee_r = self._fee_r(entry, risk_per_unit)
        is_long = direction == "long"

        for i, bar in enumerate(future_bars):
            high = float(bar["high"])
            low = float(bar["low"])
            hit_sl = (low <= sl) if is_long else (high >= sl)
            hit_tp = (high >= tp) if is_long else (low <= tp)

            if hit_sl and hit_tp:
                # Ambiguous bar — assume SL first (conservative, never optimistic).
                return self._exit(bar, sl, "sl", -1.0 - fee_r, entry, risk_per_unit)
            if hit_sl:
                return self._exit(bar, sl, "sl", -1.0 - fee_r, entry, risk_per_unit)
            if hit_tp:
                tp_r = abs(tp - entry) / risk_per_unit
                return self._exit(bar, tp, "tp", tp_r - fee_r, entry, risk_per_unit)

            if self.timeout_bars and (i + 1) >= self.timeout_bars:
                close = float(bar["close"])
                raw_r = (close - entry) / risk_per_unit
                if not is_long:
                    raw_r = -raw_r
                return self._exit(bar, close, "timeout", raw_r - fee_r, entry, risk_per_unit)

        return None  # never resolved within available data

    @staticmethod
    def _exit(bar, price, reason, r, entry, risk_per_unit) -> dict[str, Any]:
        return {
            "exit_ts": bar.get("ts"),
            "exit": float(price),
            "exit_reason": reason,
            "r_multiple": round(float(r), 6),
        }
