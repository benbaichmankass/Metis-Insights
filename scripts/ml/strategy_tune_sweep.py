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
from datetime import datetime, timezone
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
    real ``json.dumps`` payload last. So a naive first-``{`` / last-``}`` span
    captures the table junk. Instead scan every ``{`` position with a strict
    decoder and keep the **last** one that parses cleanly — the payload.
    """
    s = stdout.strip()
    try:
        return json.loads(s)  # fast path: whole stdout is the JSON
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    found: Optional[dict[str, Any]] = None
    for i, ch in enumerate(s):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(s[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found = obj  # keep the last valid object (the harness prints it last)
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
) -> dict[str, Any]:
    """Execute the recipe's sweep and return the canonical tune result."""
    spec = resolve_spec(recipe)
    grid = parse_search_space(recipe.search_space, recipe.current_value, samples)
    window_days = recipe.evidence_window_days

    rows: list[dict[str, Any]] = []
    if spec.native_sweep_flag:
        # One call; the harness walks its own grid. We re-key its rows onto value.
        argv = build_invocation(
            spec, value=None, data=data, fee_bps=fee_bps, window_days=window_days,
            fixed_args=recipe.fixed_args,
        )
        out = runner(argv)
        native_rows = out.get(spec.native_rows_key or "", [])
        for r in native_rows:
            val = r.get(spec.native_value_key) if spec.native_value_key else None
            rows.append(normalize_row(float(val) if val is not None else math.nan, r))
    else:
        for value in grid:
            argv = build_invocation(
                spec, value=value, data=data, fee_bps=fee_bps, window_days=window_days,
                fixed_args=recipe.fixed_args,
            )
            out = runner(argv)
            rows.append(normalize_row(value, out))

    rows.sort(key=lambda r: (r["value"] if r["value"] == r["value"] else math.inf))
    best_total = _best(rows, "net_total", min_trades=0)
    best_exp = _best(rows, "net_expectancy", min_trades=MIN_TRADES_FOR_EXPECTANCY)
    baseline = _baseline_row(rows, recipe.current_value)

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
        "note": recipe.note,
        "current_value": recipe.current_value,
        "grid": rows,
        "best_by_net_total": best_total,
        "best_by_net_expectancy_minN": best_exp,
        "baseline_row": baseline,
        "recommendation": _recommendation(recipe, best_total, best_exp, baseline),
    }


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
) -> dict[str, Any]:
    """Advisory only. Prefer the expectancy optimum (it carries the min-N floor);
    fall back to total. Never returns a config write — names the Tier-3 line.
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
    return {
        "tier": 3,
        "action": "propose_value" if improves or baseline is None else "hold_current",
        "proposed_value": pick["value"],
        "yaml_line": f"{recipe.target} : {pick['value']}",
        "beats_baseline": bool(improves),
        "detail": (
            "ADVISORY — applying this is a Tier-3 config/strategies.yaml change the "
            "operator approves; this harness never writes config."
        ),
    }


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
    L += ["", "## Grid (net-of-fee)", "",
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
    p.add_argument("--dry-run", action="store_true", help="Print the grid + planned invocations; don't run.")
    args = p.parse_args(argv)

    recipe = _recipe_from_args(args)

    if args.dry_run:
        spec = resolve_spec(recipe)
        grid = parse_search_space(recipe.search_space, recipe.current_value, args.samples)
        print(f"strategy={recipe.strategy} param={recipe.param} harness={spec.module}")
        print(f"grid ({len(grid)}): {grid}")
        if recipe.fixed_args:
            print(f"fixed_args: {recipe.fixed_args}")
        if spec.native_sweep_flag:
            argv_ = build_invocation(spec, value=None, data=args.data,
                                     fee_bps=args.fee_bps_roundtrip, window_days=recipe.evidence_window_days,
                                     fixed_args=recipe.fixed_args)
            print("native sweep:", " ".join(argv_))
        else:
            for v in grid:
                argv_ = build_invocation(spec, value=v, data=args.data,
                                         fee_bps=args.fee_bps_roundtrip, window_days=recipe.evidence_window_days,
                                         fixed_args=recipe.fixed_args)
                print(f"  value={v}:", " ".join(argv_))
        return 0

    result = run_sweep(
        recipe, data=args.data, fee_bps=args.fee_bps_roundtrip, samples=args.samples
    )
    out_dir = Path(args.out_dir) if args.out_dir else None
    json_path, md_path = write_outputs(result, out_dir)
    print(render_markdown(result))
    print(f"\nJSON  -> {json_path}", file=sys.stderr)
    print(f"MD    -> {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
