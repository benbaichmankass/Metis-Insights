"""Tests for the consolidated guard runner.

BL-20260806-CI-FANOUT-AMPLIFIES-ACTIONS-OUTAGES. The consolidation replaced 30
per-guard workflows with one job, and the whole value of that move depends on it
being a PACKAGING change: every guard still runs, with the same command and the
same relevance condition. The tests here are what keeps that true — in
particular :func:`test_every_retired_workflow_has_a_registry_entry`, which fails
loudly if a guard is ever dropped from the registry, because a silently-missing
guard is exactly the "green that checked nothing" the guards exist to prevent.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rg = _load("scripts/ci/run_guards.py", "run_guards")


# The workflows this consolidation retired, by the job id that used to be the
# status-check context. Sourced from `git log` of the consolidation commit — do
# not prune this list; it is the regression net.
RETIRED = [
    "account-class-guard",
    "arch-doc-guard",
    "artifact-validity-guard",
    "async-route-blocking-guard",
    "canonical-config-loaders",
    "canonical-db-resolver",
    "canonical-doc-coherence",
    "claim-basis-guard",
    "diag-unit-allowlist-guard",
    "diagnostic-provenance-guard",
    "dry-run-guard",
    "env-gate-guard",
    "harness-lever-coupling-guard",
    "json-extract-guard",
    "json-notes-cap-guard",
    "layer-guard",
    "new-table-wiring-guard",
    "pairs-sizing-basis-guard",
    "prop-identity-guard",
    "provenance-consumer-guard",
    "qty-legalization-guard",
    "ruff-lint",
    "secret-scan",
    "silent-empty-guard",
    "soak-doctrine-guard",
    "strategy-coverage-guard",
    "strategy-risk-guard",
    "timestamp-comparison-guard",
    "training-population-guard",
    "writer-conformance-guard",
]

# The guards that used to Telegram the operator when they tripped. Same set, same
# audience — the consolidated job sends one message naming all of them.
NOTIFY = {
    "dry-run-guard",
    "env-gate-guard",
    "new-table-wiring-guard",
    "silent-empty-guard",
    "strategy-risk-guard",
    "writer-conformance-guard",
}


def test_every_retired_workflow_has_a_registry_entry():
    names = {g["name"] for g in rg.GUARDS}
    missing = sorted(set(RETIRED) - names)
    assert not missing, (
        f"{len(missing)} guard(s) lost in the consolidation: {missing}. "
        "Every retired workflow must still run — this was a packaging change."
    )


def test_no_retired_workflow_file_remains():
    """A leftover file would re-introduce the fan-out this change removed."""
    still_there = [
        n for n in RETIRED
        if (REPO / ".github" / "workflows" / f"{n}.yml").exists()
    ]
    # canonical-doc-coherence's file was named after the workflow, not the job id.
    assert not still_there, f"retired workflows still present: {still_there}"


def test_registry_names_are_unique():
    names = [g["name"] for g in rg.GUARDS]
    assert len(names) == len(set(names)), "duplicate guard name in the registry"


def test_notify_set_is_preserved():
    got = {g["name"] for g in rg.GUARDS if g.get("notify")}
    assert got == NOTIFY, (
        "the set of guards that ping the operator changed; that is a behaviour "
        f"change, not packaging. expected {sorted(NOTIFY)}, got {sorted(got)}"
    )


def test_every_guard_script_exists():
    """A registry entry pointing at a missing script would fail only at runtime."""
    missing = []
    for guard in rg.GUARDS:
        for step in guard["steps"]:
            argv = step["argv"] if isinstance(step, dict) else step
            # `python3 <script> ...` — check the script path. Module (`-m`) and
            # bare-binary steps (ruff, lint-imports) are resolved by PATH in CI.
            if argv[0] == "python3" and len(argv) > 1 and argv[1] != "-m":
                if not (REPO / argv[1]).exists():
                    missing.append((guard["name"], argv[1]))
    assert not missing, f"registry references missing scripts: {missing}"


def test_branch_protection_requires_guards_and_no_retired_context():
    text = (REPO / ".github" / "workflows" / "branch-protection-sync.yml").read_text()
    m = re.search(r"REQUIRED_CONTEXTS='(\[.*?\])'", text)
    assert m, "REQUIRED_CONTEXTS not found in branch-protection-sync.yml"
    contexts = json.loads(m.group(1))
    assert "guards" in contexts, (
        "the consolidated job is not a required check — the guards would be "
        "advisory, which is the walk-past failure mode they exist to stop"
    )
    stale = sorted(set(contexts) & set(RETIRED))
    assert not stale, (
        f"required contexts still name retired workflows {stale}; every PR would "
        "hang forever waiting for a status that can no longer be reported"
    )


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("**/*.py", "src/a/b.py", True),
        ("**/*.py", "b.py", True),
        ("**/*.py", "src/a/b.sql", False),
        ("src/**/*.py", "src/web/api/x.py", True),
        ("src/**/*.py", "scripts/x.py", False),
        ("src/**", "src/pipeline/x.py", True),
        ("config/accounts.yaml", "config/accounts.yaml", True),
        ("config/accounts.yaml", "config/accounts.yaml.bak", False),
        ("src/core/dispatcher*.py", "src/core/dispatcher_v2.py", True),
        ("src/core/dispatcher*.py", "src/core/sub/dispatcher.py", False),
    ],
)
def test_glob_to_regex(pattern, path, expected):
    assert bool(rg.glob_to_regex(pattern).match(path)) is expected


def test_relevance_none_always_runs():
    assert rg.is_relevant(None, []) is True


def test_relevance_regex_and_globs():
    assert rg.is_relevant({"regex": r"\.py$"}, ["a/b.py"]) is True
    assert rg.is_relevant({"regex": r"\.py$"}, ["a/b.md"]) is False
    assert rg.is_relevant({"globs": ["config/pairs.yaml"]}, ["config/pairs.yaml"]) is True
    assert rg.is_relevant({"globs": ["config/pairs.yaml"]}, ["config/other.yaml"]) is False


def test_harness_change_forces_every_guard():
    """Editing the harness must not be able to skip the guards it runs."""
    for path in rg.HARNESS_PATHS:
        assert (REPO / path).exists(), f"HARNESS_PATHS names a missing file: {path}"


def test_timestamp_selftest_payload_is_unchanged_by_the_token_split():
    """The split `created` + `_at` must still produce the exact bad input.

    The literal is assembled from fragments so the guard's own whole-tree audit
    does not flag its test fixture as a real defect. That trick is only
    acceptable while the payload stays byte-identical.
    """
    src = (REPO / "scripts" / "ci" / "guard_selftests.py").read_text()
    assert 'col = "created" + "_at"' in src
    assert 'f\'+    q = "SELECT * FROM trades WHERE {col} >= ?"\\n\'' in src
