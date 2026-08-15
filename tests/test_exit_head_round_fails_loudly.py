"""A round whose training step dies must NOT report success.

`m20_exit_head_round.py` checked the DATASET-BUILD subprocess's return code and
not the TRAINING one. Control fell through to `if e1.exists():`, so a dead
training run left the report empty, the driver wrote a **zero-row**
`rounds.jsonl`, and `main` returned **0**.

Measured twice on 2026-08-15, both from the trainer's ~15-min
`Reset to origin/main` removing a branch-only flag MID-ARM
(`unrecognized arguments: --fold-offset 4`):

  * 2h round arm `off12` — 73 min of emit+build, then 0 rows, `exit=0`.
  * 5m round arm `off4`  — identical. Arm `off0` of the SAME round produced 3
    rows, because `if a.fold_offset:` treats **0 as falsy** so the control arm
    never passes the flag and needs no branch code. That asymmetry is why the
    failure read as a partial success rather than a broken run.

Both were caught only by an EXTERNAL row-count assertion in the relay. `exit=0`
passed. A `[ -f rounds.jsonl ]` existence check passed too — the file is
created, just empty. A dispersion screen built on `exit=$?` recorded both dead
arms as completed, which is how a 4-draw result silently became a 3-draw one.

These tests pin the three properties that make that impossible, without running
a round (the real thing needs the trainer and hours of compute):

  1. the training subprocess's `returncode` is CHECKED at all,
  2. a failure produces a NON-ZERO exit from `main`,
  3. the artifact states the failure rather than merely lacking rows.

⚠️ They are deliberately structural — asserting on the source — rather than
behavioural. A behavioural test would need to stand up a family dir, a fake
trainer and a fake harness; that is worth building, but a structural test that
exists today beats a behavioural one that does not, and this defect's whole
history is that nothing checked it at all.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROUND = REPO / "scripts" / "research" / "m20_exit_head_round.py"


def _source() -> str:
    return ROUND.read_text()


def test_the_module_still_parses() -> None:
    """Positive control — an unparseable file would make every scan below vacuous."""
    tree = ast.parse(_source())
    assert any(isinstance(n, ast.FunctionDef) and n.name == "main"
               for n in ast.walk(tree)), "main() is gone — re-read this module"


def test_the_build_step_is_still_checked() -> None:
    """The half that was ALREADY correct, pinned so a 'cleanup' cannot remove it.

    The bug was an asymmetry between two adjacent subprocess calls. Guarding only
    the training half would let the asymmetry be restored from the other side.
    """
    src = _source()
    assert "p = sh(build_cmd" in src, "the build subprocess call moved — re-read"
    build_at = src.index("p = sh(build_cmd")
    after = src[build_at:build_at + 400]
    assert "returncode" in after, (
        "the dataset-build subprocess's returncode is no longer checked right "
        "after the call; that check is what the training half was missing")


def test_the_training_subprocess_returncode_IS_checked() -> None:
    """The actual fix. Without this the round cannot know it failed."""
    src = _source()
    assert "p = sh(train_cmd" in src, "the training subprocess call moved — re-read"
    train_at = src.index("p = sh(train_cmd")
    window = src[train_at:train_at + 900]
    assert "p.returncode" in window, (
        "the TRAINING subprocess's returncode is not examined after the call. "
        "This is the exact defect that let two dead arms report exit=0 with "
        "zero rows on 2026-08-15 — see this module's docstring.")


def test_a_training_failure_produces_a_NON_ZERO_exit() -> None:
    """`exit=$?` is what a wrapper reads; returning 0 is what hid the failures."""
    src = _source()
    assert "train_failures" in src, (
        "no failure accumulator — a round that loses a family has nothing to "
        "turn into a non-zero exit")
    tail = src[src.index("round done ->"):]
    assert "return 2" in tail or "return 1" in tail, (
        "main() returns only 0 after the round completes, so a caller looping "
        "arms cannot distinguish a complete round from one whose training died")
    assert "if train_failures" in tail, (
        "the non-zero return is not conditioned on the failure list")


def test_the_ARTIFACT_states_the_failure_rather_than_merely_lacking_rows() -> None:
    """A partial round must be distinguishable from a small complete one.

    Otherwise a reader needs to already know the expected leg count to notice
    anything is wrong — which is the unasserted-denominator shape (CLAUDE.md
    § "Diagnostic provenance" sub-class C) one level up from the exit code.
    """
    src = _source()
    meta_at = src.index('"total_sort": bool(a.total_sort),')
    meta_block = src[meta_at:meta_at + 800]
    assert "train_failures" in meta_block, (
        "round_report.json's _round_meta does not record train_failures, so a "
        "partial round's artifact looks like a complete one")
    assert "families_trained" in meta_block, (
        "the artifact does not state which families DID train, so 'no failures' "
        "is implied by absence rather than stated")


def test_the_capability_preflight_runs_BEFORE_any_expensive_work() -> None:
    """A branch-only flag must be checked before the hour is spent, not after.

    The trainer VM re-checks out from origin/main every ~15 min, so a reset
    landing mid-arm removes `--fold-offset` from the trainer while this driver
    still forwards it. Twice on 2026-08-15 that cost 73 minutes of emit+build
    before argparse rejected the flag at the very last step.

    A file-hash gate cannot catch this — the screen harness hashed both files at
    ARM START and passed, because the reset arrived afterwards. Asking the
    trainer whether it accepts the flag at the moment of use is the check that
    holds, and its VALUE comes from running early: the same check placed after
    the build would be correct and still waste the hour.
    """
    src = _source()
    assert "pre-flight" in src.lower(), "the capability pre-flight is gone"
    preflight_at = src.index("PRE-FLIGHT FAILED")
    # The emit loop is the first expensive thing main() does.
    emit_at = src.index('for leg in a.legs.split(",")')
    assert preflight_at < emit_at, (
        "the pre-flight runs after the emit loop has already started, so it "
        "can only confirm that an hour was wasted rather than prevent it")


def test_the_preflight_matches_flag_names_at_a_WORD_BOUNDARY() -> None:
    """A substring probe answers a question adjacent to the one asked.

    `"--fold-offset" in help_text` is TRUE for a trainer offering
    `--fold-offsets`, `--fold-offset-mode`, or any rename — so the probe reports
    a capability the trainer does not have. This is not hypothetical: the
    negative test written for this very pre-flight renamed the trainer's
    argument to `--fold-offset-REMOVED-BY-SIMULATED-RESET` and the substring
    version printed "pre-flight OK". A guard that cannot fail is not a guard.
    """
    src = _source()
    assert "re.escape" in src and r"(?![\w-])" in src, (
        "the pre-flight no longer matches flag names at a word boundary; a "
        "bare `flag in help_text` passes on any longer flag that starts with "
        "the same characters")


def test_the_round_takes_the_trainer_HEAVY_JOB_QUEUE() -> None:
    """The reset that voided two arms runs INSIDE this lock.

    `docs/claude/trainer-resource-protocol.md` § Rule 1 is binding: every
    memory-heavy job on the 6 GB trainer takes one shared blocking lock. The
    timer wrappers take it — including `run_training_cycle.sh`, whose
    `git checkout --force -B main origin/main` IS the "~15-min reset" that
    removed `--fold-offset` mid-arm. That reset is not a background force of
    nature: it runs inside the queue and skips its whole cycle when the queue is
    held. Holding the lock prevents it landing mid-arm outright.

    This round took the queue nowhere, and the `ml` CLI's enforced backstop
    cannot cover it — that fires for `python -m ml train|build-dataset`, and
    this driver shells out to `scripts/ml/train_exit_head.py`, which is not the
    CLI. So the protection was absent on both paths.

    Ordering matters and is asserted: pre-flight BEFORE the lock. A missing flag
    should fail in two seconds, not after waiting up to an hour in a queue to
    learn the trainer would have rejected it anyway.
    """
    src = _source()
    assert "acquire_heavy_lock" in src, (
        "the round does not take the trainer heavy-job queue, so a training "
        "cycle can reset the worktree mid-arm and this driver is also free to "
        "collide with any other heavy job on a 6 GB box")
    lock_at = src.index("acquire_heavy_lock(")
    preflight_at = src.index("PRE-FLIGHT FAILED")
    emit_at = src.index('for leg in a.legs.split(",")')
    assert preflight_at < lock_at, (
        "the heavy lock is taken before the capability pre-flight, so a round "
        "with a flag the trainer will reject can sit in the queue for up to an "
        "hour before finding out")
    assert lock_at < emit_at, (
        "the lock is taken after the emit loop starts, so the expensive half "
        "runs unqueued — which is the collision this protocol exists to stop")


def test_a_zero_row_round_does_NOT_write_rounds_jsonl() -> None:
    """Existence of the evidence file must imply the round produced evidence.

    The zero-row `rounds.jsonl` is the artifact that fooled the most checks: it
    is created, so `[ -f rounds.jsonl ]` passes; it is empty, so a readout loop
    that `sed`s it prints nothing and the arm reads as ABSENT rather than
    FAILED. I wrote exactly that loop on 2026-08-15 and it swallowed the off12
    arm — the file's mtime was the only evidence the arm had ever run.

    Writing a differently-named marker instead makes the three states readable
    from the filesystem with no exit code and no stdout parsing.
    """
    src = _source()
    write_at = src.index('(out / "rounds.jsonl").write_text')
    # The write must be conditional on there being rows. Look at the lines
    # immediately above it, not at the file as a whole — an `if rows:` matching
    # somewhere else entirely would prove nothing.
    before = src[max(0, write_at - 400):write_at]
    assert "if rows:" in before, (
        "rounds.jsonl is written unconditionally, so a round that produced no "
        "evidence still leaves a file that every existence check believes")
    assert "rounds.EMPTY" in src, (
        "no distinct marker for the zero-row case — 'the round ran and "
        "produced nothing' and 'the round never reached the emit step' are "
        "different facts and must not share an artifact")


def test_the_zero_exit_but_no_report_case_is_ALSO_caught() -> None:
    """Third state: exited 0 and still wrote nothing.

    Distinct from both success and a non-zero exit. If only the non-zero branch
    were handled, a trainer that swallowed its own error would reproduce the
    original defect exactly.
    """
    src = _source()
    train_at = src.index("p = sh(train_cmd")
    window = src[train_at:train_at + 2000]
    assert "e1.exists()" in window
    assert window.count("train_failures.append") >= 2, (
        "only one failure path is recorded. A training run that exits 0 and "
        "writes no e1_report.json must be recorded too, or the fix covers only "
        "the failure mode that happened to occur first")
