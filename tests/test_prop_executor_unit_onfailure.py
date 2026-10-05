"""OPS-AUDIT 2026-10-04 (OA-01): the live prop executors must alert on a failed tick.

install_systemd_units.sh installs the FIX-SA-08 OnFailure drop-in only for
oneshots behind a deploy/*.timer; both prop executor timers live under
deploy/opt-in/, so the line has to be in the unit files themselves.
"""
from __future__ import annotations

import configparser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("unit", ["ict-prop-executor.service", "ict-prop-executor@.service"])
def test_prop_executor_unit_declares_onfailure(unit: str) -> None:
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string((REPO / "deploy" / unit).read_text(encoding="utf-8"))
    assert cp["Unit"].get("OnFailure") == "ict-notify-failure@%n.service"
    assert cp["Service"].get("Type") == "oneshot"
    assert (REPO / "deploy" / "ict-notify-failure@.service").is_file()
