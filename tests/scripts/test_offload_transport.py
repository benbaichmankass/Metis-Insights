"""R3 offload transport — the runner emitter and the (unarmed) trainer drain.

The load-bearing test here is ``test_cross_machine_round_trip``: it simulates a
runner filesystem and a trainer filesystem, carries only what git would carry,
and then asserts the registered ``model_state_path`` LOADS on the destination.
That is the bar ``BL-20260827-R3-OFFLOAD-LANDING-HAS-TWO-MEASURED-BLOCKERS``
sets — *"verified by loading it there, not by the entry existing"*.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ml.registry.model_registry import ModelRegistry
from scripts.ml.gpu_burst import ingest_bundle
from scripts.ml.offload import drain_inbox, emit_bundle

MID = "btc-regime-5m-lgbm-flow-v1"
RUN = "20260827T130000Z"
STATE = {"booster": "LGBM_BLOB", "n_trees": 300}


def _make_run(root: Path, model_id: str = MID, run_id: str = RUN,
              state: dict | None = None) -> Path:
    rd = root / "ml" / "experiments-runs" / model_id / run_id
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "model_state.json").write_text(json.dumps(STATE if state is None else state))
    (rd / "metrics.json").write_text(json.dumps({"accuracy": 0.62, "n_eval": 412}))
    (rd / "manifest.json").write_text(json.dumps({
        "model_id": model_id, "trainer": "ml.trainers.lgbm",
        # Declared shadow on purpose: the drain must FORCE candidate regardless.
        "target_deployment_stage": "shadow",
    }))
    return rd


# --------------------------------------------------------------- self-tests
def test_emit_self_test_controls_fire():
    assert emit_bundle.main(["--self-test"]) == 0


def test_drain_self_test_controls_fire():
    assert drain_inbox.main(["--self-test"]) == 0


# ------------------------------------------------- the cross-machine round trip
def test_cross_machine_round_trip(tmp_path):
    """Emit on a runner path, carry only the inbox, drain on a trainer path.

    ⚠️ The runner tree is DELETED before the drain. Without that this proves
    nothing: both simulated machines share one real filesystem, so a runner
    path would still resolve and the counterfactual below would silently pass.
    The first hand-run of this scenario made exactly that mistake.
    """
    runner = tmp_path / "runner" / "work" / "Metis-Insights"
    trainer = tmp_path / "trainer" / "home" / "ubuntu" / "ict-trading-bot"
    rd = _make_run(runner)
    runner_abs_state = str((rd / "model_state.json").resolve())

    state, note, row = emit_bundle.emit(
        run_dir=rd, inbox=runner / "ml" / "offload-inbox", source="test",
        code_revision="abc1234", run_url="", symbol="BTCUSDT", timeframe="5m",
        dataset_version="v002", emitted_at="2026-08-27T13:00:00Z",
    )
    assert state == emit_bundle.EMITTED, note
    assert row is not None and row["model_id"] == MID and row["run_id"] == RUN
    # R1: the row states its own power rather than leaving n to be guessed.
    assert row["n_eval"] == 412.0

    # The transport: git carries ONLY the tracked inbox.
    trainer.mkdir(parents=True)
    shutil.copytree(runner / "ml" / "offload-inbox", trainer / "ml" / "offload-inbox")
    # ...and the runner is gone, as it is the moment the job ends.
    shutil.rmtree(tmp_path / "runner")

    # COUNTERFACTUAL — the fix the backlog row implied (copy the entry, keep the
    # path) yields a registered-and-unloadable model. This is why the drain
    # re-registers on the destination instead.
    assert not Path(runner_abs_state).is_file()

    reg_root = trainer / "ml" / "registry-store"
    rc = drain_inbox.main([
        "--inbox", str(trainer / "ml" / "offload-inbox"),
        "--registry-root", str(reg_root),
        "--experiments-root", str(trainer / "ml" / "experiments-runs"),
        "--apply",
    ])
    assert rc == 0

    entry = ModelRegistry(reg_root).get(f"{MID}{drain_inbox.OFFLOAD_SUFFIX}")
    # THE BAR: load the artifact from the path a consumer would literally use.
    assert json.loads(Path(entry.model_state_path).read_text()) == STATE
    # ...and no trace of the machine that trained it leaked into the entry.
    assert "runner" not in entry.model_state_path


def test_unarmed_drain_writes_nothing(tmp_path):
    """The shipped default. `--apply` is off, so the registry stays untouched."""
    runner = tmp_path / "runner"
    rd = _make_run(runner)
    emit_bundle.emit(
        run_dir=rd, inbox=runner / "inbox", source="t", code_revision="c",
        run_url="", symbol="", timeframe="", dataset_version="", emitted_at="",
    )
    reg_root = tmp_path / "registry-store"
    rc = drain_inbox.main([
        "--inbox", str(runner / "inbox"), "--registry-root", str(reg_root),
        "--experiments-root", str(tmp_path / "exp"),
    ])
    assert rc == 0
    assert not list(reg_root.glob("*.json")), "the UNARMED drain wrote a registry entry"


# ------------------------------------------------------------- safety guards
def test_drain_forces_candidate_never_shadow(tmp_path):
    """A fresh registration defaults to `shadow`, which AUTO-WIRES onto every
    strategy's predictor list. The drain must override the manifest's declared
    stage — the fixture above declares `shadow` precisely to catch a pass-through.
    """
    runner = tmp_path / "runner"
    rd = _make_run(runner)
    emit_bundle.emit(run_dir=rd, inbox=runner / "inbox", source="t",
                     code_revision="c", run_url="", symbol="", timeframe="",
                     dataset_version="", emitted_at="")
    reg_root = tmp_path / "registry-store"
    drain_inbox.main(["--inbox", str(runner / "inbox"), "--registry-root", str(reg_root),
                      "--experiments-root", str(tmp_path / "exp"), "--apply"])
    entry = ModelRegistry(reg_root).get(f"{MID}{drain_inbox.OFFLOAD_SUFFIX}")
    assert entry.target_deployment_stage == "candidate"
    assert entry.manifest["target_deployment_stage"] == "candidate"


def test_drain_never_touches_the_bare_production_id(tmp_path):
    """The manifests worth offloading are often live heads. Registering under the
    bare id would repoint a production model at runner-trained weights."""
    runner = tmp_path / "runner"
    rd = _make_run(runner)
    emit_bundle.emit(run_dir=rd, inbox=runner / "inbox", source="t",
                     code_revision="c", run_url="", symbol="", timeframe="",
                     dataset_version="", emitted_at="")
    reg_root = tmp_path / "registry-store"
    drain_inbox.main(["--inbox", str(runner / "inbox"), "--registry-root", str(reg_root),
                      "--experiments-root", str(tmp_path / "exp"), "--apply"])
    assert not (reg_root / f"{MID}.json").exists()
    assert (reg_root / f"{MID}{drain_inbox.OFFLOAD_SUFFIX}.json").exists()


def test_drain_refuses_to_refresh_an_operator_promoted_id(tmp_path):
    runner = tmp_path / "runner"
    reg_root = tmp_path / "registry-store"
    emit_bundle.emit(run_dir=_make_run(runner), inbox=runner / "inbox", source="t",
                     code_revision="c", run_url="", symbol="", timeframe="",
                     dataset_version="", emitted_at="")
    args = ["--inbox", str(runner / "inbox"), "--registry-root", str(reg_root),
            "--experiments-root", str(tmp_path / "exp"), "--apply"]
    drain_inbox.main(args)
    oid = f"{MID}{drain_inbox.OFFLOAD_SUFFIX}"
    ModelRegistry(reg_root).promote_stage(oid, "shadow", by="operator", reason="test")

    # A NEW run arrives for the now-promoted id.
    emit_bundle.emit(run_dir=_make_run(runner, run_id="20260828T000000Z"),
                     inbox=runner / "inbox", source="t", code_revision="c",
                     run_url="", symbol="", timeframe="", dataset_version="",
                     emitted_at="")
    drain_inbox.main(args)
    after = ModelRegistry(reg_root).get(oid)
    assert after.target_deployment_stage == "shadow"
    assert len(after.runs) == 1, "a promoted id had its served weights refreshed"


def test_drain_is_idempotent(tmp_path):
    runner = tmp_path / "runner"
    reg_root = tmp_path / "registry-store"
    emit_bundle.emit(run_dir=_make_run(runner), inbox=runner / "inbox", source="t",
                     code_revision="c", run_url="", symbol="", timeframe="",
                     dataset_version="", emitted_at="")
    args = ["--inbox", str(runner / "inbox"), "--registry-root", str(reg_root),
            "--experiments-root", str(tmp_path / "exp"), "--apply"]
    assert drain_inbox.main(args) == 0
    assert drain_inbox.main(args) == 0
    entry = ModelRegistry(reg_root).get(f"{MID}{drain_inbox.OFFLOAD_SUFFIX}")
    assert len(entry.runs) == 1


def test_the_two_ingests_share_one_safety_policy():
    """Pin the drain against `ingest_bundle`'s constants.

    Both ingest an off-machine model into the same registry and face the same
    hazard. Two copies of "what stage is safe" WILL drift; this fails the moment
    one of them moves.
    """
    assert drain_inbox.FORCED_STAGE == ingest_bundle._FORCED_STAGE == "candidate"
    assert drain_inbox.OFFLOAD_SUFFIX.startswith("-")
    assert drain_inbox.OFFLOAD_SUFFIX != ingest_bundle._BURST_SUFFIX, (
        "the two ingests must namespace DIFFERENTLY — a GPU-burst model and a "
        "runner-offload model are different provenance and must not collide"
    )


# ----------------------------------------------------------------- refusals
@pytest.mark.parametrize("mutate,expected", [
    (lambda rd: (rd / "model_state.json").unlink(), emit_bundle.REFUSED_NO_STATE),
    (lambda rd: (rd / "model_state.json").write_text("{}"), emit_bundle.REFUSED_EMPTY_STATE),
    (lambda rd: (rd / "manifest.json").write_text("{}"), emit_bundle.REFUSED_NO_MODEL_ID),
])
def test_emit_refuses_unpublishable_runs(tmp_path, mutate, expected):
    rd = _make_run(tmp_path / "runner")
    mutate(rd)
    state, _, row = emit_bundle.emit(
        run_dir=rd, inbox=tmp_path / "inbox", source="t", code_revision="c",
        run_url="", symbol="", timeframe="", dataset_version="", emitted_at="")
    assert state == expected
    assert row is None
    assert not (tmp_path / "inbox" / "index.jsonl").exists()


def test_emit_refuses_an_oversize_artifact(tmp_path):
    """Git history is immutable, so this refuses BEFORE the commit."""
    rd = _make_run(tmp_path / "runner", state={"blob": "x" * 5000})
    state, _, _ = emit_bundle.emit(
        run_dir=rd, inbox=tmp_path / "inbox", source="t", code_revision="c",
        run_url="", symbol="", timeframe="", dataset_version="", emitted_at="",
        max_bytes=1024)
    assert state == emit_bundle.REFUSED_OVERSIZE


def test_emit_refuses_to_append_to_a_corrupt_index(tmp_path):
    """A corrupt index is a repo problem, not a verdict on this run — and
    appending to a file we cannot read would compound the corruption."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "index.jsonl").write_text("this is not json\n")
    with pytest.raises(ValueError, match="refusing to append"):
        emit_bundle.emit(run_dir=_make_run(tmp_path / "runner"), inbox=inbox,
                         source="t", code_revision="c", run_url="", symbol="",
                         timeframe="", dataset_version="", emitted_at="")


def test_drain_reports_an_unreadable_index_as_could_not_read(tmp_path):
    """`could_not_read` is exit 2 and is NOT 'nothing pending'."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "index.jsonl").write_text("not json\n")
    assert drain_inbox.main(["--inbox", str(inbox),
                             "--registry-root", str(tmp_path / "r"),
                             "--experiments-root", str(tmp_path / "e")]) == 2


def test_emit_records_a_relative_artifact_dir(tmp_path, monkeypatch):
    """A committed row is read on another machine — an absolute path is wrong
    for every reader by construction. This is Blocker 1's class, one level up."""
    monkeypatch.chdir(tmp_path)
    rd = _make_run(tmp_path / "runner")
    _, _, row = emit_bundle.emit(
        run_dir=rd, inbox=Path("ml/offload-inbox"), source="t", code_revision="c",
        run_url="", symbol="", timeframe="", dataset_version="", emitted_at="")
    assert row is not None
    assert row["artifact_dir"] == f"ml/offload-inbox/{MID}/{RUN}"
    assert not Path(row["artifact_dir"]).is_absolute()


# ------------------------------------------------------- the shipped posture
# These pin "unarmed" as a property of the REPO, not of a comment. Arming the
# drain is Tier-2; if that decision is taken, these tests are the place it gets
# recorded, deliberately, rather than a unit quietly changing under a refactor.
_ROOT = Path(__file__).resolve().parents[2]


def _directives(path: Path) -> str:
    """The file's LIVE lines, with `#` comments stripped.

    ⚠️ Load-bearing. The first version of these tests substring-matched the whole
    file, so they graded the arming instructions in the header rather than the
    directives — a test that claims to check config and actually checks prose.
    Both unit and wrapper document how to arm, and must be free to.
    """
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def test_the_drain_unit_ships_plan_only():
    svc = _directives(_ROOT / "deploy/trainer/ict-offload-drain.service")
    assert "Environment=OFFLOAD_DRAIN_APPLY=0" in svc
    assert "Environment=OFFLOAD_DRAIN_APPLY=1" not in svc
    # A positive control: the header DOES describe arming, so a whole-file match
    # would pass this vacuously. This pins that the stripper is doing real work.
    raw = (_ROOT / "deploy/trainer/ict-offload-drain.service").read_text()
    assert "OFFLOAD_DRAIN_APPLY=1" in raw, "the unit should still document arming"


def test_nothing_in_the_repo_enables_the_drain_timer():
    """Half of arming is enabling the timer. No installer or cloud-init may do it.

    The live-VM installer globs `deploy/*.service` (non-recursive), so a unit in
    `deploy/trainer/` is out of its reach by construction — but that is a
    property of a glob someone could widen, so it is asserted rather than
    assumed.
    """
    hits = []
    for path in list(_ROOT.glob("scripts/**/*.sh")) + list(_ROOT.glob("deploy/*.yaml")):
        try:
            body = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "ict-offload-drain" in body and "enable" in body:
            hits.append(str(path.relative_to(_ROOT)))
    assert not hits, f"something enables the offload drain: {hits}"


def test_the_wrapper_arms_only_on_an_exact_1():
    """A typo must never arm an order-adjacent write path. `true`/`yes`/`01`
    are all NOT armed — the permissive reading would register a model."""
    body = _directives(_ROOT / "scripts/ops/run_offload_drain.sh")
    assert '"${OFFLOAD_DRAIN_APPLY:-0}" = "1"' in body
    # ...and it must NOT copy trainer_git_sync.sh's swallow-everything posture:
    # a drain that exits 0 on failure is a model that silently never arrived.
    assert "exit 0" not in body
    # Control: trainer_git_sync.sh really does have that posture, so this
    # assertion distinguishes the two rather than being trivially true.
    assert "exit 0" in _directives(_ROOT / "scripts/ops/trainer_git_sync.sh")


def test_the_publish_job_is_opt_in_and_write_scoped():
    import yaml
    wf = yaml.safe_load((_ROOT / ".github/workflows/trainer-offload-train.yml").read_text())
    train, publish = wf["jobs"]["train"], wf["jobs"]["publish"]
    # `on:` parses as the YAML 1.1 boolean True, not the string "on".
    triggers = wf.get("on", wf.get(True))
    # The default path must be byte-for-byte the old behaviour.
    assert triggers["workflow_dispatch"]["inputs"]["publish"]["default"] is False
    assert publish["if"] == "needs.train.outputs.publish == 'true'"
    # Only the publish job may write, and the train job must stay read-only.
    assert publish["permissions"] == {"contents": "write"}
    assert wf["permissions"]["contents"] == "read"
    assert "permissions" not in train
    # The runner must never register: the drain does that, on the trainer.
    assert any("--no-register" in (s.get("run") or "") for s in train["steps"])
    assert not any("--apply" in (s.get("run") or "") for s in publish["steps"])


def test_the_publish_job_asserts_its_own_landing():
    """R2: a job that exits 0 having landed nothing is a FAILED job.

    ⚠️ `--store` must name the path the job literally `git add`s. Pointing a
    landing assertion at the wrong store is worse than having none — it grades a
    healthy run `absent` forever, which is the mistake assert_rows_landed.py's
    own docstring records from its first wiring.
    """
    import yaml
    wf = yaml.safe_load((_ROOT / ".github/workflows/trainer-offload-train.yml").read_text())
    steps = wf["jobs"]["publish"]["steps"]
    added = [s for s in steps if "git add ml/offload-inbox" in (s.get("run") or "")]
    asserted = [s for s in steps if "assert_rows_landed.py" in (s.get("run") or "")]
    assert added, "the publish job stages nothing"
    assert asserted, "the publish job never asserts its rows landed"
    assert "--store ml/offload-inbox/index.jsonl" in asserted[0]["run"], (
        "the landing assertion must name the store the job actually commits"
    )


# ------------------------------------------------- the publish gate, EXECUTED
# The gate decides whether anything is committed to permanent history at all.
# A YAML parse proves it is well-formed; only running it proves it is right.
# Extracted from the workflow rather than duplicated, so the test cannot pass
# against a copy of the logic that has drifted from the shipped one.
def _publish_gate_script() -> str:
    import yaml
    wf = yaml.safe_load((_ROOT / ".github/workflows/trainer-offload-train.yml").read_text())
    step = next(s for s in wf["jobs"]["train"]["steps"] if s.get("id") == "publish_gate")
    return step["run"]


@pytest.mark.parametrize("case,want,train_out,make_dir,expect", [
    ("opted in, train produced a run", "true",
     {"experiment_dir": "ml/experiments-runs/m/RUN1"}, True, "true"),
    ("NOT opted in", "false",
     {"experiment_dir": "ml/experiments-runs/m/RUN1"}, True, "false"),
    # exit 78 — dataset absent / 0 rows. A real outcome, not a failure, and it
    # must publish nothing rather than fail the job.
    ("train SKIPPED", "true", {"skipped": True, "reason": "dataset_absent"}, True, "false"),
    ("train DIED (no stdout at all)", "true", None, True, "false"),
    # A dir the CLI named but that is not on disk: publish nothing rather than
    # let the artifact upload fail the run.
    ("dir reported but absent on disk", "true",
     {"experiment_dir": "ml/experiments-runs/m/RUN1"}, False, "false"),
])
def test_the_publish_gate_only_fires_when_opted_in_and_a_run_exists(
    tmp_path, case, want, train_out, make_dir, expect
):
    import json as _json
    import os
    import subprocess
    (tmp_path / "artifacts/offload").mkdir(parents=True)
    if train_out is not None:
        (tmp_path / "artifacts/offload/train_out.json").write_text(_json.dumps(train_out))
    if make_dir and train_out and train_out.get("experiment_dir"):
        (tmp_path / train_out["experiment_dir"]).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, of_publish=want, of_manifest="m", of_symbol="BTCUSDT",
               of_timeframe="5m", GITHUB_OUTPUT=str(tmp_path / "out.txt"))
    rc = subprocess.run(["bash", "-c", _publish_gate_script()], cwd=tmp_path,
                        env=env, capture_output=True, text=True)
    assert rc.returncode == 0, f"{case}: the gate must never fail the job\n{rc.stderr}"
    out = (tmp_path / "out.txt").read_text()
    pairs = dict(ln.split("=", 1) for ln in out.strip().splitlines() if "=" in ln)
    got = pairs["publish"]
    assert got == expect, f"{case}: expected publish={expect}, got {got}"
