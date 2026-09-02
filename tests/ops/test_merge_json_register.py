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

REGISTERS = [
    "docs/claude/health-review-backlog.json",
    "docs/claude/OPEN-ITEMS.json",
    "docs/claude/work/MANAGER-CHECKLIST.json",
    "docs/claude/work/SESSIONS.json",
    "docs/claude/work/OPEN-PRS.json",
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


def test_REFUSE_is_the_driver_exit_code(tmp_path):
    b, o, t = (tmp_path / n for n in ("b", "o", "t"))
    b.write_text(doc([("A", 1)]))
    o.write_text(doc([("A", 2)]))
    t.write_text(doc([("A", 3)]))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts/ops/merge_json_register.py"),
                        str(b), str(o), str(t)], capture_output=True)
    assert r.returncode == 1
    assert b"REFUSING" in r.stderr
    assert o.read_text() == doc([("A", 2)])  # left untouched for the human


# ------------------------------------------------- it never reformats bytes

@pytest.mark.parametrize("rel", REGISTERS)
def test_round_trip_is_byte_identical(rel):
    """Parse -> reassemble must reproduce the file EXACTLY.

    OPEN-ITEMS.json is NOT byte-reproducible through json.dumps (it mixes a
    literal em-dash with an escaped one). Splicing original byte spans is what
    makes it safe, and this is the proof.
    """
    text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    assert M.round_trip(text) == text


def test_untouched_rows_keep_their_exact_bytes():
    """Protects backlog_append.py::append_row's exact-serialisation contract:
    a merge must not re-attribute ~21k unrelated lines to whoever merged."""
    weird = '{"id": "A", "t": "an em-dash — literal", "u": "escaped \\u2014"}'
    mk = lambda extra: ('{\n  "items": [\n    %s%s\n  ]\n}\n'
                        % (weird, extra))
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
