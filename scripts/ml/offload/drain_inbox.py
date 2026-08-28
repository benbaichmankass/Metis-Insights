#!/usr/bin/env python3
"""Trainer side of R3 — ingest committed offload drops into the registry.

⚠️ **SHIPPED UNARMED, DELIBERATELY.** ``--apply`` is OFF by default, so the
default invocation grades every pending drop and writes NOTHING. Its systemd
unit ships **installed-disabled**, the same posture ``ict-drift-retrain`` uses
(``deploy/training-vm-cloud-init.yaml``: *"installed, disabled … RETRAIN_PLAN_ONLY=1
by default"*). Operator decision, 2026-08-27: **build the transport, leave the
drain unarmed** — a runner-trained model joining the live shadow fleet is Tier-2
and gets an explicit OK; it is not slipped in as plumbing.

WHY IT IS A SEPARATE UNIT AND NOT PART OF trainer_git_sync.sh
------------------------------------------------------------
``scripts/ops/trainer_git_sync.sh`` force-checks-out ``origin/main`` every 15
minutes, which IS what puts a committed drop on the trainer's disk. It is also
**code-only by contract** and ``exit 0``s on ANY error by design, so a sync
failure never leaves a failed unit alarming. A registry write inside a script
that swallows its own errors is the silent-landing defect wearing the fix's
clothes — the very thing R2 exists to stop. So the sync delivers; this drains,
declared in ``deploy/trainer/`` from birth (unlike the 8 undeclared units in
``BL-20260827-TRAINER-SCHEDULE-IS-UNDECLARED-THE-VM-IS-A-PET``).

WHY THIS RESOLVES BLOCKER 1 BY CONSTRUCTION
-------------------------------------------
It re-registers **here**, on the destination, so ``model_state_path.resolve()``
is a trainer-local path because that is the machine resolving it. No path is
rewritten, so no path can be rewritten wrongly. Copying the runner's registry
entry — the fix the backlog row implied — would have carried
``/home/runner/work/...`` and produced a model that is *registered and
unloadable*.

THE THREE SAFETY GUARDS, TAKEN FROM scripts/ml/gpu_burst/ingest_bundle.py
-------------------------------------------------------------------------
That script solves the same problem for a rented GPU pod. Its guards are not
re-derived here, they are copied, because the hazard is identical:

1. **Forced ``candidate`` stage.** A freshly-registered model defaults to
   ``shadow``, and the shadow-default flip AUTO-WIRES a shadow model onto every
   strategy's predictor list. ``candidate`` is REFUSED by the shadow factory, so
   an ingested model observes nothing until an operator promotes it.
2. **Namespaced id** (``<model_id>-offload``). The manifests worth offloading are
   often live heads, and ``ModelRegistry.register()`` on an existing id refreshes
   ``model_state_path`` while PRESERVING the stage — so ingesting under the bare
   id would silently repoint a live advisory model at runner-trained weights.
3. **Refuse an already-promoted id.** If someone has advanced the offload id past
   candidate, this will not refresh its served weights.

``tests/test_offload_drain.py`` pins guards 1 and 2 against ``ingest_bundle``'s
own constants, so the two ingests cannot drift into different safety policies.

VERIFICATION IS A LOAD, NOT AN EXISTENCE CHECK
----------------------------------------------
``BL-20260827-R3-OFFLOAD-LANDING-HAS-TWO-MEASURED-BLOCKERS`` sets the bar:
*"verified by loading it there, not by the entry existing."* After registering,
this re-reads the entry from disk, takes ``entry.model_state_path`` **verbatim**
— the literal string a consumer would use — and loads it. A drop that registers
but cannot be loaded back is reported ``verify_failed``, which is a FAILURE, not
a pass with a warning.

STATES, NEVER COLLAPSED
-----------------------
``ingested``          — registered here AND loaded back from the registered path.
``would_ingest``      — graded publishable; ``--apply`` was not passed. The
                        unarmed default. **Not** a success and not a refusal.
``already_ingested``  — this (model_id, run_id) is already a RunRecord. Idempotent.
``refused_promoted``  — the offload id was advanced past candidate by an operator.
``refused_bad_drop``  — the drop is missing/malformed on disk.
``verify_failed``     — it registered and the artifact did NOT load back.
``could_not_read``    — the index itself was unreadable. Not "nothing pending".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

INGESTED = "ingested"
WOULD_INGEST = "would_ingest"
ALREADY_INGESTED = "already_ingested"
REFUSED_PROMOTED = "refused_promoted"
REFUSED_BAD_DROP = "refused_bad_drop"
VERIFY_FAILED = "verify_failed"
COULD_NOT_READ = "could_not_read"

# Identical in intent to ingest_bundle.py's _FORCED_STAGE / _BURST_SUFFIX. A
# test pins the stage against that module so the two ingests cannot drift.
FORCED_STAGE = "candidate"
OFFLOAD_SUFFIX = "-offload"


def namespaced_id(model_id: str) -> str:
    """Idempotent: an id that already carries the suffix is returned unchanged."""
    return model_id if model_id.endswith(OFFLOAD_SUFFIX) else f"{model_id}{OFFLOAD_SUFFIX}"


def numeric_metrics(metrics: dict[str, Any] | None) -> dict[str, float]:
    """Registry ``metrics`` is a flat {str: float}. A bool is an int subclass and
    must NOT read as a metric — the same trap the offload workflow's own verdict
    step documents."""
    out: dict[str, float] = {}
    for k, v in (metrics or {}).items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[k] = float(v)
    return out


def read_index(index_path: Path) -> tuple[list[dict[str, Any]] | None, str]:
    """(rows, note). rows is None only when the index EXISTS and is unparseable.

    An ABSENT index is an empty inbox — readable, nothing pending. A CORRUPT
    index is ``could_not_read``. Folding those together would report a broken
    inbox as a quiet one, which is the collapse this repo keeps paying for.
    """
    if not index_path.is_file():
        return [], f"no index at {index_path} — inbox is empty"
    try:
        text = index_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"could not read {index_path}: {exc}"
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            return None, f"{index_path} line {i} is not JSON: {exc}"
        if isinstance(obj, dict):
            rows.append(obj)
    return rows, f"read {len(rows)} row(s) from {index_path}"


def grade_drop(
    *,
    drop_ok: bool,
    existing_stage: str | None,
    existing_run_ids: frozenset[str],
    run_id: str,
) -> tuple[str, str]:
    """Decide what may happen to one drop. Pure — no I/O, so testable.

    ``existing_stage`` is None when the offload id has no entry yet.
    """
    if not drop_ok:
        return REFUSED_BAD_DROP, (
            "the drop is missing or malformed on disk — model_state/manifest "
            "absent, unparseable, or empty"
        )
    if run_id in existing_run_ids:
        return ALREADY_INGESTED, f"run_id {run_id} is already a RunRecord — no-op"
    if existing_stage is not None and existing_stage != FORCED_STAGE:
        return REFUSED_PROMOTED, (
            f"the offload id already sits at stage {existing_stage!r} — an operator "
            f"advanced it, so refusing to refresh its served weights"
        )
    return WOULD_INGEST, "publishable"


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def drain_one(
    *,
    row: dict[str, Any],
    inbox: Path,
    registry_root: Path,
    experiments_root: Path,
    code_revision: str,
    apply: bool,
    by: str = "offload-drain",
) -> tuple[str, str]:
    """Ingest one index row. Returns (state, note)."""
    from ml.registry.model_registry import ModelRegistry, RegistryError

    base_id = str(row.get("model_id") or "").strip()
    run_id = str(row.get("run_id") or "").strip()
    if not base_id or not run_id:
        return REFUSED_BAD_DROP, "index row carries no model_id/run_id"

    # ⚠️ The drop is located from (inbox, model_id, run_id), NOT from the row's
    # `artifact_dir`. That field is informational and was written on another
    # machine; deriving the path here means a bad value can misinform a reader
    # but can never misdirect this ingest.
    drop = inbox / base_id / run_id
    state_obj = _load_json(drop / "model_state.json")
    manifest = _load_json(drop / "manifest.json")
    metrics = _load_json(drop / "metrics.json") or {}
    drop_ok = (
        isinstance(state_obj, dict) and bool(state_obj)
        and isinstance(manifest, dict) and bool(manifest)
    )

    model_id = namespaced_id(base_id)
    reg = ModelRegistry(registry_root)
    existing_stage: str | None = None
    existing_runs: frozenset[str] = frozenset()
    if reg.exists(model_id):
        try:
            entry = reg.get(model_id)
        except (RegistryError, ValueError, KeyError, OSError) as exc:
            return REFUSED_BAD_DROP, f"existing entry {model_id} unreadable: {exc}"
        existing_stage = entry.target_deployment_stage
        existing_runs = frozenset(r.run_id for r in entry.runs)

    state, note = grade_drop(
        drop_ok=drop_ok,
        existing_stage=existing_stage,
        existing_run_ids=existing_runs,
        run_id=run_id,
    )
    if state != WOULD_INGEST:
        return state, note
    if not apply:
        return WOULD_INGEST, (
            f"{model_id} @ {run_id} is ready to ingest at stage '{FORCED_STAGE}' "
            f"— NOT written (--apply not passed)"
        )

    # Materialize into the standard experiment layout ON THIS MACHINE, so the
    # path registered below resolves here by construction (Blocker 1).
    out_manifest = {
        **manifest,
        "model_id": model_id,
        "target_deployment_stage": FORCED_STAGE,
    }
    run_dir = experiments_root / model_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    model_state_path = run_dir / "model_state.json"
    model_state_path.write_text(json.dumps(state_obj), encoding="utf-8")
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (run_dir / "manifest.json").write_text(json.dumps(out_manifest), encoding="utf-8")

    try:
        reg.register(
            model_id=model_id,
            manifest=out_manifest,
            model_state_path=str(model_state_path.resolve()),
            metrics=numeric_metrics(metrics),
            code_revision=code_revision,
            run_id=run_id,
            by=by,
        )
    except (RegistryError, ValueError, OSError) as exc:
        return REFUSED_BAD_DROP, f"register failed for {model_id}: {exc}"

    # VERIFY BY LOADING, NOT BY EXISTING. Re-read the entry from disk and load
    # the artifact at the path a consumer would literally use. An entry that
    # cannot be loaded back is a FAILURE, not a pass with a warning.
    try:
        back = reg.get(model_id)
        loaded = _load_json(Path(back.model_state_path))
    except (RegistryError, ValueError, KeyError, OSError) as exc:
        return VERIFY_FAILED, f"{model_id} registered but the entry re-read failed: {exc}"
    if not isinstance(loaded, dict) or not loaded:
        return VERIFY_FAILED, (
            f"{model_id} registered, but model_state_path "
            f"{back.model_state_path!r} did not load back as a model. The entry "
            f"exists and is UNUSABLE — this is Blocker 1's failure mode."
        )
    return INGESTED, (
        f"{model_id} @ {run_id} registered at stage '{FORCED_STAGE}' and verified "
        f"loadable from {back.model_state_path}"
    )


def _self_test() -> int:
    fails: list[str] = []

    st, _ = grade_drop(drop_ok=False, existing_stage=None,
                       existing_run_ids=frozenset(), run_id="r")
    if st != REFUSED_BAD_DROP:
        fails.append(f"control 1: a malformed drop must refuse, got {st}")

    st, _ = grade_drop(drop_ok=True, existing_stage=None,
                       existing_run_ids=frozenset(), run_id="r")
    if st != WOULD_INGEST:
        fails.append(f"control 2: a fresh good drop must be ingestable, got {st}")

    st, _ = grade_drop(drop_ok=True, existing_stage="candidate",
                       existing_run_ids=frozenset({"r"}), run_id="r")
    if st != ALREADY_INGESTED:
        fails.append(f"control 3: a known run_id must be idempotent, got {st}")

    # The load-bearing guard: an operator-promoted id is never refreshed.
    for stage in ("shadow", "advisory"):
        st, _ = grade_drop(drop_ok=True, existing_stage=stage,
                           existing_run_ids=frozenset(), run_id="r")
        if st != REFUSED_PROMOTED:
            fails.append(f"control 4: stage {stage!r} must refuse, got {st}")

    # ...but an id still sitting at candidate may take another run.
    st, _ = grade_drop(drop_ok=True, existing_stage="candidate",
                       existing_run_ids=frozenset({"other"}), run_id="r")
    if st != WOULD_INGEST:
        fails.append(f"control 5: a still-candidate id must accept a new run, got {st}")

    # Idempotency is checked BEFORE the promotion guard: a run already ingested
    # must report `already_ingested` even after an operator promotes it, or a
    # re-run of the drain would report a refusal for work it already did.
    st, _ = grade_drop(drop_ok=True, existing_stage="advisory",
                       existing_run_ids=frozenset({"r"}), run_id="r")
    if st != ALREADY_INGESTED:
        fails.append(f"control 6: an ingested run must stay idempotent post-promotion, got {st}")

    if namespaced_id("m") != "m-offload":
        fails.append("control 7: the id must be namespaced")
    if namespaced_id("m-offload") != "m-offload":
        fails.append("control 8: namespacing must be idempotent")

    if numeric_metrics({"a": True, "b": 2, "c": "x"}) != {"b": 2.0}:
        fails.append("control 9: a bool/str must not read as a metric")

    if FORCED_STAGE == "shadow":
        fails.append("control 10: FORCED_STAGE must never be shadow — shadow "
                     "auto-wires onto every strategy's predictor list")

    rows, _ = read_index(Path("/nonexistent/index.jsonl"))
    if rows != []:
        fails.append(f"control 11: an ABSENT index is an empty inbox, got {rows!r}")

    if fails:
        for f in fails:
            print(f"::error::self-test: {f}")
        return 1
    print("offload-drain: self-test OK — 12 planted controls all fire")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Drain the committed offload inbox.")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--inbox", default="ml/offload-inbox")
    ap.add_argument("--registry-root", default="ml/registry-store")
    ap.add_argument("--experiments-root", default="ml/experiments-runs")
    ap.add_argument("--code-revision", default="offload-drain")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually write the registry. OFF by default — this ships UNARMED "
             "and a runner-trained model joining the shadow fleet is Tier-2.",
    )
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    inbox = Path(args.inbox)
    rows, note = read_index(inbox / "index.jsonl")
    if rows is None:
        print(f"offload-drain: {COULD_NOT_READ} — {note}")
        print("::error::the inbox index could not be read. This is NOT 'nothing "
              "pending' — we did not look.")
        return 2

    print(f"offload-drain: {note} (apply={args.apply})")
    counts: dict[str, int] = {}
    worst = 0
    for row in rows:
        state, detail = drain_one(
            row=row,
            inbox=inbox,
            registry_root=Path(args.registry_root),
            experiments_root=Path(args.experiments_root),
            code_revision=args.code_revision,
            apply=args.apply,
        )
        counts[state] = counts.get(state, 0) + 1
        print(f"  [{state}] {detail}")
        if state in (VERIFY_FAILED, REFUSED_BAD_DROP):
            worst = 1
    print(f"offload-drain: summary {json.dumps(counts, sort_keys=True)}")
    if not args.apply and counts.get(WOULD_INGEST):
        print(f"::notice::{counts[WOULD_INGEST]} drop(s) are ready and NOTHING was "
              f"written — this drain is unarmed by design. Arming it is Tier-2.")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
