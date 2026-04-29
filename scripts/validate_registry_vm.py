"""S-007 VM registry validation script.

Run on the Oracle VM (or locally) to verify that config/strategies.yaml
is consistent and all referenced artifacts are in place before starting
the live trader.

Usage:
    PYTHONPATH=. python scripts/validate_registry_vm.py
    PYTHONPATH=. python scripts/validate_registry_vm.py --json

Exit codes:
    0 — all checks passed
    1 — one or more checks failed
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO_ROOT)


def _check_registry_loads() -> tuple[bool, str, list[dict]]:
    try:
        from src.strategy_registry import load_strategies
        strategies = load_strategies()
        return True, f"{len(strategies)} strategies loaded", strategies
    except Exception as exc:
        return False, f"Registry failed to load: {exc}", []


def _check_service_prefix(strategy: dict) -> tuple[bool, str]:
    svc = strategy.get("service", "")
    if svc.startswith("ict-trader-"):
        return True, f"service={svc}"
    return False, f"service='{svc}' does not start with 'ict-trader-'"


def _check_signal_prefixes(strategy: dict) -> tuple[bool, str]:
    prefixes = strategy.get("signal_prefixes") or []
    if prefixes:
        return True, f"signal_prefixes={prefixes}"
    return False, "signal_prefixes is empty — DB attribution will not work"


def _check_model_path(strategy: dict) -> tuple[bool, str]:
    model = strategy.get("model")
    if not model:
        return True, "no model configured (skip)"
    try:
        from src.strategy_registry import model_path
        path = model_path(strategy["name"])
        if path and os.path.exists(path):
            return True, f"model exists at {path}"
        return False, (
            f"model artifact missing: {path or '(path not resolved)'} — "
            "run huggingface_hub.snapshot_download or place the file manually"
        )
    except Exception as exc:
        return False, f"model_path() raised: {exc}"


def run_checks(verbose: bool = True) -> list[dict]:
    results: list[dict] = []

    ok, msg, strategies = _check_registry_loads()
    results.append({"check": "registry_loads", "ok": ok, "detail": msg})
    if not ok:
        return results

    for s in strategies:
        name = s["name"]
        for check_fn, check_name in [
            (_check_service_prefix, "service_prefix"),
            (_check_signal_prefixes, "signal_prefixes"),
            (_check_model_path, "model_path"),
        ]:
            ok, detail = check_fn(s)
            results.append({
                "check": check_name,
                "strategy": name,
                "ok": ok,
                "detail": detail,
            })

    return results


def _print_results(results: list[dict]) -> int:
    passed = failed = 0
    for r in results:
        prefix = "PASS" if r["ok"] else "FAIL"
        strat = f"[{r['strategy']}] " if r.get("strategy") else ""
        print(f"  {prefix}  {strat}{r['check']}: {r['detail']}")
        if r["ok"]:
            passed += 1
        else:
            failed += 1
    return failed


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate S-007 strategy registry")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args(argv)

    results = run_checks()

    if args.json:
        print(json.dumps(results, indent=2))
        failed = sum(1 for r in results if not r["ok"])
    else:
        print("\nS-007 Registry Validation\n" + "=" * 40)
        failed = _print_results(results)
        total = len(results)
        passed = total - failed
        print(f"\n{passed}/{total} checks passed", end="")
        if failed:
            print(f" — {failed} FAILED")
        else:
            print(" — OK")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
