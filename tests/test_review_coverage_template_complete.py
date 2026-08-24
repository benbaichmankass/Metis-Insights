"""The report TEMPLATE must carry every key the renderer REQUIRES.

`comms/schema/system_report_response.template.json` is what CLAUDE.md names as
the output schema for /system-review — it is the shape a session copies to build
its payload. `_REQUIRED_COVERAGE_KEYS` in the renderer is what actually gets
enforced. Nothing kept the two in step, and they drifted:

Measured 2026-08-24 — the template carried EIGHT review_coverage keys while the
renderer required ELEVEN. Missing from the template: since_last_build_verification,
backlog_classes, ml_output_actionability (all 2026-08-20) and unexercised_fixes
(2026-08-24). So the canonical shape actively taught a payload that the renderer
would reject, and the only feedback was a --strict failure at render time, after
the review work was already done.

This is the same family as the guards it sits beside: a rule declared in one
place and enforced in another, with nothing asserting they agree. The renderer is
authoritative; the template mirrors it; this test is the detector.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "comms/schema/system_report_response.template.json"


def _required_keys() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location(
        "render_system_report", REPO / "scripts/reports/render_system_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._REQUIRED_COVERAGE_KEYS


def _template_keys() -> set[str]:
    doc = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    rc = (doc.get("consolidated") or {}).get("review_coverage") or {}
    return {k for k in rc if not k.startswith("_")}


def test_template_covers_every_required_key():
    missing = sorted(set(_required_keys()) - _template_keys())
    assert not missing, (
        "review_coverage keys REQUIRED by the renderer but absent from "
        f"{TEMPLATE.relative_to(REPO)}: {missing}. The template is the shape a "
        "review copies — if it omits a required key the review builds a payload "
        "--strict will reject, after the work is done. Add the key to the "
        "template (with a _comment saying what it proves), do not remove it from "
        "_REQUIRED_COVERAGE_KEYS.")


def test_template_declares_no_key_the_renderer_does_not_require():
    """Drift in the other direction is also a defect: a template key nothing
    enforces reads as mandatory to whoever copies it, and quietly is not."""
    required = set(_required_keys())
    # flags_raised is referenced throughout the guard as the escalation channel
    # for several keys, so it legitimately appears in the template without being
    # a required-coverage key of its own.
    extra = sorted(_template_keys() - required - {"flags_raised"})
    assert not extra, (
        f"template declares review_coverage keys the renderer does not require: "
        f"{extra}. Either add them to _REQUIRED_COVERAGE_KEYS or drop them — a "
        "key that looks mandatory and is not will be filled in with noise.")


def test_template_is_valid_json_and_has_the_block():
    doc = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert isinstance((doc.get("consolidated") or {}).get("review_coverage"), dict)
