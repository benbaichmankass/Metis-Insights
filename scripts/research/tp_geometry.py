"""TP geometry for the backtest harnesses: a take-profit that MOVES.

GEOM-B1-HARNESS (docs/ARCHITECTURE-CANONICAL.md § "TP doctrine", clauses 2-3).
The live fleet's SL moves and its TP never does; before this module no harness
could even *measure* a target that moves, so the doctrine's central question --
does a thesis-conditioned, re-estimated target beat a trail-only exit -- had no
instrument. This is the measurement half. The LIVE producer is another lane's
(TP-DOCTRINE, in `monitor()`); the two meet at the pure decision function
`src.runtime.target_expectation.evaluate_extension`, which this module IMPORTS
and never re-implements.

Three levers, all OFF unless declared (`TPGeometrySpec.armed`), so a default run
is bit-for-bit what it was before this file existed
(`tests/test_tp_geometry_harness.py::test_defaults_reproduce_pre_lever_golden`):

``target_r``
    A FINITE entry-time target in R. Replaces the leg's `tp_r` for the target
    price; the venue clamp (`tp_cap_pct`) still applies when set.
``extend_r > 0`` (+ ``approach_frac``, ``max_extends``, ``thesis``)
    Extend while the thesis holds. Decided per closed bar by
    `evaluate_extension`; `thesis` says where `thesis_intact` comes from:
    ``native`` (the strategy's OWN continuation test -- supplied by the harness),
    ``always`` (the geometry measured with the thesis question removed -- the
    upper bound, a control not a candidate) or ``unknown`` (passes `None`, which
    `evaluate_extension` documents as never extending -- the negative control:
    it MUST equal the no-extension run).
``retarget_mode``
    ``atr_rescale``  the target's R-distance is rescaled by ATR_now/ATR_entry
                      (the prediction follows current volatility, both ways).
    ``stall_pull_in`` after `retarget_stall_bars` bars with no new favourable
                      extreme the target is pulled in to the best price reached
                      +/- `retarget_pull_r` R (momentum ran out; the prediction
                      says where it ended). It only ever TIGHTENS.

Bar-close semantics, deliberately conservative: the target is evaluated for
revision on a bar's CLOSE, after the bar's own stop/target test, and the revised
level binds from the NEXT bar. A bar that reaches the old target exits at the old
target -- extension never rescues a trade that was already filled.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.runtime.target_expectation import (  # noqa: E402  (the ONE decision half)
    EXT_EXTEND,
    evaluate_extension,
    resolve_expectation,
)

THESIS_MODES = ("native", "always", "unknown")
RETARGET_MODES = ("atr_rescale", "stall_pull_in")

#: Calibration band: an exit "matched its prediction" when its price-R lands
#: within this fraction of the FINAL target's R (RQ-20261007-001 statistic (a)).
CALIBRATION_BAND = 0.20


@dataclass(frozen=True)
class TPGeometrySpec:
    target_r: Optional[float] = None
    extend_r: float = 0.0
    approach_frac: float = 0.85
    max_extends: int = 3
    thesis: str = "native"
    retarget_mode: Optional[str] = None
    retarget_stall_bars: int = 3
    retarget_pull_r: float = 0.5
    #: REPORT-ONLY: change no exit, but emit the per-trade target fields and the
    #: `tp_geometry` summary for the harness's OWN target. The sweep runs the
    #: config-exact base this way so a cell's calibration share has a base to be
    #: read beside (RQ-20261007-001 statistic (a)) without the base being re-priced.
    report: bool = False

    def __post_init__(self) -> None:
        if self.thesis not in THESIS_MODES:
            raise ValueError(f"tp thesis must be one of {THESIS_MODES}, got {self.thesis!r}")
        if self.retarget_mode is not None and self.retarget_mode not in RETARGET_MODES:
            raise ValueError(f"tp retarget mode must be one of {RETARGET_MODES}, "
                             f"got {self.retarget_mode!r}")
        if self.target_r is not None and not (math.isfinite(self.target_r) and self.target_r > 0):
            raise ValueError(f"tp target_r must be a finite positive R, got {self.target_r!r}")
        if self.extend_r < 0 or self.max_extends < 0 or not 0.0 < self.approach_frac <= 1.0:
            raise ValueError("tp extend_r/max_extends must be >= 0 and approach_frac in (0, 1]")
        if self.retarget_stall_bars < 1 or self.retarget_pull_r < 0:
            raise ValueError("tp retarget_stall_bars must be >= 1 and retarget_pull_r >= 0")

    @property
    def armed(self) -> bool:
        return (self.target_r is not None or self.extend_r > 0.0
                or self.retarget_mode is not None or self.report)

    @property
    def revises(self) -> bool:
        return self.extend_r > 0.0 or self.retarget_mode is not None

    def params(self) -> Dict[str, Any]:
        """The echo for a summary's `params` -- present ONLY when armed."""
        out: Dict[str, Any] = {}
        if self.report:
            out["tp_report"] = True
        if self.target_r is not None:
            out["tp_target_r"] = self.target_r
        if self.extend_r > 0.0:
            out.update(tp_extend_r=self.extend_r, tp_approach_frac=self.approach_frac,
                       tp_max_extends=self.max_extends, tp_thesis=self.thesis)
        if self.retarget_mode is not None:
            out.update(tp_retarget_mode=self.retarget_mode,
                       tp_retarget_stall_bars=self.retarget_stall_bars,
                       tp_retarget_pull_r=self.retarget_pull_r)
        return out


def spec_from_args(args: Any) -> TPGeometrySpec:
    """Build the spec from an argparse namespace carrying the `--tp-*` flags."""
    return TPGeometrySpec(
        target_r=args.tp_target_r, extend_r=args.tp_extend_r,
        approach_frac=args.tp_approach_frac, max_extends=args.tp_max_extends,
        thesis=args.tp_thesis, retarget_mode=args.tp_retarget_mode,
        retarget_stall_bars=args.tp_retarget_stall_bars,
        retarget_pull_r=args.tp_retarget_pull_r, report=args.tp_report)


def add_cli_flags(p: Any) -> None:
    """Register the `--tp-*` flags on a parser. Every default is OFF."""
    p.add_argument("--tp-target-r", type=float, default=None,
                   help="GEOM-B1: a FINITE entry-time target in R (the doctrine's "
                        "predictive TP). Replaces --tp-r for the target price; "
                        "--tp-cap-pct still clamps it when set. Unset = off, "
                        "byte-identical.")
    p.add_argument("--tp-extend-r", type=float, default=0.0,
                   help="GEOM-B1: push the target out by this many R each time "
                        "price nears it AND the thesis holds (decided by "
                        "target_expectation.evaluate_extension). 0 = off.")
    p.add_argument("--tp-approach-frac", type=float, default=0.85,
                   help="Share of the entry->target distance that must be travelled "
                        "before an extension is considered (default 0.85).")
    p.add_argument("--tp-max-extends", type=int, default=3,
                   help="Bound on extensions per trade (default 3).")
    p.add_argument("--tp-thesis", choices=list(THESIS_MODES), default="native",
                   help="Where thesis_intact comes from: native = the strategy's own "
                        "continuation test; always = thesis question removed (an "
                        "upper-bound control); unknown = None, which never extends "
                        "(negative control). Default native.")
    p.add_argument("--tp-retarget-mode", choices=list(RETARGET_MODES), default=None,
                   help="GEOM-B1: re-estimate the target through the trade. "
                        "atr_rescale = scale its R by ATR_now/ATR_entry; "
                        "stall_pull_in = pull it in to the peak after a stall. "
                        "Unset = off.")
    p.add_argument("--tp-retarget-stall-bars", type=int, default=3,
                   help="stall_pull_in: bars without a new favourable extreme "
                        "before the target is pulled in (default 3).")
    p.add_argument("--tp-report", action="store_true",
                   help="GEOM-B1: change NO exit; emit the per-trade final_target_r / "
                        "exit_r fields and the tp_geometry summary for the run's own "
                        "target, so a base can be read beside a lever cell.")
    p.add_argument("--tp-retarget-pull-r", type=float, default=0.5,
                   help="stall_pull_in: the pulled-in target sits this many R "
                        "beyond the best price reached (default 0.5).")


def refusal(spec: TPGeometrySpec, *, has_target: bool) -> Optional[str]:
    """A message when the combination cannot MEASURE what it names, else None.

    A revision lever with no target to revise returns exactly-zero deltas that
    are indistinguishable from a lever that was measured and does nothing --
    the shape of BL-20260817-A-SHIPPED-LEVER-RE-SWEPT-AGAINST-ITSELF-READS-AS-A-MEASURED-NO-OP.
    `has_target` is whether a finite target exists: `--tp-target-r`, or a capped
    `--tp-r` (`tp_cap_pct > 0`).
    """
    if spec.revises and spec.target_r is None and not has_target:
        return ("--tp-extend-r / --tp-retarget-mode need a target to revise: pass "
                "--tp-target-r, or --tp-cap-pct > 0 with a finite --tp-r. Without "
                "one the lever cannot fire and the run would report a zero delta "
                "that is NOT a measurement of it.")
    return None


def entry_target(spec: TPGeometrySpec, *, anchor: float, risk: float, is_long: bool,
                 tp_cap_pct: float, legacy_tp: Optional[float]) -> Optional[float]:
    """The entry-time target price. `legacy_tp` is the harness's existing
    `tp_cap_pct`/`tp_r` price (None when off) and is returned untouched unless
    `target_r` is declared."""
    if spec.target_r is None:
        return legacy_tp
    want = anchor + spec.target_r * risk if is_long else anchor - spec.target_r * risk
    if tp_cap_pct > 0.0:
        cap = anchor * (1.0 + tp_cap_pct) if is_long else anchor * (1.0 - tp_cap_pct)
        want = min(cap, want) if is_long else max(cap, want)
    return want


class TPTracker:
    """One trade's moving target. Pure bookkeeping around `evaluate_extension`."""

    def __init__(self, spec: TPGeometrySpec, *, anchor: float, sl: float, risk: float,
                 is_long: bool, tp_cap_pct: float, target: Optional[float],
                 atr0: float) -> None:
        self.spec, self.anchor, self.sl, self.risk = spec, anchor, sl, risk
        self.is_long, self.tp_cap_pct, self.atr0 = is_long, tp_cap_pct, atr0
        self.target = target
        self.r0 = None if target is None else self._to_r(target)
        self.ext_offset_r = 0.0
        self.n_extends = 0
        self.n_retargets = 0

    def _to_r(self, price: float) -> float:
        return ((price - self.anchor) if self.is_long else (self.anchor - price)) / self.risk

    def _to_price(self, r: float) -> float:
        return self.anchor + r * self.risk if self.is_long else self.anchor - r * self.risk

    def _clamp(self, price: float) -> float:
        if self.tp_cap_pct <= 0.0:
            return price
        cap = (self.anchor * (1.0 + self.tp_cap_pct) if self.is_long
               else self.anchor * (1.0 - self.tp_cap_pct))
        return min(cap, price) if self.is_long else max(cap, price)

    def _not_behind(self, price: float, close: float) -> float:
        """A target at or behind the market would fill instantly; hold it at the
        close so it binds as a take-profit at the current level, never worse."""
        return max(price, close) if self.is_long else min(price, close)

    @property
    def final_target_r(self) -> Optional[float]:
        return None if self.target is None else round(self._to_r(self.target), 4)

    def on_bar_close(self, *, close: float, ext: float, bars_since_peak: int,
                     atr_now: Optional[float],
                     thesis_fn: Optional[Callable[[], Optional[bool]]]) -> None:
        """Re-estimate the target from this closed bar. Binds from the next bar."""
        if self.target is None:
            return
        spec = self.spec
        # 1. retarget -- the prediction follows the market.
        if spec.retarget_mode == "atr_rescale":
            if atr_now is not None and self.atr0 > 0 and math.isfinite(atr_now) and atr_now > 0:
                new_r = self.r0 * (atr_now / self.atr0) + self.ext_offset_r
                new = self._not_behind(self._clamp(self._to_price(new_r)), close)
                if new != self.target:
                    self.target, self.n_retargets = new, self.n_retargets + 1
        elif spec.retarget_mode == "stall_pull_in":
            if bars_since_peak >= spec.retarget_stall_bars:
                pull = (ext + spec.retarget_pull_r * self.risk if self.is_long
                        else ext - spec.retarget_pull_r * self.risk)
                tighter = pull < self.target if self.is_long else pull > self.target
                if tighter:
                    self.target = self._not_behind(self._clamp(pull), close)
                    self.n_retargets += 1
        # 2. extension -- only while the thesis holds, and only by the ONE
        #    decision function. `thesis_intact` is evaluated lazily so a run with
        #    extension off never pays for the strategy's continuation test.
        if spec.extend_r > 0.0:
            if spec.thesis == "always":
                thesis: Optional[bool] = True
            elif spec.thesis == "unknown" or thesis_fn is None:
                thesis = None
            else:
                thesis = thesis_fn()
            expectation = resolve_expectation(
                {"target_r": max(self._to_r(self.target), 1e-9)},
                entry=self.anchor, sl=self.sl, direction="long" if self.is_long else "short")
            verdict = evaluate_extension(
                expectation, price=close, entry=self.anchor,
                direction="long" if self.is_long else "short", thesis_intact=thesis,
                extends_so_far=self.n_extends, approach_frac=spec.approach_frac,
                extend_r=spec.extend_r, max_extends=spec.max_extends)
            if verdict["state"] == EXT_EXTEND and verdict["new_target"] is not None:
                new = self._clamp(float(verdict["new_target"]))
                if new != self.target:
                    self.ext_offset_r += (self._to_r(new) - self._to_r(self.target))
                    self.target = new
                    self.n_extends += 1


def summarize_geometry(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calibration + activity over `{final_target_r, n_extends, n_retargets,
    exit_r, outcome}` rows -- present in a summary ONLY when a lever is armed.

    `calibration_share` is RQ-20261007-001's statistic (a): the share of trades
    whose exit price-R lands within `CALIBRATION_BAND` of their FINAL target's R.
    `None` (not 0.0) when no trade carried a target: *we did not look*.
    """
    graded = [r for r in rows if r.get("final_target_r") is not None]
    out: Dict[str, Any] = {
        "n_trades": len(rows), "n_with_target": len(graded),
        "n_extended_trades": sum(1 for r in rows if (r.get("n_extends") or 0) > 0),
        "n_retargeted_trades": sum(1 for r in rows if (r.get("n_retargets") or 0) > 0),
        "total_extends": sum(int(r.get("n_extends") or 0) for r in rows),
        "total_retargets": sum(int(r.get("n_retargets") or 0) for r in rows),
        "tp_hits": sum(1 for r in graded if r.get("outcome") == "take_profit"),
        "calibration_band": CALIBRATION_BAND,
        "calibration_share": None,
        "read_state": "no_trades" if not rows else (
            "no_target" if not graded else "measured"),
    }
    if graded:
        ok = sum(1 for r in graded
                 if abs(float(r["exit_r"]) - float(r["final_target_r"]))
                 <= CALIBRATION_BAND * abs(float(r["final_target_r"])))
        out["calibration_share"] = round(ok / len(graded), 4)
    return out


__all__ = ["TPGeometrySpec", "TPTracker", "add_cli_flags", "spec_from_args", "refusal",
           "entry_target", "summarize_geometry", "THESIS_MODES", "RETARGET_MODES",
           "CALIBRATION_BAND", "atr_series"]


def atr_series(df: Any, period: int = 14) -> Any:
    """Rolling-mean true range -- the SAME formula as `backtest_trend._atr`, for
    harnesses (the scalp) that carry no ATR column but need ATR_now/ATR_entry."""
    import pandas as pd
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()
