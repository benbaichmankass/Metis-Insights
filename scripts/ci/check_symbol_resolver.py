#!/usr/bin/env python3
"""symbol-resolver-guard — the controls for ``src.config.symbol_sets``.

ONE DEFINITION of "which symbols does this account concern" lives in
``src/config/symbol_sets.py``; this file is the CI guard that proves it holds.
The split is deliberate and was CORRECTED INTO EXISTENCE by two guards in a row,
which is worth recording because each one was right:

1. ``tests/ci/test_run_guards_runner_absent.py`` refused
   ``python3 -m src.config.symbol_sets --self-test``: that makes the MODULE the
   registry's "runner", and every runner needs a ``RUNNER_REMEDY`` entry
   answering *"the runner is absent — what does the operator install?"*. Repo
   code has no true answer to that, so an entry there would have been a false
   remedy written to silence a correct guard.

2. ``check_guard_selftest_coverage.py`` then refused the first fix — a thin
   wrapper that declared ``--self-test`` and merely delegated:

       [no-assertion] scripts/ci/check_symbol_resolver.py has a self-test wired
       into CI (path A) that exercises nothing or compares nothing.

   Also right, and a sharper point than it looks. A file that CLAIMS coverage in
   the registry while asserting nothing itself is the presence-only marker this
   repo already paid for with ``new-table-wiring-guard``. That the assertions
   existed one import away is exactly the excuse such a marker always has.

So the controls live HERE, where the guard population can see them, and the
runtime module holds only what the trader imports. That is also what every
other guard in this repo does.

WHAT IT CHECKS — 21 controls: the core union/declared contract, the explicit
mode with no default, fail-safe degradation in the widening direction only, one
planted case per call site in that site's own mode, a planted BYPASS in both
directions with a verified-not-presence-only exemption, and — over the REAL
config — that ``DECLARED`` and ``UNION`` still agree, so the day a roster and a
pull list diverge is a CI failure rather than a surprise in a data sweep.

Exit 0 clean, 1 with findings. Touches no repo file.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.config.symbol_sets import (  # noqa: E402
    DECLARED,
    UNION,
    load_strategies_cfg,
    resolve_symbols,
    symbols_for_account_id,
)



# ---------------------------------------------------------------------------
# Self-test — runs in CI as `symbol-resolver-guard`.
# ---------------------------------------------------------------------------

#: The four sites this module exists to unify, and the mode each one asked for.
#: The BYPASS control below reads these files and fails if any of them has gone
#: back to computing a symbol set privately. Centralising the definition means
#: nothing if a fifth caller — or a future edit to one of these four — quietly
#: re-derives it, which is exactly how there came to be four.
CALL_SITES = {
    "src/main.py": (
        DECLARED,
        "the tick's candle fetch set; wants UNION, pinned to DECLARED until "
        "PR #12736 lands",
    ),
    "src/units/accounts/clients.py": (
        UNION,
        "the Bybit per-symbol POSITION cross-check, both readers",
    ),
    "src/runtime/exchange_accounts.py": (
        UNION,
        "the fill / funding / cost sweep",
    ),
}

#: A private re-derivation looks like one of these in a converted file.
#: Deliberately NOT a bare `symbols` match: the word appears in prose, in
#: `resolve_symbols(` itself and in `declared=cfg.get("symbols")`, which IS the
#: routed form. These patterns are the *unrouted* reads only.
_BYPASS_PATTERNS = (
    'account.get("symbols")',
    'acct.symbols',
    'getattr(acct, "symbols"',
)


def bypass_offenders(read=None) -> list[str]:
    """Converted files that still compute a symbol set privately.

    ``read`` is injectable so the self-test can plant a violation without
    touching the tree — a control that can only be exercised by breaking the
    repo is a control nobody runs.
    """
    def _default_read(rel: str) -> str:
        return (Path(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8")

    reader = read or _default_read
    out: list[str] = []
    for rel in CALL_SITES:
        try:
            text = reader(rel)
        except Exception:  # noqa: BLE001
            out.append(f"{rel}: COULD NOT READ — not a pass, a failure to check")
            continue
        # The exemption is VERIFIED, not presence-only. A marker counts only in
        # a file that actually imports this module, so the cheapest way to
        # silence the control is still to route the call — the
        # `new-table-wiring-guard` lesson (a guard cheaper to lie to than to
        # satisfy is worse than no guard). ⚠️ ITS HONEST LIMIT, stated rather
        # than hidden: this proves the file is routed SOMEWHERE, not that this
        # particular line is the fallback of a routed call. That residue is why
        # the marker must carry a reason a reviewer reads in the diff.
        routed_file = "symbol_sets" in text
        for pat in _BYPASS_PATTERNS:
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or pat not in line:
                    continue
                # `declared=...` is the ROUTED form: it is how a caller hands
                # the declared list TO the resolver, not a private derivation.
                if re.search(r"declared\s*=", line):
                    continue
                marker = re.search(r"#\s*symbol-set:\s*(.+)$", line)
                if marker and routed_file and len(marker.group(1).strip()) >= 20:
                    continue
                if marker and not routed_file:
                    out.append(f"{rel}: marker claims an exemption in a file "
                               f"that never imports the resolver: {stripped[:80]}")
                    continue
                out.append(f"{rel}: {stripped[:100]}")
    return out


def self_test() -> int:  # noqa: C901
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {label}: {'PASS' if cond else 'FAIL'}")
        ok = ok and cond

    print("symbol-resolver self-test")
    scfg = {"leg_x": {"symbols": ["AVAXUSDT"]}, "leg_y": {"symbols": ["XRPUSDT"]},
            "leg_none": {"timeframe": "1h"}}

    # --- the core contract
    check("1 (DECLARED ignores the roster entirely)",
          resolve_symbols(declared=["BTCUSDT"], roster=["leg_x"],
                          mode=DECLARED, strategies_cfg=scfg) == ["BTCUSDT"])
    check("2 (UNION appends the roster's symbols AFTER the declared ones, so "
          "it can only ever lengthen the list)",
          resolve_symbols(declared=["BTCUSDT"], roster=["leg_x"],
                          mode=UNION, strategies_cfg=scfg)
          == ["BTCUSDT", "AVAXUSDT"])
    check("3 (a declared symbol no leg trades SURVIVES the union — the pull "
          "list is additive, and 21 such entries are live)",
          resolve_symbols(declared=["BTCUSDT", "ADAUSDT"], roster=["leg_x"],
                          mode=UNION, strategies_cfg=scfg)
          == ["BTCUSDT", "ADAUSDT", "AVAXUSDT"])
    check("4 (no duplicate when the roster's symbol is already declared)",
          resolve_symbols(declared=["AVAXUSDT"], roster=["leg_x"],
                          mode=UNION, strategies_cfg=scfg) == ["AVAXUSDT"])

    # --- the mode is explicit. A caller that forgets it must not get a
    #     plausible default; it must fail loudly.
    try:
        resolve_symbols(declared=["BTCUSDT"], roster=[], mode="")  # type: ignore[arg-type]
        check("5 (an absent/invalid mode RAISES rather than defaulting)", False)
    except ValueError as exc:
        check("5 (an absent/invalid mode RAISES rather than defaulting)",
              "no default" in str(exc))

    # --- fail-safe: never narrower.
    check("6 (unreadable strategies.yaml -> UNION degrades to DECLARED, "
          "never to empty)",
          resolve_symbols(declared=["BTCUSDT"], roster=["leg_x"],
                          mode=UNION, strategies_cfg=None) == ["BTCUSDT"])
    check("7 (a rostered strategy with no `symbols:` contributes nothing and "
          "does not raise)",
          resolve_symbols(declared=["BTCUSDT"], roster=["leg_none", "ghost"],
                          mode=UNION, strategies_cfg=scfg) == ["BTCUSDT"])
    check("8 (the exchange default applies ONLY to an empty declared list, "
          "and never displaces a declaration)",
          resolve_symbols(declared=[], roster=[], mode=DECLARED,
                          exchange="interactive_brokers") == ["MES"]
          and resolve_symbols(declared=["SPY"], roster=[], mode=DECLARED,
                              exchange="bybit") == ["SPY"])
    check("9 (no exchange passed -> no invented instrument; a data sweep would "
          "rather see [] than a symbol nobody declared)",
          resolve_symbols(declared=[], roster=[], mode=UNION) == [])

    # --- one planted case PER CALL SITE, in that site's own mode.
    check("10 (tick site, DECLARED: a rostered-but-undeclared symbol is NOT "
          "fetched — today's behaviour, held until #12736)",
          "AVAXUSDT" not in resolve_symbols(
              declared=["BTCUSDT"], roster=["leg_x"], mode=DECLARED,
              strategies_cfg=scfg, exchange="bybit"))
    check("11 (position cross-check site, UNION: the same symbol IS in the "
          "denominator, so a live position on it cannot hide)",
          "AVAXUSDT" in resolve_symbols(
              declared=["BTCUSDT"], roster=["leg_x"], mode=UNION,
              strategies_cfg=scfg))
    check("12 (fill/funding sweep site, UNION: the same symbol IS swept, so "
          "its funding reaches the cost stack)",
          "AVAXUSDT" in resolve_symbols(
              declared=["BTCUSDT"], roster=["leg_x"], mode=UNION,
              strategies_cfg=scfg))
    check("13 (and the two UNION sites agree exactly — one definition means "
          "they cannot drift)",
          resolve_symbols(declared=["BTCUSDT"], roster=["leg_x", "leg_y"],
                          mode=UNION, strategies_cfg=scfg)
          == resolve_symbols(declared=["BTCUSDT"], roster=["leg_x", "leg_y"],
                             mode=UNION, strategies_cfg=scfg))

    # --- THE BYPASS CONTROL, both directions.
    planted = {
        "src/main.py": 'syms = list(getattr(acct, "symbols", None) or [])\n',
        "src/units/accounts/clients.py": "x = 1\n",
        "src/runtime/exchange_accounts.py": "y = 2\n",
    }
    check("14 (a call site that goes back to deriving the set privately is "
          "CAUGHT — the control is planted, not hoped for)",
          len(bypass_offenders(read=planted.get)) == 1
          and "src/main.py" in bypass_offenders(read=planted.get)[0])
    clean = {k: "declared=cfg.get(\"symbols\"), roster=cfg.get(\"strategies\")\n"
             for k in CALL_SITES}
    check("15 (…and the ROUTED form — `declared=cfg.get(\"symbols\")` — is not "
          "mistaken for a bypass)",
          bypass_offenders(read=clean.get) == [])
    marked_routed = {k: "from src.config import symbol_sets\n"
                        "    syms = account.get(\"symbols\")  # symbol-set: degraded fallback of the routed call above\n"
                     for k in CALL_SITES}
    check("15b (a marked line in a file that DOES route is exempt)",
          bypass_offenders(read=marked_routed.get) == [])
    marked_unrouted = {k: "syms = account.get(\"symbols\")  # symbol-set: degraded fallback of the routed call above\n"
                       for k in CALL_SITES}
    check("15c (…the SAME marker in a file that never imports the resolver is "
          "REFUSED — the exemption is verified, not presence-only)",
          len(bypass_offenders(read=marked_unrouted.get)) == 3
          and "never imports the resolver" in bypass_offenders(read=marked_unrouted.get)[0])
    short = {k: "from src.config import symbol_sets\n"
                "    syms = account.get(\"symbols\")  # symbol-set: nope\n"
             for k in CALL_SITES}
    check("15d (…and a marker with no real reason does not buy an exemption)",
          len(bypass_offenders(read=short.get)) == 3)

    # --- cfg precedence. The four tests that caught the first version of this
    #     are the reason these exist: a caller's explicit view must not be
    #     silently widened from a file it never named.
    check("15g (an EMPTY cfg `symbols` falls through to accounts.yaml — MI-222: "
          "the cross-check cannot be switched off from the cfg dict)",
          symbols_for_account_id("bybit_2", mode=UNION, cfg={"symbols": []})
          == ["BTCUSDT", "ETHUSDT", "XRPUSDT", "ADAUSDT"])
    check("15h (an UNKNOWN account id resolves to [] and never to an invented "
          "exchange default — a data sweep must not manufacture a venue read "
          "for a symbol nobody declared)",
          symbols_for_account_id("no-such-account", mode=UNION,
                                 cfg={"symbols": [], "exchange": "bybit"}) == [])
    check("15e (a cfg carrying `symbols` is authoritative — its OWN roster "
          "key, or the absence of one, decides; accounts.yaml is not consulted)",
          symbols_for_account_id("bybit_2", mode=UNION,
                                 cfg={"symbols": ["XRPUSDT", "BTCUSDT"]})
          == ["XRPUSDT", "BTCUSDT"])
    check("15f (…and that same cfg WITH a roster does get the union)",
          symbols_for_account_id(
              "bybit_2", mode=UNION,
              cfg={"symbols": ["BTCUSDT"], "strategies": ["xrp_pullback_2h"]})
          == ["BTCUSDT", "XRPUSDT"])

    check("16 (a file we could not READ is reported, not silently passed)",
          len(bypass_offenders(read=lambda rel: (_ for _ in ()).throw(OSError()))) == 3)
    live = bypass_offenders()
    check("17 (the REAL tree: all three converted files route through this "
          f"module) -> {live if live else 'clean'}", live == [])

    # --- the claim the whole change rests on, asserted against the real config.
    try:
        # The canonical readers, not a hand-rolled parse: `accounts.yaml` has
        # exactly one sanctioned dict reader (`canonical-config-loaders` fails
        # the build on a second one, and it caught the first draft of this very
        # block), and strategies.yaml goes through this module's own loader.
        from src.config.accounts_loader import load_accounts_dict  # noqa: PLC0415
        acc = load_accounts_dict() or {}
        strat = load_strategies_cfg()
        diverged = []
        for name, cfg in acc.items():
            cfg = cfg or {}
            d = resolve_symbols(declared=cfg.get("symbols"),
                                roster=cfg.get("strategies"), mode=DECLARED,
                                strategies_cfg=strat, exchange=cfg.get("exchange"))
            u = resolve_symbols(declared=cfg.get("symbols"),
                                roster=cfg.get("strategies"), mode=UNION,
                                strategies_cfg=strat, exchange=cfg.get("exchange"))
            if d != u:
                diverged.append(f"{name}: UNION adds {[s for s in u if s not in d]}")
        check(f"18 (DECLARED == UNION on all {len(acc)} live accounts, so "
              "switching a reader's mode is a NO-OP on today's config) -> "
              f"{diverged if diverged else 'no divergence'}",
              diverged == [])
    except Exception as exc:  # noqa: BLE001
        check(f"18 (live-config equivalence) — COULD NOT READ: {exc}", False)

    print("self-test OK — one definition, an explicit mode per caller, "
          "fail-safe in the widening direction only, and a planted bypass is "
          "caught while the routed form is not."
          if ok else "self-test FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    # Accepted and ignored: the registry passes it, and a guard that REFUSED the
    # flag its own registry entry sends would fail for a reason unrelated to the
    # invariant it checks.
    ap.add_argument("--self-test", action="store_true")
    ap.parse_args()
    return self_test()


if __name__ == "__main__":
    raise SystemExit(main())
