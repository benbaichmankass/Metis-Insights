#!/usr/bin/env python3
"""pairs-sizing-basis guard — the pairs sleeve must size off the canonical risk basis.

BL-20260716-PAIRS-EXEC. The market-neutral pairs sleeve (M22 D2) originally
sized every pair off a HARDCODED dollar constant (`risk_budget_usd: 20.0` in
`config/pairs.yaml`), divorced from the account. That is an architectural
violation of the repo's one sizing contract: dollar-risk-per-trade is
`balance × risk_pct × confidence` resolved by `RiskManager.position_size` from
the account's `risk:` block (CLAUDE.md: "sizing is the per-account RiskManager's
job; account basis × …"). A magic constant strands the sleeve — at 20.0 on the
~$166k paper balance every leg sized below the exchange minimum and nothing
traded — and, worse, silently drifts from the account it's supposed to risk
against. This guard makes "no hardcoded dollar risk basis in the pairs order
path; derive it from balance × risk_pct" a merge gate so the class can't recur.

It fails the build when:

  1. `config/pairs.yaml` carries a `risk_budget_usd` key (the removed hardcode —
     a dollar risk basis living as a config literal instead of being derived).
  2. `src/units/strategies/pairs_executor.py` (the config-reading LIVE layer)
     reads `"risk_budget_usd"` from config again — the sleeve's per-pair budget
     must come from the account basis, not a config dollar literal.
  3. `pairs_executor.py` no longer references BOTH `_fetch_balance` AND
     `risk_pct` — the two halves of the canonical derive (live balance read ×
     the account's risk_pct). Losing either means the derive was ripped out and
     something is sizing off a guess again.

NOT flagged — the pure-math parameter name. `pairs_sizing.pair_notionals(
risk_budget_usd=…)` and the unit tests pass a budget as a function argument;
that's the sized $-at-risk flowing THROUGH, not a hardcoded basis. The guard is
scoped to the config file + the one config-reading executor module precisely so
it never touches the math layer or the tests.

A reviewed exception carries an inline `# pairs-sizing-allow: <reason>` marker
on the offending line.

Usage::

    python scripts/ci/check_pairs_sizing_basis.py     # scan the canonical paths (CI default)
    python scripts/ci/check_pairs_sizing_basis.py --self-test

Exit 0 = clean; exit 1 = a hardcoded / removed-derive violation (file:line printed).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

PAIRS_CONFIG = "config/pairs.yaml"
PAIRS_EXECUTOR = "src/units/strategies/pairs_executor.py"

_HARDCODE_KEY = "risk_budget_usd"
_ALLOW_MARKER = "# pairs-sizing-allow"
# The two halves of the canonical derive the executor MUST keep referencing.
_DERIVE_TOKENS = ("_fetch_balance", "risk_pct")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def find_config_violations(source: str, rel_path: str) -> List[Tuple[int, str]]:
    """A top-level `risk_budget_usd:` YAML key is a hardcoded dollar risk basis."""
    hits: List[Tuple[int, str]] = []
    for i, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue                                   # a comment mentioning it is fine
        code = line.split("#", 1)[0]                   # ignore trailing comments
        key = code.strip()
        if key.startswith(f"{_HARDCODE_KEY}:") and _ALLOW_MARKER not in line:
            hits.append((i, f"`{_HARDCODE_KEY}` is a hardcoded dollar risk basis in "
                            f"{rel_path} — derive the per-pair budget from balance × "
                            f"risk_pct × pairs_risk_fraction instead"))
    return hits


def find_executor_violations(source: str, rel_path: str) -> List[Tuple[int, str]]:
    """The executor must not re-read the hardcoded key, and must keep the derive."""
    hits: List[Tuple[int, str]] = []
    lines = source.splitlines()
    for i, line in enumerate(lines, start=1):
        code = line.split("#", 1)[0]
        # A string-literal read of the removed config key (cfg.get("risk_budget_usd"…),
        # config[...]["risk_budget_usd"], etc.). A bare mention in a comment is skipped
        # (we split off the comment above); an allow-marked line is exempt.
        if f'"{_HARDCODE_KEY}"' in code or f"'{_HARDCODE_KEY}'" in code:
            if _ALLOW_MARKER not in line:
                hits.append((i, f"{rel_path} reads `{_HARDCODE_KEY}` from config — the "
                                f"pairs budget must derive from the account basis "
                                f"(balance × risk_pct), not a config dollar literal"))
    # Positive check: both halves of the canonical derive must still be present.
    missing = [t for t in _DERIVE_TOKENS if t not in source]
    if missing:
        hits.append((0, f"{rel_path} no longer references {missing} — the canonical "
                        f"risk-basis derive (live balance × risk_pct) was removed; the "
                        f"sleeve must size off the account basis, never a constant"))
    return hits


def scan(root: Path) -> List[str]:
    violations: List[str] = []
    cfg = root / PAIRS_CONFIG
    if cfg.exists():
        for ln, msg in find_config_violations(cfg.read_text(encoding="utf-8"), PAIRS_CONFIG):
            violations.append(f"{PAIRS_CONFIG}:{ln}: {msg}")
    ex = root / PAIRS_EXECUTOR
    if ex.exists():
        for ln, msg in find_executor_violations(ex.read_text(encoding="utf-8"), PAIRS_EXECUTOR):
            loc = f"{PAIRS_EXECUTOR}:{ln}" if ln else PAIRS_EXECUTOR
            violations.append(f"{loc}: {msg}")
    return violations


def _self_test() -> int:
    # A hardcoded config key trips (1). A commented mention does not.
    assert find_config_violations("risk_budget_usd: 20.0\n", PAIRS_CONFIG)
    assert not find_config_violations("# risk_budget_usd: legacy note\n", PAIRS_CONFIG)
    assert not find_config_violations("pairs_risk_fraction: 1.0\n", PAIRS_CONFIG)
    # An allow-marked config key is exempt.
    assert not find_config_violations(
        "risk_budget_usd: 20.0  # pairs-sizing-allow: legacy backtest fixture\n", PAIRS_CONFIG)
    # An executor re-reading the key trips (2); the derive tokens present avoids (3).
    bad = 'x = cfg.get("risk_budget_usd", 20.0)\n_fetch_balance\nrisk_pct\n'
    assert any("reads `risk_budget_usd`" in m for _, m in
               find_executor_violations(bad, PAIRS_EXECUTOR))
    # A clean derive (no key read, both tokens present) is OK.
    good = "b = _fetch_balance(client, cfg)\nrisk_pct = cfg['risk']['risk_pct']\n"
    assert find_executor_violations(good, PAIRS_EXECUTOR) == []
    # Ripping out the derive trips (3).
    assert any("no longer references" in m for _, m in
               find_executor_violations("budget = 20.0\n", PAIRS_EXECUTOR))
    print("pairs-sizing-basis guard: self-test OK")
    return 0


def main(argv: List[str]) -> int:
    if "--self-test" in argv:
        return _self_test()
    root = _repo_root()
    violations = scan(root)
    if not violations:
        print("pairs-sizing-basis guard: OK — the pairs sleeve sizes off the "
              "canonical balance × risk_pct basis, no hardcoded dollar constant.")
        return 0
    print("pairs-sizing-basis guard: FAIL — the pairs order path must size off the")
    print("account's canonical risk basis (balance × risk_pct), never a hardcoded $.\n")
    for v in violations:
        print(f"  {v}")
    print(f"\nIf a hit is a genuinely-reviewed exception, annotate its line with "
          f"'{_ALLOW_MARKER}: <reason>'.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
