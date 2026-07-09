"""BL-20260618-CLOSEDFLAT-MALFORMED-JSON — the merge-gate half.

Self-test for the ``json-notes-cap`` CI guard
(``scripts/ci/check_json_notes_cap.py``): the machine check that keeps the
``json.dumps(payload)[:N]`` char-slice-truncation footgun from returning after
the write-side was migrated to ``dump_capped``. A planted slice must fail; a
plain index / the ``dump_capped`` replacement / the seam / an allow-marked line
must pass; and — the live invariant — the real ``src/`` tree must currently be
clean (this is what caught the 5 leftover sites in ``order_monitor.py`` a weak
regex had missed).

Also re-asserts the property the guard protects: ``dump_capped`` over an
over-budget payload yields VALID JSON (never a half-token), unlike a char slice.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.ci.check_json_notes_cap import find_violations_in_source, scan_paths

_REPO_ROOT = Path(__file__).resolve().parents[1]


# --- guard self-test ---------------------------------------------------------

def test_char_slice_truncation_is_flagged():
    hits = find_violations_in_source('x = json.dumps(payload)[:500]\n', "src/runtime/order_monitor.py")
    assert len(hits) == 1 and "char-slice truncation" in hits[0][1]


def test_bare_dumps_slice_is_flagged():
    src = "from json import dumps\nx = dumps(d)[:100]\n"
    hits = find_violations_in_source(src, "src/x.py")
    assert len(hits) == 1 and hits[0][0] == 2


def test_plain_index_is_clean():
    assert find_violations_in_source('x = json.dumps(d)[0]\n', "src/x.py") == []


def test_dump_capped_is_clean():
    assert find_violations_in_source('x = dump_capped(d, 500)\n', "src/x.py") == []


def test_seam_is_exempt():
    assert find_violations_in_source('x = json.dumps(d)[:500]\n', "src/utils/json_notes.py") == []


def test_allow_marker_suppresses():
    src = 'x = json.dumps(d)[:500]  # json-cap-allow: reviewed\n'
    assert find_violations_in_source(src, "src/x.py") == []


def test_live_src_tree_is_clean():
    """The real invariant: no char-slice truncation of json.dumps in src/."""
    violations = scan_paths([_REPO_ROOT / "src"], _REPO_ROOT)
    assert violations == [], "json.dumps(...)[:N] truncation regressions:\n" + "\n".join(violations)


# --- the property the guard protects ----------------------------------------

def test_dump_capped_over_budget_is_valid_json():
    """The hard guarantee: valid JSON AND <= max_len, even for a pathological payload."""
    from src.utils.json_notes import dump_capped

    payload = {"closed_at": "2026-07-09T00:00:00Z", "closed_reason": "x" * 5000}
    out = dump_capped(payload, 500)
    assert len(out) <= 500
    json.loads(out)  # must NOT raise — the whole point of the migration


def test_dump_capped_preserves_protected_key_when_there_is_room():
    """Realistic reconciler-note shape (a few hundred chars): protected key survives."""
    from src.utils.json_notes import dump_capped

    payload = {"closed_at": "2026-07-09T00:00:00Z", "closed_reason": "x" * 400}
    out = dump_capped(payload, 500)
    assert len(out) <= 500
    assert json.loads(out)["closed_at"] == "2026-07-09T00:00:00Z"


def test_naive_char_slice_would_be_invalid():
    """Contrast: the old pattern produces invalid JSON (documents the bug)."""
    payload = {"closed_at": "2026-07-09T00:00:00Z", "closed_reason": "x" * 5000}
    truncated = json.dumps(payload, ensure_ascii=False)[:500]
    try:
        json.loads(truncated)
        raised = False
    except json.JSONDecodeError:
        raised = True
    assert raised, "char-slice of an over-budget payload should be invalid JSON"
