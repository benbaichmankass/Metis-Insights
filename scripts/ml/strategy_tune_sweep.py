#!/usr/bin/env python3
"""Canonical M8 parameter-sweep harness — execute a strategy-review-gate
``tune_recipe`` and emit one **tune result** naming the net-of-fee optimal value.

M7 (the strategy review gate, ``docs/strategy-review-gate.md``) emits a
``tune_recipe`` block on every ``proposed_action == "tune"`` packet. M8 makes
that recipe *executable*: this script ingests the recipe, expands its
``search_space`` into a concrete grid, dispatches to the right existing
backtester (the per-strategy research harnesses + the vwap workhorse), reads
each run's **net-of-fee** metrics through a per-harness normalizer, picks the
optimum, and writes::

    runtime_logs/strategy_tunes/<UTC-date>/<strategy>__<param>.json
    runtime_logs/strategy_tunes/<UTC-date>/<strategy>__<param>.md

The result carries an **advisory** recommendation — the exact
``config/strategies.yaml`` line the optimum implies. Applying it is **Tier-3**
(operator-approved); this script never writes config. It is the evidence step
that precedes a tuning change, mirroring how ``strategy_review_packet.py`` is
the evidence step that precedes a kill.

Canonical doc: ``docs/strategy-tuning.md``.

CLI
---
From a recipe (a review packet with a ``tune_recipe``, or a bare recipe file)::

    python scripts/ml/strategy_tune_sweep.py --recipe runtime_logs/strategy_reviews/2026-06-09/vwap.json \
        --data data/backtest_candles.csv

Constructed inline (no packet on hand)::

    python scripts/ml/strategy_tune_sweep.py \
        --target 'config/strategies.yaml::fade_breakout.min_confidence' \
        --current-value 0.0 --search-space 'uniform [0.0, 0.6]' \
        --harness scripts/backtest_fade.py --data data/backtest_candles.csv

``--dry-run`` prints the planned grid + per-value invocations without running
the backtester (handy where the sandbox has no candle data).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# Default fee charged round-trip by every research harness. Kept in sync with
# the backtesting skill (scripts/backtest_*.py FEE_BPS_ROUNDTRIP). Net-of-fee
# is non-negotiable — S-STRAT-IMPROVE-S2/S4-A showed vwap gross-positive /
# net-negative once round-trip fees were charged.
DEFAULT_FEE_BPS_ROUNDTRIP = 7.5

# How many points a continuous (uniform / log-uniform) search space expands to
# when the recipe doesn't pin an explicit count.
DEFAULT_SAMPLES = 9

# Minimum trade count before a row is eligible to win on expectancy — variance
# can't be ruled out below this. Mirrors the research harnesses' min-20 rule.
MIN_TRADES_FOR_EXPECTANCY = 20

_REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Recipe + target parsing
# --------------------------------------------------------------------------- #
@dataclass
class TuneRecipe:
    """A strategy-review-gate ``tune_recipe`` block (M7 schema)."""

    target: str                       # "config/strategies.yaml::vwap.threshold"
    current_value: Optional[float]
    search_space: str                 # "log-uniform [0.001, 0.05]"
    harness: str                      # "scripts/backtest_vwap.py"
    evidence_window_days: Optional[int] = None
    note: str = ""
    # Extra backtester flags forwarded verbatim to every invocation, so the
    # sweep pins the strategy's LIVE params (timeframe, donchian, trail, …).
    # Without this the harness runs at its CLI defaults and the optimum shifts
    # off the live config (the backtesting skill's "match the live params
    # exactly or the optimum shifts" rule). Holds tokens already split, e.g.
    # ["--timeframe", "1h", "--donchian", "20"].
    fixed_args: list[str] = field(default_factory=list)

    # Derived (filled by parse_target)
    config_file: str = field(init=False, default="")
    strategy: str = field(init=False, default="")
    param: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self.config_file, self.strategy, self.param = parse_target(self.target)


def parse_target(target: str) -> tuple[str, str, str]:
    """Split ``"config/strategies.yaml::vwap.threshold"`` into
    ``(config_file, strategy, param)``.
    """
    if "::" not in target:
        raise ValueError(
            f"tune_recipe.target must be 'file::strategy.param', got {target!r}"
        )
    config_file, dotted = target.split("::", 1)
    if "." not in dotted:
        raise ValueError(
            f"tune_recipe.target field must be 'strategy.param', got {dotted!r}"
        )
    strategy, param = dotted.split(".", 1)
    if not strategy or not param:
        raise ValueError(f"tune_recipe.target has an empty strategy/param: {target!r}")
    return config_file.strip(), strategy.strip(), param.strip()


def load_recipe(path: Path) -> TuneRecipe:
    """Read a ``tune_recipe`` from a JSON file.

    Accepts either a bare recipe object (``{"target": ..., ...}``) or a full
    review packet (``{"tune_recipe": {...}, ...}``) — the gate writes the
    latter, so a packet path Just Works.
    """
    obj = json.loads(path.read_text())
    if "tune_recipe" in obj and isinstance(obj["tune_recipe"], dict):
        obj = obj["tune_recipe"]
    if not obj or "target" not in obj:
        raise ValueError(
            f"{path} carries no tune_recipe (no 'target'); is this a 'tune' packet?"
        )
    cv = obj.get("current_value")
    return TuneRecipe(
        target=obj["target"],
        current_value=float(cv) if cv is not None else None,
        search_space=obj.get("search_space", ""),
        harness=obj.get("harness", ""),
        evidence_window_days=obj.get("evidence_window_days"),
        note=obj.get("note", ""),
        fixed_args=_coerce_fixed_args(obj.get("fixed_args")),
    )


def _coerce_fixed_args(raw: Any) -> list[str]:
    """Accept fixed_args as a token list (preferred) or a shell-style string."""
    if not raw:
        return []
    if isinstance(raw, str):
        import shlex

        return shlex.split(raw)
    return [str(x) for x in raw]


# --------------------------------------------------------------------------- #
# Search-space grammar
# --------------------------------------------------------------------------- #
_RANGE_RE = re.compile(r"^\s*([a-z-]+)?\s*\[(.*)\]\s*$", re.IGNORECASE)


def parse_search_space(
    spec: str, current_value: Optional[float], samples: int = DEFAULT_SAMPLES
) -> list[float]:
    """Expand a recipe ``search_space`` string into a sorted, de-duplicated
    grid of float values. Grammars (case-insensitive):

    * ``"log-uniform [lo, hi]"`` — ``samples`` points geometric-spaced.
    * ``"uniform [lo, hi]"``     — ``samples`` points linear-spaced.
    * ``"grid [a, b, c]"`` / ``"[a, b, c]"`` — exactly those values.
    * ``"lo:hi:step"``           — inclusive range (the confidence-sweep grammar).

    ``current_value`` (when not None) is always folded in so the baseline is
    measured on the same footing as the candidates.
    """
    if not spec or not spec.strip():
        raise ValueError("search_space is empty — nothing to sweep")

    spec = spec.strip()
    values: list[float] = []

    # "lo:hi:step" colon grammar (no brackets).
    if ":" in spec and "[" not in spec:
        parts = [p.strip() for p in spec.split(":")]
        if len(parts) != 3:
            raise ValueError(f"colon search_space must be 'lo:hi:step', got {spec!r}")
        lo, hi, step = (float(p) for p in parts)
        if step <= 0:
            raise ValueError("colon search_space step must be > 0")
        n = lo
        while n <= hi + 1e-12:
            values.append(round(n, 12))
            n += step
    else:
        m = _RANGE_RE.match(spec)
        if not m:
            raise ValueError(
                f"unparseable search_space {spec!r} — expected "
                "'<kind> [lo, hi]', '[a, b, c]', or 'lo:hi:step'"
            )
        kind = (m.group(1) or "grid").lower().replace("-", "").replace("_", "")
        inner = [x.strip() for x in m.group(2).split(",") if x.strip()]
        nums = [float(x) for x in inner]
        if kind in ("grid", "list", ""):
            values = nums
        elif kind in ("uniform", "linear", "lin"):
            values = _linspace(*_lohi(nums), samples)
        elif kind in ("loguniform", "log", "logarithmic", "geometric", "geo"):
            values = _logspace(*_lohi(nums), samples)
        else:
            raise ValueError(
                f"unknown search_space kind {kind!r} (use grid/uniform/log-uniform)"
            )

    if current_value is not None:
        values.append(float(current_value))

    # Sort + de-dup with a tolerance so 0.0100000001 and 0.01 collapse.
    out: list[float] = []
    for v in sorted(values):
        if not out or abs(v - out[-1]) > 1e-9:
            out.append(round(v, 12))
    if not out:
        raise ValueError(f"search_space {spec!r} expanded to an empty grid")
    return out


def _lohi(nums: list[float]) -> tuple[float, float]:
    if len(nums) != 2:
        raise ValueError("uniform/log-uniform search_space needs exactly [lo, hi]")
    lo, hi = nums
    if hi <= lo:
        raise ValueError(f"search_space hi ({hi}) must exceed lo ({lo})")
    return lo, hi


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    if n < 2:
        return [lo, hi]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def _logspace(lo: float, hi: float, n: int) -> list[float]:
    if lo <= 0:
        raise ValueError(f"log-uniform search_space lo ({lo}) must be > 0")
    if n < 2:
        return [lo, hi]
    la, lb = math.log(lo), math.log(hi)
    return [math.exp(la + (lb - la) * i / (n - 1)) for i in range(n)]


# --------------------------------------------------------------------------- #
# Harness dispatch registry
# --------------------------------------------------------------------------- #
@dataclass
class HarnessSpec:
    """How to drive one backtester for one swept parameter.

    Two dispatch styles:

    * ``per_value`` — the param maps to a CLI flag; we invoke the harness once
      per grid value (``flag`` carries the value). Uniform + simple; the
      default for research-harness params.
    * native sweep — set ``native_sweep_flag`` when the harness has a built-in
      sweep mode covering this param (e.g. vwap ``--threshold-sweep`` walks a
      module constant a CLI flag can't reach). We invoke once and read the
      harness's own grid via ``native_rows_key`` / ``native_value_key``.
    """

    module: str                       # "scripts/backtest_fade.py" or "src.backtest.run_backtest_vwap"
    is_module: bool                   # True → `python -m <module>`, False → `python <path>`
    flag: Optional[str] = None        # per-value flag, e.g. "--min-confidence"
    native_sweep_flag: Optional[str] = None
    native_rows_key: Optional[str] = None   # top-level key holding the grid rows
    native_value_key: Optional[str] = None  # per-row key carrying the swept value
    extra_args: list[str] = field(default_factory=list)
    # Date-window flags the harness accepts for a walk-forward split. All the
    # research harnesses + vwap take "--start"/"--end" (inclusive ISO dates).
    window_flags: tuple[str, str] = ("--start", "--end")


@dataclass
class WalkForward:
    """A chronological train / out-of-sample holdout split.

    Each grid value is evaluated on the train window AND the OOS window via the
    harness's ``window_flags``; the recommendation is gated on **OOS** metrics —
    an in-sample optimum that doesn't hold out-of-sample is not actionable
    evidence (the live ``trend_donchian`` floor was set under walk-forward CV).
    ``oos_start`` is the split boundary; train = ``[train_start, oos_start)``,
    OOS = ``[oos_start, oos_end]`` (open ends default to the data extent).
    """

    oos_start: str
    train_start: Optional[str] = None
    oos_end: Optional[str] = None


@dataclass
class KFold:
    """Anchored (expanding-window) k-fold walk-forward over ``[wf_start, wf_end]``.

    The first ``train_frac`` of the span is the initial train window; the
    remaining span is split into ``folds`` equal OOS segments. Fold *k* trains on
    everything before its OOS segment (expanding/anchored) and tests on the
    segment. A tuning value that wins on a single split can be a lucky regime;
    k-fold confirms it holds across several — the discipline the live
    ``trend_donchian`` floor was originally set under (3-fold).
    """

    wf_start: str
    wf_end: str
    folds: int = 3
    train_frac: float = 0.4


def _parse_iso_date(s: str) -> datetime:
    """Parse an ISO date/datetime (date-only accepted) to a UTC datetime."""
    s = s.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.fromisoformat(s + "T00:00:00")


def _iso_day(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def fold_windows(wf: Optional[WalkForward], kf: Optional[KFold]) -> Optional[
    list[tuple[tuple[Optional[str], Optional[str]], tuple[Optional[str], Optional[str]]]]
]:
    """Return the list of ``((train_start, train_end), (oos_start, oos_end))``
    windows for the run, or None for a full-sample (no-split) run.

    A single ``WalkForward`` yields one fold; a ``KFold`` yields N anchored folds.
    """
    if kf is not None:
        start, end = _parse_iso_date(kf.wf_start), _parse_iso_date(kf.wf_end)
        total = (end - start).days
        if total <= 0:
            raise ValueError(f"kfold wf_end ({kf.wf_end}) must be after wf_start ({kf.wf_start})")
        if kf.folds < 1:
            raise ValueError("kfold folds must be >= 1")
        if not 0.0 < kf.train_frac < 1.0:
            raise ValueError("kfold train_frac must be in (0, 1)")
        train_days = total * kf.train_frac
        seg = (total - train_days) / kf.folds
        if seg <= 0:
            raise ValueError("kfold: train_frac leaves no room for OOS folds")
        out = []
        for k in range(kf.folds):
            oos_s = start + timedelta(days=round(train_days + seg * k))
            oos_e = start + timedelta(days=round(train_days + seg * (k + 1)))
            out.append(((kf.wf_start, _iso_day(oos_s)), (_iso_day(oos_s), _iso_day(oos_e))))
        return out
    if wf is not None:
        return [((wf.train_start, wf.oos_start), (wf.oos_start, wf.oos_end))]
    return None


# Registry keyed by (harness basename, param). Seeded with the verifiable cases;
# the doc names this as the extension seam — add a row here to cover a new
# (harness, param). An unknown pair raises with a pointer to docs/strategy-tuning.md.
_REGISTRY: dict[tuple[str, str], HarnessSpec] = {
    # Research harnesses share the confidence-floor flag + sweep grammar.
    ("backtest_fade.py", "min_confidence"): HarnessSpec(
        module="scripts/backtest_fade.py", is_module=False, flag="--min-confidence"
    ),
    ("backtest_squeeze.py", "min_confidence"): HarnessSpec(
        module="scripts/backtest_squeeze.py", is_module=False, flag="--min-confidence"
    ),
    ("backtest_trend.py", "min_confidence"): HarnessSpec(
        module="scripts/backtest_trend.py", is_module=False, flag="--min-confidence"
    ),
    ("backtest_ict_scalp.py", "min_confidence"): HarnessSpec(
        module="scripts/backtest_ict_scalp.py", is_module=False, flag="--min-confidence"
    ),
    # vwap entry threshold is a module constant — only the native sweep reaches it.
    ("run_backtest_vwap.py", "threshold"): HarnessSpec(
        module="src.backtest.run_backtest_vwap",
        is_module=True,
        native_sweep_flag="--threshold-sweep",
        native_rows_key="threshold_comparison",
        native_value_key="entry_std_threshold",
    ),
}

# Aliases so a recipe can name the harness loosely (path or bare name).
_HARNESS_ALIASES = {
    "scripts/backtest_vwap.py": "run_backtest_vwap.py",
    "backtest_vwap.py": "run_backtest_vwap.py",
    "src/backtest/run_backtest_vwap.py": "run_backtest_vwap.py",
}


def resolve_spec(recipe: TuneRecipe) -> HarnessSpec:
    """Look up the (harness, param) → HarnessSpec, tolerating loose harness names."""
    raw = recipe.harness.strip()
    base = _HARNESS_ALIASES.get(raw, Path(raw).name)
    key = (base, recipe.param)
    if key not in _REGISTRY:
        known = ", ".join(sorted(f"{h}:{p}" for h, p in _REGISTRY))
        raise KeyError(
            f"no harness mapping for ({base}, {recipe.param}). "
            f"Add a row to scripts/ml/strategy_tune_sweep.py::_REGISTRY "
            f"(see docs/strategy-tuning.md § Extending the registry). Known: {known}"
        )
    return _REGISTRY[key]


# --------------------------------------------------------------------------- #
# Metric normalization — fold heterogeneous harness output to one canonical row
# --------------------------------------------------------------------------- #
# Candidate keys per canonical field, in priority order. The research harnesses
# emit R-denominated net metrics; the vwap rows emit their own; we read whatever
# the run produced and fall back to None so a missing metric is honest, not 0.
_METRIC_KEYS: dict[str, tuple[str, ...]] = {
    "trades": ("trades", "total_trades", "num_trades", "n_trades"),
    "win_rate_pct": ("win_rate_pct", "win_rate", "winRate"),
    "net_total": ("net_total_r", "net_total", "net_pnl", "total_pnl", "total_return_pct"),
    "net_expectancy": ("net_expectancy_r", "net_expectancy", "expectancy"),
    "max_drawdown": ("max_drawdown_r", "max_drawdown_pct", "max_drawdown"),
}


def normalize_row(value: float, summary: dict[str, Any]) -> dict[str, Any]:
    """Project a harness's per-run summary onto the canonical metric row."""
    row: dict[str, Any] = {"value": value}
    for canonical, candidates in _METRIC_KEYS.items():
        row[canonical] = _first_present(summary, candidates)
    return row


def _first_present(d: dict[str, Any], keys: tuple[str, ...]) -> Optional[float]:
    for k in keys:
        if k in d and d[k] is not None:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                return None
    return None


_METRIC_FIELDS = ("trades", "win_rate_pct", "net_total", "net_expectancy", "max_drawdown")


def _wf_row(value: float, train_summary: dict, oos_summary: dict) -> dict[str, Any]:
    """A walk-forward grid row: top-level = OOS metrics (so the pickers select on
    OOS), with the in-sample metrics nested under ``train``."""
    row = normalize_row(value, oos_summary)  # top-level == OOS
    train = normalize_row(value, train_summary)
    row["train"] = {k: train[k] for k in _METRIC_FIELDS}
    return row


def _agg_sum(rows: list[dict], key: str) -> Optional[float]:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return sum(vals) if vals else None


def _agg_mean(rows: list[dict], key: str) -> Optional[float]:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return (sum(vals) / len(vals)) if vals else None


def _agg_worst(rows: list[dict], key: str) -> Optional[float]:
    # max drawdown is reported as a positive magnitude → worst = max
    vals = [r[key] for r in rows if r.get(key) is not None]
    return max(vals) if vals else None


def _aggregate_folds(value: float, fold_results: list[dict]) -> dict[str, Any]:
    """Fold a value's per-fold (train, oos) summaries into one grid row.

    Top-level metrics are the **OOS aggregate** (net_total summed across folds,
    expectancy averaged, drawdown = worst fold), so the existing pickers select
    on OOS. In-sample aggregate is under ``train``; per-fold detail under
    ``folds``; ``folds_positive`` / ``n_folds`` drive the robustness gate. A
    single fold reproduces the S2 single-split row exactly.
    """
    oos = [normalize_row(value, f["oos"]) for f in fold_results]
    train = [normalize_row(value, f["train"]) for f in fold_results]
    row: dict[str, Any] = {
        "value": value,
        "trades": _agg_sum(oos, "trades"),
        "win_rate_pct": _agg_mean(oos, "win_rate_pct"),
        "net_total": _agg_sum(oos, "net_total"),
        "net_expectancy": _agg_mean(oos, "net_expectancy"),
        "max_drawdown": _agg_worst(oos, "max_drawdown"),
        "train": {
            "trades": _agg_sum(train, "trades"),
            "win_rate_pct": _agg_mean(train, "win_rate_pct"),
            "net_total": _agg_sum(train, "net_total"),
            "net_expectancy": _agg_mean(train, "net_expectancy"),
            "max_drawdown": _agg_worst(train, "max_drawdown"),
        },
        "folds_positive": sum(1 for r in oos if (r.get("net_total") or 0) > 0),
        "n_folds": len(oos),
        "folds": [
            {
                "oos_start": f["oos_start"], "oos_end": f["oos_end"],
                "oos": {k: o[k] for k in _METRIC_FIELDS},
                "train": {k: t[k] for k in _METRIC_FIELDS},
            }
            for f, o, t in zip(fold_results, oos, train)
        ],
    }
    return row


# --------------------------------------------------------------------------- #
# Backtester invocation
# --------------------------------------------------------------------------- #
def _python() -> str:
    return sys.executable or "python3"


def build_invocation(
    spec: HarnessSpec,
    *,
    value: Optional[float],
    data: Optional[str],
    fee_bps: float,
    window_days: Optional[int],
    fixed_args: Optional[list[str]] = None,
    window: Optional[tuple[Optional[str], Optional[str]]] = None,
) -> list[str]:
    """Assemble the argv for one backtester call (per-value or native-sweep)."""
    argv = [_python()]
    argv += ["-m", spec.module] if spec.is_module else [str(_REPO_ROOT / spec.module)]
    if data:
        argv += ["--data", data]
    argv += ["--fee-bps-roundtrip", str(fee_bps)]
    argv += spec.extra_args
    if fixed_args:
        argv += list(fixed_args)
    if window is not None:
        start, end = window
        start_flag, end_flag = spec.window_flags
        if start:
            argv += [start_flag, start]
        if end:
            argv += [end_flag, end]
    if spec.native_sweep_flag:
        # The native-sweep harness (vwap) prints its result dict to stdout
        # unconditionally and does not define a --json flag — don't pass one.
        argv += [spec.native_sweep_flag]
    elif spec.flag is not None and value is not None:
        argv += [spec.flag, str(value)]
        # Research harnesses route "--json -" to stdout (table first, JSON last).
        argv += ["--json", "-"]
    return argv


def _run_capture(argv: list[str]) -> dict[str, Any]:
    """Run a harness and parse the JSON it prints to stdout (last JSON object)."""
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"harness exited {proc.returncode}: {' '.join(argv)}\n{proc.stderr[-2000:]}"
        )
    return _extract_json(proc.stdout)


def _extract_json(stdout: str) -> dict[str, Any]:
    """Pull the JSON object a harness printed to stdout.

    The harnesses print a human-readable table first (which can itself contain a
    Python-dict repr like ``{'donchian': 20}`` — single quotes, NOT JSON) and the
    real ``json.dumps`` payload last. The payload also has **nested** objects
    (e.g. trend's ``by_year``/``by_outcome``), so a naive first-``{``/last-``}``
    span or a "last object found" scan grabs table junk or an inner sub-object.
    Scan for **top-level** objects only: decode at each ``{``, and on success
    skip past the consumed span (so nested braces inside it aren't considered),
    keeping the last top-level object — the payload the harness prints last.
    """
    s = stdout.strip()
    try:
        return json.loads(s)  # fast path: whole stdout is the JSON
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    found: Optional[dict[str, Any]] = None
    i, n = 0, len(s)
    while i < n:
        if s[i] != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(s[i:])
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            found = obj
        i += end  # jump past the consumed object — don't descend into nested braces
    if found is None:
        raise RuntimeError(f"no JSON object in harness stdout:\n{stdout[-2000:]}")
    return found


def run_sweep(
    recipe: TuneRecipe,
    *,
    data: Optional[str],
    fee_bps: float,
    samples: int,
    runner: Callable[[list[str]], dict[str, Any]] = _run_capture,
    wf: Optional[WalkForward] = None,
    kfold: Optional[KFold] = None,
) -> dict[str, Any]:
    """Execute the recipe's sweep and return the canonical tune result.

    With ``wf`` (single split) or ``kfold`` (N anchored folds) each value is run
    on the train AND OOS windows per fold; grid rows + picks are gated on the OOS
    aggregate (top-level metrics == OOS; in-sample under ``train``; per-fold under
    ``folds``). Without either, the run is full-sample (in-sample only).
    """
    spec = resolve_spec(recipe)
    grid = parse_search_space(recipe.search_space, recipe.current_value, samples)
    window_days = recipe.evidence_window_days
    folds = fold_windows(wf, kfold)

    def _invoke(value, window):
        return runner(build_invocation(
            spec, value=value, data=data, fee_bps=fee_bps, window_days=window_days,
            fixed_args=recipe.fixed_args, window=window,
        ))

    rows: list[dict[str, Any]] = []
    if spec.native_sweep_flag:
        # The harness walks its own grid in one call; re-key its rows onto value.
        if folds is None:
            for v, r in _native_rows_by_value(_invoke(None, None), spec).items():
                rows.append(normalize_row(v, r))
        else:
            per_value: dict[float, list[dict]] = {}
            for tw, ow in folds:
                train_by_val = _native_rows_by_value(_invoke(None, tw), spec)
                for v, r in _native_rows_by_value(_invoke(None, ow), spec).items():
                    per_value.setdefault(v, []).append(
                        {"oos_start": ow[0], "oos_end": ow[1], "train": train_by_val.get(v, {}), "oos": r}
                    )
            for v, fl in per_value.items():
                rows.append(_aggregate_folds(v, fl))
    else:
        for value in grid:
            if folds is None:
                rows.append(normalize_row(value, _invoke(value, None)))
            else:
                fl = [
                    {"oos_start": ow[0], "oos_end": ow[1],
                     "train": _invoke(value, tw), "oos": _invoke(value, ow)}
                    for tw, ow in folds
                ]
                rows.append(_aggregate_folds(value, fl))

    rows.sort(key=lambda r: (r["value"] if r["value"] == r["value"] else math.inf))
    best_total = _best(rows, "net_total", min_trades=0)
    best_exp = _best(rows, "net_expectancy", min_trades=MIN_TRADES_FOR_EXPECTANCY)
    baseline = _baseline_row(rows, recipe.current_value)

    if kfold is not None:
        wf_env: Optional[dict] = {
            "scheme": "kfold_anchored", "wf_start": kfold.wf_start, "wf_end": kfold.wf_end,
            "folds": kfold.folds, "train_frac": kfold.train_frac,
            "fold_windows": [{"train": list(tw), "oos": list(ow)} for tw, ow in (folds or [])],
        }
    elif wf is not None:
        wf_env = {"scheme": "single_split", "train_start": wf.train_start,
                  "oos_start": wf.oos_start, "oos_end": wf.oos_end}
    else:
        wf_env = None

    return {
        "schema": "strategy_tune_result/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy": recipe.strategy,
        "param": recipe.param,
        "target": recipe.target,
        "harness": recipe.harness,
        "fixed_args": list(recipe.fixed_args),
        "fee_bps_roundtrip": fee_bps,
        "evidence_window_days": window_days,
        "metric_basis": "oos" if folds is not None else "full_sample",
        "walk_forward": wf_env,
        "note": recipe.note,
        "current_value": recipe.current_value,
        "grid": rows,
        "best_by_net_total": best_total,
        "best_by_net_expectancy_minN": best_exp,
        "baseline_row": baseline,
        "recommendation": _recommendation(recipe, best_total, best_exp, baseline,
                                          oos=folds is not None, kfold=kfold is not None),
    }


def _native_rows_by_value(out: dict[str, Any], spec: HarnessSpec) -> dict[float, dict]:
    """Index a native-sweep harness's grid rows by their swept value."""
    by_val: dict[float, dict] = {}
    for r in out.get(spec.native_rows_key or "", []):
        val = r.get(spec.native_value_key) if spec.native_value_key else None
        if val is not None:
            by_val[float(val)] = r
    return by_val


def _best(rows: list[dict[str, Any]], key: str, *, min_trades: int) -> Optional[dict]:
    elig = [
        r
        for r in rows
        if r.get(key) is not None
        and (r.get("trades") or 0) >= min_trades
    ]
    return max(elig, key=lambda r: r[key]) if elig else None


def _baseline_row(rows: list[dict[str, Any]], cv: Optional[float]) -> Optional[dict]:
    if cv is None:
        return None
    for r in rows:
        if r["value"] == r["value"] and abs(r["value"] - cv) <= 1e-9:
            return r
    return None


def _recommendation(
    recipe: TuneRecipe,
    best_total: Optional[dict],
    best_exp: Optional[dict],
    baseline: Optional[dict],
    oos: bool = False,
    kfold: bool = False,
) -> dict[str, Any]:
    """Advisory only. Prefer the expectancy optimum (it carries the min-N floor);
    fall back to total. Under walk-forward the metrics are OOS, so the pick is
    OOS-validated; under k-fold it must also win across folds. Never returns a
    config write — names the Tier-3 line.
    """
    pick = best_exp or best_total
    if pick is None:
        return {
            "tier": 3,
            "action": "insufficient_evidence",
            "detail": "no grid row cleared the metric/min-trade floor; widen the window or grid.",
        }
    improves = (
        baseline is not None
        and baseline.get("net_total") is not None
        and pick.get("net_total") is not None
        and pick["net_total"] > baseline["net_total"] + 1e-9
        and pick["value"] != baseline["value"]
    )
    if kfold:
        basis = "OOS (k-fold walk-forward)"
    elif oos:
        basis = "OOS (single-split walk-forward)"
    else:
        basis = "full-sample (IN-SAMPLE — not OOS-validated)"
    detail = (
        "ADVISORY — applying this is a Tier-3 config/strategies.yaml change the "
        f"operator approves; this harness never writes config. Metric basis: {basis}."
    )
    rec: dict[str, Any] = {
        "tier": 3,
        "action": "propose_value" if improves or baseline is None else "hold_current",
        "proposed_value": pick["value"],
        "yaml_line": f"{recipe.target} : {pick['value']}",
        "beats_baseline": bool(improves),
        "metric_basis": "oos" if oos else "full_sample",
    }
    if oos:
        # Consistency gate: the OOS-optimal value should also be net-positive
        # in-sample, else the OOS lead may be noise rather than a robust edge.
        tr = (pick.get("train") or {}).get("net_total")
        rec["train_oos_consistent"] = bool(tr is not None and tr > 0 and (pick.get("net_total") or 0) > 0)
        if not rec["train_oos_consistent"]:
            detail += " ⚠ OOS pick is not net-positive in-sample — treat as weak."
    if kfold:
        # Robustness gate: the pick should be net-positive in EVERY fold.
        fp, nf = pick.get("folds_positive"), pick.get("n_folds")
        rec["folds_positive"] = fp
        rec["n_folds"] = nf
        rec["robust"] = bool(nf and fp == nf)
        if not rec["robust"]:
            detail += f" ⚠ OOS pick positive in only {fp}/{nf} folds — not robust across regimes."
    if not oos:
        detail += " Add a walk-forward split (--oos-start / --wf-folds) before this clears the go-live bar."
    rec["detail"] = detail
    return rec


# --------------------------------------------------------------------------- #
# Output rendering
# --------------------------------------------------------------------------- #
def render_markdown(result: dict[str, Any]) -> str:
    L = [
        f"# Tune sweep — `{result['strategy']}.{result['param']}`",
        "",
        f"- **Target:** `{result['target']}`",
        f"- **Harness:** `{result['harness']}`  ·  fee {result['fee_bps_roundtrip']} bps round-trip",
        f"- **Current value:** {result['current_value']}",
        f"- **Generated:** {result['generated_at']}",
    ]
    if result.get("note"):
        L.append(f"- **Recipe note:** {result['note']}")
    wf = result.get("walk_forward")
    scheme = wf.get("scheme") if wf else None
    if scheme == "single_split":
        L.append(f"- **Walk-forward (single split):** train [{wf.get('train_start') or '…'}, {wf['oos_start']}) · "
                 f"OOS [{wf['oos_start']}, {wf.get('oos_end') or '…'}] — metrics below are **OOS**.")
    elif scheme == "kfold_anchored":
        L.append(f"- **Walk-forward (k-fold anchored):** {wf['folds']} folds over "
                 f"[{wf['wf_start']}, {wf['wf_end']}], train_frac {wf['train_frac']} — metrics below are the **OOS aggregate** (net Σ, exp μ).")

    if scheme == "kfold_anchored":
        L += ["", "## Grid (net-of-fee · OOS aggregate across folds)", "",
              "| value | OOS trades | OOS net Σ | OOS exp μ | OOS maxDD | folds+ | train net Σ |",
              "|---|---|---|---|---|---|---|"]
        for r in result["grid"]:
            tr = r.get("train") or {}
            L.append(
                "| {v} | {t} | {nt} | {ne} | {dd} | {fp}/{nf} | {tnt} |".format(
                    v=_fmt(r["value"]), t=_fmt(r["trades"], 0), nt=_fmt(r["net_total"]),
                    ne=_fmt(r["net_expectancy"]), dd=_fmt(r["max_drawdown"]),
                    fp=_fmt(r.get("folds_positive"), 0), nf=_fmt(r.get("n_folds"), 0),
                    tnt=_fmt(tr.get("net_total")),
                )
            )
    elif scheme == "single_split":
        L += ["", "## Grid (net-of-fee · OOS | train)", "",
              "| value | OOS trades | OOS net | OOS exp | OOS maxDD | train net | train exp |",
              "|---|---|---|---|---|---|---|"]
        for r in result["grid"]:
            tr = r.get("train") or {}
            L.append(
                "| {v} | {t} | {nt} | {ne} | {dd} | {tnt} | {tne} |".format(
                    v=_fmt(r["value"]), t=_fmt(r["trades"], 0), nt=_fmt(r["net_total"]),
                    ne=_fmt(r["net_expectancy"]), dd=_fmt(r["max_drawdown"]),
                    tnt=_fmt(tr.get("net_total")), tne=_fmt(tr.get("net_expectancy")),
                )
            )
    else:
        L += ["", "## Grid (net-of-fee · IN-SAMPLE)", "",
              "| value | trades | win% | net_total | net_exp | maxDD |",
              "|---|---|---|---|---|---|"]
        for r in result["grid"]:
            L.append(
                "| {v} | {t} | {wr} | {nt} | {ne} | {dd} |".format(
                    v=_fmt(r["value"]), t=_fmt(r["trades"], 0), wr=_fmt(r["win_rate_pct"]),
                    nt=_fmt(r["net_total"]), ne=_fmt(r["net_expectancy"]), dd=_fmt(r["max_drawdown"]),
                )
            )
    bt, be = result["best_by_net_total"], result["best_by_net_expectancy_minN"]
    L += ["", "## Picks", ""]
    if bt:
        L.append(f"- **Best net_total:** value={_fmt(bt['value'])} (net_total={_fmt(bt['net_total'])}, trades={_fmt(bt['trades'],0)})")
    if be:
        L.append(f"- **Best net_expectancy (≥{MIN_TRADES_FOR_EXPECTANCY} trades):** value={_fmt(be['value'])} (exp={_fmt(be['net_expectancy'])}, trades={_fmt(be['trades'],0)})")
    rec = result["recommendation"]
    L += ["", "## Recommendation (advisory — Tier-3)", "",
          f"- **action:** `{rec['action']}`"]
    if "proposed_value" in rec:
        L.append(f"- **proposed value:** `{rec['proposed_value']}`")
        L.append(f"- **YAML line:** `{rec['yaml_line']}`")
        L.append(f"- **beats baseline:** {rec['beats_baseline']}")
        if "train_oos_consistent" in rec:
            L.append(f"- **train/OOS consistent:** {rec['train_oos_consistent']}")
        if "robust" in rec:
            L.append(f"- **robust across folds:** {rec['robust']} ({_fmt(rec.get('folds_positive'),0)}/{_fmt(rec.get('n_folds'),0)} OOS-positive)")
    L.append(f"- {rec['detail']}")
    return "\n".join(L) + "\n"


def _fmt(v: Any, places: int = 3) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    if isinstance(v, float):
        return f"{v:.{places}f}" if places else f"{int(round(v))}"
    return str(v)


def write_outputs(result: dict[str, Any], out_dir: Optional[Path]) -> tuple[Path, Path]:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    root = out_dir or (_REPO_ROOT / "runtime_logs" / "strategy_tunes" / day)
    root.mkdir(parents=True, exist_ok=True)
    stem = f"{result['strategy']}__{result['param']}"
    json_path = root / f"{stem}.json"
    md_path = root / f"{stem}.md"
    json_path.write_text(json.dumps(result, indent=2, default=str))
    md_path.write_text(render_markdown(result))
    return json_path, md_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _recipe_from_args(args: argparse.Namespace) -> TuneRecipe:
    if args.recipe:
        recipe = load_recipe(Path(args.recipe))
        # CLI --fixed-args augments the recipe's (e.g. pin live params the gate
        # didn't author into the packet yet).
        recipe.fixed_args = recipe.fixed_args + _coerce_fixed_args(args.fixed_args)
        return recipe
    if not (args.target and args.search_space and args.harness):
        raise SystemExit(
            "provide --recipe, or all of --target/--search-space/--harness"
        )
    cv = float(args.current_value) if args.current_value is not None else None
    return TuneRecipe(
        target=args.target,
        current_value=cv,
        search_space=args.search_space,
        harness=args.harness,
        evidence_window_days=args.window_days,
        note=args.note or "",
        fixed_args=_coerce_fixed_args(args.fixed_args),
    )


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--recipe", help="Path to a review packet (with tune_recipe) or a bare recipe JSON.")
    p.add_argument("--target", help="config/strategies.yaml::strategy.param (if not using --recipe).")
    p.add_argument("--current-value", type=float, default=None)
    p.add_argument("--search-space", help="e.g. 'log-uniform [0.001, 0.05]', '[0.8,1.0,1.2]', '0:0.6:0.1'.")
    p.add_argument("--harness", help="Backtester the param belongs to (e.g. scripts/backtest_fade.py).")
    p.add_argument("--window-days", type=int, default=None, help="Evidence window in days (recipe override).")
    p.add_argument("--note", default=None)
    p.add_argument("--fixed-args", default=None,
                   help="Extra backtester flags forwarded to every run, shell-quoted, "
                        "to pin the strategy's live params, e.g. "
                        "--fixed-args '--timeframe 1h --donchian 20 --trail-mult 5.0'.")
    p.add_argument("--data", default=None, help="Candle CSV; defaults to each harness's BACKTEST_DATA_PATH.")
    p.add_argument("--fee-bps-roundtrip", type=float, default=DEFAULT_FEE_BPS_ROUNDTRIP)
    p.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help="Points for a continuous search space.")
    p.add_argument("--out-dir", default=None, help="Override output dir (default runtime_logs/strategy_tunes/<date>/).")
    p.add_argument("--oos-start", default=None,
                   help="Enable a walk-forward split at this ISO date: train=[train-start, oos-start), "
                        "OOS=[oos-start, oos-end]. Metrics + picks become OOS-gated.")
    p.add_argument("--train-start", default=None, help="Walk-forward train-window start (default: data start).")
    p.add_argument("--oos-end", default=None, help="Walk-forward OOS-window end (default: data end).")
    p.add_argument("--wf-folds", type=int, default=None,
                   help="Enable k-fold anchored walk-forward with N folds over [--wf-start, --wf-end] "
                        "(robustness across regimes). Overrides --oos-start.")
    p.add_argument("--wf-start", default=None, help="K-fold span start (ISO date).")
    p.add_argument("--wf-end", default=None, help="K-fold span end (ISO date).")
    p.add_argument("--wf-train-frac", type=float, default=0.4,
                   help="Fraction of the k-fold span used as the initial anchored train window (default 0.4).")
    p.add_argument("--dry-run", action="store_true", help="Print the grid + planned invocations; don't run.")
    args = p.parse_args(argv)

    recipe = _recipe_from_args(args)
    kfold = None
    wf = None
    if args.wf_folds:
        if not (args.wf_start and args.wf_end):
            raise SystemExit("--wf-folds requires --wf-start and --wf-end")
        kfold = KFold(wf_start=args.wf_start, wf_end=args.wf_end,
                      folds=args.wf_folds, train_frac=args.wf_train_frac)
    elif args.oos_start:
        wf = WalkForward(oos_start=args.oos_start, train_start=args.train_start, oos_end=args.oos_end)

    if args.dry_run:
        spec = resolve_spec(recipe)
        grid = parse_search_space(recipe.search_space, recipe.current_value, args.samples)
        print(f"strategy={recipe.strategy} param={recipe.param} harness={spec.module}")
        print(f"grid ({len(grid)}): {grid}")
        if recipe.fixed_args:
            print(f"fixed_args: {recipe.fixed_args}")
        folds = fold_windows(wf, kfold)
        if folds is None:
            windows = [(None, None)]
        else:
            windows = []
            for n, (tw, ow) in enumerate(folds):
                windows.append((f"fold{n}-train", tw))
                windows.append((f"fold{n}-oos", ow))
            print(f"walk-forward ({'kfold' if kfold else 'single'}): "
                  + " | ".join(f"oos[{ow[0]},{ow[1]}]" for _, ow in folds))
        if spec.native_sweep_flag:
            for label, win in windows:
                argv_ = build_invocation(spec, value=None, data=args.data,
                                         fee_bps=args.fee_bps_roundtrip, window_days=recipe.evidence_window_days,
                                         fixed_args=recipe.fixed_args, window=win)
                print(f"native sweep{f' [{label}]' if label else ''}:", " ".join(argv_))
        else:
            for v in grid:
                for label, win in windows:
                    argv_ = build_invocation(spec, value=v, data=args.data,
                                             fee_bps=args.fee_bps_roundtrip, window_days=recipe.evidence_window_days,
                                             fixed_args=recipe.fixed_args, window=win)
                    print(f"  value={v}{f' [{label}]' if label else ''}:", " ".join(argv_))
        return 0

    result = run_sweep(
        recipe, data=args.data, fee_bps=args.fee_bps_roundtrip, samples=args.samples,
        wf=wf, kfold=kfold,
    )
    out_dir = Path(args.out_dir) if args.out_dir else None
    json_path, md_path = write_outputs(result, out_dir)
    print(render_markdown(result))
    print(f"\nJSON  -> {json_path}", file=sys.stderr)
    print(f"MD    -> {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
