"""The ops clientId on scripts/ops/attach_ib_target.py.

Why this file exists
--------------------
``attach-ib-target`` shipped 2026-08-16 as the repair action for
BL-20260816-COVERAGE-IS-ONE-SIDED and its ``_attach`` called
``ib_client_for(cfg, readonly=False)`` — which resolves the TRADER's own
execution clientId (497). While the trader runs, IBKR refuses the second
connection outright (``Error 326``), so the apply path could never place a
target. Observed live on ib_paper/MES, system-action issue #10139.

The failure shape is what makes it worth a test: the DRY RUN reports
``state: ready`` because it never builds a client, so the tool reads as
available right up to the moment it is needed. Only the apply path can
falsify it, and the apply path is the one nobody exercises in CI.

Its sibling ``flatten_ib_position.py`` carried the correct helper from the
start and ``tests/test_flatten_ib_position.py::test_ops_client_id_range``
asserts its band; this is the same assertion for the sibling that lacked it,
plus the wiring check the band alone cannot make (a correct helper that is
never PASSED to the client factory fixes nothing).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "attach_ib_target",
    Path(__file__).resolve().parents[1] / "scripts" / "ops" / "attach_ib_target.py",
)
att = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(att)  # type: ignore


def test_ops_client_id_range():
    # Must avoid the trader execution ids (496/497/498 and their reconnect
    # rotations) AND the read range (9000-9899).
    cid = att._ib_ops_client_id()
    assert 9900 <= cid <= 9989


def test_attach_passes_the_ops_client_id_to_the_factory(monkeypatch):
    """The band is worthless if ``_attach`` does not USE it.

    This is the assertion that actually falsifies the shipped defect: pre-fix
    ``_attach`` called ``ib_client_for(cfg, readonly=False)`` with no
    ``client_id``, so the factory fell back to the trader's execution id.
    """
    seen = {}

    def _fake_factory(cfg, **kwargs):
        seen.update(kwargs)
        return None  # short-circuits _attach into its could-not-build branch

    import src.units.accounts.clients as clients_mod
    monkeypatch.setattr(clients_mod, "ib_client_for", _fake_factory)

    out = att._attach({"account_id": "ib_paper"}, symbol="MES", direction="long",
                      qty=15.0, tp=8390.59, oca_group="oca-protect-336")

    assert out["retCode"] == 1  # the factory returned None, as arranged
    assert "client_id" in seen, "ib_client_for called without an explicit client_id"
    assert 9900 <= seen["client_id"] <= 9989
    assert seen.get("readonly") is False
