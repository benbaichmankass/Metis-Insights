#!/usr/bin/env python3
# wiring: manual-only — a one-shot measurement of ONE harness/unit SEAM. It asks
# whether `backtest_ict_scalp.py` hands the live `order_package` the same inputs
# the live builder does. It has no scheduled caller and wants none: the answer is
# a finding to be read once, not a metric to trend.
"""MI-321 — the `ict_scalp` seam: same decision function, different inputs?

WHY THIS IS A DIFFERENT QUESTION FROM MI-319 AND MI-320
------------------------------------------------------
MI-320 established that `scripts/backtest_ict_scalp.py` **imports and calls the
live `order_package`** rather than reimplementing it. So there is no port to
diverge and the entry decision is exact BY CONSTRUCTION — which moves the
question rather than answering it. What can still differ is what the harness
FEEDS that function:

  * the **WINDOW** — the harness slides a `window_size`-bar slice; the live
    builder fetches `limit=200`. This is MI-319's declared-minimum class.
  * the **CONFIG** — the harness read ONE hardcoded YAML block; the live fleet
    runs EIGHT legs, seven of them through a variant builder reading their own.
    ⚠️ **PAST TENSE SINCE 2026-09-22 (E28): the hardcoding is FIXED** —
    `backtest_ict_scalp.py::_load_yaml_params(name)` now takes the block
    `--strategy-name` selects and RAISES on an unknown name. What this tool
    still measures is unchanged and still worth measuring: it describes a
    DEFAULT run (it calls `_load_yaml_params()` with no argument), i.e. what a
    caller gets when it does NOT say which leg it means. The `not_expressible`
    / `asymmetric_gate` verdicts on `off_cells` / `vol_spec` are NOT fixed by
    E28 and stand exactly as recorded — no flag was added for either, and
    `regime_debt_matrix` grades `ict_scalp_xrp_5m` `approximate` naming both.
  * the **GATES OUTSIDE THE UNIT** — the live variant builder applies an
    off-cell regime suppression that `order_package` knows nothing about.

⚠️ THE WINDOW QUESTION IS SETTLED BY MEASUREMENT AND THE ANSWER IS A CLEAN
NEGATIVE, WHICH MUST NOT BE GENERALISED FROM MI-319. `fvg_range_15m` diverged
at its declared minimum because its ADX is a three-deep `ewm(adjust=False)`
recursion, whose value depends on how much history preceded it. `ict_scalp`'s
`_add_atr` is `rolling(period, min_periods=period).mean()` — FINITE — and its
sweep / displacement / FVG scans are bounded slices. Quoting fvg's 21.9% here
would be the unprovenanced generalisation MI-319's own decision request warns
against; this module measures instead.

THE STATES
----------
Window:  `identical` · `diverges` · `no_accepts_in_population` (**NOT
         agreement** — a probe that accepted nothing has no denominator) ·
         `could_not_measure`.
Config:  per differing key, from TWO INDEPENDENT MEASUREMENTS rather than one
         clever predicate — (a) does the harness declare a CLI flag for it, and
         (b) does the key reach a SKIP (an `if` guarding `continue` / `break` /
         `return` / `raise`) on each side. The verdict is their JOIN:
         `not_expressible` (no flag AND the live side gates on it) ·
         `asymmetric_gate` (a flag exists, the live side SKIPS on it and the
         harness does not) · `restorable` (a flag exists and neither side is
         asymmetric) · `symmetric_no_gate` · `could_not_look`.

⚠️ THE JOIN IS THE POINT, AND A SINGLE PREDICATE WAS TRIED FIRST AND
ABANDONED. "Does this name reach a control-flow decision in the harness?" gives
`vol_spec` a TRUE — it guards `if vol_spec:` inside the routine that stamps a
regime LABEL — so a one-sided test reports it as implemented. What separates
`vol_spec` from `timeframe` is not how the harness treats it but that the LIVE
side SUPPRESSES AN ENTRY on it and the harness never skips at all. Measuring
both sides the same way and joining them says that; a predicate tuned until it
produced the answer already believed would not, and would be the fitted
classifier MI-320 had to widen for the same reason.

⚠️ `asymmetric_gate` IS NEVER POOLED WITH `restorable`. A flag that makes the
output SAY the right thing while the run is unchanged is this repo's
UNPROVENANCED DIAGNOSTIC OUTPUT class, sub-class A — and "there is a flag for
it" is exactly the reassuring reading the separate state exists to refuse.

Usage
-----
    python3 scripts/research/mi321_ict_scalp_seam.py --selftest
    python3 scripts/research/mi321_ict_scalp_seam.py --run [--candles PATH]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "scripts")):
    if _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
sys.path.insert(0, str(_REPO_ROOT))

HARNESS = _REPO_ROOT / "scripts" / "backtest_ict_scalp.py"
UNIT = _REPO_ROOT / "src" / "units" / "strategies" / "ict_scalp.py"
BUILDERS = _REPO_ROOT / "src" / "runtime" / "strategy_signal_builders.py"
STRATEGIES_YAML = _REPO_ROOT / "config" / "strategies.yaml"

# The block `_load_yaml_params` serves when the caller names no leg — its
# DEFAULT since E28 (2026-09-22), a hardcode before it. Mirrors
# `backtest_ict_scalp.DEFAULT_CFG_KEY`; this tool deliberately keeps its own
# literal so a change to that constant shows up as a disagreement here.
HARNESS_CFG_KEY = "ict_scalp_5m"
# The keys `_load_yaml_params` strips before handing cfg to the unit.
HARNESS_STRIPS = ("enabled", "model", "signal_prefixes", "symbols",
                  "risk_pct", "shadow_model_ids")
LIVE_FETCH_LIMIT = 200          # strategy_signal_builders: fetch_candles(limit=200)

W_IDENTICAL = "identical"
W_DIVERGES = "diverges"
W_NO_ACCEPTS = "no_accepts_in_population"   # NOT agreement
W_COULD_NOT = "could_not_measure"

K_RESTORABLE = "restorable"
K_NOT_EXPRESSIBLE = "not_expressible"
K_ASYMMETRIC = "asymmetric_gate"
K_SYMMETRIC_NO_GATE = "symmetric_no_gate"
K_COULD_NOT = "could_not_look"

# Where the LIVE side's gates live: the unit itself, and the builder layer above
# it. Both are read, because MI-320 measured that a gate can sit in either --
# `side_filter` appears in ZERO unit files and is enforced in the builder.
LIVE_GATE_SOURCES = (UNIT, BUILDERS)


# ---------------------------------------------------------------------------
def harness_window_size(cfg: Dict[str, Any], warmup_bars: int = 50) -> int:
    """Reproduce the harness's own window arithmetic, from its source values.

    Deliberately re-derived from `cfg` rather than hardcoded, so that a change
    to `swing_lookback_bars` in YAML moves this the way it moves the harness.
    """
    base = max(int(cfg.get("swing_lookback_bars", 20)),
               int(cfg.get("sweep_lookback_bars", 12)),
               int(cfg.get("atr_period", 14))) + 10
    return max(base, warmup_bars)


def cli_flags(harness_path: Path = HARNESS) -> Dict[str, Any]:
    """Every `--flag` the harness declares, with its argparse default.

    Read from the AST, never from the help text: the help string is prose about
    the flag and this needs the value the parser will actually use.
    """
    try:
        tree = ast.parse(harness_path.read_text())
    except (OSError, SyntaxError, ValueError):
        return {}
    out: Dict[str, Any] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) != "add_argument":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        name = node.args[0].value
        if not isinstance(name, str) or not name.startswith("--"):
            continue
        default: Any = None
        for kw in node.keywords:
            if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                default = kw.value.value
        out[name] = default
    return out


def flag_for(key: str, flags: Dict[str, Any]) -> Optional[str]:
    """The CLI flag that would carry this config key, or None.

    ⚠️ AN EXACT MATCH IS TRIED FIRST AND A PREFIX MATCH IS THE FALLBACK, because
    a flag may name the FORMAT rather than the key: `vol_spec` is carried by
    `--vol-spec-json`, and an exact-only lookup reports "no flag exists" -- which
    would push the key into `not_expressible` and lose the very distinction this
    module is for (a flag that exists and changes nothing is a DIFFERENT and
    more misleading state than no flag at all).

    A prefix match must be UNAMBIGUOUS: two candidates mean we cannot say which
    flag carries the key, and guessing is how a fuzzy match becomes a fact.
    """
    exact = "--" + key.replace("_", "-")
    if exact in flags:
        return exact
    prefix = exact + "-"
    cands = sorted(f for f in flags if f.startswith(prefix))
    return cands[0] if len(cands) == 1 else None


def reaches_skip(identifier: str, path: Path) -> Optional[bool]:
    """Does this identifier guard a SKIP in this file?

    A skip is an `if`/`while` whose TEST mentions the name and whose BODY
    reaches a `continue`, `break`, `return` or `raise`. That is deliberately
    narrower than "appears in a condition": `vol_spec` guards `if vol_spec:`
    inside the regime-STAMPING routine, whose body assigns and returns nothing,
    so a condition-only test calls it implemented.

    ⚠️ `None` means WE COULD NOT LOOK (unreadable or unparseable), never False.
    """
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError, ValueError):
        return None

    def _names(node: ast.AST) -> set:
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}

    def _skips(body: List[ast.stmt]) -> bool:
        for stmt in body:
            for n in ast.walk(stmt):
                if isinstance(n, (ast.Continue, ast.Break, ast.Raise)):
                    return True
                if isinstance(n, ast.Return):
                    return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While)) and identifier in _names(node.test):
            if _skips(node.body) or _skips(getattr(node, "orelse", [])):
                return True
    return False


def live_reaches_skip(identifier: str,
                      sources: tuple = LIVE_GATE_SOURCES) -> Optional[bool]:
    """Does the LIVE side skip on this key, in the unit OR the builder above it?

    ⚠️ `None` if EVERY source was unreadable -- we did not look. A single
    readable source answering False is an answer.
    """
    seen_any = False
    for src in sources:
        r = reaches_skip(identifier, src)
        if r is None:
            continue
        seen_any = True
        if r:
            return True
    return False if seen_any else None


def classify_key(key: str, flags: Dict[str, Any],
                 harness_path: Path = HARNESS) -> Dict[str, Any]:
    """Join the two measurements into one verdict for this config key."""
    f = flag_for(key, flags)
    h = reaches_skip(key, harness_path)
    lv = live_reaches_skip(key)
    row: Dict[str, Any] = {
        "flag": f,
        "default": flags.get(f) if f else None,
        "harness_reaches_skip": h,
        "live_reaches_skip": lv,
    }
    if h is None or lv is None:
        row["class"] = K_COULD_NOT
        return row
    if lv and not h:
        row["class"] = K_NOT_EXPRESSIBLE if f is None else K_ASYMMETRIC
        row["note"] = (
            "the live side SKIPS an entry on this key and the harness never "
            "does" + ("" if f is None else
                      f"; `{f}` exists but the value reaches no skip, so a run "
                      "can be LABELLED with it while behaving as if it were "
                      "unset"))
        return row
    row["class"] = K_RESTORABLE if f else K_SYMMETRIC_NO_GATE
    return row


def live_off_cell_suppression(builders_path: Path = BUILDERS) -> Dict[str, Any]:
    """Does the LIVE variant builder suppress on an off-cell?

    The positive half of the asymmetry. Without it, "the harness has no
    off_cells" is a fact about one file and says nothing about the fleet.
    """
    try:
        src = builders_path.read_text()
    except OSError:
        return {"state": "could_not_look"}
    assigns = bool(re.search(r"off_cells\s*=\s*.*\.get\(\s*[\"']off_cells[\"']", src))
    gated = bool(re.search(r"if\s+off_cells\s+and\s+vol_spec", src))
    return {"state": "found" if (assigns and gated) else "not_found",
            "reads_config_key": assigns, "gates_on_it": gated,
            "occurrences": len(re.findall(r"\boff_cells\b", src))}


# ---------------------------------------------------------------------------
def measure_window(candles_path: Path, cfg: Dict[str, Any],
                   harness_bars: int, live_bars: int = LIVE_FETCH_LIMIT,
                   ) -> Dict[str, Any]:
    """Run the SAME live `order_package` at both window lengths, bar by bar.

    ⚠️ ZERO ACCEPTS IS `no_accepts_in_population`, NEVER `identical`. Two probes
    that both declined everything agree about nothing, and reporting that as
    agreement is how a measurement with no denominator gets quoted as a pass.
    """
    try:
        import backtest_ict_scalp as harness          # noqa: PLC0415
        from src.units.strategies.ict_scalp import order_package  # noqa: PLC0415
        df = harness._load_candles(str(candles_path))
    except (ImportError, OSError, ValueError, KeyError) as exc:
        return {"state": W_COULD_NOT, "reason": f"{type(exc).__name__}: {exc}"}

    def run(window) -> Optional[Dict[str, Any]]:
        try:
            return order_package(dict(cfg), candles_df=window)
        except ValueError:
            return None            # the unit's own non-actionable refusal

    def key(pkg: Dict[str, Any]):
        return (pkg.get("direction"),
                round(float(pkg["entry"]), 8), round(float(pkg["sl"]), 8),
                round(float(pkg.get("tp") or 0.0), 8),
                round(float(pkg.get("confidence") or 0.0), 8))

    graded = a_acc = b_acc = both = only_a = only_b = differing = 0
    examples: List[Dict[str, Any]] = []
    for i in range(live_bars, len(df) - 1):
        a = run(df.iloc[max(0, i + 1 - harness_bars): i + 1])
        b = run(df.iloc[max(0, i + 1 - live_bars): i + 1])
        graded += 1
        a_acc += bool(a)
        b_acc += bool(b)
        if a and b:
            both += 1
            if key(a) != key(b):
                differing += 1
                if len(examples) < 3:
                    examples.append({"bar": i, "harness": key(a), "live": key(b)})
        elif a:
            only_a += 1
            if len(examples) < 3:
                examples.append({"bar": i, "harness": key(a), "live": None})
        elif b:
            only_b += 1
            if len(examples) < 3:
                examples.append({"bar": i, "harness": None, "live": key(b)})

    if a_acc == 0 and b_acc == 0:
        state = W_NO_ACCEPTS
    elif differing or only_a or only_b:
        state = W_DIVERGES
    else:
        state = W_IDENTICAL
    return {
        "state": state,
        "candles": candles_path.name,
        "bars_in_file": len(df),
        "graded_bars": graded,
        "harness_window_bars": harness_bars,
        "live_window_bars": live_bars,
        "accepts_harness_window": a_acc,
        "accepts_live_window": b_acc,
        "both_accepted": both,
        "differing_geometry": differing,
        "only_harness_window": only_a,
        "only_live_window": only_b,
        "examples": examples,
        "verdict_note": (
            "`no_accepts_in_population` is NOT agreement -- two probes that "
            "declined everything agree about nothing. A non-zero accept count "
            "is what makes `identical` mean something, so it is reported "
            "beside the verdict rather than left implicit."),
    }


def leg_config_divergence() -> Dict[str, Any]:
    """Each ict_scalp leg's consumable config against the block the harness reads."""
    try:
        import yaml                                         # noqa: PLC0415
        from src.runtime.pipeline import monitor_unit_for   # noqa: PLC0415
        raw = yaml.safe_load(STRATEGIES_YAML.read_text())
    except (ImportError, OSError, ValueError) as exc:
        return {"state": "could_not_look", "reason": f"{type(exc).__name__}: {exc}"}
    strat = raw.get("strategies", raw)
    legs = [n for n, s in strat.items()
            if isinstance(s, dict) and monitor_unit_for(n) == "ict_scalp"]
    if HARNESS_CFG_KEY not in strat:
        return {"state": "could_not_look",
                "reason": f"{HARNESS_CFG_KEY} absent from strategies.yaml"}

    def consumable(block: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in block.items() if k not in HARNESS_STRIPS}

    base = consumable(strat[HARNESS_CFG_KEY])
    flags = cli_flags()
    out: Dict[str, Any] = {}
    for name in legs:
        cur = consumable(strat[name])
        diff = sorted(k for k in set(base) | set(cur) if base.get(k) != cur.get(k))
        out[name] = {
            "execution": strat[name].get("execution", "live"),
            "differing_keys": {k: classify_key(k, flags) for k in diff},
            "n_differing": len(diff),
        }
    return {"state": "measured", "harness_reads": HARNESS_CFG_KEY,
            "legs": out, "n_legs": len(legs),
            "n_live": sum(1 for n in legs
                          if strat[n].get("execution", "live") == "live")}


# ---------------------------------------------------------------------------
def _selftest() -> int:
    ok = fail = 0

    def check(label: str, cond: bool) -> None:
        nonlocal ok, fail
        if cond:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    flags = cli_flags()
    check("flags: the harness's flags are read from the AST",
          "--timeframe" in flags and "--warmup-bars" in flags)
    check("flags: --warmup-bars default is the value the parser uses (50)",
          flags.get("--warmup-bars") == 50)
    check("flags: --strategy-name defaults to the historical literal",
          flags.get("--strategy-name") == HARNESS_CFG_KEY)
    check("flag_for: an exact match wins",
          flag_for("timeframe", flags) == "--timeframe")
    check("flag_for: a FORMAT-suffixed flag is found by prefix",
          flag_for("vol_spec", flags) == "--vol-spec-json")
    check("flag_for(neg): a key with no flag at all stays None",
          flag_for("off_cells", flags) is None)
    check("flag_for(neg): an AMBIGUOUS prefix is refused, never guessed",
          flag_for("x", {"--x-a": None, "--x-b": None}) is None)

    check("window: the harness arithmetic is re-derived from cfg, not hardcoded",
          harness_window_size({"swing_lookback_bars": 300}) == 310)
    check("window: the warmup floor applies when the lookbacks are small",
          harness_window_size({}) == 50)

    # ⚠️ The skip predicate, and the POSITIVE CONTROL that makes its
    # negatives mean something.
    check("skip(control): a name that genuinely gates an entry is detected",
          reaches_skip("min_confidence", HARNESS) is True)
    check("skip: `off_cells` guards no skip in the harness",
          reaches_skip("off_cells", HARNESS) is False)
    check("skip: `vol_spec` guards no skip despite 12 occurrences and an `if`",
          reaches_skip("vol_spec", HARNESS) is False)
    check("skip(neg): an unreadable file returns None -- we did not look",
          reaches_skip("anything", _REPO_ROOT / "__nope__.py") is None)
    check("skip(live): the live side DOES skip on off_cells",
          live_reaches_skip("off_cells") is True)
    check("skip(live,neg): the live side does not skip on `timeframe`",
          live_reaches_skip("timeframe") is False)

    oc = classify_key("off_cells", flags)
    check("classify: off_cells -- live skips, harness cannot, no flag exists",
          oc["class"] == K_NOT_EXPRESSIBLE and oc["flag"] is None
          and oc["live_reaches_skip"] is True
          and oc["harness_reaches_skip"] is False)
    vs = classify_key("vol_spec", flags)
    check("classify: vol_spec -- a flag exists but reaches no skip",
          vs["class"] == K_ASYMMETRIC and vs["flag"] == "--vol-spec-json")
    tf = classify_key("timeframe", flags)
    check("classify: timeframe gates nothing on EITHER side -- no asymmetry",
          tf["class"] in (K_RESTORABLE, K_SYMMETRIC_NO_GATE))
    check("classify(neg): an asymmetric gate is NEVER pooled with a restorable "
          "key -- 'there is a flag for it' is the reading this refuses",
          vs["class"] != tf["class"])
    check("classify(neg): an unreadable side yields could_not_look, not a verdict",
          classify_key("off_cells", flags,
                       _REPO_ROOT / "__nope__.py")["class"] == K_COULD_NOT)

    sup = live_off_cell_suppression()
    check("live: the variant builder DOES suppress on an off-cell (control)",
          sup["state"] == "found" and sup["gates_on_it"] is True)
    check("live(neg): an absent builders file is could_not_look, not not_found",
          live_off_cell_suppression(_REPO_ROOT / "__nope__.py")["state"]
          == "could_not_look")

    div = leg_config_divergence()
    check("legs: every ict_scalp leg is resolved and graded",
          div["state"] == "measured" and div["n_legs"] >= 8)
    check("legs: the block the harness reads differs from itself in 0 keys",
          div["legs"][HARNESS_CFG_KEY]["n_differing"] == 0)

    # window-measure states, on synthetic inputs so no candles are needed
    empty = {"state": W_NO_ACCEPTS, "accepts_harness_window": 0,
             "accepts_live_window": 0}
    check("window(neg): zero accepts is its OWN state, never `identical`",
          empty["state"] != W_IDENTICAL)
    check("window: a missing candle file is could_not_measure, not a verdict",
          measure_window(_REPO_ROOT / "__no_such.csv", {}, 50)["state"]
          == W_COULD_NOT)

    print(f"mi321 selftest: {ok}/{ok + fail} passed")
    return 0 if fail == 0 else 1


def _run(candles: Path, out_path: Path) -> int:
    try:
        import backtest_ict_scalp as harness      # noqa: PLC0415
        cfg = harness._load_yaml_params()
    except (ImportError, OSError, ValueError) as exc:
        print(f"could not load harness cfg: {exc}")
        return 1
    cfg.setdefault("symbol", "BTCUSDT")
    cfg.setdefault("timeframe", "5m")
    hb = harness_window_size(cfg)

    window = measure_window(candles, cfg, hb)
    legs = leg_config_divergence()
    art = {
        "unit": "MI-321",
        "question": ("backtest_ict_scalp.py CALLS the live order_package -- so "
                     "does it hand it the same inputs the live builder does?"),
        "generated_at_utc": subprocess.run(
            ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
            capture_output=True, text=True, check=False).stdout.strip(),
        "window_seam": window,
        "config_seam": legs,
        "live_off_cell_suppression": live_off_cell_suppression(),
        "harness_cli_flags": len(cli_flags()),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(art, indent=2) + "\n")
    print(f"wrote {out_path.relative_to(_REPO_ROOT)}")
    print(f"WINDOW {window['state']}  harness={window.get('harness_window_bars')} "
          f"live={window.get('live_window_bars')} "
          f"accepts={window.get('accepts_harness_window')}/"
          f"{window.get('accepts_live_window')} "
          f"differing={window.get('differing_geometry')} "
          f"one_sided={window.get('only_harness_window')}/"
          f"{window.get('only_live_window')} "
          f"over {window.get('graded_bars')} graded bars")
    if legs.get("state") == "measured":
        print(f"CONFIG {legs['n_legs']} legs ({legs['n_live']} live), harness "
              f"reads {legs['harness_reads']}")
        for name, row in legs["legs"].items():
            if not row["n_differing"]:
                continue
            bits = ", ".join(f"{k}:{v['class']}"
                             for k, v in row["differing_keys"].items())
            print(f"  {name:22} {row['execution']:7} {bits}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--candles", default="data/btc_1m_sample.csv")
    ap.add_argument("--out",
                    default="docs/research/mi321-ict-scalp-seam-2026-09-18.json")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.run:
        return _run(_REPO_ROOT / args.candles, _REPO_ROOT / args.out)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
