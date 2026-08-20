"""The trainer cycle's missing state: a manifest we LOOKED AT and REFUSED.

F-35 found 7 of 76 manifests stale while `/api/bot/ml/status` read green;
F-103 found the cause. Five manifests (`baseline-prop-mission-policy`,
`btc-regime-15m-lgbm-base-vt003-pcv-v1`, `mes-regime-1d-lgbm-v2`,
`setup-candidates-metalabel-xsym-yz-v1`, `setup-quality-lgbm-v2`) had been
enforced-skipped for 25 days under `overall_rc: 0`.

MEASURED on the live mirror 2026-08-20 (7 consecutive `cycle_end` pairs
spanning 76.4 h, the full retained window — not a sample), which corrects the
audit's own wording. There are TWO cycles a day and they report differently:

    ~01:30Z  trained=68 skipped=8 failed=0 already_done=0  outcome=trained
    ~05:30Z  trained=0  skipped=0 failed=0 already_done=76 outcome=already_complete

So a refusal is booked as `already_done` only on the SAME-DAY RESUME — and
that resume cycle is the `last_cycle` published to `/api/bot/ml/status` for
**60.3 of 76.4 hours = 78.9%** of all time. The visible summary is
systematically the one reporting `skipped: 0`.

Both collapses are fixed here: `skipped_enforced` splits refusals out of the
four-reason `skipped` bucket, and `carried_done`/`carried_skipped` split
`already_done` into trained-earlier vs refused-earlier.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CYCLE_SH = REPO / "scripts" / "ops" / "run_training_cycle.sh"


def _sh(script: str) -> str:
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# The outcome ladder, executed — not asserted against as text
# ---------------------------------------------------------------------------
def _outcome_block() -> str:
    """Lift the SHIPPING outcome branch out of the script and run it.

    Extracted rather than re-declared: a test that restates the logic it is
    testing passes against a fiction (the `pairs_soak` lesson, where tests
    declared an `order_packages` schema production does not have).
    """
    text = CYCLE_SH.read_text(encoding="utf-8")
    m = re.search(r"(refusals_total=\$\(\( skipped_enforced_n \+ carried_skipped_n \)\).*?\nfi)",
                  text, flags=re.DOTALL)
    assert m, "outcome branch not found — the extractor, not the logic, is stale"
    return m.group(1)


def _outcome(trained, enforced, carried_skipped, to_run_len):
    to_run = " ".join(f"m{i}" for i in range(to_run_len))
    return _sh(
        f'trained_n={trained}\nskipped_enforced_n={enforced}\n'
        f'carried_skipped_n={carried_skipped}\nTO_RUN_LIST=({to_run})\n'
        + _outcome_block() + '\necho "$cycle_outcome"'
    )


def test_positive_control_the_extractor_yields_runnable_bash():
    block = _outcome_block()
    assert "cycle_outcome=" in block and len(block.splitlines()) > 5
    assert _sh("trained_n=1\nskipped_enforced_n=0\ncarried_skipped_n=0\n"
               "TO_RUN_LIST=()\n" + block + '\necho "$cycle_outcome"')


@pytest.mark.parametrize("trained,enf,carried,to_run,expected", [
    # the live 01:30 cycle: trained 68, and 5 of the 8 skips are refusals
    (68, 5, 0, 76, "trained_with_refusals"),
    # the live 05:30 resume: nothing to run, 5 still refusing
    (0,  0, 5, 0,  "complete_with_refusals"),
    # a genuinely clean fleet — must NOT gain a refusal label
    (68, 0, 0, 76, "trained"),
    (0,  0, 0, 0,  "already_complete"),
    (0,  0, 0, 3,  "nothing_trained"),
])
def test_outcome_ladder(trained, enf, carried, to_run, expected):
    assert _outcome(trained, enf, carried, to_run) == expected


def test_the_two_live_cycles_no_longer_report_the_same_thing_as_a_clean_fleet():
    """The whole point, stated as one assertion.

    Before this change both live cycles were indistinguishable from a healthy
    fleet: `trained` and `already_complete` are exactly what a clean run emits.
    """
    dirty_train = _outcome(68, 5, 0, 76)
    dirty_resume = _outcome(0, 0, 5, 0)
    clean_train = _outcome(68, 0, 0, 76)
    clean_resume = _outcome(0, 0, 0, 0)
    assert dirty_train != clean_train
    assert dirty_resume != clean_resume


# ---------------------------------------------------------------------------
# The carried-forward split, executed against the real progress loader
# ---------------------------------------------------------------------------
def _loader_python() -> str:
    text = CYCLE_SH.read_text(encoding="utf-8")
    m = re.search(r"mapfile -t TO_RUN_LIST < <\(\n\s*python - [^\n]*\n(.*?)\nPY\n\)",
                  text, flags=re.DOTALL)
    assert m, "progress-loader python not found"
    return m.group(1)


def test_loader_splits_carried_done_from_carried_skipped(tmp_path):
    """A refused manifest must not be counted as trained-earlier."""
    prog = tmp_path / "cycle_progress_2026-08-20.json"
    prog.write_text(json.dumps({
        "date": "2026-08-20", "manifests": {
            "a.yaml": {"status": "done"},
            "b.yaml": {"status": "done"},
            "c.yaml": {"status": "skipped"},   # <- the refusal
            "d.yaml": {"status": "pending"},
        }}), encoding="utf-8")
    script = tmp_path / "loader.py"
    script.write_text(_loader_python(), encoding="utf-8")
    out = _sh(f'cd {tmp_path} && python3 loader.py "{prog}" 2026-08-20 sha "" '
              f'a.yaml b.yaml c.yaml d.yaml')
    assert out.split() == ["d.yaml"], f"run list wrong: {out!r}"

    carried = json.loads((tmp_path / (prog.name + ".carried")).read_text())
    assert carried["carried_done"] == 2
    assert carried["carried_skipped"] == 1, (
        "the refused manifest was folded into carried_done — the exact "
        "collapse that made 25 days of refusals read as already_done"
    )
    assert carried["carried_skipped_manifests"] == ["c.yaml"], (
        "the refusing manifest is not NAMED, so a consumer sees a count with "
        "no way to act on it"
    )


def test_carried_split_sums_to_the_backcompat_already_done(tmp_path):
    """`carried_done + carried_skipped == already_done`, or the two disagree.

    Arithmetic, not a re-read — counts and sums catch what proofreading misses.
    """
    prog = tmp_path / "cycle_progress_2026-08-20.json"
    manifests = {f"m{i}.yaml": {"status": s} for i, s in enumerate(
        ["done"] * 68 + ["skipped"] * 8)}
    prog.write_text(json.dumps({"date": "2026-08-20", "manifests": manifests}),
                    encoding="utf-8")
    script = tmp_path / "loader.py"
    script.write_text(_loader_python(), encoding="utf-8")
    args = " ".join(manifests)
    out = _sh(f'cd {tmp_path} && python3 loader.py "{prog}" 2026-08-20 sha "" {args}')
    to_run = len(out.split()) if out else 0
    carried = json.loads((tmp_path / (prog.name + ".carried")).read_text())
    already_done = len(manifests) - to_run           # how the script derives it
    assert carried["carried_done"] + carried["carried_skipped"] == already_done == 76
    assert carried["carried_skipped"] == 8


# ---------------------------------------------------------------------------
# Contract-level assertions on the shipping script
# ---------------------------------------------------------------------------
def _cycle_end_emitter() -> str:
    text = CYCLE_SH.read_text(encoding="utf-8")
    emit = [ln for ln in text.splitlines()
            if '"status":"cycle_end"' in ln and not ln.lstrip().startswith("#")]
    assert len(emit) == 1, f"expected one cycle_end emitter, found {len(emit)}"
    return emit[0]


def test_cycle_end_publishes_the_split_and_its_read_state():
    line = _cycle_end_emitter()
    for field in ("skipped_enforced", "carried_done", "carried_skipped",
                  "carried_read", "refusals_total"):
        assert field in line, f"cycle_end does not publish {field}"
    assert "already_done" in line, (
        "already_done was dropped — the mirror, the review skills and "
        "/api/bot/ml/status all read it; this must stay back-compatible"
    )


def test_the_header_contract_and_the_emitter_do_not_drift():
    """The header documents the `cycle_end` shape. Field beats comment — so
    hold the comment to the field rather than letting it rot into a claim
    about a payload that no longer exists (the PR #1358 class)."""
    text = CYCLE_SH.read_text(encoding="utf-8")
    header = text[:text.index("set -")]
    documented = set(re.findall(r'"(\w+)":', header[header.index('"status":"cycle_end"'):]))
    emitted = set(re.findall(r'"(\w+)":', _cycle_end_emitter()))
    documented.discard("status")
    missing = documented - emitted - {"ts"}
    extra = emitted - documented - {"ts", "status"}
    assert not missing, f"header documents fields the emitter does not send: {sorted(missing)}"
    assert not extra, f"emitter sends fields the header never documents: {sorted(extra)}"


def test_carried_read_can_say_we_could_not_look():
    """Three-state, never collapsed: 0/0 must not silently mean 'no refusals'."""
    text = CYCLE_SH.read_text(encoding="utf-8")
    assert 'carried_read="unavailable"' in text, (
        "the default is not `unavailable` — an unreadable side file would "
        "report 0 refusals, which is a measurement it never made"
    )
    assert '"0 0 unavailable"' in text


def test_the_enforced_skip_still_increments_the_backcompat_skipped_count():
    """A refusal is BOTH a skip and a refusal; dropping it from `skipped`
    would silently change a number other consumers already read."""
    text = CYCLE_SH.read_text(encoding="utf-8")
    blk = text.split("manifest_audit_skipped_enforced")[1][:900]
    assert "skipped_n=$((skipped_n + 1))" in blk
    assert "skipped_enforced_n=$((skipped_enforced_n + 1))" in blk


def test_script_still_parses():
    subprocess.run(["bash", "-n", str(CYCLE_SH)], check=True)
