#!/usr/bin/env python3
"""CI guard: no E7 orphan-harness dispatch may invoke the 5,001-row smoke fixture.

WHY THIS EXISTS
----------------
Checklist row E7/C4: nine (of thirteen) `scripts/backtest_*.py` harnesses had
**no workflow at all**, so nothing could regress their data source — there was
no runner to regress. `research-harness-dispatch.yml` is the first runner any
of them get. The moment a runner exists, so does the failure mode row E4 spent
a whole checklist item removing: a workflow step that pins `--data
data/backtest_candles.csv` (or any path resembling it) as a convenience
default, silently reproducing `BL-20260813-HARNESS-SYMBOL-IS-A-LABEL-DATA-
DEFAULTS-TO-BTC` one layer up — in the *workflow*, where
`scripts/ops/symbol_data_binding_census.py` (which grades the harnesses'
own argparse defaults, not their callers) cannot see it at all.

WHAT IT CHECKS, PER HARNESS SHAPE
-------------------------------------
Scoped to exactly the ten E7 harnesses (chop_scalp / fade / fvg_range / orb /
pullback / squeeze / funding_carry — the seven `--symbol`/`--data` harnesses;
pairs — two-leg `--symbol-a`/`--symbol-b`; xsec_momentum — basket
`--asset`/`--assets-dir`; vol_target — a pure overlay over another harness's
own `--emit-trades`, `--trades`/`--daily`), scanned wherever they are invoked
across `.github/workflows/*.yml`. Deliberately NOT scoped to the three
harnesses that already had a workflow before this row (ict_scalp, system,
trend) — one of those workflows (`ict-scalp-backtest.yml`) predates row E4 and
still declares `data: default: "data/backtest_candles.csv"` as an
operator-facing convenience input, which is a separate, already-known,
already-filed shape (the harness itself still refuses a bare invocation; only
the WORKFLOW default is pre-E4) and is not this guard's job to re-litigate.

For each invocation line of one of the ten:
  1. it must not contain the literal smoke-fixture path anywhere — the one
     check that applies uniformly regardless of shape;
  2. it must carry the flag(s) that give the harness a REAL population to
     resolve against (`--symbol`, or the harness's own equivalent) — an
     invocation with the corpus-flag missing entirely reaches the fixture by
     a different route (the harness's own historical default), which is
     exactly the `legacy_default` state row E4 made an explicit, deliberate
     opt-in rather than a default in every harness listed above.

THREE STATES, NEVER COLLAPSED. `clean` — matches neither problem. `fixture_hit`
— the literal fixture path appears in an invocation of one of the ten.
`corpus_flag_missing` — an invocation of one of the ten carries none of its
real-population flags. Both failure states are reported by name, never merged
into one generic "bad" count.

SELF-TEST. Plants BOTH a clean synthetic workflow (must pass) and one
carrying each violation shape (each must be caught, and caught by name) —
the guard is not demonstrated until it can fail loudly on its own target.

Tier-1: reads `.github/workflows/*.yml` text. No live path, no network.

Run:
    python3 scripts/ci/check_harness_dispatch_no_fixture_default.py --self-test
    python3 scripts/ci/check_harness_dispatch_no_fixture_default.py
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO / ".github" / "workflows"

#: The one path row E4 exists to stop a harness reaching by default.
FIXTURE_PATH = "backtest_candles.csv"

#: harness slug -> (script glob fragment, at-least-one-of these flags proves a
#: real population was named). Scoped to exactly the ten E7 orphans — see
#: module docstring for why the three pre-existing-workflow harnesses are
#: deliberately excluded.
HARNESS_REQUIRED_FLAGS: Dict[str, Tuple[str, ...]] = {
    "chop_scalp": ("--symbol",),
    "fade": ("--symbol",),
    "fvg_range": ("--symbol",),
    "orb": ("--symbol",),
    "pullback": ("--symbol",),
    "squeeze": ("--symbol",),
    "funding_carry": ("--symbol",),
    "pairs": ("--symbol-a", "--symbol-b"),
    "xsec_momentum": ("--asset", "--assets-dir"),
    "vol_target": ("--trades", "--daily"),
}

def _script_re() -> "re.Pattern[str]":
    """Built inside a function, deliberately, and not a module-level constant.

    `scripts/ci/check_guard_liveness.py` reads every MODULE-LEVEL string
    literal starting with a repo prefix as a declared subject path this guard
    depends on. A module-level `re.compile("scripts/backtest_(" + ...)` would
    hand it the fragment `scripts/backtest_(`, which is not a path at all —
    exactly the false subject that instrument's own docstring says function
    bodies are excluded to avoid. Memoised so it is still built once.
    """
    global _SCRIPT_RE_CACHE
    if _SCRIPT_RE_CACHE is None:
        _SCRIPT_RE_CACHE = re.compile(
            "scripts/backtest_(" + "|".join(re.escape(h) for h in HARNESS_REQUIRED_FLAGS)
            + r")\.py")
    return _SCRIPT_RE_CACHE


_SCRIPT_RE_CACHE: "Optional[re.Pattern[str]]" = None


def _logical_lines(text: str) -> List[str]:
    """Join backslash line-continuations so a wrapped shell command reads as
    ONE line — the same command a reader (and a shell) sees as one."""
    lines = text.splitlines()
    out: List[str] = []
    buf = ""
    for ln in lines:
        stripped = ln.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
        else:
            out.append(buf + ln)
            buf = ""
    if buf:
        out.append(buf)
    return out


def scan_text(text: str) -> List[str]:
    """Every problem found in ONE workflow file's text. Empty == clean."""
    problems: List[str] = []
    for ln in _logical_lines(text):
        m = _script_re().search(ln)
        if not m:
            continue
        harness = m.group(1)
        if FIXTURE_PATH in ln:
            problems.append(
                f"fixture_hit: {harness} invocation names {FIXTURE_PATH!r} — "
                f"row E4 exists so a harness never reaches the smoke fixture "
                f"by default; an EXPLICIT choice to smoke-test belongs in a "
                f"one-off manual run, not a standing dispatch workflow: {ln.strip()[:160]!r}")
        required = HARNESS_REQUIRED_FLAGS[harness]
        if not any(flag in ln for flag in required):
            problems.append(
                f"corpus_flag_missing: {harness} invocation carries none of "
                f"{required!r}, so it has no real population to resolve "
                f"against and falls through to the harness's own historical "
                f"default: {ln.strip()[:160]!r}")
    return problems


def _self_test() -> int:
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        ok &= cond
        print(f"  self-test {'ok  ' if cond else 'FAIL'}: {label}")

    clean = """
    - name: Run the harness
      run: |
        python3 scripts/backtest_squeeze.py --symbol "${SYM}" --timeframe "${TF}" \\
          --json result.json --emit-trades trades.jsonl
        python3 scripts/backtest_pairs.py --symbol-a "${SYM}" --symbol-b "${SYM_B}" \\
          --data-a "data/${SYM}_${TF}.csv" --data-b "data/${SYM_B}_${TF}.csv"
        python3 scripts/backtest_xsec_momentum.py --asset "SPY=data/SPY_1d.csv" \\
          --asset "QQQ=data/QQQ_1d.csv" --json result.json
        python3 scripts/backtest_vol_target.py --trades trades.jsonl --json result.json
    """
    check("positive control: a clean multi-harness dispatch is CLEAN",
         scan_text(clean) == [])

    fixture_hit = """
    - run: |
        python3 scripts/backtest_fade.py --symbol BTCUSDT \\
          --data data/backtest_candles.csv --json result.json
    """
    problems = scan_text(fixture_hit)
    check("negative: an explicit smoke-fixture --data is CAUGHT as fixture_hit",
         any(p.startswith("fixture_hit:") for p in problems))

    missing_symbol = """
    - run: |
        python3 scripts/backtest_orb.py --timeframe 5m --json result.json
    """
    problems = scan_text(missing_symbol)
    check("negative: an orb invocation with no --symbol is CAUGHT as corpus_flag_missing",
         any(p.startswith("corpus_flag_missing:") for p in problems))

    no_leg_at_all = """
    - run: |
        python3 scripts/backtest_pairs.py --timeframe 1h --json result.json
    """
    problems = scan_text(no_leg_at_all)
    check("negative: pairs with NEITHER --symbol-a nor --symbol-b is CAUGHT",
         any(p.startswith("corpus_flag_missing:") for p in problems))

    empty_basket = """
    - run: |
        python3 scripts/backtest_xsec_momentum.py --formation-days 28 --json result.json
    """
    problems = scan_text(empty_basket)
    check("negative: xsec_momentum with no --asset/--assets-dir is CAUGHT",
         any(p.startswith("corpus_flag_missing:") for p in problems))

    no_trades_source = """
    - run: |
        python3 scripts/backtest_vol_target.py --target-vol 0.1 --json result.json
    """
    problems = scan_text(no_trades_source)
    check("negative: vol_target with no --trades/--daily is CAUGHT",
         any(p.startswith("corpus_flag_missing:") for p in problems))

    already_wired_untouched = """
    - run: |
        python3 scripts/backtest_trend.py --data data/backtest_candles.csv --json result.json
    """
    check("positive control: a PRE-EXISTING harness outside the ten (trend) is "
         "OUT OF SCOPE for this guard, by design — see module docstring",
         scan_text(already_wired_untouched) == [])

    print(f"harness-dispatch-no-fixture-default self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not WORKFLOWS_DIR.is_dir():
        print(f"::error::{WORKFLOWS_DIR} not found")
        return 1

    total_problems: List[str] = []
    files_scanned = 0
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if not _script_re().search(text):
            continue
        files_scanned += 1
        for p in scan_text(text):
            total_problems.append(f"{path.relative_to(REPO)}: {p}")

    print(f"harness-dispatch-no-fixture-default: {files_scanned} workflow file(s) "
          f"touch an E7 harness, {len(total_problems)} problem(s)")
    for p in total_problems:
        print(f"  {p}")
    return 1 if total_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
