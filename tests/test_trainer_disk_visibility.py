"""The trainer disk metric: writer, consumer, and the states in between.

Context (measured 2026-08-20, trainer-vm-diag #10057): the trainer root sat at
94% used / 3.2 GB free with `datasets-out/` at 12G, and **no surface anywhere
published a disk figure** — verified against the publish script, the live
`/api/bot/ml/status` payload (12 keys, none disk), and `health.py::check_disk`
(which runs on the LIVE trader). So a training box about to fail its next
dataset build reported green.

These tests hold BOTH halves to each other, because a writer with no reader is
the `exit_price_source` shape this repo has already paid for (written in 12
files, branched on in 1).

Every state assertion carries its own **negative control** — an assertion that
the probe can distinguish the state from its neighbour — because the specific
failure this guards against is *"we did not look"* silently reading as
*"the disk is fine"*, and a test that only checks the happy path cannot see it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PUBLISH_SH = REPO / "scripts" / "ops" / "publish_trainer_mirror.sh"


# --------------------------------------------------------------------------
# Producer: the writer lives inside a shell heredoc, so assert on real code
# --------------------------------------------------------------------------
def _status_builder_python() -> str:
    """Extract the embedded python that builds `trainer_status.json`.

    Asserting on the SHIPPING text, never a copy — a test that re-declares the
    block it is testing passes against a fiction (the `pairs_soak` lesson,
    where tests declared an `order_packages` schema production does not have).
    """
    text = PUBLISH_SH.read_text(encoding="utf-8")
    # The builder heredoc is the one that imports shutil and writes the payload.
    blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", text, flags=re.DOTALL)
    blocks += re.findall(r"<<'PYEOF'\n(.*?)\nPYEOF\n", text, flags=re.DOTALL)
    blocks += re.findall(r"<<'EOF'\n(.*?)\nEOF\n", text, flags=re.DOTALL)
    cands = [b for b in blocks if "shutil" in b and "disk" in b]
    assert cands, (
        "no embedded python block containing the disk computation was found in "
        f"{PUBLISH_SH} — either the heredoc delimiter changed or the writer was "
        "removed. This assertion is the positive control: without it, every "
        "test below would vacuously pass on an empty extraction."
    )
    return cands[0]


def test_positive_control_the_extractor_finds_real_parseable_python():
    """Guard the guard: prove the extraction yields code, not an empty string."""
    block = _status_builder_python()
    tree = ast.parse(block)  # raises if we extracted shell, not python
    assert len(block.splitlines()) > 50, (
        f"extracted only {len(block.splitlines())} lines — suspiciously small "
        "for the status builder; the regex probably matched a different heredoc"
    )
    assert any(isinstance(n, ast.Assign) for n in ast.walk(tree))


def test_writer_puts_disk_in_the_published_payload_not_just_in_a_local():
    """A computed-but-unpublished figure is exactly the defect being fixed."""
    block = _status_builder_python()
    assert '"disk": disk' in block or "'disk': disk" in block, (
        "the disk block is computed but never placed in the published payload — "
        "that is a value written and never read, the shape "
        "provenance-consumer-guard exists to catch"
    )


def test_writer_imports_shutil_inside_the_same_block_that_uses_it():
    block = _status_builder_python()
    tree = ast.parse(block)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "shutil" in imported, (
        "shutil is used but not imported in the SAME embedded block — the "
        "heredoc is its own program, so an import in a sibling block does not "
        "carry, and this would raise NameError only on the trainer at runtime"
    )


def test_writer_failure_path_reports_a_reason_never_a_comfortable_zero():
    """`measured: false` + a reason, not `used_pct: 0`.

    A fabricated 0% would read as an empty disk — the opposite of the truth —
    and is the FABRICATED-value class `provenance.py` exists to stop.
    """
    block = _status_builder_python()
    tree = ast.parse(block)
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    src_of_handlers = "\n".join(ast.unparse(h) for h in handlers)
    assert "measured" in src_of_handlers and "reason" in src_of_handlers, (
        "the disk except-handler does not set measured/reason — a failed "
        "measurement must say it failed, never fall through to a default"
    )
    assert "'used_pct': 0" not in src_of_handlers.replace('"', "'"), (
        "the failure path fabricates a used_pct"
    )


# --------------------------------------------------------------------------
# Consumer: the four states, each with a control that it is distinguishable
# --------------------------------------------------------------------------
def _banner(monkeypatch, payload, *, trainer_down=False):
    from src.web.api.routers import notifications as N

    monkeypatch.setattr(
        "src.web.api.routers.training_center._read_json",
        lambda _p: payload,
    )
    return N._trainer_disk_banner(trainer_down=trainer_down)


def test_state_ok_measured_and_roomy_emits_nothing(monkeypatch):
    b = _banner(monkeypatch, {"ts": "t", "disk": {"measured": True, "free_gb": 30.0,
                                                  "used_pct": 33.0}})
    assert b is None


def test_state_not_published_is_reported_not_silently_healthy(monkeypatch):
    """The load-bearing case: a mirror with no `disk` key at all.

    Its NEGATIVE CONTROL is the test above — a roomy measured disk returns
    None. If both returned None, the banner could not distinguish 'we did not
    look' from 'the disk is fine', which is the entire bug.
    """
    b = _banner(monkeypatch, {"ts": "t", "cycles_24h": 3})  # no disk key
    assert b is not None, (
        "a trainer publishing WITHOUT a disk block produced no banner — so an "
        "un-redeployed trainer is indistinguishable from a healthy one"
    )
    assert b["kind"] == "trainer_disk_unknown"
    assert "not" in b["message"].lower()


def test_state_measure_failed_carries_the_reason(monkeypatch):
    b = _banner(monkeypatch, {"ts": "t", "disk": {"measured": False,
                                                  "reason": "OSError: boom",
                                                  "path": "/x"}})
    assert b is not None and b["kind"] == "trainer_disk_unknown"
    assert "OSError: boom" in b["detail"], (
        "the failure reason is dropped — a consumer cannot tell a permissions "
        "error from a missing path"
    )


def test_not_published_and_measure_failed_are_distinguishable_in_the_payload(monkeypatch):
    """Same KIND, different DETAIL — the states must not collapse."""
    a = _banner(monkeypatch, {"ts": "t"})
    c = _banner(monkeypatch, {"ts": "t", "disk": {"measured": False,
                                                  "reason": "OSError: boom"}})
    assert a["detail"] != c["detail"]


@pytest.mark.parametrize("free_gb,expected", [(0.5, "alert"), (2.0, "alert"),
                                              (3.2, "warning"), (5.0, "warning")])
def test_state_low_grades_severity_by_free_space(monkeypatch, free_gb, expected):
    b = _banner(monkeypatch, {"ts": "t", "disk": {"measured": True,
                                                  "free_gb": free_gb,
                                                  "used_pct": 94.0}})
    assert b is not None and b["kind"] == "trainer_disk_low"
    assert b["severity"] == expected


def test_the_live_measured_state_would_have_fired(monkeypatch):
    """Regression anchor on the REAL 2026-08-20 reading (3.2 GB free, 94%).

    If a future threshold change silences this, that change must be deliberate
    and must update BL-20260820-TRAINER-DISK-THRESHOLDS-UNCALIBRATED.
    """
    b = _banner(monkeypatch, {"ts": "t", "disk": {"measured": True, "total_gb": 45.0,
                                                  "free_gb": 3.2, "used_pct": 94.0}})
    assert b is not None and b["severity"] == "warning"
    assert "3.2" in b["message"] and "94" in b["message"]


def test_a_trainer_that_is_DOWN_gets_no_disk_banner(monkeypatch):
    """One cause, one banner. A stale mirror has no current disk fact."""
    b = _banner(monkeypatch, {"ts": "t", "disk": {"measured": True, "free_gb": 0.1}},
                trainer_down=True)
    assert b is None, (
        "a DOWN trainer produced BOTH trainer_down and a disk banner — two "
        "alarms for one cause is the desensitized-alarm pattern"
    )


def test_no_mirror_at_all_is_not_reported_as_disk_unknown(monkeypatch):
    """No contact with the trainer is not a disk finding."""
    assert _banner(monkeypatch, None) is None


def test_thresholds_are_declared_as_chosen_with_a_calibration_path():
    """The declaring comment is load-bearing, not decoration.

    A threshold shipped without saying it is uncalibrated gets read as measured
    by the next session — the exposure-ceiling mistake.
    """
    src = (REPO / "src" / "web" / "api" / "routers" / "notifications.py").read_text(
        encoding="utf-8")
    assert "CHOSEN, NOT MEASURED" in src
    assert "BL-20260820-TRAINER-DISK-THRESHOLDS-UNCALIBRATED" in src


def test_the_banner_is_wired_into_the_endpoint(monkeypatch):
    """A detector nothing calls is the build-and-abandon class itself."""
    src = (REPO / "src" / "web" / "api" / "routers" / "notifications.py").read_text(
        encoding="utf-8")
    body = src.split("def get_notifications")[1]
    assert "_trainer_disk_banner(" in body, (
        "_trainer_disk_banner is defined but never called from the endpoint"
    )


# ---------------------------------------------------------------------------
# Staleness + refusal publishing (F-35 / F-103, the other half of fix 1.3)
# ---------------------------------------------------------------------------
def _builder_block() -> str:
    """The embedded python that builds `trainer_status.json`."""
    text = PUBLISH_SH.read_text(encoding="utf-8")
    delim = "P" + "Y"
    blocks = re.findall(r"<<'" + delim + r"'\n(.*?)\n" + delim + r"\n",
                        text, flags=re.DOTALL)
    cands = [b for b in blocks if "last_cycle" in b and "training_staleness" in b]
    assert cands, "status-builder block not found — the extractor is stale"
    return cands[0]


def _run_cycle_classification(rows):
    """Execute the SHIPPING classification loop against synthetic cycle rows.

    Extracted and run, not re-declared. Everything before the loop that the
    loop needs is stubbed minimally; the loop body itself is verbatim.
    """
    block = _builder_block()
    start = block.index("cycles_24h = 0")
    end = block.index("# --- Dataset build history")
    loop_src = block[start:end]
    # the real block computes cycle_rows/cutoff_24h/parse_iso above this point
    ns = {
        "Counter": __import__("collections").Counter,
        "cycle_rows": rows,
        "cutoff_24h": 0,
        "parse_iso": lambda _s: 1,   # every row is "within 24h"
    }
    exec(loop_src, ns)
    return ns


def test_positive_control_the_classification_loop_is_real_and_runs():
    ns = _run_cycle_classification([{"status": "manifest_ok", "ts": "t"}])
    assert ns["manifests"].get("manifest_ok") == 1, (
        "the extracted loop did not classify a plain manifest_ok — the "
        "extraction, not the logic, is wrong, and every test below would be "
        "measuring a stub"
    )


def test_an_enforced_refusal_is_counted_and_NAMED():
    ns = _run_cycle_classification([
        {"status": "manifest_ok", "ts": "t"},
        {"status": "manifest_audit_skipped_enforced", "ts": "t",
         "manifest": "ml/configs/setup-quality-lgbm-v2.yaml"},
    ])
    assert ns["manifests"].get("manifest_audit_skipped_enforced") == 1, (
        "an enforced refusal counted toward NOTHING in manifests_24h — it was "
        "absent from the status set entirely, so 25 days of refusals were "
        "invisible on the one surface consumers read"
    )
    assert ns["refusing_manifests"] == {"ml/configs/setup-quality-lgbm-v2.yaml"}, (
        "the refusing manifest is counted but not NAMED — a bare count cannot "
        "be acted on"
    )


def test_a_refusal_with_no_manifest_field_is_not_silently_dropped():
    ns = _run_cycle_classification([
        {"status": "manifest_audit_skipped_enforced", "ts": "t"}])
    assert ns["refusing_manifests"] == {"(unnamed)"}, (
        "a refusal row missing its manifest field vanished from the named set "
        "while still incrementing the count — the two would disagree"
    )


def test_staleness_summary_is_captured_and_the_NEWEST_wins():
    ns = _run_cycle_classification([
        {"status": "training_staleness_summary", "ts": "t1", "stale": 3, "scanned": 76},
        {"status": "training_staleness_summary", "ts": "t2", "stale": 7, "scanned": 76},
    ])
    assert ns["staleness"] is not None
    assert ns["staleness"]["stale"] == 7, "an older summary overwrote the newest"


def test_absent_staleness_is_None_not_an_empty_dict():
    """`{}` would read as 'scanned nothing / nothing stale'."""
    ns = _run_cycle_classification([{"status": "manifest_ok", "ts": "t"}])
    assert ns["staleness"] is None


def test_the_published_block_distinguishes_absent_from_clean():
    """`present:false` must be reachable and must carry an explanation.

    Without it, a mirror with no staleness row is byte-identical to one
    reporting zero stale manifests — the collapse this whole PR is about.
    """
    block = _builder_block()
    assert '"present": staleness is not None' in block
    assert "not 'nothing is stale'" in block or "NOT \"nothing is stale\"" in block or \
           "NOT 'nothing is stale'" in block, (
        "the present:false branch does not say what it means"
    )


def test_manifests_24h_and_the_classifier_agree_on_the_enforced_key():
    """The published key and the counted key must be the same string.

    A typo here publishes a permanent 0 that reads as 'no refusals'.
    """
    block = _builder_block()
    assert block.count('"manifest_audit_skipped_enforced"') >= 2
    assert 'manifests.get("manifest_audit_skipped_enforced", 0)' in block


def test_staleness_and_refusals_reach_the_payload_not_just_a_local():
    block = _builder_block()
    assert '"training_staleness": {' in block
    assert '"refusing_manifests_24h": sorted(refusing_manifests)' in block
