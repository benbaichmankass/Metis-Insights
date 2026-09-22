"""Tests for the register merge driver.

The REFUSAL tests matter more than the merge tests. A union-by-id merge that
picks a winner is exactly the failure this driver exists to prevent: the
manager's own resolver reported "no id lost, none resurrected" while silently
dropping an edit, because both sides had ADDED the same id with different
content. Every test below named REFUSE encodes a case a machine must not decide.
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts", "ops"))

import merge_json_register as M  # noqa: E402

# ⚠️ FOUR PATHS REMOVED 2026-09-21 by the operating reset:
# health-review-backlog.json, OPEN-ITEMS.json, work/SESSIONS.json and
# work/OPEN-PRS.json are archived under
# docs/archive/2026-09-21-operating-reset/. The round-trip proof below reads
# the LIVE file, so it cannot run against a path that no longer exists.
#
# `MANAGER-CHECKLIST.json` stays, and it is the one that matters now: it is the
# only shared register left, so it is the only one where a whole-file
# reserialisation could turn a 4-line edit into a guaranteed conflict — which
# is the defect this whole module exists to prevent. The property is still
# PROVEN against a real file, not merely unit-tested against fixtures.
#
# ⚠️ The em-dash case OPEN-ITEMS.json used to carry — a file mixing a literal
# and an escaped em-dash, which `json.dumps` cannot reproduce byte-for-byte —
# is NOT lost with it. `test_round_trip_survives_mixed_escapes` below plants
# exactly that shape as a fixture, so the hard case keeps its coverage.
REGISTERS = [
    "docs/claude/work/MANAGER-CHECKLIST.json",
]


def doc(rows, **hdr):
    h = "".join('  "%s": %s,\n' % (k, json.dumps(v)) for k, v in hdr.items())
    body = ",\n".join('    {"id": "%s", "v": %s}' % (i, json.dumps(v)) for i, v in rows)
    return "{\n%s  \"items\": [\n%s\n  ]\n}\n" % (h, body)


def ids(text):
    _, arrs = M.parse(text)
    return [r[0] for r in arrs["items"].rows]


# ----------------------------------------------------------------- it merges

def test_append_append_is_unioned():
    base = doc([("A", 1)])
    ours = doc([("A", 1), ("B", 2)])
    theirs = doc([("A", 1), ("C", 3)])
    out = M.merge(base, ours, theirs)
    assert ids(out) == ["id=A", "id=B", "id=C"]


def test_disjoint_edits_both_apply():
    base = doc([("A", 1), ("B", 1)])
    ours = doc([("A", 2), ("B", 1)])
    theirs = doc([("A", 1), ("B", 2)])
    out = json.loads(M.merge(base, ours, theirs))
    assert {r["id"]: r["v"] for r in out["items"]} == {"A": 2, "B": 2}


def test_both_sides_bumping_a_timestamp_takes_the_later():
    """The measured majority case: 74% of MANAGER-CHECKLIST conflict pairs."""
    base = doc([("A", 1)], updated_at="2026-09-02T10:00:00Z")
    ours = doc([("A", 1)], updated_at="2026-09-02T15:46:00Z")
    theirs = doc([("A", 1)], updated_at="2026-09-02T11:22:00Z")
    assert json.loads(M.merge(base, ours, theirs))["updated_at"] == "2026-09-02T15:46:00Z"


def test_deletion_is_intent_and_is_not_resurrected():
    base = doc([("A", 1), ("B", 1)])
    ours = doc([("A", 1)])            # B pruned
    theirs = doc([("A", 1), ("B", 1)])  # B untouched
    assert ids(M.merge(base, ours, theirs)) == ["id=A"]
    assert ids(M.merge(base, theirs, ours)) == ["id=A"]  # symmetric


# --------------------------------------------------------------- it REFUSES

def test_REFUSE_divergent_same_id_add():
    """The case that silently dropped an edit. Base has no A, so a
    base-only divergence check would not have looked at it."""
    base = doc([("Z", 0)])
    ours = doc([("Z", 0), ("A", 1)])
    theirs = doc([("Z", 0), ("A", 2)])
    with pytest.raises(M.Refuse, match="both sides ADDED"):
        M.merge(base, ours, theirs)


def test_REFUSE_divergent_same_id_edit():
    base = doc([("A", 1)])
    with pytest.raises(M.Refuse, match="both sides EDITED"):
        M.merge(base, doc([("A", 2)]), doc([("A", 3)]))


def test_REFUSE_delete_versus_edit():
    base = doc([("A", 1), ("B", 1)])
    ours = doc([("A", 1)])              # B deleted
    theirs = doc([("A", 1), ("B", 9)])  # B edited
    with pytest.raises(M.Refuse, match="DELETED by one side, EDITED"):
        M.merge(base, ours, theirs)
    with pytest.raises(M.Refuse, match="DELETED by one side, EDITED"):
        M.merge(base, theirs, ours)


def test_REFUSE_non_timestamp_header_divergence():
    base = doc([("A", 1)], cycle="one")
    with pytest.raises(M.Refuse):
        M.merge(base, doc([("A", 1)], cycle="two"), doc([("A", 1)], cycle="three"))


def _run_driver(tmp_path, base, ours, theirs, label=None):
    b, o, t = (tmp_path / n for n in ("b", "o", "t"))
    b.write_text(base)
    o.write_text(ours)
    t.write_text(theirs)
    argv = [sys.executable, os.path.join(ROOT, "scripts/ops/merge_json_register.py"),
            str(b), str(o), str(t)]
    if label:
        argv.append(label)
    return subprocess.run(argv, capture_output=True), o


def test_REFUSE_is_the_driver_exit_code(tmp_path):
    r, _o = _run_driver(tmp_path, doc([("A", 1)]), doc([("A", 2)]), doc([("A", 3)]))
    assert r.returncode == 1
    assert b"REFUSING" in r.stderr


def test_a_REFUSAL_leaves_conflict_markers_and_not_valid_json(tmp_path):
    """⚠️ THIS TEST USED TO ASSERT THE DEFECT, and that is why the defect lived.

    Its last line read ``assert o.read_text() == doc([("A", 2)])`` with the
    comment ``# left untouched for the human`` — i.e. the suite BLESSED the
    driver leaving pure OURS behind, which is the state that silently drops
    every row existing only on THEIRS
    (`BL-20260912-THE-JSONREGISTER-MERGE-DRIVER-LEAVES-PURE-OURS-WITH-NO-MARKERS-WHEN-IT-REFUSES-SO-A-REFUSAL-READS-AS-A-CLEAN-MERGE`,
    measured on PR #11957 at 8 rows lost). The module docstring promised
    markers the whole time; field beat comment, and the test agreed with the
    field. It now pins the PROMISE.

    ⚠️ ASSERTING exit==1 IS NOT THIS TEST — the driver already exited 1 before
    the fix. What matters is what is left ON DISK.
    """
    base = doc([("A", 1)])
    ours = doc([("A", 2), ("ONLY-OURS", 9)])
    theirs = doc([("A", 3), ("ONLY-THEIRS", 9)])
    r, o = _run_driver(tmp_path, base, ours, theirs, label="docs/claude/x.json")
    assert r.returncode == 1
    left = o.read_text()

    assert left != ours, (
        "the refusal left pure OURS on disk — valid JSON, no markers, and "
        "ONLY-THEIRS silently gone. This is the defect verbatim.")
    # ⚠️ THE LITERALS, NOT THE MODULE CONSTANTS. Asserting `M.OURS_MARK in
    # left` is VACUOUS — it passes for any value the constant happens to hold,
    # so renaming the marker to something no merge tool recognises escapes it.
    # A planted mutation (`OURS_MARK = "// <<<<<<< ours"`) did exactly that and
    # the whole suite stayed green, which is why these are spelled out.
    assert "\n<<<<<<< " in "\n" + left, "no git `<<<<<<< ` marker at line start"
    assert "\n=======\n" in left, "no git `=======` separator on its own line"
    assert "\n>>>>>>> " in left, "no git `>>>>>>> ` marker at line start"
    # ...and the constants must BE those literals, so the two can never drift.
    assert M.OURS_MARK.startswith("<<<<<<< ")
    assert M.SPLIT_MARK == "======="
    assert M.THEIRS_MARK.startswith(">>>>>>> ")
    with pytest.raises(ValueError):
        json.loads(left)          # cannot slip through `git add -A` unnoticed

    # Both sides survive in the working tree, which is what makes markers the
    # right shape rather than a stub: nothing has to be recovered to resolve it.
    assert "ONLY-OURS" in left and "ONLY-THEIRS" in left
    assert "docs/claude/x.json" in left, "the banner does not name the file"


def test_the_refused_file_is_not_silently_parseable_as_a_register(tmp_path):
    """The property that actually protects the register: every consumer fails.

    A guard, a reader, or `backlog_append` must not be able to treat a refused
    merge as a register. `M.parse` is the driver's own reader, so if IT accepts
    the file the invalidity is cosmetic.
    """
    r, o = _run_driver(tmp_path, doc([("A", 1)]), doc([("A", 2)]), doc([("A", 3)]))
    assert r.returncode == 1
    with pytest.raises(Exception):
        M.parse(o.read_text())


def test_the_CLEAN_path_is_unchanged_by_the_refusal_fix(tmp_path):
    """⚠️ THE CONTROL. A fix that made every merge conflict would be WORSE than
    the defect — the clean path is measured fine (nine sibling branches merged
    the same main with 0 losses). A clean merge must still write a valid,
    marker-free register carrying every row from BOTH sides."""
    base = doc([("A", 1)])
    ours = doc([("A", 1), ("OURS-ONLY", 2)])
    theirs = doc([("A", 1), ("THEIRS-ONLY", 3)])
    r, o = _run_driver(tmp_path, base, ours, theirs)
    assert r.returncode == 0, r.stderr.decode()
    left = o.read_text()
    assert M.OURS_MARK not in left and M.THEIRS_MARK not in left
    assert "<<<<<<<" not in left and ">>>>>>>" not in left
    json.loads(left)
    assert set(ids(left)) == {"id=A", "id=OURS-ONLY", "id=THEIRS-ONLY"}


# --------------------------------------------- it proves it preserved the rows

def test_expected_ids_is_not_a_bare_superset_of_both_sides():
    """⚠️ The obvious invariant — 'the result is a superset of ours and theirs'
    — is FALSE here, and asserting it would break the deletion rule this driver
    documents. A row deleted by theirs and untouched by ours STAYS DELETED, so
    the result is deliberately NOT a superset of ours."""
    base = M.parse(doc([("A", 1), ("B", 1)]))[1]["items"]
    ours = M.parse(doc([("A", 1), ("B", 1)]))[1]["items"]
    theirs = M.parse(doc([("A", 1)]))[1]["items"]
    assert M.expected_ids(base, ours, theirs) == {"id=A"}, (
        "a deliberate deletion was demanded back — the driver would resurrect "
        "rows a side meant to remove")


def test_a_driver_that_drops_a_row_cannot_exit_clean(monkeypatch):
    """The post-condition, exercised by breaking `merge_rows` underneath it.

    This is defence in depth and is labelled as such in the source: it cannot
    fire while `merge_rows` is correct. What it buys is that a FUTURE change
    dropping a row refuses instead of writing a register it cannot prove it
    preserved — which is the failure
    `BL-20260911-MERGING-MAIN-DROPPED-THREE-SIBLING-LANES-ROWS-FROM-SESSIONS-JSON-AND-THE-FILE-STAYED-VALID-JSON`
    describes, checked one level lower, at the tool.
    """
    base = doc([("A", 1)])
    ours = doc([("A", 1), ("OURS-ONLY", 2)])
    theirs = doc([("A", 1), ("THEIRS-ONLY", 3)])
    assert M.merge(base, ours, theirs)          # control: clean today

    real = M.merge_rows
    monkeypatch.setattr(M, "merge_rows",
                        lambda b, o, t, k: [r for r in real(b, o, t, k)
                                            if r[0] != "id=THEIRS-ONLY"])
    with pytest.raises(M.Refuse) as exc:
        M.merge(base, ours, theirs)
    assert "THEIRS-ONLY" in str(exc.value)
    assert "LOST" in str(exc.value)


def test_a_driver_that_invents_a_row_cannot_exit_clean(monkeypatch):
    """The other direction: a resurrected row is a defect too, and the same
    post-condition names it separately rather than pooling it with a loss."""
    base, ours, theirs = doc([("A", 1), ("B", 1)]), doc([("A", 1), ("B", 1)]), doc([("A", 1)])
    real = M.merge_rows
    monkeypatch.setattr(M, "merge_rows",
                        lambda b, o, t, k: real(b, o, t, k) + [("id=GHOST", '{"id": "GHOST"}')])
    with pytest.raises(M.Refuse) as exc:
        M.merge(base, ours, theirs)
    assert "GHOST" in str(exc.value) and "PRESENT" in str(exc.value)


def test_a_real_git_merge_cannot_silently_drop_a_row(tmp_path):
    """⚠️ THE ONE THAT MATTERS: the driver exercised AS A DRIVER, through git.

    Every other test here calls the module directly. The defect only ever bit
    through `git merge`, because git PRE-POPULATES %A with OURS before calling a
    driver — so a driver that writes nothing leaves a complete, valid,
    marker-free register that `git add -A && git commit` swallows whole.

    Reproduced against the PRE-FIX driver while writing this (same fixture,
    `git show origin/main:scripts/ops/merge_json_register.py`): markers 0, valid
    JSON yes, `ONLY-THEIRS` 0 occurrences, and the resulting COMMIT carried
    `['A', 'ONLY-OURS']`. That is the 8-row loss on PR #11957, in miniature.
    """
    repo = tmp_path / "r"
    repo.mkdir()

    def g(*a, check=True):
        return subprocess.run(["git", *a], cwd=repo, capture_output=True,
                              text=True, check=False)

    g("init", "-q", "-b", "main", ".")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    driver = os.path.join(ROOT, "scripts/ops/merge_json_register.py")
    g("config", "merge.jsonregister.driver",
      "%s %s %%O %%A %%B %%P" % (sys.executable, driver))
    (repo / ".gitattributes").write_text("reg.json merge=jsonregister\n")
    (repo / "reg.json").write_text(doc([("A", 1)]))
    g("add", "-A")
    g("commit", "-qm", "base")
    g("checkout", "-qb", "theirs")
    (repo / "reg.json").write_text(doc([("A", 3), ("ONLY-THEIRS", 9)]))
    g("commit", "-qam", "theirs")
    g("checkout", "-q", "main")
    (repo / "reg.json").write_text(doc([("A", 2), ("ONLY-OURS", 9)]))
    g("commit", "-qam", "ours")

    merged = g("merge", "theirs")
    assert merged.returncode != 0, "git reported a clean merge of a real conflict"
    left = (repo / "reg.json").read_text()

    assert "ONLY-THEIRS" in left, (
        "the row that exists only on THEIRS is gone from the working tree — "
        "this is the silent-drop defect, reproduced")
    assert "\n<<<<<<< " in "\n" + left and "\n>>>>>>> " in left
    with pytest.raises(ValueError):
        json.loads(left)

    # The sequence that actually lost the rows: resolve the file git NAMED,
    # `git add -A`, commit. It must now be impossible to do that unnoticed.
    g("add", "-A")
    committed = g("commit", "-qm", "resolved")
    if committed.returncode == 0:
        blob = g("show", "HEAD:reg.json").stdout
        with pytest.raises(ValueError):
            json.loads(blob)     # a broken commit, loudly — never a quiet one


def test_the_docstring_promise_matches_the_behaviour():
    """Criterion 3 of the row: the prose is made TRUE rather than left to rot.
    Prose describing behaviour the code does not have is what let this sit."""
    assert "conflict markers left in %A" in M.__doc__
    assert "THAT SECOND SENTENCE WAS A LIE" in M.__doc__, (
        "the correction record was removed; a future reader would take the "
        "promise as having always held and not look for the rows it lost")


# ------------------------------------------------- it never reformats bytes

@pytest.mark.parametrize("rel", REGISTERS)
def test_round_trip_is_byte_identical(rel):
    """Parse -> reassemble must reproduce the LIVE file EXACTLY."""
    text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    assert M.round_trip(text) == text


def test_round_trip_survives_mixed_escapes():
    """The hard case, kept as a FIXTURE now that its exemplar is archived.

    `OPEN-ITEMS.json` carried a literal em-dash and an escaped one IN THE SAME
    FILE, which `json.dumps` cannot reproduce byte-for-byte whatever its
    `ensure_ascii` setting: one spelling has to win. Splicing the original byte
    spans is what makes the merge driver safe on such a file, and that file was
    the proof.

    It is archived under `docs/archive/2026-09-21-operating-reset/`, so the
    proof is planted here instead rather than dropped with it. THIS IS THE
    SHAPE, NOT A PARAPHRASE: row A holds the escaped form (`\\u2014`) and row B
    the literal one, so any implementation that re-serialises instead of
    splicing normalises one of them and fails.
    """
    text = (
        '{\n'
        '  "items": [\n'
        '    {"id": "A", "v": "escaped \\u2014 dash"},\n'
        '    {"id": "B", "v": "literal \u2014 dash"}\n'
        '  ]\n'
        '}\n'
    )
    # The premise, asserted rather than assumed: a naive re-serialisation
    # really does change these bytes. Without this the test could pass against
    # an implementation that round-trips by luck.
    assert json.dumps(json.loads(text), indent=2) + "\n" != text
    assert M.round_trip(text) == text


def test_untouched_rows_keep_their_exact_bytes():
    """Protects backlog_append.py::append_row's exact-serialisation contract:
    a merge must not re-attribute ~21k unrelated lines to whoever merged."""
    weird = '{"id": "A", "t": "an em-dash — literal", "u": "escaped \\u2014"}'
    def mk(extra):
        return '{\n  "items": [\n    %s%s\n  ]\n}\n' % (weird, extra)
    base, ours = mk(""), mk("")
    theirs = mk(',\n    {"id": "B", "t": "new"}')
    out = M.merge(base, ours, theirs)
    assert weird in out                      # byte-for-byte, not re-escaped
    assert out.count("\\u2014") == 1
    assert "—" in out


def test_real_register_merge_produces_valid_json():
    text = open(os.path.join(ROOT, "docs/claude/work/MANAGER-CHECKLIST.json"),
                encoding="utf-8").read()
    bumped = text.replace('"as_of"', '"as_of"', 1)
    out = M.merge(text, bumped, text)
    assert json.loads(out)


# ------------------------------------- the authorship half of the header

# ⚠️ WHY THESE EXIST, MEASURED. `updated_by` is in neither TIMESTAMP_KEYS nor
# COUPLED_WITH until this change, and BOTH sides always stamp it — so two lanes
# whose row sets are provably disjoint still collided on that one scalar. The
# E31 lane hit it twice in six minutes and resolved it by hand, naming BOTH
# sessions in the header because both had written. Measured over the 20 adjacent
# register-touching commit pairs on origin/main dated 2026-09-22, the driver
# refused 5; `updated_by` alone was the whole disagreement in 1 of them.


def test_updated_by_beside_a_timestamp_resolves_and_NAMES_BOTH_WRITERS():
    """The bookkeeping case — and the half that is NOT merely "later wins".

    Taking the later side's name alone would leave the file asserting that one
    session wrote a register carrying two sessions' rows. That is a lie with no
    expiry and no way for a later reader to detect it, which is why the union is
    part of the contract rather than a nicety.
    """
    base = doc([("A", 1)], updated_at="2026-09-22T10:00:00Z", updated_by="base")
    ours = doc([("A", 1), ("OURS", 2)],
               updated_at="2026-09-22T15:46:00Z", updated_by="manager (session_X)")
    theirs = doc([("A", 1), ("THEIRS", 3)],
                 updated_at="2026-09-22T11:22:00Z", updated_by="E31 lane (session_Y)")
    out = json.loads(M.merge(base, ours, theirs))

    assert out["updated_at"] == "2026-09-22T15:46:00Z", "the later clock must win"
    # Later writer first, so the name heading the list is the one whose
    # timestamp the header now carries.
    assert out["updated_by"] == "manager (session_X) + E31 lane (session_Y)"
    assert set(r["id"] for r in out["items"]) == {"A", "OURS", "THEIRS"}


def test_the_union_is_symmetric_on_the_LATER_side_not_on_ours():
    """Swapping the sides must reorder the names by CLOCK, not by which
    argument happened to be `ours` — otherwise the header's first name and its
    timestamp can disagree about who wrote last."""
    base = doc([("A", 1)], updated_at="2026-09-22T10:00:00Z", updated_by="base")
    early = doc([("A", 1)], updated_at="2026-09-22T11:00:00Z", updated_by="early")
    late = doc([("A", 1)], updated_at="2026-09-22T15:00:00Z", updated_by="late")
    for ours, theirs in ((early, late), (late, early)):
        out = json.loads(M.merge(base, ours, theirs))
        assert out["updated_at"] == "2026-09-22T15:00:00Z"
        assert out["updated_by"] == "late + early"


def test_REFUSE_updated_by_divergence_WITH_a_real_row_disagreement():
    """⚠️ THE CONTROL. The planted case the change must NOT swallow.

    A conflict that differs on `updated_by` AND on a real row is substantive.
    If this passes clean, the driver was WIDENED rather than sharpened, and the
    bookkeeping resolution has become a way to auto-resolve row disagreements
    that happen to travel with a header bump.

    It is not a hypothetical shape: pair f50a25928 -> 4ca43ed4a on origin/main
    (2026-09-22) is exactly it — `updated_by` divergent AND row R7 edited on
    both sides — and it still refuses, on the row.
    """
    base = doc([("A", 1)], updated_at="2026-09-22T10:00:00Z", updated_by="base")
    ours = doc([("A", 2)], updated_at="2026-09-22T15:46:00Z", updated_by="manager")
    theirs = doc([("A", 3)], updated_at="2026-09-22T11:22:00Z", updated_by="E31 lane")
    with pytest.raises(M.Refuse, match="both sides EDITED"):
        M.merge(base, ours, theirs)


def test_REFUSE_an_unrecognised_scalar_travelling_WITH_updated_by():
    """The polarity `check_pr_landing.py` and `check_manager_scope.py` both
    document: an unrecognised scalar keeps refusing. There is no "ignore what
    you do not recognise" escape, and pairing one with a resolvable key must not
    smuggle it through."""
    base = doc([("A", 1)], updated_at="2026-09-22T10:00:00Z",
               updated_by="base", cycle="one")
    ours = doc([("A", 1)], updated_at="2026-09-22T15:46:00Z",
               updated_by="manager", cycle="two")
    theirs = doc([("A", 1)], updated_at="2026-09-22T11:22:00Z",
                 updated_by="E31 lane", cycle="three")
    with pytest.raises(M.Refuse, match="header scalars disagree"):
        M.merge(base, ours, theirs)


def test_updated_by_ALONE_in_the_hunk_still_resolves():
    """⚠️ THE SHAPE EVERY REAL CONFLICT ACTUALLY HAS, and the one an earlier
    draft of this change got wrong.

    That draft coupled `updated_by` to `updated_at` and asserted a REFUSE here,
    reasoning that with no clock in the hunk nothing ranks the two names. It is
    true that nothing ranks them — and irrelevant, because ranking is not what
    the field needs. MEASURED on origin/main 2026-09-22: `MANAGER-CHECKLIST.json`
    stamps `updated_at` at DATE granularity, so two lanes landing the same day
    write that line IDENTICALLY and git leaves it outside the hunk. BOTH real
    refusals (9323a0bf9 -> abda697ce, f50a25928 -> 4ca43ed4a) carry `updated_by`
    and nothing else, so a coupling-only fix moved the refusal count 5 -> 5.

    A union needs no ranking: it states that both sessions wrote, which is the
    true and complete thing to say. It is also why this is safe where taking the
    later name would not be — a union cannot lose a fact.
    """
    base = doc([("A", 1)], updated_by="base")
    out = json.loads(M.merge(base, doc([("A", 1), ("OURS", 2)], updated_by="manager"),
                             doc([("A", 1), ("THEIRS", 3)], updated_by="E31 lane")))
    assert out["updated_by"] == "manager + E31 lane"
    assert set(r["id"] for r in out["items"]) == {"A", "OURS", "THEIRS"}


def test_the_real_2026_09_22_refusal_shape_resolves():
    """The measured case rebuilt as a fixture: a DATE-granularity `updated_at`
    equal on both sides, `updated_by` divergent, row sets disjoint. Verbatim the
    two values from 9323a0bf9 -> abda697ce."""
    o_by = "manager (session_01XQ) — main + the E27 lane row"
    t_by = "manager (session_01XQ) — counted union of origin/main and the E33 lane"
    base = doc([("A", 1)], updated_at="2026-09-22", updated_by="R7 research lane")
    ours = doc([("A", 1), ("E27", 2)], updated_at="2026-09-22", updated_by=o_by)
    theirs = doc([("A", 1), ("E33", 3)], updated_at="2026-09-22", updated_by=t_by)
    out = json.loads(M.merge(base, ours, theirs))
    assert out["updated_at"] == "2026-09-22"
    # ⚠️ The same session under two descriptions is named TWICE. Exact-string
    # dedupe is deliberate: collapsing on the session id would have to discard
    # one of the two descriptions, which is the erasure this change exists to
    # prevent. Verbose and true beats short and lossy.
    assert out["updated_by"] == o_by + " + " + t_by
    assert set(r["id"] for r in out["items"]) == {"A", "E27", "E33"}


def test_REFUSE_when_updated_by_is_not_a_plain_string():
    """A shape the splicer does not recognise must REFUSE, not guess. This is
    the same polarity as the unrecognised-scalar rule: no silent pass-through."""
    base = '{\n  "updated_by": ["base"],\n  "items": [\n    {"id": "A", "v": 1}\n  ]\n}\n'
    ours = base.replace('["base"]', '["manager"]')
    theirs = base.replace('["base"]', '["E31 lane"]')
    with pytest.raises(M.Refuse, match="header scalars disagree"):
        M.merge(base, ours, theirs)


def test_two_plain_timestamps_are_STILL_resolved_per_line():
    """⚠️ THE NARROWING CONTROL. Adding `updated_at` to COUPLED_WITH could have
    routed every hunk containing it through the coupled path, which demands the
    hunk hold nothing but that group — so `updated_at` + `as_of` with no
    `updated_by`, resolved per-line by max today, would have started REFUSING.
    A fix that refuses more than it did is a regression wearing a feature's name.
    """
    base = doc([("A", 1)], updated_at="2026-09-22T10:00:00Z", as_of="2026-09-20")
    ours = doc([("A", 1)], updated_at="2026-09-22T15:00:00Z", as_of="2026-09-21")
    theirs = doc([("A", 1)], updated_at="2026-09-22T11:00:00Z", as_of="2026-09-22")
    out = json.loads(M.merge(base, ours, theirs))
    assert out["updated_at"] == "2026-09-22T15:00:00Z"   # ours is later
    assert out["as_of"] == "2026-09-22"                  # theirs is later


def test_the_author_union_is_idempotent():
    """A branch that merges `main` twice must not name the same session twice.
    Without this the header grows by a duplicate on every re-merge, which is the
    serialization cost this change exists to remove, paid in bytes instead."""
    base = doc([("A", 1)], updated_at="2026-09-22T10:00:00Z", updated_by="base")
    ours = doc([("A", 1)], updated_at="2026-09-22T15:00:00Z",
               updated_by="manager + E31 lane")
    theirs = doc([("A", 1)], updated_at="2026-09-22T11:00:00Z", updated_by="E31 lane")
    assert json.loads(M.merge(base, ours, theirs))["updated_by"] == "manager + E31 lane"


def test_the_author_union_splices_bytes_and_does_not_renormalise_escapes():
    """The same property the row merge has, applied to the header line: each
    retained name keeps its own side's exact escaping. A merge that re-serialised
    would pick one spelling of the em-dash and re-attribute the other."""
    base = doc([("A", 1)], updated_at="2026-09-22T10:00:00Z", updated_by="base")
    ours = doc([("A", 1)], updated_at="2026-09-22T15:00:00Z",
               updated_by="manager — literal")
    theirs = doc([("A", 1)], updated_at="2026-09-22T11:00:00Z", updated_by="X")
    # `doc` uses json.dumps (ensure_ascii default), so OURS carries the ESCAPED
    # form on the wire. The premise, asserted rather than assumed:
    assert "\\u2014" in ours and "—" not in ours
    out = M.merge(base, ours, theirs)
    assert "\\u2014" in out, "the kept name's escaping was renormalised"
    assert json.loads(out)["updated_by"] == "manager — literal + X"


def test_the_docstring_states_the_authorship_contract():
    """Criterion 3 again: a reader must be able to learn from the module that a
    merged header names both writers, or they will read the first name as the
    only one."""
    assert "NAME BOTH WRITERS" in M.__doc__
