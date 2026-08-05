"""Manifest-path resolution shared by the ML manifest-contract tests.

`manifest_path()` resolves a manifest by basename from EITHER `ml/configs/`
(the daily-cycle roster) or `ml/configs/retired/`.

Why it exists: several contract tests hardcoded `ml/configs/<name>.yaml`, so
retiring a manifest to `ml/configs/retired/` — a routine, documented
housekeeping move that deliberately keeps the file in-repo and runnable ad hoc
— broke CI with a `FileNotFoundError` that had nothing to do with the contract
under test (2026-08-05, PR #8501: 4 tests failed on 3 retired manifests).

Retirement changes only whether the DAILY CYCLE trains a manifest; it does not
make the manifest invalid, so its contract test should keep passing and keep
guarding it. Resolving by basename across both directories keeps that true and
stops the next retirement from breaking unrelated tests.
"""
from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CONFIG_DIRS = (
    _REPO_ROOT / "ml" / "configs",
    _REPO_ROOT / "ml" / "configs" / "retired",
)


def manifest_path(name: str) -> pathlib.Path:
    """Return the path to manifest *name*, active or retired.

    *name* is a basename, with or without the ``.yaml`` suffix. Raises
    ``FileNotFoundError`` naming both searched directories when the manifest
    genuinely does not exist — a real deletion must still fail loudly, and must
    not be confused with a retirement.
    """
    fname = name if name.endswith(".yaml") else f"{name}.yaml"
    for d in _CONFIG_DIRS:
        candidate = d / fname
        if candidate.is_file():
            return candidate
    searched = " or ".join(str(d.relative_to(_REPO_ROOT)) for d in _CONFIG_DIRS)
    raise FileNotFoundError(
        f"manifest {fname!r} not found in {searched} — if it was deleted "
        "rather than retired, update or remove the test that references it."
    )
