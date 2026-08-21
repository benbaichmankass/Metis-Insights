"""The self-ping dedupe must suppress ONLY on a verified successful ping.

BL-20260821-OCI-INVENTORY-CRON-UNWATCHED. `claude-run-failure-alert.yml` is the
loud backstop for runs nobody is watching. Two workflows that send their own
operator ping were EXCLUDED from it to avoid double-pinging — which left their
silent failure modes covered by nothing. The dedupe lets them be listed.

That makes the suppression load-bearing: if it ever suppresses wrongly, this
listener goes quiet on exactly the dead cron it exists to catch, and nothing
would announce that. So the failure path is tested here rather than trusted.

The jq filter is EXTRACTED from the shipping workflow, never copied — a copy
drifts from the thing that actually runs, and then the test passes while the
workflow is broken (the reasoning `tests/test_merge_slot_guard.py` already
applies to the merge guard's shell).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/claude-run-failure-alert.yml"
SENTINEL = "[operator-ping]"


def _shipping_jq() -> str:
    """Pull the real jq filter out of the workflow that ships."""
    text = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(r"jq '(\[\.jobs\[\]\.steps\[\]\?.*?length)'", text, re.S)
    assert m, "could not locate the dedupe jq filter in the workflow"
    return m.group(1)


def _count(payload: dict) -> int:
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq not installed")
    out = subprocess.run(
        [jq, _shipping_jq()], input=json.dumps(payload),
        capture_output=True, text=True, check=True,
    )
    return int(out.stdout.strip())


def _run(*steps) -> dict:
    return {"jobs": [{"steps": [{"name": n, "conclusion": c} for n, c in steps]}]}


def test_successful_sentinel_suppresses():
    """A ping that really went out is the ONLY reason to stay quiet."""
    assert _count(_run((f"Telegram alert {SENTINEL} (drift)", "success"))) == 1


def test_skipped_sentinel_does_not_suppress():
    """The core case. `oci-inventory`'s notify is skipped when an earlier step
    fails (GitHub reads a custom `if:` as `success() && <expr>`), so the step
    EXISTS and sent nothing. Matching on presence would suppress here and lose
    the alert — which is the whole silent-cron failure this closes."""
    assert _count(_run((f"Telegram alert {SENTINEL} (drift)", "skipped"))) == 0


def test_failed_sentinel_does_not_suppress():
    """A notify step that errored (bad token, network) also sent nothing."""
    assert _count(_run((f"Notify operator {SENTINEL}", "failure"))) == 0


def test_run_without_a_sentinel_does_not_suppress():
    """The ordinary watched workflow: no self-ping, so the listener must fire."""
    assert _count(_run(("Run inventory", "success"), ("Post report", "success"))) == 0


def test_unmarked_telegram_step_does_not_suppress():
    """Opt-in is by the SENTINEL, not by looking like a notifier. A workflow
    that sends a ping without marking it still gets a listener alert — a
    duplicate, which is the safe direction and is visible enough to fix."""
    assert _count(_run(("Telegram alert (drift)", "success"))) == 0


def test_sentinel_found_across_multiple_jobs():
    payload = {"jobs": [
        {"steps": [{"name": "build", "conclusion": "failure"}]},
        {"steps": [{"name": f"Notify {SENTINEL}", "conclusion": "success"}]},
    ]}
    assert _count(payload) == 1


def test_job_without_steps_does_not_explode():
    """A queued/expired job carries no `steps`; the `?` in the filter is what
    keeps that from erroring into a could_not_check."""
    assert _count({"jobs": [{"steps": None}, {}]}) == 0


def test_every_listed_self_pinger_carries_the_sentinel():
    """The wiring half: a workflow listed in the watcher that sends its own
    operator ping MUST mark it, or listing it re-introduces the double-ping
    the dedupe was built to remove."""
    text = WORKFLOW.read_text(encoding="utf-8")
    for name in ("oci-inventory", "health-snapshot"):
        wf = REPO / f".github/workflows/{name}.yml"
        body = wf.read_text(encoding="utf-8")
        assert "api.telegram.org" in body or "notify_session" in body, (
            f"{name} was expected to self-ping; if that changed, revisit its "
            "entry in the watcher rather than deleting this assertion"
        )
        assert SENTINEL in body, (
            f"{name}.yml self-pings but no step carries {SENTINEL!r} — the "
            "watcher cannot verify the suppression and will double-ping"
        )
    assert "- oci-inventory" in text and "- Health Snapshot" in text


def test_could_not_look_is_not_treated_as_self_pinged():
    """The collapsed-state rule at the centre of this design: an unreadable
    API response must PING, never suppress. Asserted against the shipping
    script text because the branch is bash, not jq."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "state=could_not_check" in text
    idx = text.index("state=could_not_check")
    window = text[idx: idx + 400]
    assert "alerting anyway" in window, (
        "the could_not_check branch must fall through to an alert; suppressing "
        "on an unread response is the swallowed-ping bug this workflow exists "
        "to prevent"
    )
