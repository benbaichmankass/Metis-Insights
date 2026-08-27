#!/usr/bin/env python3
"""Runner side of R3 — turn a finished experiment run into a COMMITTABLE drop.

WHY THIS EXISTS
---------------
``trainer-offload-train.yml`` trains a manifest on a free 16 GB runner, then
``--no-register``s, uploads an artifact, and pushes nothing. It is the ONE
confirmed instance of the defect
``docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md`` filed as R2:

    A job that exits 0 having landed nothing is a FAILED job.

The compute was never the gap — a 4 vCPU / 16 GB runner trains the manifest the
6 GB trainer OOM-quarantines. The gap is that the RESULT dies with the runner.
This module is the transport's first half: it materializes the run into
``ml/offload-inbox/`` so a plain ``git`` push carries it, and the trainer's
existing 15-minute ``trainer_git_sync.sh`` force-checkout puts it on disk.

WHAT IT DOES **NOT** DO, DELIBERATELY
-------------------------------------
It does **not** write a registry entry, and it does **not** rewrite any path.

``BL-20260827-R3-OFFLOAD-LANDING-HAS-TWO-MEASURED-BLOCKERS`` records that
``ml/experiments/runner.py:422`` registers ``str(model_state_path.resolve())`` —
an absolute path on the machine that trained. Confirmed on the trainer
(trainer-diag #10366): 3 of 3 sampled entries carry
``/home/ubuntu/ict-trading-bot/ml/experiments-runs/...``. A runner's would be
``/home/runner/work/...``, which does not exist there, so a copied entry is
**registered and unloadable**.

The row's implied fix was to rewrite that path. **This ships the better one: do
not carry a registry entry at all.** The drain re-registers ON THE TRAINER, so
``resolve()`` returns a trainer-local path *by construction* — there is no path
to rewrite and nothing to get wrong. That is the shape
``scripts/ml/gpu_burst/ingest_bundle.py`` already uses for a rented GPU pod
whose local registry dies with it, and this is the same problem.

⚠️ A SECOND, INDEPENDENT RESOLUTION EXISTS AND IS **NOT** BEING RELIED ON.
``ml/shadow/factory.py::_resolve_state_path_via_mirror`` already maps any
absolute path containing an ``experiments-runs`` segment onto
``<registry_root>/../experiments-runs/<suffix>`` — which a runner path satisfies,
so a copied entry would in fact resolve. It is not relied on because that
fallback is **silent on success**: a right path and a wrong path would look
identical, and the failure would surface as a model that quietly serves the
wrong weights rather than as an error. Register-on-destination is correct
without needing the fallback to be.

WHAT LANDS
----------
``ml/offload-inbox/<model_id>/<run_id>/{model_state,metrics,manifest}.json``
plus one row appended to ``ml/offload-inbox/index.jsonl`` — the store
``scripts/ci/assert_rows_landed.py`` reads to prove the run's output reached the
shared ref. The row carries the R1 fields (what ran · on what data · params ·
what it measured · its power · where the artifact is) so a later session can
judge the run without re-reading the workflow.

BOUNDED BY OPT-IN, NOT BY THIS SCRIPT
-------------------------------------
Operator decision, 2026-08-27: commit the artifact only for runs that update a
registry entry — never every run. Measured basis: model_state.json is p50
**1.34 MB** (n=5,419; max 2.05 MB), and the trainer turns over ~52 runs/day, so
committing every run is ~70 MB/day of PERMANENT, unreclaimable git history.

⚠️ **THIS SCRIPT CANNOT ENFORCE THAT POLICY AND DOES NOT PRETEND TO.** The
registry lives on the trainer and is untracked (``git ls-files ml/registry-store``
-> 0 against 96 on-disk entries), so a runner has no way to ask whether a
model_id has an entry. The bound is therefore the CALLER's: the workflow's
``publish`` input defaults to ``false``, so publishing is a per-run decision
someone makes, not a default. What this script owns is the size guard below.

STATES, NEVER COLLAPSED
-----------------------
``emitted``            — the drop was written and the index row appended.
``already_present``    — this (model_id, run_id) is already in the index.
                         Idempotent: re-running is a no-op, not a duplicate.
``refused_no_state``   — the run produced no ``model_state.json``. This is the
                         normal shape of a train that SKIPPED or DIED, and it is
                         refused rather than published as an empty model.
``refused_empty_state``— the file exists and holds no model. Distinct from
                         ``refused_no_state`` on purpose: "the trainer wrote
                         nothing" and "the trainer never ran" have different
                         causes and different fixes.
``refused_no_model_id``— the manifest carries no model_id, so nothing downstream
                         could address it.
``refused_oversize``   — the artifact exceeds ``--max-bytes``. Git history is
                         immutable, so an oversize drop is not reclaimable; this
                         refuses BEFORE the commit rather than after.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

EMITTED = "emitted"
ALREADY_PRESENT = "already_present"
REFUSED_NO_STATE = "refused_no_state"
REFUSED_EMPTY_STATE = "refused_empty_state"
REFUSED_NO_MODEL_ID = "refused_no_model_id"
REFUSED_OVERSIZE = "refused_oversize"

_REFUSALS = frozenset(
    {
        REFUSED_NO_STATE,
        REFUSED_EMPTY_STATE,
        REFUSED_NO_MODEL_ID,
        REFUSED_OVERSIZE,
    }
)

# A CHOSEN bound with a measured basis, not a tuned value. Measured population:
# every model_state.json on the trainer, n=5,419 — p50 1.34 MB, max 2.05 MB
# (trainer-diag #10366). 25 MB is ~12x the largest artifact ever produced, so it
# refuses a pathological serialization without ever refusing a real one. It is
# NOT a throughput knob: the per-run bound that matters is the opt-in above.
DEFAULT_MAX_BYTES = 25 * 1024 * 1024

INDEX_NAME = "index.jsonl"


def _load_json(path: Path) -> Any | None:
    """Return parsed JSON, or None when the file is absent/unreadable/not JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_index(inbox: Path) -> tuple[list[dict[str, Any]], bool]:
    """Return (rows, readable).

    ``readable`` is False only when the index EXISTS and could not be parsed —
    an absent index is a readable empty one (a fresh inbox), which is a
    different fact from a corrupt index and must not be confused with it.
    """
    path = inbox / INDEX_NAME
    if not path.is_file():
        return [], True
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [], False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return [], False
        if isinstance(obj, dict):
            rows.append(obj)
    return rows, True


def already_indexed(rows: list[dict[str, Any]], model_id: str, run_id: str) -> bool:
    return any(
        r.get("model_id") == model_id and r.get("run_id") == run_id for r in rows
    )


def grade_run(
    *,
    state_exists: bool,
    state_obj: Any,
    model_id: str | None,
    state_bytes: int,
    max_bytes: int,
) -> tuple[str, str]:
    """Decide whether this run may be published. Pure — no I/O, so testable.

    Ordering is deliberate: identity is checked before size, so a nameless
    artifact is refused for being unaddressable rather than for being large.
    """
    if not state_exists:
        return REFUSED_NO_STATE, (
            "the run produced no model_state.json — the train step skipped or died. "
            "Nothing is published; read the run log rather than the inbox."
        )
    if not isinstance(state_obj, dict) or not state_obj:
        return REFUSED_EMPTY_STATE, (
            "model_state.json exists but holds no model (not a non-empty object) — "
            "publishing it would put an unloadable artifact in permanent history."
        )
    if not model_id or not str(model_id).strip():
        return REFUSED_NO_MODEL_ID, (
            "the manifest carries no model_id — nothing downstream could address "
            "this run."
        )
    if state_bytes > max_bytes:
        return REFUSED_OVERSIZE, (
            f"model_state.json is {state_bytes} bytes, over the {max_bytes}-byte "
            f"cap. Git history is immutable, so this refuses BEFORE the commit."
        )
    return EMITTED, "publishable"


def relative_artifact_dir(dest: Path) -> str:
    """Record the artifact location as a REPO-RELATIVE posix path.

    ⚠️ This is the exact defect the whole R3 slice exists to remove, one level
    up: ``ml/experiments/runner.py:422`` records ``str(path.resolve())`` and
    that machine-local absolute path is Blocker 1. A committed index row is read
    on a DIFFERENT machine than the one that wrote it, so an absolute path here
    would be wrong for every reader by construction. Caught by writing this
    module's own first end-to-end run and reading the row it produced.

    The drain does not depend on this field — it derives ``<inbox>/<model_id>/
    <run_id>`` itself, so a bad value here can misinform a human but cannot
    misdirect the ingest.
    """
    try:
        return dest.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        # Outside the tree we are running in (a test tmpdir, a caller that cd'd
        # elsewhere). Recorded as given rather than silently absolutised.
        return dest.as_posix()


def build_index_row(
    *,
    model_id: str,
    run_id: str,
    manifest: dict[str, Any],
    metrics: dict[str, Any],
    state_bytes: int,
    artifact_dir: str,
    source: str,
    code_revision: str,
    run_url: str,
    symbol: str,
    timeframe: str,
    dataset_version: str,
    emitted_at: str,
) -> dict[str, Any]:
    """One R1-shaped results row.

    ``power`` carries n and NOT a p-value: R4's gate is about whether a result
    could have been detected at all, and ``n_eval``/``n_train_final`` are what
    the evaluator actually reports. A missing n is recorded as None — never 0,
    which would assert a measurement nobody made.
    """
    def _num(key: str) -> float | None:
        v = metrics.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)

    return {
        # --- what ran
        "kind": "ml_offload_train",
        "model_id": model_id,
        "run_id": run_id,
        "trainer": manifest.get("trainer"),
        "code_revision": code_revision,
        "source": source,
        "run_url": run_url,
        # --- on what data
        "symbol": symbol,
        "timeframe": timeframe,
        "dataset_version": dataset_version,
        "dataset_family": manifest.get("dataset_family"),
        # --- params
        "target_deployment_stage_declared": manifest.get("target_deployment_stage"),
        "trainer_config": manifest.get("trainer_config"),
        "evaluator_config": manifest.get("evaluator_config"),
        # --- what it measured
        "metrics": {
            k: v for k, v in (metrics or {}).items() if not isinstance(v, (dict, list))
        },
        # --- its power
        "n_eval": _num("n_eval"),
        "n_train_final": _num("n_train_final"),
        # --- where the artifact is
        "artifact_dir": artifact_dir,
        "model_state_bytes": state_bytes,
        "emitted_at": emitted_at,
        # --- what the drain is allowed to do with it (see drain_inbox.py)
        "drain_policy": "namespaced_candidate",
    }


def emit(
    *,
    run_dir: Path,
    inbox: Path,
    source: str,
    code_revision: str,
    run_url: str,
    symbol: str,
    timeframe: str,
    dataset_version: str,
    emitted_at: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[str, str, dict[str, Any] | None]:
    """Materialize ``run_dir`` into ``inbox``. Returns (state, note, index_row).

    Idempotent on (model_id, run_id): re-running re-writes byte-identical
    artifact files and appends nothing, so a retried push can safely re-run
    this against a freshly-reset tree.
    """
    state_path = run_dir / "model_state.json"
    state_exists = state_path.is_file()
    state_obj = _load_json(state_path) if state_exists else None
    state_bytes = state_path.stat().st_size if state_exists else 0

    manifest = _load_json(run_dir / "manifest.json")
    manifest = manifest if isinstance(manifest, dict) else {}
    metrics = _load_json(run_dir / "metrics.json")
    metrics = metrics if isinstance(metrics, dict) else {}
    model_id = manifest.get("model_id")

    state, note = grade_run(
        state_exists=state_exists,
        state_obj=state_obj,
        model_id=model_id,
        state_bytes=state_bytes,
        max_bytes=max_bytes,
    )
    if state in _REFUSALS:
        return state, note, None

    run_id = run_dir.name
    rows, readable = read_index(inbox)
    if not readable:
        # Not folded into a refusal: a corrupt index is a repo-level problem,
        # not a verdict on this run. Refusing loudly beats appending to a file
        # we cannot read, which would compound the corruption.
        raise ValueError(
            f"{inbox / INDEX_NAME} exists and could not be parsed — refusing to "
            f"append to an index we cannot read."
        )

    dest = inbox / str(model_id) / run_id
    dest.mkdir(parents=True, exist_ok=True)
    # Written on every call (including the idempotent one) so a re-run against a
    # reset tree restores the artifact as well as leaving the index alone.
    (dest / "model_state.json").write_text(
        json.dumps(state_obj, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (dest / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    row = build_index_row(
        model_id=str(model_id),
        run_id=run_id,
        manifest=manifest,
        metrics=metrics,
        state_bytes=state_bytes,
        artifact_dir=relative_artifact_dir(dest),
        source=source,
        code_revision=code_revision,
        run_url=run_url,
        symbol=symbol,
        timeframe=timeframe,
        dataset_version=dataset_version,
        emitted_at=emitted_at,
    )

    if already_indexed(rows, str(model_id), run_id):
        return ALREADY_PRESENT, (
            f"{model_id} @ {run_id} is already in {INDEX_NAME}; artifact re-written, "
            f"no row appended"
        ), row

    with (inbox / INDEX_NAME).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return EMITTED, f"{model_id} @ {run_id} -> {dest}", row


def _self_test() -> int:
    """Planted controls over the pure half. Runs anywhere — no ML deps."""
    fails: list[str] = []

    st, _ = grade_run(
        state_exists=False, state_obj=None, model_id="m", state_bytes=0, max_bytes=10
    )
    if st != REFUSED_NO_STATE:
        fails.append(f"control 1: a missing state must refuse, got {st}")

    st, _ = grade_run(
        state_exists=True, state_obj={}, model_id="m", state_bytes=2, max_bytes=10
    )
    if st != REFUSED_EMPTY_STATE:
        fails.append(f"control 2: an empty state must refuse, got {st}")

    # The load-bearing distinction: absent and empty are DIFFERENT states.
    if REFUSED_NO_STATE == REFUSED_EMPTY_STATE:
        fails.append("control 3: absent and empty state are not distinct")

    st, _ = grade_run(
        state_exists=True, state_obj={"a": 1}, model_id="", state_bytes=2, max_bytes=10
    )
    if st != REFUSED_NO_MODEL_ID:
        fails.append(f"control 4: a nameless manifest must refuse, got {st}")

    st, _ = grade_run(
        state_exists=True, state_obj={"a": 1}, model_id="m", state_bytes=99, max_bytes=10
    )
    if st != REFUSED_OVERSIZE:
        fails.append(f"control 5: an oversize artifact must refuse, got {st}")

    # Identity is graded BEFORE size: a nameless AND oversize run reports the
    # cause that actually blocks it downstream.
    st, _ = grade_run(
        state_exists=True, state_obj={"a": 1}, model_id=None, state_bytes=99, max_bytes=10
    )
    if st != REFUSED_NO_MODEL_ID:
        fails.append(f"control 6: identity must be graded before size, got {st}")

    st, _ = grade_run(
        state_exists=True, state_obj={"a": 1}, model_id="m", state_bytes=5, max_bytes=10
    )
    if st != EMITTED:
        fails.append(f"control 7: a good run must be publishable, got {st}")

    rows = [{"model_id": "m", "run_id": "r"}]
    if not already_indexed(rows, "m", "r"):
        fails.append("control 8: an indexed run must be recognised")
    if already_indexed(rows, "m", "other"):
        fails.append("control 9: a different run_id must NOT match")
    if already_indexed(rows, "other", "r"):
        fails.append("control 10: a different model_id must NOT match")

    # A missing n is None, never 0 — 0 would assert a measurement nobody made.
    row = build_index_row(
        model_id="m", run_id="r", manifest={}, metrics={"accuracy": 0.5},
        state_bytes=1, artifact_dir="d", source="s", code_revision="c",
        run_url="u", symbol="S", timeframe="1h", dataset_version="v",
        emitted_at="t",
    )
    if row["n_eval"] is not None:
        fails.append(f"control 11: an unreported n must be None, got {row['n_eval']!r}")
    row2 = build_index_row(
        model_id="m", run_id="r", manifest={}, metrics={"n_eval": 40},
        state_bytes=1, artifact_dir="d", source="s", code_revision="c",
        run_url="u", symbol="S", timeframe="1h", dataset_version="v",
        emitted_at="t",
    )
    if row2["n_eval"] != 40.0:
        fails.append(f"control 12: a reported n must survive, got {row2['n_eval']!r}")
    # A bool must not read as a number (bool is an int subclass) — the same trap
    # trainer-offload-train.yml's own verdict step documents.
    row3 = build_index_row(
        model_id="m", run_id="r", manifest={}, metrics={"n_eval": True},
        state_bytes=1, artifact_dir="d", source="s", code_revision="c",
        run_url="u", symbol="S", timeframe="1h", dataset_version="v",
        emitted_at="t",
    )
    if row3["n_eval"] is not None:
        fails.append(f"control 13: a bool n must not read as a number, got {row3['n_eval']!r}")

    # The artifact path recorded in a COMMITTED row must never be absolute —
    # it is read on a different machine than the one that wrote it. This is the
    # same class as Blocker 1 and was a real defect in this module's first run.
    import os
    rel = relative_artifact_dir(Path.cwd() / "ml" / "offload-inbox" / "m" / "r")
    if os.path.isabs(rel):
        fails.append(f"control 14: an in-tree artifact_dir must be relative, got {rel!r}")
    if rel != "ml/offload-inbox/m/r":
        fails.append(f"control 15: artifact_dir must be repo-relative posix, got {rel!r}")

    if fails:
        for f in fails:
            print(f"::error::self-test: {f}")
        return 1
    print("emit-bundle: self-test OK — 15 planted controls all fire")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Emit an offload run into the inbox.")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--run-dir", help="experiments-runs/<model_id>/<run_id>")
    ap.add_argument("--inbox", default="ml/offload-inbox")
    ap.add_argument("--source", default="trainer-offload-train")
    ap.add_argument("--code-revision", default="unknown")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--symbol", default="")
    ap.add_argument("--timeframe", default="")
    ap.add_argument("--dataset-version", default="")
    ap.add_argument("--emitted-at", default="")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument(
        "--require-publishable",
        action="store_true",
        help="exit non-zero on a refusal. Off by default so a caller can ask "
             "'is there anything to publish?' without the answer 'no' being an "
             "error — a train that skipped is a real outcome, not a broken job.",
    )
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.run_dir:
        print("::error::--run-dir is required")
        return 2

    try:
        state, note, row = emit(
            run_dir=Path(args.run_dir),
            inbox=Path(args.inbox),
            source=args.source,
            code_revision=args.code_revision,
            run_url=args.run_url,
            symbol=args.symbol,
            timeframe=args.timeframe,
            dataset_version=args.dataset_version,
            emitted_at=args.emitted_at,
            max_bytes=args.max_bytes,
        )
    except (OSError, ValueError) as exc:
        print(f"::error::emit-bundle failed: {exc}")
        return 2

    print(f"emit-bundle: {state} — {note}")
    if row is not None:
        print(f"  model_id={row['model_id']} run_id={row['run_id']}")
    if state in _REFUSALS:
        print(f"::notice::nothing published ({state}).")
        return 1 if args.require_publishable else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
