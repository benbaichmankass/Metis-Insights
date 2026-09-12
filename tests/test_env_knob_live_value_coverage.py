"""Every live-flippable env knob either states its live value, or is counted.

`BL-20260901-DOC-FRESHNESS-CANNOT-SEE-A-FLIPPED-ENV-KNOB-AND-ITS-GUARD-COVERS-0-OF-67`:
`check_declared_values` catches *the doc asserts X and the source says Y*. It
cannot catch **silence** — and silence is the more common failure, because a doc
that says nothing reads exactly like a doc that is correct.

⚠️ THE CONTROLS GRADE THE THREE STATES AND THE DENOMINATOR, not the doc's
current coverage. A guard with no denominator reports green on 0 of 73.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "ci" / "check_canonical_doc_coherence.py"


def _load():
    spec = importlib.util.spec_from_file_location("_cdc_env", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DOC = """
| `FOO_MODE` | LIVE VALUE (verified 2026-09-12): `apply`. |
| `BAR_SECONDS` | Cadence knob. Default `300`. |
| `BAZ_MODE` / `BAZ_ACCOUNTS` | SHIPPED STATE: both unset. |
| `QUUX_FLAG` | Read it off `/proc/<MainPID>/environ` with `get-env`. |
"""


def test_the_three_states_are_distinct_and_reachable():
    m = _load()
    cov = m.env_knob_coverage(
        DOC, {"FOO_MODE", "BAR_SECONDS", "BAZ_MODE", "BAZ_ACCOUNTS",
              "QUUX_FLAG", "NEVER_MENTIONED"})
    per = cov["per_key"]
    assert per["FOO_MODE"] == m.ENV_CLAIMED
    assert per["QUUX_FLAG"] == m.ENV_CLAIMED, "a /proc read pointer is a live claim"
    # ⚠️ THE REAL SHAPE, verified against CLAUDE.md: 24 rows use
    # `| `A` / `B` |` with SEPARATE backticks. A fixture using one backtick span
    # would match nothing and this control would grade nothing.
    assert per["BAZ_MODE"] == per["BAZ_ACCOUNTS"] == m.ENV_CLAIMED, \
        "a multi-key row credits every key it names — an UPPER BOUND, documented"
    # ⚠️ THE ONE THAT MATTERS. A row that states only the CODE default says
    # nothing about the running process.
    assert per["BAR_SECONDS"] == m.ENV_SILENT, \
        "`Default `300`` is a fact about the code, NOT a claim about the live value"
    assert per["NEVER_MENTIONED"] == m.ENV_UNDOCUMENTED
    assert cov["counts"] == {m.ENV_CLAIMED: 4, m.ENV_SILENT: 1,
                             m.ENV_UNDOCUMENTED: 1}
    assert cov["population"] == 6, "the denominator ships with the counts"


def test_a_code_default_never_counts_as_coverage():
    """The bulk-by-construction trap, as its own control.

    Counting `Default `X`` would credit nearly every row in the table and report
    excellent coverage over a document that states no live value at all — the
    same defect the work store's stage histogram already paid for.
    """
    m = _load()
    # Names are >= 4 chars because that is what a real env knob looks like and
    # what `_ENV_ROW` requires; `K0` would match no row and grade
    # `undocumented`, which would pass this control for the wrong reason.
    names = [f"KNOB_{i:02d}" for i in range(20)]
    doc = "\n".join(f"| `{n}` | A knob. Default `300`. |" for n in names)
    cov = m.env_knob_coverage(doc, set(names))
    assert cov["counts"][m.ENV_CLAIMED] == 0, cov["counts"]
    assert cov["counts"][m.ENV_SILENT] == 20


def test_silent_and_undocumented_are_not_the_same_fact():
    """A knob with a row that says nothing, and a knob with no row at all, are
    different remedies: write the value, versus write the row."""
    m = _load()
    # ⚠️ The row text must not itself contain a claim phrase. An earlier draft
    # read "says nothing about the live value" and graded `claimed` — the
    # fixture asserted the opposite of what it demonstrated.
    cov = m.env_knob_coverage("| `HAS_ROW` | a knob, with no statement of what it is set to |",
                              {"HAS_ROW", "HAS_NO_ROW"})
    assert cov["per_key"]["HAS_ROW"] == m.ENV_SILENT
    assert cov["per_key"]["HAS_NO_ROW"] == m.ENV_UNDOCUMENTED
    assert m.ENV_SILENT != m.ENV_UNDOCUMENTED


def test_an_unreadable_denominator_is_None_and_FAILS(monkeypatch):
    """`we could not look` must not render as `0 of 0`, which reads clean."""
    m = _load()
    assert m.env_knob_coverage(DOC, set()) is None
    monkeypatch.setattr(m, "_allowed_keys", lambda: None)
    fails = m.check_env_knob_live_values()
    assert fails and "silently disabled" in fails[0], fails


def test_the_ratchet_fails_on_a_regression_and_only_on_one(monkeypatch, capsys):
    m = _load()
    real = m.env_knob_coverage()
    assert real, "positive control: the live tree must be gradeable"
    base = m.ENV_CLAIMED_BASELINE

    monkeypatch.setattr(m, "env_knob_coverage", lambda: dict(
        real, counts={m.ENV_CLAIMED: base - 1, m.ENV_SILENT: 0,
                      m.ENV_UNDOCUMENTED: 0}))
    fails = m.check_env_knob_live_values()
    assert fails and "REGRESSED" in fails[0], fails

    monkeypatch.setattr(m, "env_knob_coverage", lambda: dict(
        real, counts={m.ENV_CLAIMED: base, m.ENV_SILENT: 0,
                      m.ENV_UNDOCUMENTED: 0}))
    assert m.check_env_knob_live_values() == [], "the baseline itself passes"
    monkeypatch.setattr(m, "env_knob_coverage", lambda: dict(
        real, counts={m.ENV_CLAIMED: base + 5, m.ENV_SILENT: 0,
                      m.ENV_UNDOCUMENTED: 0}))
    assert m.check_env_knob_live_values() == [], "improving must never fail"


def test_the_denominator_is_PRINTED_on_every_run(capsys):
    """A guard that reports green on 0-of-73 without saying so is the state this
    check exists to end. The count must be on stdout even when it passes."""
    m = _load()
    m.check_env_knob_live_values()
    out = capsys.readouterr().out
    assert "env live-value coverage" in out, out
    assert "readable knob(s)" in out, out
    assert "silent" in out and "undocumented" in out, out


def test_the_denominator_is_the_read_surface_IMPORTED_not_restated():
    """`ALLOWED_KEYS` already answers 'which knobs can we read live?'. A second
    list here would be free to drift from the one the read path uses."""
    m = _load()
    keys = m._allowed_keys()
    assert keys and len(keys) > 50, f"expected the real ALLOWED_KEYS, got {keys}"
    src = (REPO / "scripts" / "ops" / "get_env.py").read_text(encoding="utf-8")
    for k in sorted(keys)[:5]:
        assert f'"{k}"' in src, f"{k} is not in {m.GET_ENV}"


def test_it_is_registered_in_CHECKS():
    m = _load()
    assert any(fn is m.check_env_knob_live_values for _n, fn in m.CHECKS), \
        "an unregistered check runs nowhere and reports nothing"
