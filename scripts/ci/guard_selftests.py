#!/usr/bin/env python3
"""Failure-path self-tests for the CI guards that carry one.

A guard whose failure path is never exercised is indistinguishable from a guard
that always passes — the "green that checked nothing" this repo already has a
rule about (`docs/CLAUDE-RULES-CANONICAL.md` § "Green is not evidence"). Five
guards therefore feed themselves a known-bad input on every run and fail the job
unless the guard returns non-zero.

Those self-tests used to live as inline heredocs inside five separate workflow
YAML files. They are lifted here **verbatim in behaviour** as part of the CI
fan-out consolidation (BL-20260806-CI-FANOUT-AMPLIFIES-ACTIONS-OUTAGES) — same
bad input, same expected exit code, same failure message. Moving them out of
YAML also makes each one runnable locally:

    python3 scripts/ci/guard_selftests.py diagnostic-provenance
    python3 scripts/ci/guard_selftests.py --all

Each self-test cleans up whatever it planted, including on failure, so a run
never leaves a poisoned working tree behind for the next step.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict

REPO = Path(__file__).resolve().parents[2]


def _load(path: str, name: str = "g"):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def _planted(path: Path, content: str = ""):
    """Create a file for the duration of the test, then always remove it.

    Directories this creates are removed too. Every earlier planter wrote into
    a directory that already existed, so the omission was invisible; the first
    one that did not (``canonical-doc-values``, planting under
    ``.claude/skills/``) left an empty ``_selftest_*`` directory behind on
    every run. Git does not track empty directories, so ``git status`` stayed
    clean and the litter was silent — while sitting inside the skills tree a
    catalog scan walks. Only directories this call created are removed, and
    only while empty, so a planter aimed at a real directory can never delete
    it.
    """
    created: list[Path] = []
    probe = path.parent
    while not probe.exists():
        created.append(probe)
        probe = probe.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    try:
        yield path
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        for leaf in created:  # innermost first
            with contextlib.suppress(OSError):
                leaf.rmdir()


def _rc(argv) -> int:
    return subprocess.run(argv, cwd=REPO).returncode


def _rc_out(argv) -> tuple:
    """(returncode, combined output) — for assertions on WHY a guard failed.

    A bare `rc != 0` proves only that something failed, which is not the claim
    a planted-failure test makes: it must fail ON THE PLANT. Without the text,
    a probe that never fired and a staging mistake are indistinguishable, and
    both read as a passing self-test.
    """
    p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ---------------------------------------------------------------------------


def selftest_claim_basis() -> None:
    """A backlog claim row with no basis must be flagged."""
    sys.path.insert(0, str(REPO / "scripts"))
    from check_claim_basis import check_new_rows  # noqa: E402

    head = json.dumps(
        {
            "items": [
                {
                    "id": "BL-SELFTEST",
                    "title": "selftest",
                    "description": "the head is 97% calm, promote it",
                }
            ]
        }
    )
    fails = check_new_rows("{}", head, "selftest.json")
    if len(fails) != 1:
        raise SystemExit(
            "::error::guard did NOT flag a basis-less claim row — failure path broken"
        )
    print("failure path verified: basis-less claim row correctly flagged")


def selftest_impossibility_claim() -> None:
    """An 'X cannot be measured' claim naming nothing must be flagged, and a
    `checked:` annotation naming a path that does NOT exist must not rescue it.

    The second half is the load-bearing one: `new-table-wiring-guard` was
    defeated because its marker was presence-only, so the cheapest way to
    silence a real finding was to name a table that did not exist."""
    sys.path.insert(0, str(REPO / "scripts"))
    from check_impossibility_claims import check_lines  # noqa: E402

    # the literal sentence from the 2026-08-07 incident
    bare = "it is the one item that cannot be worked around by writing code"
    if len(check_lines([(1, bare)], "selftest.md", context=bare)) != 1:
        raise SystemExit(
            "::error::guard did NOT flag a bare impossibility claim — "
            "the failure path is broken")

    lying = "this cannot be measured\nchecked: scripts/research/does_not_exist.py"
    if len(check_lines([(1, "this cannot be measured")], "selftest.md",
                       context=lying)) != 1:
        raise SystemExit(
            "::error::guard ACCEPTED a `checked:` annotation naming a "
            "nonexistent path — presence-only marker, cheaper to lie to than "
            "to satisfy")

    honest = ("this cannot be measured\n"
              "checked: scripts/research/backtest_fidelity_calibrate.py")
    if check_lines([(1, "this cannot be measured")], "selftest.md",
                   context=honest):
        raise SystemExit(
            "::error::guard flagged a claim that DID name a real tool — "
            "false positive, the escape hatch is broken")

    # The annotation window is per file TYPE — sparse markdown prose gets a
    # wider reach than a row-dense backlog JSON, where a neighbouring row's
    # annotation must NOT satisfy this row's claim.
    claim = "this cannot be measured"
    spread = "\n".join([claim] + ["filler"] * 9
                       + ["checked: scripts/research/backtest_fidelity_calibrate.py"])
    if check_lines([(1, claim)], "selftest.md", context=spread):
        raise SystemExit(
            "::error::guard rejected a prose annotation 10 lines from its "
            "claim — the markdown window is too tight to annotate a normal "
            "paragraph, which forces authors to reshape text to appease it")
    if len(check_lines([(1, claim)], "selftest.json", context=spread)) != 1:
        raise SystemExit(
            "::error::guard accepted an annotation 10 lines away in a backlog "
            "JSON — that reach spans whole rows, so one row's `checked:` can "
            "silently satisfy another row's claim")

    # ── item-scoped window on a row-structured BACKLOG json (2026-08-23) ──────
    # A ±6-line window is BOTH too tight and too loose on these files. Measured:
    # an annotation sat 45-75 lines from its claim INSIDE THE SAME ROW and was
    # rejected, while on a run of short rows the window spills into neighbours.
    # For `*-backlog.json` the locality is the enclosing ROW — resolved from
    # the `items` array, so it works on the real file AND on these fixtures.
    long_row = "\n".join(
        ['{', '  "items": [', '  {', '    "id": "BL-X",',
         '    "title": "this cannot be measured",']
        + [f'    "filler_{i}": "x",' for i in range(30)]
        + ['    "checked": "checked: scripts/research/backtest_fidelity_calibrate.py"',
           '  }', '  ]', '}'])
    claim_ln = 5
    if check_lines([(claim_ln, '    "title": "this cannot be measured",')],
                   "docs/claude/health-review-backlog.json",
                   body_lines=long_row.split("\n")):
        raise SystemExit(
            "::error::guard rejected an annotation inside the SAME backlog row "
            "as its claim — the item-scoped window is broken, and hand-annotating "
            "a long row becomes impossible")

    # ...and the neighbour must still NOT satisfy it. This is the property the
    # tight window existed to protect; item-scoping must not lose it.
    two_rows = "\n".join(
        ['{', '  "items": [',
         '  {', '    "id": "BL-A",', '    "title": "this cannot be measured"',
         '  },', '  {', '    "id": "BL-B",',
         '    "checked": "checked: scripts/research/backtest_fidelity_calibrate.py"',
         '  }', '  ]', '}'])
    if len(check_lines([(5, '    "title": "this cannot be measured"')],
                       "docs/claude/health-review-backlog.json",
                       body_lines=two_rows.split("\n"))) != 1:
        raise SystemExit(
            "::error::a NEIGHBOURING row's annotation satisfied this row's claim "
            "— item-scoping leaked, which is the exact defect the fixed window "
            "was protecting against")

    # An impossibility phrase inside a row's IDENTIFIER is a LABEL, not a claim.
    id_line = '    "id": "FIXTURE-ROW-CANNOT-BE-MEASURED",'
    if check_lines([(4, id_line)], "docs/claude/health-review-backlog.json",
                   body_lines=['{', '  "items": [', '  {', id_line, '  }', '  ]', '}']):
        raise SystemExit(
            "::error::guard demanded evidence for a row's NAME — an id is what "
            "the finding is CALLED, it asserts nothing")

    # ...but the exemption must be keyed on the `id` FIELD, not on the text, so
    # a real claim in `title`/`detail` is still caught.
    t_line = '    "title": "BL-ish text: this cannot be measured",'
    if len(check_lines([(4, t_line)], "docs/claude/health-review-backlog.json",
                       body_lines=['{', '  "items": [', '  {', t_line, '  }',
                                   '  ]', '}'])) != 1:
        raise SystemExit(
            "::error::the id exemption leaked into a non-id field — a real claim "
            "in `title` must still be flagged")


    print("failure path verified: bare claim flagged, fake `checked:` path "
          "rejected, real one accepted, annotation window scoped per file type")


def selftest_diag_unit_allowlist() -> None:
    """A deploy unit missing from the diag allowlist must fail the guard.

    The probe lands in a THROWAWAY COPY of `deploy/`, never the tracked tree.
    Planting it in the real directory (as this did until 2026-08-17) is safe
    only while cleanup runs: a SIGKILL, runner timeout or cancelled workflow
    strands the file, the next guard run then fails naming a unit nobody added
    — which reads as a real finding and costs a session time to dismiss — and
    `deploy/` being tracked put it one `git add -A` from being committed
    (BL-20260817-GUARD-SELFTESTS-PLANT-PROBE-FILES-IN-THE-LIVE-REPO-TREE).

    The copy is FAITHFUL rather than a bare directory holding one probe,
    because the guard also fails on stale exemptions and on units listed both
    ways. Scanning an empty staging dir would fire all 8 exemption-stale errors
    and still return non-zero — a pass for entirely the wrong reason.

    Hence the CONTROL below, which is the load-bearing half: the staged copy is
    asserted CLEAN before planting, so the later non-zero is attributable to
    the probe and not to the staging. `rc != 0` alone cannot tell those apart.
    """
    guard = ["python3", "scripts/check_diag_unit_allowlist.py", "--deploy-dir"]
    with tempfile.TemporaryDirectory() as td:
        stage = Path(td) / "deploy"
        stage.mkdir()
        for src in (REPO / "deploy").iterdir():
            if src.suffix in (".timer", ".service"):
                shutil.copy2(src, stage / src.name)

        rc, out = _rc_out(guard + [str(stage)])
        if rc != 0:
            raise SystemExit(
                "::error::SELF-TEST SETUP BROKEN — the staged copy of deploy/ "
                "failed the guard BEFORE anything was planted, so a later "
                "failure would prove nothing about the probe. Output:\n" + out)

        probe_name = "ict-selftest-unlisted.timer"
        (stage / probe_name).write_text("", encoding="utf-8")
        rc, out = _rc_out(guard + [str(stage)])
        if rc == 0:
            raise SystemExit(
                "::error::guard did NOT fail on an unlisted deploy unit — "
                "the failure path is broken")
        if probe_name not in out:
            raise SystemExit(
                "::error::guard failed, but its output never names the planted "
                "unit — it failed for some OTHER reason, so the failure path "
                "for THIS contract is unproven. Output:\n" + out)

    print("failure path verified: unlisted unit correctly failed the guard, "
          "the pre-plant control was clean, and the probe never touched the "
          "tracked deploy/ tree")


def selftest_diagnostic_provenance() -> None:
    """`max(proba)` printed under a `P(volatile)` label must be caught."""
    bad_diff = (
        "+++ b/scripts/ml/_selftest_probe.py\n"
        "@@ -0,0 +1,4 @@\n"
        "+def go(rows):\n"
        "+    for r in rows:\n"
        "+        s = r.get(\"score\")\n"
        "+        print(f\"P(volatile) = {s}\")\n"
    )
    src = (
        "def go(rows):\n"
        "    for r in rows:\n"
        "        s = r.get(\"score\")\n"
        "        print(f\"P(volatile) = {s}\")\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as fh:
        fh.write(bad_diff)
        diff_path = fh.name
    try:
        with _planted(REPO / "scripts" / "ml" / "_selftest_probe.py", src):
            rc = _rc(["python3", "scripts/check_diagnostic_provenance.py", diff_path])
        if rc != 1:
            raise SystemExit(
                f"::error::SELF-TEST FAILED — the guard returned {rc} on a known-bad "
                "diagnostic (expected 1). Its failure path is broken, so a green from "
                "it means nothing. Fix the guard before trusting this check."
            )
    finally:
        with contextlib.suppress(FileNotFoundError):
            Path(diff_path).unlink()
    print("self-test OK — the guard still fails closed (exit 1 on a known-bad input).")


def selftest_canonical_doc_values() -> None:
    """A doc asserting a live gate's WRONG value must be caught.

    `canonical-doc-coherence` passed 4/4 on 2026-08-10 while five canonical
    docs described branch protection incorrectly — none of its checks compared
    a claim against the file that sets it. The `declared values` check closes
    that. Its failure path is exercised here because the check is otherwise
    only ever seen passing, and a doc-drift guard that cannot fail is the drift.
    """
    planted = REPO / ".claude" / "skills" / "_selftest_doc_values" / "SKILL.md"
    # The exact stale phrasing that shipped: prose asserting require-up-to-date
    # is the safety net, while branch-protection-sync.yml sets STRICT=false.
    bad = ("# selftest\n"
           "The hard safety net is GitHub branch-protection (require-up-to-date).\n")
    with _planted(planted, bad):
        rc = _rc(["python3", "scripts/ci/check_canonical_doc_coherence.py"])
    if rc == 0:
        raise SystemExit(
            "::error::SELF-TEST FAILED — canonical-doc-coherence returned 0 on a doc "
            "asserting a value its source contradicts. The declared-values check is "
            "not detecting drift, so its PASS means nothing. Fix it before trusting it."
        )
    # And it must be quiet on the corrected, historically-marked form — a guard
    # that fires on its own retraction notes gets silenced wholesale.
    ok = ("# selftest\n"
          "This previously read: the safety net is branch-protection "
          "(require-up-to-date). No longer true as of 2026-08-10.\n")
    with _planted(planted, ok):
        rc_ok = _rc(["python3", "scripts/ci/check_canonical_doc_coherence.py"])
    if rc_ok != 0:
        raise SystemExit(
            "::error::SELF-TEST FAILED — canonical-doc-coherence flagged a CORRECTED, "
            "historically-marked statement. A guard that fires on its own retraction "
            "notes trains contributors to ignore it."
        )
    # Suppression must be LOCAL to the match. On a long single line — minified
    # JSON like `.claude/settings.json`, whose merge-guard hook is one ~2 KB
    # line — a whole-line historical test is guaranteed to find some "was" or
    # "correct" and suppress everything. That is not hypothetical: the stale
    # `sync IMMEDIATELY before merging` in the deny message matched a pattern,
    # sat in a scanned file, and passed anyway until `_historical_near` landed.
    far = ("# selftest\n"
           "The hard safety net is GitHub branch-protection (require-up-to-date). "
           + "Filler about unrelated matters. " * 30
           + "Separately and long ago, the old trainer ladder was removed.\n")
    with _planted(planted, far):
        rc_far = _rc(["python3", "scripts/ci/check_canonical_doc_coherence.py"])
    if rc_far == 0:
        raise SystemExit(
            "::error::SELF-TEST FAILED — a stale claim went unreported because a "
            "historical marker 900 characters away on the SAME line suppressed it. "
            "Suppression must be local to the match, or one minified file silences "
            "the whole check."
        )
    print("self-test OK — declared-values drift is caught (incl. on a long single "
          "line), corrected prose is not.")


def selftest_api_tier_policy() -> None:
    """A new route with no tier row must be caught.

    Plants a real router file (the guard reads from disk, joining the
    decorator to its ``APIRouter(prefix=...)``) plus a diff that adds it, and
    asserts the guard exits 1. If this ever passes, the inventory's "single
    source of truth" claim is unenforced again — which is the exact state the
    guard was written to end.
    """
    planted = REPO / "src" / "web" / "api" / "routers" / "_selftest_route.py"
    src = (
        "from fastapi import APIRouter\n"
        'router = APIRouter(prefix="/api/bot", tags=["bot"])\n'
        '@router.get("/selftest-undocumented-route")\n'
        "def handler():\n"
        "    return {}\n"
    )
    bad_diff = (
        "--- /dev/null\n"
        "+++ b/src/web/api/routers/_selftest_route.py\n"
        "@@ -0,0 +1,5 @@\n"
        + "".join(f"+{line}\n" for line in src.splitlines())
    )
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as fh:
        fh.write(bad_diff)
        diff_path = fh.name
    try:
        with _planted(planted, src):
            rc = _rc(["python3", "scripts/check_api_tier_policy.py", diff_path])
        if rc != 1:
            raise SystemExit(
                f"::error::SELF-TEST FAILED — the guard returned {rc} on a route "
                "with no tier row (expected 1). Its failure path is broken, so a "
                "green from it means nothing. Fix the guard before trusting it."
            )
    finally:
        with contextlib.suppress(FileNotFoundError):
            Path(diff_path).unlink()
    print("self-test OK — the guard still fails closed (exit 1 on an unrowed route).")


def selftest_harness_lever_coupling() -> None:
    """An injected unclassified strategy key must be reported as a coupling gap."""
    g = _load("scripts/check_harness_lever_coupling.py")
    strat = {
        "x": {
            "donchian": 20,
            "enabled": True,
            "symbols": ["BTCUSDT"],
            "timeframe": "1h",
            "brand_new_unclassified_lever": 3,
        }
    }
    gaps = g.find_coupling_gaps(strat)
    if gaps != [("x", "trend", "brand_new_unclassified_lever")]:
        raise SystemExit(
            f"::error::self-test FAILED — expected one unclassified-key gap, got {gaps}"
        )
    print("self-test OK — guard detects an injected unclassified key")


def selftest_timestamp_comparison() -> None:
    """A raw string comparison on created_at must be caught."""
    # The bad-input token is assembled from two fragments ON PURPOSE. Inline in
    # a workflow heredoc this sample was invisible to the guard's own whole-tree
    # audit; in a .py file it is not, and a verbatim literal here would make
    # `check_timestamp_comparisons.py --all` flag its own test fixture as a real
    # defect. Splitting the column name keeps the PAYLOAD byte-identical (asserted
    # in tests/ci/test_run_guards.py) without planting a genuine-looking site in
    # the tree. Do NOT "fix" this back into one literal.
    col = "created" + "_at"
    bad = (
        "+++ b/scripts/ml/_ts_selftest.py\n"
        "@@ -0,0 +1,1 @@\n"
        f'+    q = "SELECT * FROM trades WHERE {col} >= ?"\n'
    )
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as fh:
        fh.write(bad)
        path = fh.name
    try:
        rc = _rc(["python3", "scripts/check_timestamp_comparisons.py", path])
        if rc != 1:
            raise SystemExit(
                f"::error::SELF-TEST FAILED — guard returned {rc} on a known-bad "
                "comparison (expected 1). Its failure path is broken; a green means "
                "nothing. Fix the guard before trusting this check."
            )
    finally:
        with contextlib.suppress(FileNotFoundError):
            Path(path).unlink()
    print("self-test OK — guard fails closed (exit 1 on known-bad input).")


def selftest_collapsed_state() -> None:
    """A producer that stops emitting a declared state must be caught — and
    a SIBLING FIELD in the same file must not rescue it.

    That second half is the whole point. Until 2026-08-14 producer integrity
    searched the whole producer FILE, so two contracts in one module with
    overlapping state vocabularies each satisfied the other. Measured on
    `db_explorer.py`: collapsing `filter_state` so it could only ever say
    "applied" left the guard CLEAN, because `order_state` still carried the
    literals "not_requested" and "ignored_unknown_column". This guard had no
    self-test at all, which is why the hole survived its own review — the
    failure path was never exercised (§ "Green is not evidence").

    The planted producer reproduces exactly that shape: `good_state` emits all
    three, `bad_state` emits one. A file-scoped check passes it; the
    field-scoped one must not.
    """
    g = _load("scripts/ci/check_collapsed_states.py", "collapsed")

    producer = REPO / "src/runtime/_selftest_collapsed_producer.py"
    body = (
        '"""planted by guard_selftests; removed on exit."""\n'
        "def good_state():\n"
        '    good_state = "applied"\n'
        '    good_state = "not_requested"\n'
        '    good_state = "ignored_unknown_column"\n'
        "    return good_state\n"
        "\n"
        "def bad_state():\n"
        '    bad_state = "applied"   # the other two collapsed away\n'
        "    return bad_state\n"
    )
    contract = {
        "name": "selftest.bad_state",
        "producer": "src/runtime/_selftest_collapsed_producer.py",
        "producer_field": "bad_state",
        "consumer_token": r"\bselftest_never_matches_anything\b",
        "states": ["applied", "not_requested", "ignored_unknown_column"],
        "why": "planted",
    }

    with _planted(producer, body):
        # Sanity FIRST: without field scoping the sibling rescues it, which is
        # the bug. If this half ever stops passing, the planted file no longer
        # reproduces the shape and the assertion below proves nothing.
        loose = dict(contract)
        loose.pop("producer_field")
        g.CONTRACTS = [loose]
        if g.main(["x"]) != 1:
            # It still fails, but on CONSUMER coverage, not producer integrity —
            # so assert on the message, not merely the exit code.
            raise SystemExit("::error::planted contract did not even run")
        emitted_loose = g._states_in(body, contract["states"], "")
        if len(emitted_loose) != 3:
            raise SystemExit(
                "::error::self-test no longer reproduces the file-scoped false "
                f"negative (file-wide evidence saw {sorted(emitted_loose)})")

        emitted_scoped = g._states_in(body, contract["states"], "bad_state")
        if emitted_scoped != {"applied"}:
            raise SystemExit(
                "::error::field-scoped producer check did NOT narrow the "
                f"evidence — saw {sorted(emitted_scoped)}, expected ['applied']")

    print("failure path verified: a sibling field no longer supplies a "
          "collapsed field's producer evidence")


def selftest_matrix_corpus_agreement() -> None:
    """Run that guard's own planted-failure suite, which nothing was running.

    `check_matrix_corpus_agreement.py --self-test` has existed since the guard
    shipped and was never registered here, so its planted disagreements were
    exercised only when a human ran the flag by hand. That is the same shape the
    guard itself exists to catch — evidence that is written and never read — and
    it matters more now that the suite covers the blocked-cell check added
    2026-08-17, whose false-positive controls are the load-bearing half.
    """
    rc = _rc([sys.executable, "scripts/ci/check_matrix_corpus_agreement.py",
              "--self-test"])
    if rc != 0:
        raise SystemExit(
            "::error::self-test FAILED — check_matrix_corpus_agreement's own "
            f"planted-failure suite exited {rc}")
    print("self-test OK — planted disagreement, planted stale block, and the "
          "below-floor / legacy-geometry / declined-to-grade false-positive "
          "controls all behave")


def selftest_workflow_catalog() -> None:
    """Run the workflow-catalog guard's own planted-failure suite.

    Path B (see COVERED_BY_CHECKER): the checker owns its `--self-test` and
    `run_guards.py` runs that flag directly, so this entry is a manual alias
    whose covering path `check_selftest_wiring.py` VERIFIES rather than trusts.

    The suite exercises both directions independently — an unnamed workflow and
    a doc naming a file that does not exist — plus the negative control that
    matters most: a real non-workflow file (`config/accounts.yaml`) must never
    be reported as a phantom. Without that control, the obvious over-broad
    implementation of the phantom check passes every positive test and starts
    flagging ordinary config references.
    """
    rc = _rc([sys.executable, "scripts/ci/check_workflow_catalog.py",
              "--self-test"])
    if rc != 0:
        raise SystemExit(
            "::error::self-test FAILED — check_workflow_catalog's own "
            f"planted-failure suite exited {rc}. The catalog guard's failure "
            "path is broken, so a green from it means nothing.")
    print("self-test OK — both catalog directions fail closed, and a real "
          "non-workflow file is not mistaken for a phantom")


def selftest_manifest_scope_constants() -> None:
    """Plant each of the three defects `manifest-scope-constants` claims to catch.

    THE PLANT FOR C1 IS THE REAL HISTORICAL DEFECT, not a synthetic stand-in:
    `hour_of_day` declared on a `1d` bar is exactly what `mes-regime-1d-lgbm-v2`
    carried while it sat 34.0 days untrained under a green rc=0 cycle
    (MB-20260829-MES-1D-DECLARES-A-FEATURE-THAT-CANNOT-VARY-AT-ITS-OWN-TIMEFRAME).
    If this planted manifest does not fail the guard, the guard would not have
    caught the incident it was written for.

    THE NEGATIVE CONTROL IS THE ONE THAT MATTERS. The obvious over-broad
    implementation of C1 flags `hour_of_day` wherever it appears — and 54 of the
    55 manifests that declare it are on 5m/15m/1h/all bars where the hour genuinely
    varies. Such a guard passes every positive test above and then fails the whole
    fleet. So a 15m manifest declaring the same column must PASS, and that is
    asserted here, not assumed.
    """
    script = "scripts/ci/check_manifest_scope_constants.py"
    cfg = REPO / "ml" / "configs"

    def _manifest(model_id, timeframe, feats, cats=(), symbol="MES",
                  family="market_features"):
        feat_yaml = "\n".join(f"  - {c}" for c in feats)
        cat_yaml = "\n".join(f"  - {c}" for c in cats)
        return (
            f"manifest_version: v1\n"
            f"model_id: {model_id}\n"
            f"model_family: classification_lightgbm\n"
            f"trainer: ml.trainers.lightgbm_multiclass.LightGBMMulticlassTrainer\n"
            f"trainer_config:\n"
            f"  target_column: regime_label\n"
            f"  feature_columns:\n{feat_yaml}\n"
            + (f"  categorical_columns:\n{cat_yaml}\n" if cats else "")
            + f"dataset:\n"
            f"  family: {family}\n"
            f"  symbol_scope: {symbol}\n"
            f"  timeframe: {timeframe}\n"
            f"  version: v001\n"
        )

    base = ["vol_bucket", "rolling_log_return_vol", "log_return"]

    # --- C1: the real mes-regime-1d defect --------------------------------
    plant = cfg / "_selftest_scope_c1.yaml"
    with _planted(plant, _manifest("_selftest-c1", "1d", base + ["hour_of_day"])):
        rc, out = _rc_out(["python3", script])
    if rc == 0:
        raise SystemExit(
            "manifest-scope-constants self-test FAILED: `hour_of_day` on a 1d bar "
            "(the real mes-regime-1d-lgbm-v2 defect) did not fail the guard"
        )
    if "scope_constant" not in out or "_selftest-c1" not in out:
        raise SystemExit(
            "manifest-scope-constants self-test FAILED: the guard returned non-zero "
            "but not ON THE PLANT — no scope_constant finding for _selftest-c1.\n"
            + out
        )

    # --- C2: a column no builder emits ------------------------------------
    plant = cfg / "_selftest_scope_c2.yaml"
    with _planted(plant, _manifest("_selftest-c2", "15m",
                                   base + ["__NOT_A_REAL_COLUMN__"])):
        rc, out = _rc_out(["python3", script])
    if rc == 0 or "absent_from_builder" not in out or "_selftest-c2" not in out:
        raise SystemExit(
            "manifest-scope-constants self-test FAILED: a feature absent from the "
            "family builder's schema was not reported.\n" + out
        )

    # --- C3: a categorical the trainer would raise on ---------------------
    plant = cfg / "_selftest_scope_c3.yaml"
    with _planted(plant, _manifest("_selftest-c3", "15m", base,
                                   cats=["vol_bucket", "dayofweek"])):
        rc, out = _rc_out(["python3", script])
    if rc == 0 or "categorical_orphan" not in out or "_selftest-c3" not in out:
        raise SystemExit(
            "manifest-scope-constants self-test FAILED: a categorical_columns entry "
            "absent from feature_columns was not reported — that is a hard train-time "
            "raise (lightgbm_multiclass.py:119-123), strictly worse than a skip.\n"
            + out
        )

    # --- NEGATIVE CONTROL: the same column where it genuinely varies ------
    plant = cfg / "_selftest_scope_ok.yaml"
    with _planted(plant, _manifest("_selftest-ok", "15m",
                                   base + ["hour_of_day", "dayofweek"],
                                   cats=["vol_bucket", "hour_of_day"])):
        rc, out = _rc_out(["python3", script])
    if rc != 0:
        raise SystemExit(
            "manifest-scope-constants self-test FAILED (NEGATIVE CONTROL): a 15m "
            "manifest declaring hour_of_day/dayofweek is CORRECT and must pass. An "
            "over-broad C1 would fail 54 of the 55 manifests that declare "
            "hour_of_day.\n" + out
        )


SELFTESTS: Dict[str, Callable[[], None]] = {
    "api-tier-policy": selftest_api_tier_policy,
    # REDUNDANT-BY-DESIGN, and the comment is load-bearing: `run_guards.py` does
    # NOT invoke this name. That guard's entry runs
    # `check_matrix_corpus_agreement.py --self-test` directly, which is the same
    # suite, so its controls DO execute in CI — this entry is only a manual
    # alias. A 2026-08-17 session grepped for the `guard_selftests.py` call
    # site, found none, and wrongly concluded the suite was unwired; do not
    # repeat that inference. Presence here is not evidence a self-test runs, and
    # absence of a call site is not evidence it does not.
    #
    # That reasoning is no longer only prose: this name is declared in
    # COVERED_BY_CHECKER below, and `check_selftest_wiring.py` now VERIFIES the
    # covering path on every run — so the claim in this comment is enforced
    # rather than asserted, and a future session need not re-derive it.
    "matrix-corpus-agreement": selftest_matrix_corpus_agreement,
    "workflow-catalog": selftest_workflow_catalog,
    "collapsed-state": selftest_collapsed_state,
    "canonical-doc-values": selftest_canonical_doc_values,
    "claim-basis": selftest_claim_basis,
    "impossibility-claim": selftest_impossibility_claim,
    "diag-unit-allowlist": selftest_diag_unit_allowlist,
    "diagnostic-provenance": selftest_diagnostic_provenance,
    "harness-lever-coupling": selftest_harness_lever_coupling,
    "timestamp-comparison": selftest_timestamp_comparison,
    "manifest-scope-constants": selftest_manifest_scope_constants,
}

# The SECOND covering path. A name here is one whose controls reach CI via the
# checker's OWN `--self-test` rather than via `run_guards.py` invoking this
# module by name — so `run_guards.py` legitimately has no call site for it, and
# grepping for one and finding nothing proves nothing.
#
# This is a DECLARATION, and `scripts/ci/check_selftest_wiring.py` refuses to
# take it on trust: it verifies that `run_guards.py` really runs that exact
# script with `--self-test`, AND that the script really declares the flag in its
# argparse. Naming a script that cannot self-test FAILS. Keep it that way — a
# mapping cheaper to fake than to satisfy is worse than none at all
# (`new-table-wiring-guard`'s presence-only marker is the cautionary case).
COVERED_BY_CHECKER: Dict[str, str] = {
    "matrix-corpus-agreement": "scripts/ci/check_matrix_corpus_agreement.py",
    "workflow-catalog": "scripts/ci/check_workflow_catalog.py",
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", nargs="?", choices=sorted(SELFTESTS))
    ap.add_argument("--all", action="store_true", help="run every self-test")
    args = ap.parse_args(argv)

    if not args.all and not args.name:
        ap.error("give a self-test name or --all")

    names = sorted(SELFTESTS) if args.all else [args.name]
    for name in names:
        print(f"[selftest] {name}")
        SELFTESTS[name]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
