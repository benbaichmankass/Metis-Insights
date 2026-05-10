"""S-067 follow-up #9 — shared vm_health helper tests.

Pins the behaviour of the consolidated helper at
``src/web/api/_vm_health.py``, which both dashboard and diag routers
re-export under the legacy ``_vm_health`` name. The two routers used
to carry forks of this body; this test guards against the
divergence coming back.
"""
from __future__ import annotations

import sys

import pytest

from src.web.api import _vm_health as helper_module


def test_vm_health_returns_three_field_dict():
    out = helper_module.vm_health()
    assert set(out.keys()) == {"cpu", "memory", "disk"}


def test_vm_health_returns_none_per_field_on_psutil_import_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    """If psutil isn't importable, return None per field — distinct
    from a fabricated 0.0 (which the dashboard would render as a real
    0% measurement)."""
    # Force ImportError when the helper does its lazy `import psutil`.
    monkeypatch.setitem(sys.modules, "psutil", None)
    out = helper_module.vm_health()
    assert out == {"cpu": None, "memory": None, "disk": None}


def test_vm_health_returns_none_per_field_when_psutil_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    import psutil  # type: ignore[import-not-found]

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic psutil failure")

    monkeypatch.setattr(psutil, "cpu_percent", boom)
    out = helper_module.vm_health()
    assert out == {"cpu": None, "memory": None, "disk": None}


def test_dashboard_router_reexports_helper():
    """The dashboard router's ``_vm_health`` name must be the same
    callable as the shared helper — the re-export is the consolidation
    contract."""
    from src.web.api.routers import dashboard as dashboard_router

    assert dashboard_router._vm_health is helper_module.vm_health


def test_diag_router_reexports_helper():
    from src.web.api.routers import diag as diag_router

    assert diag_router._vm_health is helper_module.vm_health
