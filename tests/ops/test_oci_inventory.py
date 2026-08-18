"""Logic tests for the OCI inventory diff + free-tier budget.

Pure functions only — no OCI client is constructed, so these run anywhere.
"""

from __future__ import annotations

import pytest

from scripts.ops.oci_inventory import (
    AMPERE_SHAPE,
    ampere_budget,
    diff,
    to_markdown,
)


def inst(name, shape=AMPERE_SHAPE, ocpus=1, mem=6, state="RUNNING"):
    return {"display_name": name, "shape": shape, "ocpus": ocpus, "memory_gb": mem,
            "lifecycle_state": state, "availability_domain": "AD-1",
            "time_created": "2026-01-01", "ocid": f"ocid.{name}"}


# --- the declaration state must not collapse -------------------------------

def test_absent_expectations_file_is_not_a_pass():
    """'nothing to compare against' and 'everything matches' are opposite findings."""
    d = diff([inst("live-vm")], None)
    assert d["declaration_state"] == "not_declared"
    assert d["findings"] == []
    assert "NOT a pass" in d["note"]


def test_declared_and_matching():
    live = [inst("live-vm", ocpus=2, mem=12)]
    exp = [{"display_name": "live-vm", "shape": AMPERE_SHAPE, "ocpus": 2, "memory_gb": 12}]
    f = diff(live, exp)["findings"]
    assert len(f) == 1 and f[0]["verdict"] == "match"


def test_shape_change_is_drift_and_names_the_field():
    live = [inst("live-vm", ocpus=2, mem=12)]
    exp = [{"display_name": "live-vm", "shape": AMPERE_SHAPE, "ocpus": 4, "memory_gb": 24}]
    f = diff(live, exp)["findings"][0]
    assert f["verdict"] == "drift"
    assert f["deltas"]["ocpus"] == {"expected": 4, "actual": 2}
    assert f["deltas"]["memory_gb"] == {"expected": 24, "actual": 12}


def test_declared_but_gone_is_missing():
    exp = [{"display_name": "ict-bot", "shape": AMPERE_SHAPE, "ocpus": 1, "memory_gb": 1}]
    f = diff([], exp)["findings"][0]
    assert f["verdict"] == "missing" and f["actual"] is None


def test_live_but_undeclared_is_flagged():
    f = diff([inst("mystery-vm")], [])["findings"][0]
    assert f["verdict"] == "undeclared"


def test_terminated_instances_are_not_reported_as_undeclared():
    """A terminated box holds no allocation and must not read as surprise drift."""
    assert diff([inst("old-micro", state="TERMINATED")], [])["findings"] == []


def test_terminated_declared_instance_reads_as_missing():
    """The x86 micro case: still declared, actually terminated -> missing, not match."""
    live = [inst("ict-bot", state="TERMINATED")]
    exp = [{"display_name": "ict-bot", "shape": AMPERE_SHAPE, "ocpus": 1, "memory_gb": 1}]
    assert diff(live, exp)["findings"][0]["verdict"] == "missing"


# --- free-tier budget ------------------------------------------------------

def test_budget_sums_only_ampere_and_only_alive():
    live = [
        inst("live", ocpus=2, mem=12),
        inst("trainer", ocpus=1, mem=6),
        inst("gateway", ocpus=1, mem=6),
        inst("x86", shape="VM.Standard.E2.1.Micro", ocpus=1, mem=1),
        inst("dead", ocpus=4, mem=24, state="TERMINATED"),
    ]
    b = ampere_budget(live)
    assert b["total_all_non_terminated"] == {"instances": 3, "ocpus": 4, "memory_gb": 24}
    assert b["exceeds_current_ceiling"] is True     # 4/24 over the 2/12 Always Free bar
    assert b["exceeds_legacy_ceiling"] is False     # exactly at the legacy 4/24 bar


def test_budget_within_current_ceiling():
    b = ampere_budget([inst("live", ocpus=2, mem=12)])
    assert b["exceeds_current_ceiling"] is False and b["exceeds_legacy_ceiling"] is False


def test_budget_splits_by_lifecycle_state():
    """STOPPED is reported separately — the tool does not assert a billing rule."""
    b = ampere_budget([inst("a", state="RUNNING"), inst("b", state="STOPPED")])
    assert set(b["by_lifecycle_state"]) == {"RUNNING", "STOPPED"}
    assert "does not assert" in b["caveat"]


def test_budget_states_that_the_account_type_is_unreadable_here():
    b = ampere_budget([])
    assert "console" in b["caveat"].lower()


# --- rendering -------------------------------------------------------------

def test_markdown_surfaces_the_not_declared_warning():
    report = {"region": "uk-london-1", "instance_count": 1,
              "ampere_budget": ampere_budget([inst("a")]),
              "diff": diff([inst("a")], None)}
    md = to_markdown(report)
    assert "not_declared" in md and "NOT a pass" in md


def test_markdown_renders_a_row_per_finding():
    live = [inst("live", ocpus=2, mem=12), inst("extra")]
    exp = [{"display_name": "live", "shape": AMPERE_SHAPE, "ocpus": 2, "memory_gb": 12}]
    report = {"region": "uk-london-1", "instance_count": 2,
              "ampere_budget": ampere_budget(live), "diff": diff(live, exp)}
    md = to_markdown(report)
    assert "`live`" in md and "`extra`" in md and "undeclared" in md
