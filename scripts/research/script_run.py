#!/usr/bin/env python3
# wiring: .github/workflows/research-script-run.yml (the token-free research runner)
"""The brain of ``research-script-run.yml`` — run ONE queue unit's declared
command(s) on a GitHub runner and derive the result record from what it wrote.

WHY THIS EXISTS (lane RQ-RUN, 2026-09-28)
-----------------------------------------
MEASURED on origin/main at 17:45Z: ``dispatch_queue.py`` (dry) over 27 units
gave would_dispatch=7, and five of those seven declared
``run.workflow: "none — session-local ..."``. ``_fire`` then runs
``gh workflow run "none — ..."``, which fails — that is why
research-queue-dispatch run 36434038723 ended red. Every session-local unit
therefore waited on a Claude session, i.e. on tokens, while Claude usage was
capped and 4-core runners sat idle. The operator's direction (2026-09-28):
*"keep the research queue moving through the slowdown, even if we're out of
tokens we should still be making the most of the other resources available."*

THE CONTRACT — the unit declares the command, the workflow takes ONLY the id
--------------------------------------------------------------------------
A unit opts in with::

    run:
      workflow: research-script-run.yml
      command: ["python3", "scripts/research/prop_ev_grid.py", "--out", "{out_dir}", ...]
      # or several, run in order:
      commands: [[...], [...]]
      timeout_minutes: 120        # optional; bounded below
      inputs:
        research_unit: RQ-YYYYMMDD-NNN

⚠️ SECURITY. The workflow's ONLY inputs are ``research_unit`` (and the
dispatcher's pass-through ``power_state`` safety label). The command is read
from the unit file ON THE CHECKED-OUT REF, never from a free-form dispatch
input — so the only way to make the runner execute something is to land a
YAML change on ``main`` through the same review the rest of the repo gets.
And what it will execute is bounded:

  * every command is ``python3 <script> [args...]`` with ``<script>`` under
    ``scripts/research/`` or matching ``scripts/backtest*.py`` — the two
    research-harness families. Nothing else, no shell, no ``-c``, no ``-m``;
  * argv is passed to ``subprocess.run`` as a LIST (no shell), so no token
    is ever interpreted by ``sh``;
  * no token may contain ``..``, a newline or a NUL — the paths the script
    receives stay inside the checkout;
  * the job holds no secret beyond ``GITHUB_TOKEN`` (the landing job is a
    separate job that never runs the command).

Three placeholders are substituted in every token: ``{out_dir}`` (the
directory the run's outputs must be written to), ``{unit}`` and ``{run_id}``.

WHAT LANDS, AND HOW A RUN BECOMES A RESULT
------------------------------------------
Outputs go under ``comms/research/<unit>/<run_id>/`` and are committed to
``main`` through ``.github/actions/commit-to-main`` (the one owner of "how a
workflow gets a file onto protected main"). Then an E5 result record lands
through ``.github/actions/research-result``, derived by ``--derive-record``:

  * if the command wrote ``<out_dir>/verdict.json`` — ``{verdict, read_state,
    population, n, measurement?, note?, records?: [...]}`` — that IS the
    record (``records`` fans out one E5 row per leg/cell);
  * if every command exited 0 but nothing wrote ``verdict.json`` — the run
    lands as ``read_state: producer_failed`` / ``verdict: not_applicable``
    with a note saying the outputs are on ``main`` and ungraded. A green
    script that graded nothing is NOT ``measured``: that would fabricate a
    verdict the unit's pre-registered rule never saw;
  * if any command exited non-zero — ``producer_failed``, with the exit
    codes and the log paths in the note.

The record's ``decision_rule`` id/registered_at come from the unit's own
``decision_rule`` block, so a result can never be matched to a rule other
than the one registered before the run.

Tier-1 research tooling: reads the queue, writes only under ``comms/research/``
and (via the actions) ``research/results/``. No config write, no live path.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# collapsed-state: not_applicable — THIS FILE BRANCHES ON NO POWER STATE AT
# ALL. It carries the dispatcher's `power_state` label through opaquely (the
# workflow input the runner never reads), and the `not_applicable` the guard
# sees is a member of the research-result VERDICTS set — the same token
# collision scripts/research/research_result.py documents on its annotation.
_REPO = Path(__file__).resolve().parents[2]
_DEFAULT_QUEUE = _REPO / "research" / "queue"

WORKFLOW = "research-script-run.yml"
OUT_ROOT = Path("comms") / "research"

#: The only script families the runner will execute. Anchored, no `..`, no
#: directories other than the two research-harness homes.
ALLOWED_SCRIPT_RE = re.compile(
    r"^(scripts/research/[A-Za-z0-9_\-]+\.py|scripts/backtest[A-Za-z0-9_\-]*\.py)$")
_INTERPRETERS = ("python3", "python")
_ID_RE = re.compile(r"^RQ-\d{8}-\d{3}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

MIN_TIMEOUT_MIN = 10
MAX_TIMEOUT_MIN = 330   # the job's own ceiling is 360; leave room to land
DEFAULT_TIMEOUT_MIN = 120
LOG_TAIL_BYTES = 200_000
#: Written by --derive-record beside run-manifest.json; read by the batch collector.
RECORD_INPUTS = "record-inputs.json"

VERDICTS = ("pass", "fail", "no_action_warranted", "indeterminate", "not_applicable")
READ_STATES = ("measured", "no_data", "producer_failed", "not_attempted")


@dataclass
class Plan:
    unit: str
    path: Path
    commands: List[List[str]]
    timeout_minutes: int
    out_dir: Path
    rule_id: str
    rule_registered_at: str
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ── static check ────────────────────────────────────────────────────────────
def _check_argv(argv: Any, *, repo: Path, idx: int) -> List[str]:
    errs: List[str] = []
    tag = f"run.command[{idx}]"
    if not isinstance(argv, list) or not argv:
        return [f"{tag}: must be a non-empty list of strings (argv), got {type(argv).__name__}"]
    for j, tok in enumerate(argv):
        if not isinstance(tok, str) or not tok:
            errs.append(f"{tag}[{j}]: every token must be a non-empty string, got {tok!r}")
        elif ".." in tok or "\n" in tok or "\0" in tok:
            errs.append(f"{tag}[{j}]: token {tok!r} contains '..', a newline or NUL — refused")
        else:
            left = sorted(set(_PLACEHOLDER_RE.findall(tok)) - _RUNNER_PLACEHOLDERS)
            if left:
                errs.append(f"{tag}[{j}]: token {tok!r} still carries template placeholder(s) {left} "
                            f"that the runner does not substitute (it fills only "
                            f"{sorted(_RUNNER_PLACEHOLDERS)}) — a generator left them unresolved; "
                            "refused before anything runs")
    if errs:
        return errs
    if argv[0] not in _INTERPRETERS:
        errs.append(f"{tag}: argv[0] must be one of {_INTERPRETERS}, got {argv[0]!r} — "
                    "the runner executes python research scripts, never a shell or a binary")
        return errs
    if len(argv) < 2:
        return [f"{tag}: names an interpreter and no script"]
    script = argv[1]
    if not ALLOWED_SCRIPT_RE.match(script):
        errs.append(f"{tag}: script {script!r} is not under scripts/research/ or "
                    "scripts/backtest*.py — the only families the runner executes")
    elif not (repo / script).is_file():
        errs.append(f"{tag}: script {script!r} does not exist in the checkout")
    return errs


_RUNNER_PLACEHOLDERS = frozenset({"out_dir", "unit", "run_id"})
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def _substitute(argv: List[str], *, out_dir: Path, unit: str, run_id: str) -> List[str]:
    return [tok.replace("{out_dir}", out_dir.as_posix())
               .replace("{unit}", unit)
               .replace("{run_id}", run_id) for tok in argv]


def plan(unit: str, *, run_id: str, queue_dir: Path = _DEFAULT_QUEUE,
         repo: Path = _REPO) -> Plan:
    """Static resolution of a unit into an executable plan. Never raises on a
    bad unit — the errors ride on the Plan so the caller can print them all."""
    errs: List[str] = []
    if not _ID_RE.match(unit or ""):
        errs.append(f"research_unit {unit!r} is not RQ-YYYYMMDD-NNN")
    if not _RUN_ID_RE.match(run_id or ""):
        errs.append(f"run_id {run_id!r} is not path-safe")
    path = queue_dir / f"{unit}.yaml"
    out_dir = OUT_ROOT / (unit or "invalid") / (run_id or "invalid")
    if errs:
        return Plan(unit, path, [], DEFAULT_TIMEOUT_MIN, out_dir, "", "", errs)

    import yaml
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return Plan(unit, path, [], DEFAULT_TIMEOUT_MIN, out_dir, "", "",
                    [f"{path}: unreadable — {type(exc).__name__}: {exc}"])
    if not isinstance(raw, dict):
        return Plan(unit, path, [], DEFAULT_TIMEOUT_MIN, out_dir, "", "",
                    [f"{path}: top level is not a mapping"])
    if raw.get("id") != unit:
        errs.append(f"{path.name}: id {raw.get('id')!r} != {unit!r}")

    run = raw.get("run") or {}
    if not isinstance(run, dict):
        errs.append("`run` must be a mapping")
        run = {}
    if str(run.get("workflow") or "").strip() != WORKFLOW:
        errs.append(f"run.workflow must be {WORKFLOW!r} for this runner, got "
                    f"{run.get('workflow')!r}")
    if "command" in run and "commands" in run:
        errs.append("declare `run.command` (one argv) OR `run.commands` (a list of argv), not both")
    if "commands" in run:
        cmds = run.get("commands")
        if not isinstance(cmds, list) or not cmds:
            errs.append("`run.commands` must be a non-empty list of argv lists")
            cmds = []
    elif "command" in run:
        cmds = [run.get("command")]
    else:
        errs.append("`run.command` (argv list) or `run.commands` is required — the runner "
                    "reads the command from the unit file, never from a dispatch input")
        cmds = []
    commands: List[List[str]] = []
    for i, argv in enumerate(cmds):
        e = _check_argv(argv, repo=repo, idx=i)
        if e:
            errs.extend(e)
        else:
            commands.append(_substitute(list(argv), out_dir=out_dir, unit=unit, run_id=run_id))

    inputs = run.get("inputs") or {}
    if not isinstance(inputs, dict) or inputs.get("research_unit") != unit:
        errs.append("`run.inputs.research_unit` must equal the unit id — that is how the "
                    "dispatcher knows the workflow accepts `research_unit`/`power_state`")

    timeout = run.get("timeout_minutes", DEFAULT_TIMEOUT_MIN)
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        errs.append(f"run.timeout_minutes must be an integer, got {timeout!r}")
        timeout = DEFAULT_TIMEOUT_MIN
    elif not (MIN_TIMEOUT_MIN <= timeout <= MAX_TIMEOUT_MIN):
        errs.append(f"run.timeout_minutes {timeout} outside [{MIN_TIMEOUT_MIN}, {MAX_TIMEOUT_MIN}]")

    rule = raw.get("decision_rule") or {}
    rule_id = str(rule.get("id") or "").strip() if isinstance(rule, dict) else ""
    rule_at = str(rule.get("registered_at") or "").strip() if isinstance(rule, dict) else ""
    if not rule_id or not rule_at:
        errs.append("`decision_rule.id` and `decision_rule.registered_at` are required — a "
                    "result must be matched to the rule registered before the run")

    return Plan(unit, path, commands, timeout, out_dir, rule_id, rule_at, errs)


def units_targeting_runner(queue_dir: Path = _DEFAULT_QUEUE) -> List[str]:
    """Ids of every unit whose run.workflow names this runner (any status)."""
    import yaml
    out: List[str] = []
    for p in sorted(queue_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        run = raw.get("run") if isinstance(raw, dict) else None
        if isinstance(run, dict) and str(run.get("workflow") or "").strip() == WORKFLOW:
            out.append(p.stem)
    return out


# ── execution ───────────────────────────────────────────────────────────────
def _tail(path: Path, limit: int = LOG_TAIL_BYTES) -> None:
    """Keep a log committable: truncate to its last `limit` bytes."""
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= limit:
        return
    data = path.read_bytes()[-limit:]
    path.write_bytes(b"[... truncated to the last %d bytes ...]\n" % limit + data)


def execute(p: Plan, *, repo: Path = _REPO) -> Dict[str, Any]:
    """Run the plan's commands in order; write `<out_dir>/run-manifest.json`.
    Returns the manifest. Never raises for a failing command — the failure is
    recorded (exit code + log) and the caller decides."""
    out_dir = repo / p.out_dir
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"RESEARCH_UNIT": p.unit, "RESEARCH_OUT_DIR": p.out_dir.as_posix(),
                "PYTHONPATH": str(repo) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")})
    deadline = time.monotonic() + p.timeout_minutes * 60
    rows: List[Dict[str, Any]] = []
    for i, argv in enumerate(p.commands):
        log = logs / f"cmd-{i}.log"
        remaining = max(1.0, deadline - time.monotonic())
        started = time.time()
        row: Dict[str, Any] = {"index": i, "argv": argv, "log": (p.out_dir / "logs" / log.name).as_posix()}
        print(f"::group::[{p.unit}] cmd {i}: {' '.join(argv)}", flush=True)
        try:
            with log.open("wb") as fh:
                proc = subprocess.run(argv, cwd=str(repo), env=env, stdout=fh,
                                      stderr=subprocess.STDOUT, timeout=remaining)
            row["exit_code"] = proc.returncode
        except subprocess.TimeoutExpired:
            row["exit_code"] = None
            row["timed_out"] = True
        except OSError as exc:
            row["exit_code"] = None
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["seconds"] = round(time.time() - started, 1)
        _tail(log)
        print(f"exit={row.get('exit_code')} seconds={row['seconds']}", flush=True)
        print("::endgroup::", flush=True)
        rows.append(row)
        if row.get("exit_code") != 0:
            # Stop at the first failure: a later command reading a missing
            # input would report a different, misleading error.
            break
    manifest = {
        "unit": p.unit, "run_id": p.out_dir.name, "commit_sha": os.environ.get("GITHUB_SHA"),
        "run_url": (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                    f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/"
                    f"{os.environ.get('GITHUB_RUN_ID', '')}") if os.environ.get("GITHUB_RUN_ID") else None,
        "timeout_minutes": p.timeout_minutes,
        "commands": rows,
        "all_ok": bool(rows) and all(r.get("exit_code") == 0 for r in rows) and len(rows) == len(p.commands),
    }
    (out_dir / "run-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


# ── record derivation ───────────────────────────────────────────────────────
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def derive_record(p: Plan, *, repo: Path = _REPO) -> Dict[str, Any]:
    """Map the run's outputs to the research-result action's inputs.

    Returns a flat dict of the action's `with:` keys (snake_case), plus
    `records_file`/`min_rows` when the verdict fans out per leg. Every
    branch states which of the three cases it took in `note`.
    """
    out_dir = repo / p.out_dir
    manifest = _read_json(out_dir / "run-manifest.json") or {}
    verdict = _read_json(out_dir / "verdict.json")
    base = {
        "decision_rule_id": p.rule_id,
        "decision_rule_registered_at": p.rule_registered_at,
        "artifact_store": p.out_dir.as_posix(),
        "artifact_locator": f"{p.out_dir.as_posix()}/ (run-manifest.json + verdict.json + logs/)",
        "tool": " ; ".join(" ".join(c["argv"][:2]) for c in manifest.get("commands") or []) or "(none ran)",
        "records_file": "", "min_rows": "1",
        "measurement_file": "",
    }
    failed = [c for c in (manifest.get("commands") or []) if c.get("exit_code") != 0]
    if not manifest or failed or not manifest.get("all_ok"):
        detail = "; ".join(f"cmd {c['index']} exit={c.get('exit_code')}"
                           + (" (timed out)" if c.get("timed_out") else "")
                           + f" log={c.get('log')}" for c in failed) or "no run-manifest.json"
        base.update(verdict="not_applicable", read_state="producer_failed",
                    population=f"{p.unit}: the declared command did not complete — NEVER MEASURED",
                    n="null",
                    note=f"producer_failed: {detail}. Outputs (if any) are under {p.out_dir.as_posix()}/ on main.")
        return base
    if verdict is None:
        base.update(verdict="not_applicable", read_state="producer_failed",
                    population=f"{p.unit}: every command exited 0 but none wrote "
                               f"{p.out_dir.as_posix()}/verdict.json — outputs landed, NOTHING GRADED",
                    n="null",
                    note="producer_failed: no verdict.json. The script ran but did not grade its "
                         f"own output against {p.rule_id}; the raw outputs are on main under "
                         f"{p.out_dir.as_posix()}/ for a session to grade by hand. A green script "
                         "that graded nothing is not `measured`.")
        return base

    problems: List[str] = []
    v, rs = str(verdict.get("verdict") or ""), str(verdict.get("read_state") or "")
    if v not in VERDICTS:
        problems.append(f"verdict {v!r} not in {VERDICTS}")
    if rs not in READ_STATES:
        problems.append(f"read_state {rs!r} not in {READ_STATES}")
    if not str(verdict.get("population") or "").strip():
        problems.append("population is required")
    if "n" not in verdict:
        problems.append("n must be present (null when not measured)")
    records = verdict.get("records")
    if records is not None and (not isinstance(records, list) or not records
                                or not all(isinstance(r, dict) for r in records)):
        problems.append("records, when present, must be a non-empty list of objects")
    if problems:
        base.update(verdict="not_applicable", read_state="producer_failed",
                    population=f"{p.unit}: verdict.json is malformed — NOT GRADED", n="null",
                    note="producer_failed: verdict.json malformed: " + "; ".join(problems))
        return base

    n = verdict.get("n")
    base.update(verdict=v, read_state=rs,
                population=str(verdict["population"]),
                n="null" if n is None else str(n),
                note=str(verdict.get("note") or ""))
    if isinstance(verdict.get("measurement"), dict) and verdict["measurement"]:
        mpath = out_dir / "measurement.json"
        mpath.write_text(json.dumps(verdict["measurement"], indent=2) + "\n", encoding="utf-8")
        base["measurement_file"] = (p.out_dir / "measurement.json").as_posix()
    if records:
        rpath = out_dir / "records.jsonl"
        with rpath.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        base["records_file"] = (p.out_dir / "records.jsonl").as_posix()
        base["min_rows"] = str(len(records))
    return base


def _emit_outputs(values: Dict[str, Any]) -> None:
    gh_out = os.environ.get("GITHUB_OUTPUT")
    lines = []
    for k, val in values.items():
        s = str(val)
        if "\n" in s:
            lines.append(f"{k}<<__EOF__\n{s}\n__EOF__")
        else:
            lines.append(f"{k}={s}")
    text = "\n".join(lines) + "\n"
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(text)
    sys.stdout.write(text)


# ── self-test ───────────────────────────────────────────────────────────────
def _self_test() -> int:
    import tempfile
    import yaml

    def write_unit(qdir: Path, **over: Any) -> Path:
        base: Dict[str, Any] = {
            "id": "RQ-20260101-001", "title": "t", "question": "q", "status": "queued",
            "cadence": "once",
            "run": {"workflow": WORKFLOW,
                    "command": ["python3", "scripts/research/_st_ok.py", "--out", "{out_dir}"],
                    "inputs": {"research_unit": "RQ-20260101-001"}},
            "decision_rule": {"id": "RULE-ST", "registered_at": "2026-01-01"},
            "lands": {"store": "research/results/"},
        }
        for k, val in over.items():
            if k == "run":
                merged = {**base["run"], **val}
                if "commands" in val:
                    merged.pop("command", None)   # the override chooses the plural form
                base["run"] = merged
            else:
                base[k] = val
        p = qdir / "RQ-20260101-001.yaml"
        p.write_text(yaml.safe_dump(base))
        return p

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / "scripts" / "research").mkdir(parents=True)
        (repo / "scripts" / "research" / "_st_ok.py").write_text(
            "import json,sys,os\n"
            "out=sys.argv[sys.argv.index('--out')+1]\nos.makedirs(out,exist_ok=True)\n"
            "json.dump({'verdict':'pass','read_state':'measured','population':'p','n':3,"
            "'measurement':{'x':1},'records':[{'verdict':'pass','read_state':'measured',"
            "'population':'leg a','n':3}]}, open(os.path.join(out,'verdict.json'),'w'))\n")
        (repo / "scripts" / "research" / "_st_silent.py").write_text("print('ran')\n")
        (repo / "scripts" / "research" / "_st_fail.py").write_text("import sys; sys.exit(3)\n")
        (repo / "scripts" / "backtest_st.py").write_text("print('bt')\n")
        (repo / "scripts" / "ops").mkdir()
        (repo / "scripts" / "ops" / "evil.py").write_text("print('no')\n")
        qdir = repo / "q"
        qdir.mkdir()

        # 1. a good unit plans cleanly, substitutes the placeholders, runs, and
        #    derives a measured, fanned-out record.
        write_unit(qdir)
        p = plan("RQ-20260101-001", run_id="r1", queue_dir=qdir, repo=repo)
        assert p.ok, p.errors
        assert p.commands[0][-1] == "comms/research/RQ-20260101-001/r1", p.commands
        m = execute(p, repo=repo)
        assert m["all_ok"], m
        rec = derive_record(p, repo=repo)
        assert rec["verdict"] == "pass" and rec["read_state"] == "measured" and rec["n"] == "3", rec
        assert rec["records_file"].endswith("records.jsonl") and rec["min_rows"] == "1", rec
        assert (repo / rec["measurement_file"]).is_file()
        assert rec["decision_rule_id"] == "RULE-ST"

        # 2. a script that runs green but writes no verdict.json is NOT measured.
        write_unit(qdir, run={"command": ["python3", "scripts/research/_st_silent.py"]})
        p = plan("RQ-20260101-001", run_id="r2", queue_dir=qdir, repo=repo)
        assert p.ok, p.errors
        assert execute(p, repo=repo)["all_ok"]
        rec = derive_record(p, repo=repo)
        assert rec["read_state"] == "producer_failed" and rec["verdict"] == "not_applicable", rec
        assert rec["n"] == "null" and "no verdict.json" in rec["note"], rec

        # 3. a failing command -> producer_failed with the exit code named,
        #    and the later command never runs.
        write_unit(qdir, run={"commands": [["python3", "scripts/research/_st_fail.py"],
                                           ["python3", "scripts/research/_st_ok.py", "--out", "{out_dir}"]]})
        p = plan("RQ-20260101-001", run_id="r3", queue_dir=qdir, repo=repo)
        assert p.ok, p.errors
        m = execute(p, repo=repo)
        assert not m["all_ok"] and len(m["commands"]) == 1 and m["commands"][0]["exit_code"] == 3, m
        rec = derive_record(p, repo=repo)
        assert rec["read_state"] == "producer_failed" and "exit=3" in rec["note"], rec

        # 4. the refusals — each one must be caught STATICALLY, before anything runs.
        bad = {
            "shell": ["bash", "-c", "echo hi"],
            "python -c": ["python3", "-c", "print(1)"],
            "outside families": ["python3", "scripts/ops/evil.py"],
            "traversal": ["python3", "scripts/research/../ops/evil.py"],
            "missing script": ["python3", "scripts/research/nope.py"],
            "dotdot in arg": ["python3", "scripts/research/_st_ok.py", "--out", "../../etc"],
            "unresolved placeholder": ["python3", "scripts/research/_st_ok.py", "--out", "{out_dir}",
                                       "--arm", "A={sizing_args}"],
            "non-string token": ["python3", "scripts/research/_st_ok.py", 3],
            "empty": [],
        }
        for label, argv in bad.items():
            write_unit(qdir, run={"command": argv})
            p = plan("RQ-20260101-001", run_id="rx", queue_dir=qdir, repo=repo)
            assert not p.ok, f"{label}: must be refused, got {p.commands}"
        # scripts/backtest*.py IS allowed
        write_unit(qdir, run={"command": ["python3", "scripts/backtest_st.py"]})
        assert plan("RQ-20260101-001", run_id="rx", queue_dir=qdir, repo=repo).ok
        # wrong workflow, both command forms, bad timeout, missing rule
        write_unit(qdir, run={"workflow": "other.yml"})
        assert not plan("RQ-20260101-001", run_id="rx", queue_dir=qdir, repo=repo).ok
        both = yaml.safe_load((qdir / "RQ-20260101-001.yaml").read_text())
        both["run"]["commands"] = [["python3", "scripts/backtest_st.py"]]   # alongside `command`
        (qdir / "RQ-20260101-001.yaml").write_text(yaml.safe_dump(both))
        assert not plan("RQ-20260101-001", run_id="rx", queue_dir=qdir, repo=repo).ok  # command+commands
        write_unit(qdir, run={"timeout_minutes": 10_000})
        assert not plan("RQ-20260101-001", run_id="rx", queue_dir=qdir, repo=repo).ok
        write_unit(qdir, decision_rule={})
        assert not plan("RQ-20260101-001", run_id="rx", queue_dir=qdir, repo=repo).ok
        write_unit(qdir, run={"inputs": {}})
        assert not plan("RQ-20260101-001", run_id="rx", queue_dir=qdir, repo=repo).ok
        assert not plan("RQ-20260101-001", run_id="../x", queue_dir=qdir, repo=repo).ok
        assert not plan("RQ-20260101-001", run_id="rx", queue_dir=qdir / "missing", repo=repo).ok

        # 5. a malformed verdict.json is not silently promoted to a verdict.
        write_unit(qdir)
        p = plan("RQ-20260101-001", run_id="r5", queue_dir=qdir, repo=repo)
        execute(p, repo=repo)
        (repo / p.out_dir / "verdict.json").write_text('{"verdict": "maybe", "read_state": "measured", "population": "p", "n": 1}')
        rec = derive_record(p, repo=repo)
        assert rec["read_state"] == "producer_failed" and "malformed" in rec["note"], rec

        # 6. units_targeting_runner sees only units naming this workflow.
        write_unit(qdir)
        (qdir / "RQ-20260101-002.yaml").write_text(yaml.safe_dump(
            {"id": "RQ-20260101-002", "run": {"workflow": "other.yml"}}))
        assert units_targeting_runner(qdir) == ["RQ-20260101-001"]

    print("script_run self-test OK")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--unit", help="RQ-YYYYMMDD-NNN")
    ap.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID") or "manual")
    ap.add_argument("--queue-dir", default=str(_DEFAULT_QUEUE))
    ap.add_argument("--check", action="store_true", help="static check only; exit 1 on refusal")
    ap.add_argument("--run", action="store_true", help="execute the plan (writes comms/research/<unit>/<run_id>/)")
    ap.add_argument("--derive-record", action="store_true",
                    help="print (and append to $GITHUB_OUTPUT) the research-result inputs, and write them "
                         "to <out_dir>/record-inputs.json so the batch collector can land the record")
    ap.add_argument("--power-state", default="",
                    help="the dispatcher's computed R4 label, recorded verbatim in record-inputs.json")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    if not args.unit:
        ap.error("--unit is required")
    p = plan(args.unit, run_id=args.run_id, queue_dir=Path(args.queue_dir).resolve())
    if not p.ok:
        for e in p.errors:
            print(f"::error::{args.unit}: {e}", file=sys.stderr)
        return 1
    if args.check:
        print(f"{args.unit}: OK — {len(p.commands)} command(s), timeout {p.timeout_minutes} min, "
              f"out {p.out_dir.as_posix()}, rule {p.rule_id} ({p.rule_registered_at})")
        for c in p.commands:
            print("  " + " ".join(c))
        return 0
    rc = 0
    if args.run:
        m = execute(p)
        if not m["all_ok"]:
            print(f"::error::{args.unit}: a command failed — see run-manifest.json", file=sys.stderr)
            rc = 1
    if args.derive_record:
        rec = derive_record(p)
        rec["power_state"] = args.power_state
        rec["research_unit"] = p.unit
        _emit_outputs(rec)
        # The batch collector (scripts/research/collect_runner_results.py,
        # manager review 2026-09-29) lands records one PR per dispatcher cycle
        # instead of one PR per run; it reads exactly this file from the
        # run's artifact.
        p.out_dir.mkdir(parents=True, exist_ok=True)
        (p.out_dir / RECORD_INPUTS).write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not (args.run or args.derive_record):
        ap.error("one of --check, --run, --derive-record, --self-test")
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
