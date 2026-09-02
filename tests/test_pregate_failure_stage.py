"""The replay pre-gate must name the STAGE that failed, not guess at a cause.

Anchored on `replay-pregate-nightly` run #4365 (`33491178494`, `event=schedule`,
2026-09-01T09:14:38Z): the SSH session dropped at model 10 of 22 and the
workflow reported `no JSON object in driver output` -- a PARSING label for a
CONNECTION failure (UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A).

BOTH DIRECTIONS ARE ASSERTED, because a branch verified in one direction only
is decorative: a transport failure must name transport and NOT parsing, and a
genuinely unparseable stream must still name parsing and NOT transport.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.ml import pregate_stream as ps  # noqa: E402

# The stderr OpenSSH actually emitted in run #4365, copied from the job log.
OBSERVED_SSH_STDERR = "client_loop: send disconnect: Broken pipe"

# The stderr the driver had produced by model 10 of 22 -- nine graded heads, in
# HUMAN-READABLE progress form. It is reproduced here to pin the point of the
# fix: these lines carry the numbers, and they must never be the thing parsed.
OBSERVED_DRIVER_STDERR = "\n".join(
    [
        "[fleet] scoring 22 head(s): " + ", ".join(f"m{i}" for i in range(1, 23)),
        "[fleet] (1/22) btc-regime-15m-baseline-v1 ...",
        "[fleet]   -> TRUSTWORTHY_SIGNAL auc=0.7075 n=24071",
        "[fleet] (10/22) btc-regime-5m-lgbm-v2 ...",
    ]
)


def _result_row(model_id: str, auc: float) -> dict:
    return {
        "model_id": model_id, "symbol": "BTCUSDT", "timeframe": "5m",
        "overall": {"auc": auc, "n": 15816}, "auc_verdict": "TRUSTWORTHY_SIGNAL",
        "n_scored": 15816,
    }


def _truncated_stream(n_declared: int = 22, n_done: int = 9) -> str:
    """The stdout a stream truncated mid-run leaves behind, under the new format."""
    lines = [ps.frame(ps.BEGIN, {"model_ids": [f"m{i}" for i in range(1, n_declared + 1)]})]
    for i in range(1, n_done + 1):
        lines.append(ps.frame(ps.RESULT, _result_row(f"m{i}", 0.70 + i / 100)))
    # No REPORT line: the run never finished. Trailing newline omitted on
    # purpose -- a pipe breaks mid-write, it does not end tidily.
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# DIRECTION 1 -- a transport failure must name TRANSPORT, never parsing.
# --------------------------------------------------------------------------- #

def test_broken_pipe_is_graded_transport_not_parsing():
    out = ps.classify(255, _truncated_stream(), OBSERVED_SSH_STDERR)
    assert out["state"] == ps.TRANSPORT_FAILED
    assert out["signature_line"] == OBSERVED_SSH_STDERR
    text = ps.describe(out).lower()
    assert "broken pipe" in text
    for parsing_word in ("json", "pars", "unparseable", "output format"):
        assert parsing_word not in text, (
            f"a transport failure must not be described with {parsing_word!r}: {text}")


def test_broken_pipe_with_byte_empty_stdout_still_grades_transport():
    """The EXACT shape of run #4365: stdout was byte-empty when the pipe broke.

    Both `driver_output_absent` and `transport_failed` were true of that run.
    Classification is by the EARLIEST failed stage, so transport wins -- an
    empty stdout is the CONSEQUENCE of the drop, not an independent finding.
    """
    out = ps.classify(255, "", OBSERVED_SSH_STDERR)
    assert out["state"] == ps.TRANSPORT_FAILED
    assert out["stdout_bytes"] == 0
    assert out["salvaged_count"] == 0


def test_old_blind_scan_would_have_said_parsing_on_this_input():
    """CONTROL: the replaced logic, run on the observed input, says 'parsing'.

    Without this the fix cannot be shown to change anything -- it pins that the
    old branch really did produce the wrong label on the real failure, so the
    new branch is a correction rather than a reword.
    """
    raw = ""  # run #4365's stdout, verbatim: `print(raw[-2000:])` printed blank
    start, end = raw.find("{"), raw.rfind("}")
    old_verdict = "no JSON object in driver output" if (start < 0 or end < 0) else "parsed"
    assert old_verdict == "no JSON object in driver output"
    assert ps.classify(255, raw, OBSERVED_SSH_STDERR)["state"] == ps.TRANSPORT_FAILED


# --------------------------------------------------------------------------- #
# DIRECTION 2 -- genuinely unparseable output must still name PARSING.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "garbage",
    [
        "{not json at all",
        '##PREGATE-REPORT## {"results": [1,2,',      # framed but truncated JSON
        "Traceback (most recent call last):\n  File x\nValueError: boom\n{",
        "{}{}{}",                                     # parses, but is not a report
    ],
)
def test_unparseable_driver_output_is_graded_parsing_not_transport(garbage):
    out = ps.classify(0, garbage, "")
    assert out["state"] == ps.DRIVER_OUTPUT_UNPARSEABLE
    text = ps.describe(out).lower()
    assert "parsed" in text or "unparseable" in text
    assert "ssh" not in text and "broken pipe" not in text


def test_a_remote_traceback_naming_a_network_word_is_not_a_transport_failure():
    """A driver that dies saying 'Connection refused' is a REMOTE failure.

    Matching transport signatures anywhere in stderr would file this as a
    network fault -- the same misattribution as run #4365, merely reversed.
    """
    stderr = ("Traceback (most recent call last):\n"
              "  File \"replay_pregate_fleet.py\", line 1, in <module>\n"
              "ConnectionRefusedError: [Errno 111] Connection refused\n")
    out = ps.classify(1, "", stderr)
    assert out["state"] == ps.REMOTE_COMMAND_FAILED
    assert out["signature_line"] is None


# --------------------------------------------------------------------------- #
# The remaining stages, each distinguishable from its neighbours.
# --------------------------------------------------------------------------- #

def test_empty_output_on_a_clean_exit_is_absent_not_unparseable():
    out = ps.classify(0, "   \n  \n", "")
    assert out["state"] == ps.DRIVER_OUTPUT_ABSENT


def test_exit_255_without_an_ssh_signature_is_undetermined():
    """255 is ssh's own error code AND a valid remote exit code.

    Calling it transport on the exit code alone would assert a cause no
    evidence supports -- which is the defect, not the fix.
    """
    out = ps.classify(255, _truncated_stream(), "some remote noise\n")
    assert out["state"] == ps.UNDETERMINED
    assert "not determined" in out["why"]


def test_uncaptured_exit_code_is_undetermined_never_ok():
    assert ps.classify(None, "", "")["state"] == ps.UNDETERMINED


def test_complete_report_on_a_clean_exit_is_ok():
    report = {"stage": 1, "n_models": 2, "n_scored": 2,
              "results": [_result_row("m1", 0.81), _result_row("m2", 0.79)],
              "errors": []}
    out = ps.classify(0, ps.frame(ps.REPORT, report), "")
    assert out["state"] == ps.OK
    assert out["report"]["n_scored"] == 2


def test_complete_report_on_a_dirty_exit_is_not_promoted_to_ok():
    report = {"stage": 1, "results": [_result_row("m1", 0.81)], "errors": []}
    out = ps.classify(1, ps.frame(ps.REPORT, report), "")
    assert out["state"] == ps.UNDETERMINED


def test_every_state_is_reachable_from_some_input():
    """No state in the vocabulary may be unreachable -- an unreachable state is
    already collapsed into its neighbours."""
    report = ps.frame(ps.REPORT, {"stage": 1, "results": [], "errors": []})
    reached = {
        ps.classify(0, report, "")["state"],
        ps.classify(255, "", OBSERVED_SSH_STDERR)["state"],
        ps.classify(1, "", "")["state"],
        ps.classify(0, "", "")["state"],
        ps.classify(0, "{oops", "")["state"],
        ps.classify(None, "", "")["state"],
    }
    assert reached == set(ps.STATES)


# --------------------------------------------------------------------------- #
# Partial credit: the nine graded heads must survive, WITH their denominator.
# --------------------------------------------------------------------------- #

def test_partial_report_salvages_nine_of_twenty_two_with_the_denominator():
    out = ps.classify(255, _truncated_stream(22, 9), OBSERVED_SSH_STDERR)
    assert out["salvaged_count"] == 9
    assert out["declared_count"] == 22
    part = ps.partial_report(out)
    assert part["partial"] is True
    assert part["partial_reason"] == ps.TRANSPORT_FAILED
    assert part["n_scored"] == 9 and part["n_models"] == 22
    assert len(part["results"]) == 9
    assert "9/22" in ps.describe(out)


def test_partial_report_renders_through_the_unchanged_summary_renderer():
    """A partial must be shaped like a real report so nothing downstream forks."""
    from scripts.ml.replay_pregate_summary import render
    part = ps.partial_report(ps.classify(255, _truncated_stream(22, 9),
                                         OBSERVED_SSH_STDERR))
    md = render(part)
    assert md.count("TRUSTWORTHY_SIGNAL") == 9


def test_nothing_is_salvaged_when_nothing_arrived():
    assert ps.partial_report(ps.classify(255, "", OBSERVED_SSH_STDERR)) is None


def test_salvage_never_parses_the_human_readable_progress_text():
    """The nine AUCs exist on stderr as display text. Recovering numbers from a
    LABEL rather than from data is the same defect class this fix closes."""
    out = ps.classify(255, "", OBSERVED_DRIVER_STDERR + "\n" + OBSERVED_SSH_STDERR)
    assert out["salvaged_count"] == 0
    assert ps.partial_report(out) is None


def test_malformed_wire_lines_are_counted_not_silently_dropped():
    stream = "\n".join([
        ps.frame(ps.BEGIN, {"model_ids": ["m1", "m2"]}),
        ps.frame(ps.RESULT, _result_row("m1", 0.8)),
        f"{ps.RESULT} {{truncated",
    ])
    out = ps.classify(255, stream, OBSERVED_SSH_STDERR)
    assert out["salvaged_count"] == 1
    assert out["malformed_lines"] == 1


# --------------------------------------------------------------------------- #
# End-to-end: a REAL process whose stream is truncated mid-write.
# --------------------------------------------------------------------------- #

def test_end_to_end_against_a_real_truncated_subprocess(tmp_path):
    """Plant an actual transport failure rather than only a synthetic string.

    A real child writes framed rows, is cut off mid-stream, and exits 255 with
    OpenSSH's own message on stderr -- then the CLI is invoked exactly as the
    workflow invokes it, and the artifacts it writes are asserted.
    """
    producer = tmp_path / "producer.py"
    producer.write_text(
        "import sys, json, os\n"
        "sys.path.insert(0, %r)\n"
        "from scripts.ml import pregate_stream as ps\n"
        "print(ps.frame(ps.BEGIN, {'model_ids': ['m%%d' %% i for i in range(1, 23)]}), flush=True)\n"
        "for i in range(1, 10):\n"
        "    print(ps.frame(ps.RESULT, {'model_id': 'm%%d' %% i, 'symbol': 'BTCUSDT',\n"
        "        'timeframe': '5m', 'overall': {'auc': 0.8, 'n': 15816},\n"
        "        'auc_verdict': 'TRUSTWORTHY_SIGNAL', 'n_scored': 15816}), flush=True)\n"
        "sys.stdout.write('%s {\"model_id\": \"m10\"')\n"   # cut mid-write
        "sys.stdout.flush()\n"
        "sys.stderr.write(%r + chr(10))\n"
        "sys.exit(255)\n" % (str(REPO), ps.RESULT, OBSERVED_SSH_STDERR),
        encoding="utf-8",
    )
    out_f, err_f = tmp_path / "out.txt", tmp_path / "err.txt"
    with out_f.open("wb") as o, err_f.open("wb") as e:
        rc = subprocess.call([sys.executable, str(producer)], stdout=o, stderr=e)
    assert rc == 255, "the planted transport failure must really exit 255"

    report_f = tmp_path / "report.json"
    partial_f = tmp_path / "partial.json"
    outcome_f = tmp_path / "outcome.json"
    gh_out = tmp_path / "gh_output.txt"
    gh_out.touch()

    cli = subprocess.run(
        [sys.executable, str(REPO / "scripts/ml/pregate_stream.py"),
         "--exit-code", str(rc), "--stdout", str(out_f), "--stderr", str(err_f),
         "--report-out", str(report_f), "--partial-out", str(partial_f),
         "--outcome-out", str(outcome_f), "--github-output", str(gh_out)],
        capture_output=True, text=True, check=False,
    )
    assert cli.returncode == 0, "the classifier decides the verdict, not the exit code"
    assert "::error::pre-gate transport_failed" in cli.stdout
    assert "no JSON object" not in cli.stdout

    gh = dict(ln.split("=", 1) for ln in gh_out.read_text().strip().splitlines())
    assert gh["state"] == "transport_failed"
    assert gh["salvaged_count"] == "9"
    assert gh["declared_count"] == "22"
    assert gh["wrote_report"] == "false", "a partial run must NOT advance latest.json"
    assert gh["wrote_partial"] == "true"

    assert not report_f.exists()
    part = json.loads(partial_f.read_text())
    assert part["partial"] is True and part["n_scored"] == 9 and part["n_models"] == 22
    assert json.loads(outcome_f.read_text())["signature_line"] == OBSERVED_SSH_STDERR


def test_end_to_end_unparseable_stream_names_parsing(tmp_path):
    """The other direction, through the same CLI the workflow calls."""
    out_f, err_f = tmp_path / "out.txt", tmp_path / "err.txt"
    out_f.write_text("this is not a report {", encoding="utf-8")
    err_f.write_text("", encoding="utf-8")
    gh_out = tmp_path / "gh.txt"
    gh_out.touch()
    cli = subprocess.run(
        [sys.executable, str(REPO / "scripts/ml/pregate_stream.py"),
         "--exit-code", "0", "--stdout", str(out_f), "--stderr", str(err_f),
         "--partial-out", str(tmp_path / "p.json"), "--github-output", str(gh_out)],
        capture_output=True, text=True, check=False,
    )
    assert "::error::pre-gate driver_output_unparseable" in cli.stdout
    assert "transport" not in cli.stdout
    gh = dict(ln.split("=", 1) for ln in gh_out.read_text().strip().splitlines())
    assert gh["state"] == "driver_output_unparseable"
    assert gh["wrote_partial"] == "false", "nothing to salvage from an unparseable stream"


# --------------------------------------------------------------------------- #
# The driver must actually emit the format the classifier reads.
# --------------------------------------------------------------------------- #

def test_driver_emits_the_wire_format_the_classifier_consumes():
    """Producer/consumer agreement, asserted rather than assumed."""
    src = (REPO / "scripts/ml/replay_pregate_fleet.py").read_text(encoding="utf-8")
    assert "pregate_stream" in src
    for tag in ("pregate_stream.BEGIN", "pregate_stream.RESULT",
                "pregate_stream.ERROR", "pregate_stream.REPORT"):
        assert tag in src, f"driver never emits {tag}"


def test_workflow_no_longer_discards_the_ssh_exit_code():
    """An exit code must be CAPTURED and carried to the classifier.

    ⚠️ THE ASSERTION MOVED FROM A LITERAL TO THE INVARIANT, and it now covers
    MORE, not less. It read `assert "|| rc=$?" in wf`, pinning the exact
    variable name the 2026-09-01 fix happened to use. On 2026-09-02 the run was
    DETACHED (see the detach test below) and the code that matters became the
    DRIVER's own, read back from a sentinel file, with the poll's ssh status
    captured separately as `prc` — strictly better evidence than the value this
    string named, because ssh's 255 is legitimately both a transport failure and
    a remote command's exit.

    So the literal broke while the property it guarded got stronger. Pinning the
    spelling of a variable is what made an improvement look like a regression;
    what run #4365 actually cost us was the exit code being DISCARDED, and that
    is what is asserted here.
    """
    wf = (REPO / ".github/workflows/replay-pregate-nightly.yml").read_text(encoding="utf-8")
    executable = [ln for ln in wf.splitlines() if not ln.lstrip().startswith("#")]

    # Some exit status is captured into a variable rather than thrown away.
    assert [ln for ln in executable if re.search(r"\|\|\s*\w+=\$\?", ln)], \
        "no `|| <var>=$?` capture — an exit status is being discarded again"
    # ...and it reaches the classifier rather than dying in the step.
    assert "/tmp/pregate_rc.txt" in wf, "the captured code must be persisted for the classifier"
    assert "--exit-code" in wf, "the classifier must be GIVEN the code, not left to guess"
    assert "2>/tmp/pregate_err.txt || true" not in wf, "the exit code is the evidence"
    assert "pregate_stream.py" in wf
    # The old message may survive in a COMMENT -- it is the record of what was
    # wrong -- but must never again be emitted by an executable line.
    executable = [ln for ln in wf.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in executable if "no JSON object in driver output" in ln]
    assert not [ln for ln in executable if "raw.rfind" in ln], "blind brace-scan is gone"


def test_driver_stream_round_trips_through_the_classifier(monkeypatch, capsys):
    """Run the REAL driver loop and feed its real stdout to the classifier.

    The grep above proves the tags are mentioned; this proves the two halves
    actually agree on the wire, which is what a truncated run depends on.
    """
    from scripts.ml import replay_pregate_fleet as fleet

    monkeypatch.setattr(fleet, "ModelRegistry", lambda *a, **k: object())
    monkeypatch.setattr(fleet._factory, "_resolve_default_registry_root",
                        lambda *a, **k: "/nonexistent")

    def fake_score(model_id, reg, **kw):
        if model_id == "m3":
            raise ValueError("no market_raw candles for X/5m")
        return _result_row(model_id, 0.80)

    monkeypatch.setattr(fleet, "score_model", fake_score)

    report = fleet.run(["m1", "m2", "m3"], window_n=20, folds=2,
                       positive_class="volatile")
    # Emit the final report exactly as `main()` does.
    print(ps.frame(ps.REPORT, report))
    stdout = capsys.readouterr().out

    complete = ps.classify(0, stdout, "")
    assert complete["state"] == ps.OK
    assert complete["declared_count"] == 3
    assert complete["salvaged_count"] == 2
    assert complete["salvaged_error_count"] == 1

    # Now truncate that same real stream before the report line, as a broken
    # pipe would, and confirm the partial rows survive with their denominator.
    truncated = "\n".join(ln for ln in stdout.splitlines()
                          if not ln.startswith(ps.REPORT))
    partial = ps.classify(255, truncated, OBSERVED_SSH_STDERR)
    assert partial["state"] == ps.TRANSPORT_FAILED
    assert ps.partial_report(partial)["n_models"] == 3
    assert ps.partial_report(partial)["n_scored"] == 2


def test_workflow_detaches_the_run_so_a_dropped_transport_is_not_a_lost_run():
    """The fleet run must not depend on ONE ssh channel surviving ~20 minutes.

    Anchored on run #4390 (`33637802498`, `event=workflow_dispatch`,
    2026-09-02T13:46:34Z), the first run after the detach. Its launch step
    returned in 4 SECONDS where the previous shape held the channel ~9 minutes,
    and the poll then ran its full budget with ZERO transport failures — where 7
    of the 8 preceding scheduled runs had died at ~8m55s, all at the same head.

    ⚠️ IT STILL FAILED, and that is the point of asserting the detach separately
    from the outcome: the trainer was measured at 5508/5909 MB used with 4 GB of
    swap consumed and BOTH OOM greps empty, so the driver was alive and crawling.
    The transport repair and the memory problem are different facts, and a test
    that conflated them would go red for the wrong reason.
    """
    wf = (REPO / ".github/workflows/replay-pregate-nightly.yml").read_text(encoding="utf-8")
    executable = [ln for ln in wf.splitlines() if not ln.lstrip().startswith("#")]
    body = "\n".join(executable)

    # Detached, with a completion sentinel the poller reads.
    assert "setsid" in body, "the driver must be launched detached"
    assert "/rc" in body, "a completion sentinel is what separates 'finished' from 'still running'"

    # A failed POLL must not be read as a verdict about the run.
    assert "poll_fail" in body, "a failed poll must be counted and retried, not concluded from"

    # `git add -f` is load-bearing: runtime_logs/ is gitignored, so a plain
    # `git add` refuses the path and, under `set -e`, destroys the salvaged
    # rows. Measured on #4390, and pre-existing — runtime_logs/replay_pregate/
    # has never had a file on main.
    assert "git add -f" in body, (
        "runtime_logs/ is gitignored; a plain `git add` kills the commit step "
        "and discards the salvaged heads"
    )
    assert not [ln for ln in executable
                if "git add" in ln and "-f" not in ln and "replay_pregate" not in ln
                and "$DIR" in ln], "an unforced `git add \"$DIR\"` is back"
