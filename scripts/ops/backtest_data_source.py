#!/usr/bin/env python3
# wiring: manual-only - this is a LIBRARY. Its consumers are the backtest
# harnesses that accept BOTH a `--symbol` and a `--data` (today
# scripts/backtest_trend.py and scripts/prop/account_compat_matrix.py). It has
# no schedule because deciding which file a run reads is not a job, it is
# something every run does.
"""Which candle file does this run actually read, and does it match `--symbol`?

THE DEFECT THIS EXISTS FOR
--------------------------
``BL-20260813-HARNESS-SYMBOL-IS-A-LABEL-DATA-DEFAULTS-TO-BTC``. Two harnesses
declare ``--symbol`` beside a ``--data`` that carries a HARDCODED default, so
the symbol is a DISPLAY LABEL and the data is whatever the default points at.
Nothing compares them.

REPRODUCED 2026-09-12 on this tree, which is why the fix is code and not a
docstring::

    $ python3 scripts/backtest_trend.py --symbol SOLUSDT --timeframe 1h
    trend_donchian - SOLUSDT 1h {...}
      data 2022-07-23 21:24:00+00:00 -> 2022-07-27 08:43:00+00:00  trades=144
      win_rate=31.25%  net_r=-82.6483 ...
    RC=0

A complete, confident result set - 144 trades, a win rate, a net-R - **headed
SOLUSDT and computed from the BTC fixture**, exit 0. The span printed is
verbatim the one the backlog row names as the fixture's. The sibling reaches
``bt._load_candles('/home/user/ict-trader-data/btc_5m.parquet')`` under
``--symbol SOLUSDT`` without ever asking whether the two agree.

``CLAUDE.md`` already names the class: UNPROVENANCED DIAGNOSTIC OUTPUT
**sub-class B**, *implicit input selection* - "newest / alphabetically-last / a
function DEFAULT substituted for the declared or pinned input", with nothing in
the output revealing the substitution.

THIS IS A POLICY, NOT A SECOND RESOLVER - READ THIS BEFORE EXTENDING IT
----------------------------------------------------------------------
The mapping from ``(symbol, timeframe)`` to a file already has ONE owner:
``scripts/research/m20_fleet_exit_sweep.py::resolve_data``. It knows the
``data/{SYMBOL}_{grain}.csv`` convention, the legacy prefix spellings, the
finest-grain-<=-leg rule, and the ``PROXY_DATA`` map whose default order is a
RECORDED decision (``BL-20260814-PROXY-MAP-SHADOWS-NATIVE-DATA``) that must not
be re-litigated by accident.

So this module **imports** that resolver and adds only the thing it has no
opinion about: *what to do when nothing resolves*. Writing a second mapping here
would be ``RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED``, and it would be the
WORSE kind - two functions answering "which file is SOLUSDT?" that can disagree
after the next proxy change.

FIVE STATES, NEVER COLLAPSED
----------------------------
``explicit`` - the caller named a file. Used as given and never second-guessed:
a caller naming a path is naming it, and refusing them because the name does not
look like their symbol would break every legitimate proxy, resample and ad-hoc
run.

``env`` - ``BACKTEST_DATA_PATH`` is set. Also explicit, by an operator, one
level up. ⚠️ It is a **fleet-wide** knob read by 23 files (``CLAUDE.md`` §
Environment Variables says so in terms, and corrects a prior claim that it was
M5-only), so this module must keep honouring it exactly.

``resolved`` - no explicit source, and the owner above found a file FOR THIS
SYMBOL. The good path.

``legacy_default`` - no explicit source and **no symbol was requested**. The
caller expressed no opinion, so the harness's historical default applies and
behaviour is unchanged. This is what keeps every existing invocation working.

``refused`` - a symbol WAS requested, nothing explicit was given, and no file
for that symbol exists. The only new refusal, and the whole point.

⚠️ WHY ``refused`` RATHER THAN "FALL BACK AND WARN". A warning on stderr is
exactly what the 144-trade SOLUSDT run above would have carried, and it would
still have printed a win rate. The output of a mislabelled run is not less
dangerous for being annotated - it is more, because it now looks reviewed.

⚠️ AND IT REFUSES FOR AN EXPLICITLY-NAMED DEFAULT SYMBOL TOO. ``--symbol
BTCUSDT`` with no data is refused on this tree, because nothing establishes that
``data/backtest_candles.csv`` holds BTC - the name does not say so and no
manifest does. That is a real behaviour change for anyone who typed the symbol
out; it is one line to fix (pass ``--data``), it is stated in the refusal
message, and the alternative is a special case that asserts a provenance nobody
has checked. Omitting ``--symbol`` entirely is untouched.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
from typing import Optional

__all__ = [
    "Resolution", "resolve_or_refuse", "refusal_message",
    "EXPLICIT", "ENV", "RESOLVED", "LEGACY_DEFAULT", "REFUSED",
    "DEFAULT_ENV_VAR",
]

#: The caller passed a path.
EXPLICIT = "explicit"
#: ``BACKTEST_DATA_PATH`` (or the caller's chosen env var) is set.
ENV = "env"
#: A file for this symbol was found by the canonical resolver.
RESOLVED = "resolved"
#: No symbol requested, so the harness's historical default applies.
LEGACY_DEFAULT = "legacy_default"
#: A symbol was requested and no data for it can be found. Exit non-zero.
REFUSED = "refused"

DEFAULT_ENV_VAR = "BACKTEST_DATA_PATH"

_REPO = pathlib.Path(__file__).resolve().parents[2]
_OWNER = _REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py"
_resolver = None


def _canonical_resolver():
    """``m20_fleet_exit_sweep.resolve_data``, loaded by path and memoised.

    Loaded lazily because it is a heavy module (it imports the runtime's
    tp_venue_cap and the exit-capture definition), and a harness that was given
    ``--data`` must not pay for it at all.
    """
    global _resolver
    if _resolver is None:
        spec = importlib.util.spec_from_file_location(
            "_m20_fleet_exit_sweep_for_data_source", _OWNER)
        if spec is None or spec.loader is None:  # pragma: no cover - packaging fault
            raise RuntimeError(
                f"backtest_data_source: cannot load the canonical (symbol, timeframe)"
                f" -> file resolver from {_OWNER} - refusing to guess it rather than"
                f" shipping a second definition (RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED)"
            )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _resolver = mod.resolve_data
    return _resolver


class Resolution:
    """Where this run's candles come from, and why.

    ``path`` is ``None`` only for :data:`REFUSED`. It is never a substituted
    default - a substituted default IS the defect.
    """

    __slots__ = ("state", "path", "symbol", "timeframe", "tried", "proxy", "resample")

    def __init__(self, state: str, path: Optional[str] = None, *, symbol=None,
                 timeframe=None, tried=(), proxy: bool = False, resample=None):
        self.state = state
        self.path = path
        self.symbol = symbol
        self.timeframe = timeframe
        self.tried = tuple(tried)
        self.proxy = proxy
        self.resample = resample

    @property
    def ok(self) -> bool:
        """True for every state except :data:`REFUSED`."""
        return self.state != REFUSED

    def provenance_line(self) -> str:
        """One line a harness prints so the run SAYS where its data came from.

        This is the half that makes the fix a provenance fix rather than only a
        refusal: a ``resolved`` run is still an implicit input selection unless
        the output names the file it picked.
        """
        if self.state == REFUSED:
            return f"  data REFUSED for symbol={self.symbol!r} timeframe={self.timeframe!r}"
        extra = ""
        if self.proxy:
            extra += " (PROXY series, not native)"
        if self.resample:
            extra += f" (resampled to {self.resample})"
        return (f"  data source [{self.state}] {self.path}"
                f" for symbol={self.symbol or '(unspecified)'}"
                f" timeframe={self.timeframe or '(unspecified)'}{extra}")

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (f"Resolution({self.state!r}, path={self.path!r}, "
                f"symbol={self.symbol!r}, timeframe={self.timeframe!r})")


def resolve_or_refuse(symbol: Optional[str], timeframe: Optional[str],
                      data_arg: Optional[str], *,
                      legacy_default: Optional[str] = None,
                      data_dir: Optional[pathlib.Path] = None,
                      env_var: str = DEFAULT_ENV_VAR,
                      env: Optional[dict] = None,
                      allow_implicit_default: bool = False) -> Resolution:
    """Decide this run's data source. See the module docstring for the states.

    ``data_arg`` MUST be ``None`` when the caller did not pass ``--data``, which
    means the harness's argparse default has to become ``None`` too. That is the
    load-bearing half of the wiring: while ``--data`` carries a default, the
    harness cannot tell "the caller named this file" from "argparse supplied it",
    and every run looks explicit.

    ``allow_implicit_default`` (docs/claude/work/MANAGER-CHECKLIST.json row E4,
    2026-09-25) governs the ONE case ``BL-20260813``/``BL-20260912`` left
    alone: no ``--symbol`` and no ``--data`` at all. Until E4 that combination
    silently returned :data:`LEGACY_DEFAULT` -- "no opinion expressed, so
    nothing to contradict" -- which is exactly how a bare invocation reaches
    the 5,001-row/3.5-day-of-2022 BTC fixture BY DEFAULT and prints a
    confident result computed from it. E4's mandate is that reaching the
    fixture by default become impossible while the fixture itself stays
    reachable as an explicit choice, so the DEFAULT here flips to
    :data:`REFUSED` naming ``--data <legacy_default>`` as the one-line smoke
    command. Passing ``allow_implicit_default=True`` is the escape hatch for a
    caller that wants the pre-E4 behaviour on purpose (this module's own
    ``--self-test`` uses it to keep asserting the old contract still exists).
    """
    environ = os.environ if env is None else env
    if data_arg:
        return Resolution(EXPLICIT, data_arg, symbol=symbol, timeframe=timeframe)
    env_val = (environ.get(env_var) or "").strip()
    if env_val:
        return Resolution(ENV, env_val, symbol=symbol, timeframe=timeframe)
    if not symbol:
        if allow_implicit_default:
            # No opinion expressed, so nothing to contradict. Opt-in only.
            return Resolution(LEGACY_DEFAULT, legacy_default, symbol=symbol,
                              timeframe=timeframe)
        # E4: a bare invocation no longer reaches the fixture silently.
        return Resolution(REFUSED, None, symbol=symbol, timeframe=timeframe,
                          tried=(legacy_default,) if legacy_default else ())
    ddir = pathlib.Path(data_dir) if data_dir is not None else (_REPO / "data")
    tf = timeframe or "1h"
    path, proxy, resample = _canonical_resolver()(symbol, tf, ddir)
    if path:
        return Resolution(RESOLVED, str(path), symbol=symbol, timeframe=timeframe,
                          proxy=bool(proxy), resample=resample)
    return Resolution(REFUSED, None, symbol=symbol, timeframe=timeframe,
                      tried=(str(ddir),))


def refusal_message(res: Resolution, *, harness: str,
                    legacy_default: Optional[str] = None,
                    env_var: str = DEFAULT_ENV_VAR) -> str:
    """What a refused run prints. Names the cause AND the one-line remedy."""
    tried = ", ".join(res.tried) or "(none)"
    if not res.symbol:
        # E4 (docs/claude/work/MANAGER-CHECKLIST.json row E4): a bare
        # invocation -- no --symbol AND no --data -- used to silently reach
        # the fixture. It no longer does; this is the message for that case,
        # distinct from "a named symbol could not be resolved" below.
        lines = [
            f"{harness}: REFUSING to run - no --symbol and no --data were "
            f"given, so there is nothing to run against. Reaching the "
            f"5,001-row/3.5-day-of-2022 BTC fixture BY DEFAULT is what row "
            f"E4 removed (docs/claude/work/MANAGER-CHECKLIST.json).",
        ]
        if legacy_default:
            lines.append(
                f"  the fixture still works as the fast smoke path -- say so "
                f"explicitly: --data {legacy_default}"
            )
        lines.append("  or name a real instrument: --symbol <SYMBOL>")
        lines.append(f"  or point {env_var} at the file you want.")
        return "\n".join(lines)
    lines = [
        f"{harness}: REFUSING to run - you asked for symbol {res.symbol!r} "
        f"(timeframe {res.timeframe!r}) and gave no --data, and no candle file "
        f"for that symbol was found.",
        f"  searched: {tried} via the canonical resolver "
        f"(scripts/research/m20_fleet_exit_sweep.py::resolve_data, "
        f"convention data/<SYMBOL>_<grain>.csv)",
    ]
    if legacy_default:
        lines.append(
            f"  NOT falling back to {legacy_default!r}: nothing establishes that "
            f"file holds {res.symbol} data, and running anyway is how a result set "
            f"gets headed {res.symbol} and computed from something else "
            f"(BL-20260813-HARNESS-SYMBOL-IS-A-LABEL-DATA-DEFAULTS-TO-BTC)."
        )
        lines.append(f"  if that file IS what you mean, say so: --data {legacy_default}")
    lines.append(f"  or point {env_var} at the file you want.")
    return "\n".join(lines)


def _self_test() -> int:
    """Planted controls, graded in BOTH directions."""
    import tempfile
    ok = True

    def check(label, cond):
        nonlocal ok
        ok &= bool(cond)
        print(f"  self-test {'ok  ' if cond else 'FAIL'}: {label}")

    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        (d / "SOLUSDT_1h.csv").write_text("ts,open,high,low,close,volume\n")

        r = resolve_or_refuse("SOLUSDT", "1h", "somewhere/else.csv",
                              legacy_default="data/btc.csv", data_dir=d, env={})
        check("an explicit --data is used as given, never second-guessed",
              r.state == EXPLICIT and r.path == "somewhere/else.csv")

        r = resolve_or_refuse("SOLUSDT", "1h", None, legacy_default="data/btc.csv",
                              data_dir=d, env={DEFAULT_ENV_VAR: "env/path.csv"})
        check("BACKTEST_DATA_PATH is honoured (a fleet-wide knob, 23 readers)",
              r.state == ENV and r.path == "env/path.csv")

        r = resolve_or_refuse("SOLUSDT", "1h", None, legacy_default="data/btc.csv",
                              data_dir=d, env={})
        check("a symbol with a matching file RESOLVES to it",
              r.state == RESOLVED and r.path and r.path.endswith("SOLUSDT_1h.csv"))

        r = resolve_or_refuse(None, "1h", None, legacy_default="data/btc.csv",
                              data_dir=d, env={})
        check("E4: a bare invocation (no --symbol, no --data) now REFUSES "
              "rather than reaching the fixture by default",
              r.state == REFUSED and r.path is None and not r.ok)
        msg = refusal_message(r, harness="x.py", legacy_default="data/btc.csv")
        check("its refusal names the explicit smoke command",
              "--data data/btc.csv" in msg)

        r = resolve_or_refuse(None, "1h", None, legacy_default="data/btc.csv",
                              data_dir=d, env={}, allow_implicit_default=True)
        check("allow_implicit_default=True is the opt-in escape hatch back to "
              "the pre-E4 contract",
              r.state == LEGACY_DEFAULT and r.path == "data/btc.csv")

        r = resolve_or_refuse("ETHUSDT", "1h", None, legacy_default="data/btc.csv",
                              data_dir=d, env={})
        check("a symbol with NO file is REFUSED, not silently defaulted",
              r.state == REFUSED and r.path is None and not r.ok)

        msg = refusal_message(r, harness="x.py", legacy_default="data/btc.csv")
        check("the refusal names the symbol, the fallback it declined, and the remedy",
              "ETHUSDT" in msg and "data/btc.csv" in msg and "--data" in msg)

        r = resolve_or_refuse("BTCUSDT", "1h", None, legacy_default="data/btc.csv",
                              data_dir=d, env={})
        check("an EXPLICIT default-looking symbol is refused too (no special case)",
              r.state == REFUSED)

        r = resolve_or_refuse("SOLUSDT", "1h", None, legacy_default=None,
                              data_dir=d, env={})
        check("provenance_line NAMES the file, so `resolved` is not itself implicit",
              "SOLUSDT_1h.csv" in r.provenance_line() and "[resolved]" in r.provenance_line())

        r = resolve_or_refuse("SOLUSDT", "1h", None, legacy_default="data/btc.csv",
                              data_dir=d, env={DEFAULT_ENV_VAR: "   "})
        check("a blank env var is NOT 'set' - it falls through to resolution",
              r.state == RESOLVED)

    print("backtest-data-source self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        raise SystemExit(_self_test())
    print(__doc__)
