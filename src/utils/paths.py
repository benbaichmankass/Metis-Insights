"""Canonical repo-root resolver.

Walk up from this file's location until a marker is found (.git,
pyproject.toml, or requirements.txt — first match wins). The result is
cached after the first call.

Why this exists: ad-hoc ``os.path.abspath(os.path.join(__file__, "..", ".."))``
calculations with hard-coded ``..`` counts drifted every time a module
moved to a new depth (see BUG-037, BUG-024).
"""

from __future__ import annotations

import os
from functools import lru_cache

_MARKERS = (".git", "pyproject.toml", "requirements.txt")


@lru_cache(maxsize=1)
def repo_root() -> str:
    """Return the absolute path to the repository root.

    Walks up from this file until it finds a directory that contains one
    of the marker files/dirs listed in ``_MARKERS``. Raises ``RuntimeError``
    if the root cannot be found (e.g. the file was moved outside the repo).
    """
    current = os.path.dirname(os.path.abspath(__file__))
    while True:
        for marker in _MARKERS:
            if os.path.exists(os.path.join(current, marker)):
                return current
        parent = os.path.dirname(current)
        if parent == current:
            raise RuntimeError(
                "repo_root(): could not locate repo root from "
                f"{os.path.dirname(os.path.abspath(__file__))}. "
                f"Looked for markers: {_MARKERS}"
            )
        current = parent
