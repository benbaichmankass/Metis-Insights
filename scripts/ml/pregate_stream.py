#!/usr/bin/env python3
"""Wire format + failure-STAGE classifier for the replay pre-gate nightly.

WHY THIS MODULE EXISTS
----------------------
`replay-pregate-nightly` run #4365 (`33491178494`, `event=schedule`,
2026-09-01T09:14:38Z) failed and reported::

    client_loop: send disconnect: Broken pipe
    ##[error]no JSON object in driver output

The SSH session dropped at model 10 of 22. The workflow reported a **PARSING**
failure for what was a **CONNECTION** failure, sending a reader to inspect the
driver's output format when the pipe had broken. That is UNPROVENANCED
DIAGNOSTIC OUTPUT sub-class A (`CLAUDE.md` § "Diagnostic provenance") — *a
failure message that names a cause no code path tested*.

It could not have said anything else. The step ran::

    ssh ... > out 2> err || true          # exit code DISCARDED
    start = raw.find('{'); end = raw.rfind('}')
    if start < 0 or end < 0: "no JSON object in driver output"

With the exit code thrown away, the only surviving evidence was "stdout has no
braces" — which is true of a broken pipe, a crashed driver, an empty run and
genuinely malformed JSON alike. The fix is therefore NOT to reword the label:
it is to stop discarding the evidence that separates the stages, and to branch
on the earliest stage that actually failed.

THE STAGES (ordered; a run is classified by the EARLIEST one that failed)
------------------------------------------------------------------------
``ok``                        a complete report was framed, parsed, and the
                              transport exited cleanly.
``transport_failed``          the SSH transport itself broke — exit 255 AND a
                              line-anchored OpenSSH client signature on stderr.
``remote_command_failed``     the remote ran and exited non-zero. The driver
                              was reached; it died.
``driver_output_absent``      transport clean, remote exited 0, and stdout
                              carried nothing at all.
``driver_output_unparseable`` transport clean, remote exited 0, stdout carried
                              bytes, and no report could be parsed from them.
                              THIS is the only state the old message described.
``undetermined``              we could NOT tell which stage failed. Kept
                              separate on purpose: "we did not look" and "we
                              looked and found nothing" are different states
                              (`docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed
                              states"), and collapsing this into any neighbour
                              re-creates the defect one level along.

``undetermined`` is load-bearing rather than defensive padding. Exit 255 is
genuinely ambiguous: OpenSSH uses it for its OWN errors, and a remote command
that exits 255 produces the identical code. So 255 WITHOUT a client signature
is not evidence of a transport failure, and asserting one would be the same
unprovenanced move in the opposite direction.

EVERY VERDICT CARRIES ITS EVIDENCE
----------------------------------
`classify()` returns the exit code it read and the exact stderr line it matched
(``signature_line``), so a reader checks the classification instead of trusting
the label. A verdict whose basis is not printed is the defect this module was
written to end.

NOT REGISTERED WITH ``collapsed-state-guard``, deliberately
-----------------------------------------------------------
That guard requires each state to be branched on by a real PYTHON consumer.
The consumer here is `.github/workflows/replay-pregate-nightly.yml` — YAML,
outside the guard's file scan. Registering would mean manufacturing a Python
consumer that exists only to satisfy the guard, which is the decorative-branch
defect the guard itself warns about. Stated plainly rather than registered.

Stdlib-only: this runs on the GitHub runner (no repo dependencies installed)
AND is imported by the driver on the trainer VM.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Optional, Tuple

# --- the wire format -------------------------------------------------------- #
# One line per event on the driver's STDOUT, flushed as it is produced, so a
# stream truncated mid-run still leaves STRUCTURED partial results behind. The
# failing run salvaged nothing because the driver printed its report only at
# the very end: stdout was byte-empty when the pipe broke, while nine graded
# heads existed solely as human-readable progress text on stderr. Reconstructing
# results by parsing that display text would be the same sub-class A defect in
# reverse — a number recovered from a label rather than from data.
BEGIN = "##PREGATE-BEGIN##"
RESULT = "##PREGATE-RESULT##"
ERROR = "##PREGATE-ERROR##"
REPORT = "##PREGATE-REPORT##"

OK = "ok"
TRANSPORT_FAILED = "transport_failed"
REMOTE_COMMAND_FAILED = "remote_command_failed"
DRIVER_OUTPUT_ABSENT = "driver_output_absent"
DRIVER_OUTPUT_UNPARSEABLE = "driver_output_unparseable"
UNDETERMINED = "undetermined"

STATES = (OK, TRANSPORT_FAILED, REMOTE_COMMAND_FAILED, DRIVER_OUTPUT_ABSENT,
          DRIVER_OUTPUT_UNPARSEABLE, UNDETERMINED)

# States for which salvaged per-model rows are worth preserving: the transport
# or the remote died PART WAY, so whatever arrived was produced by a driver that
# was working. An unparseable or absent stream offers nothing to salvage.
SALVAGEABLE_STATES = (TRANSPORT_FAILED, REMOTE_COMMAND_FAILED, UNDETERMINED)

# OpenSSH emits these at the START of a line on the CLIENT side. Anchoring to
# line-start matters: a remote Python traceback can legitimately contain the
# words "Connection refused", and matching that as a transport signature would
# file a driver failure as a network one — the same misattribution, relabelled.
_SSH_SIGNATURES: Tuple[str, ...] = (
    "client_loop:",
    "packet_write_wait:",
    "packet_write_poll:",
    "ssh_exchange_identification:",
    "kex_exchange_identification:",
    "ssh: connect to host",
    "Connection to ",
    "Connection closed by ",
    "Connection reset by ",
    "Host key verification failed",
    "Permission denied (publickey",
    "Timeout, server ",
    "no matching host key type found",
    "Received disconnect from ",
    "Disconnected from ",
    "Broken pipe",
)


def frame(tag: str, payload: Any) -> str:
    """Render one wire line. Separators are compact so the line never wraps."""
    return f"{tag} {json.dumps(payload, separators=(',', ':'))}"


def _ssh_signature_line(stderr_text: str) -> Optional[str]:
    """The first line-anchored OpenSSH client signature, or None.

    Returned rather than reduced to a bool so the caller can PRINT the evidence
    its verdict rests on.
    """
    for raw_line in stderr_text.splitlines():
        line = raw_line.strip()
        for sig in _SSH_SIGNATURES:
            if line.startswith(sig):
                return line
    return None


def parse_stream(stdout_text: str) -> Dict[str, Any]:
    """Recover whatever the stream carried: a full report, or partial rows.

    Never raises on malformed input — a line that does not decode is COUNTED
    (``malformed_lines``) rather than dropped, so "the stream held nothing" and
    "the stream held rows we could not read" stay distinguishable.
    """
    declared: Optional[List[str]] = None
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    report: Optional[Dict[str, Any]] = None
    malformed = 0

    for raw_line in stdout_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for tag in (BEGIN, RESULT, ERROR, REPORT):
            if not line.startswith(tag):
                continue
            body = line[len(tag):].strip()
            try:
                obj = json.loads(body)
            except (ValueError, TypeError):
                malformed += 1
                break
            if tag == BEGIN and isinstance(obj, dict):
                ids = obj.get("model_ids")
                declared = [str(m) for m in ids] if isinstance(ids, list) else None
            elif tag == RESULT and isinstance(obj, dict):
                results.append(obj)
            elif tag == ERROR and isinstance(obj, dict):
                errors.append(obj)
            elif tag == REPORT and isinstance(obj, dict):
                report = obj
            break

    if report is None and not results and not errors and stdout_text.strip():
        # Pre-sentinel fallback: an older driver printed the bare report. Accept
        # it only if it PARSES and carries `results` -- the old blind brace-scan
        # accepted any span between the first `{` and last `}`, which is how a
        # truncated stream produced a confident parse error instead of a
        # transport one.
        start, end = stdout_text.find("{"), stdout_text.rfind("}")
        if 0 <= start < end:
            try:
                candidate = json.loads(stdout_text[start:end + 1])
            except (ValueError, TypeError):
                candidate = None
            if isinstance(candidate, dict) and isinstance(candidate.get("results"), list):
                report = candidate

    return {
        "declared_model_ids": declared,
        "results": results,
        "errors": errors,
        "report": report,
        "malformed_lines": malformed,
    }


def classify(exit_code: Optional[int], stdout_text: str,
             stderr_text: str) -> Dict[str, Any]:
    """Grade a run by the EARLIEST stage that failed, with its evidence."""
    parsed = parse_stream(stdout_text)
    report = parsed["report"]
    signature = _ssh_signature_line(stderr_text)
    stdout_bytes = len(stdout_text)
    stdout_empty = not stdout_text.strip()

    if exit_code is None:
        state = UNDETERMINED
        why = ("the transport exit code was not captured, so the failing stage "
               "cannot be attributed")
    elif exit_code == 0:
        if report is not None:
            state, why = OK, "a complete report was framed and parsed"
        elif stdout_empty:
            state = DRIVER_OUTPUT_ABSENT
            why = "the remote exited 0 and wrote nothing to stdout"
        else:
            state = DRIVER_OUTPUT_UNPARSEABLE
            why = (f"the remote exited 0 and wrote {stdout_bytes} byte(s) to "
                   f"stdout, but no complete report could be parsed from them")
    elif exit_code == 255:
        if signature is not None:
            state = TRANSPORT_FAILED
            why = f"ssh exited 255 and reported: {signature}"
        else:
            state = UNDETERMINED
            why = ("ssh exited 255 with no OpenSSH client signature on stderr. "
                   "255 is both ssh's own error code and a legitimate remote "
                   "exit code, so the failing stage is not determined")
    else:
        state = REMOTE_COMMAND_FAILED
        why = (f"the transport carried the remote's exit code {exit_code}: the "
               f"driver was reached and exited non-zero")

    if state == OK and report is None:
        # Unreachable via the branches above; kept as an invariant rather than
        # a comment, so a future edit cannot make `ok` mean "no report".
        state, why = UNDETERMINED, "graded ok without a parsed report"

    if report is not None and state != OK:
        # A complete report arrived AND the run reports failure. Do not upgrade
        # to `ok` on the strength of the payload: something after the driver
        # went wrong and we cannot show the artifact is trustworthy.
        state = UNDETERMINED
        why = (f"a complete report was parsed, but the run did not exit "
               f"cleanly ({why}); the report is not shown to be trustworthy")

    declared = parsed["declared_model_ids"]
    salvaged = len(parsed["results"])
    return {
        "state": state,
        "why": why,
        "exit_code": exit_code,
        "signature_line": signature,
        "stdout_bytes": stdout_bytes,
        "malformed_lines": parsed["malformed_lines"],
        "declared_model_ids": declared,
        "declared_count": len(declared) if declared is not None else None,
        "salvaged_count": salvaged,
        "salvaged_error_count": len(parsed["errors"]),
        "report": report,
        "partial_results": parsed["results"],
        "partial_errors": parsed["errors"],
    }


def partial_report(outcome: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build a PARTIAL report from salvaged rows, or None when nothing survived.

    Shaped like a real report so `replay_pregate_summary.py` renders it
    unchanged -- but it carries `partial: true` and its own denominator, and the
    workflow never writes it to `latest.json`. A partial baseline that could
    masquerade as a complete one is worse than no baseline.
    """
    rows = outcome.get("partial_results") or []
    errs = outcome.get("partial_errors") or []
    if not rows and not errs:
        return None
    declared = outcome.get("declared_count")
    return {
        "stage": 1,
        "partial": True,
        "partial_reason": outcome.get("state"),
        "partial_why": outcome.get("why"),
        "n_models": declared,
        "n_scored": len(rows),
        "results": rows,
        "errors": errs,
        "note": (
            "PARTIAL fleet report: the run did not complete. n_scored of "
            "n_models heads were graded before it stopped; the rest were NOT "
            "measured and their absence is not a verdict on them."
        ),
    }


def describe(outcome: Dict[str, Any]) -> str:
    """One line naming the STAGE, its evidence, and the salvage denominator."""
    state = outcome["state"]
    declared, salvaged = outcome.get("declared_count"), outcome.get("salvaged_count", 0)
    scope = (f"{salvaged}/{declared} head(s) graded before it stopped"
             if declared is not None else f"{salvaged} head(s) graded")
    return f"{state}: {outcome['why']} [{scope}]"


def _read(path: Optional[str]) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Classify a pre-gate run by failure stage.")
    ap.add_argument("--exit-code", default="",
                    help="the ssh exit code; blank = not captured (undetermined)")
    ap.add_argument("--stdout", required=True)
    ap.add_argument("--stderr", default="")
    ap.add_argument("--report-out", default="",
                    help="write the COMPLETE report here (only when state is ok)")
    ap.add_argument("--partial-out", default="",
                    help="write a PARTIAL report here when rows were salvaged")
    ap.add_argument("--outcome-out", default="")
    ap.add_argument("--github-output", default="")
    a = ap.parse_args(argv)

    raw_rc = a.exit_code.strip()
    try:
        rc: Optional[int] = int(raw_rc) if raw_rc else None
    except ValueError:
        rc = None

    outcome = classify(rc, _read(a.stdout), _read(a.stderr))
    state = outcome["state"]
    line = describe(outcome)

    wrote_report = False
    if state == OK and a.report_out and outcome["report"] is not None:
        with open(a.report_out, "w", encoding="utf-8") as fh:
            json.dump(outcome["report"], fh, indent=1)
        wrote_report = True

    wrote_partial = False
    if state in SALVAGEABLE_STATES and a.partial_out:
        part = partial_report(outcome)
        if part is not None:
            with open(a.partial_out, "w", encoding="utf-8") as fh:
                json.dump(part, fh, indent=1)
            wrote_partial = True

    if a.outcome_out:
        slim = {k: v for k, v in outcome.items()
                if k not in ("report", "partial_results", "partial_errors")}
        slim["wrote_report"] = wrote_report
        slim["wrote_partial"] = wrote_partial
        with open(a.outcome_out, "w", encoding="utf-8") as fh:
            json.dump(slim, fh, indent=1)

    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"state={state}\n")
            fh.write(f"salvaged_count={outcome['salvaged_count']}\n")
            fh.write(f"declared_count={outcome.get('declared_count') if outcome.get('declared_count') is not None else ''}\n")
            fh.write(f"wrote_report={'true' if wrote_report else 'false'}\n")
            fh.write(f"wrote_partial={'true' if wrote_partial else 'false'}\n")
            fh.write(f"summary_line={line}\n")

    if state == OK:
        print(f"::notice::pre-gate {line}")
    else:
        print(f"::error::pre-gate {line}")
    print(json.dumps({k: v for k, v in outcome.items()
                      if k not in ("report", "partial_results", "partial_errors")},
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
