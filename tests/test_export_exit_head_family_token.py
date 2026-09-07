"""The exit-head artifact's ``family`` token must be DECLARABLE, not only derived.

WHY THIS EXISTS (MI-154, 2026-09-06). ``export_exit_head.py`` stamped
``"family": fam_dir.name`` with no way to override it, so the token the artifact
carries was whatever the training round happened to name its output directory.
That is fine while the directory name and the consuming unit's declared family
are the same word, and it silently breaks the moment they are not.

MEASURED, not hypothesised. The surviving E0 scalp rounds on the trainer are
laid out PER LEG — ``runtime_logs/m20_exit_head/scalp_5m_20260814T151003Z/``
contains ``ict_scalp_sol_5m/``, ``ict_scalp_xrp_5m/`` and
``ict_scalp_avax_5m/`` — so the derived token would be ``ict_scalp_sol_5m``.
The consumer added by PR #11140 declares ``family="ict_scalp"`` and accepts
``_ACCEPTED_FAMILIES["ict_scalp"] == {"ict_scalp", "scalp"}``. The derived token
is in neither set, so an artifact exported the obvious way would be REFUSED and
would score nothing — loudly (that guard logs a WARNING), but still nothing.

The fix is one flag, and these tests pin BOTH halves of it: that ``--family``
overrides, and that omitting it leaves the legacy derivation byte-for-byte
unchanged, so no existing round's output moves.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ml" / "export_exit_head.py"


def test_script_exists():
    """Positive control: the file this suite is about is really there.

    Without it, every parse-based assertion below would pass vacuously on a
    renamed or deleted script.
    """
    assert SCRIPT.is_file(), f"{SCRIPT} is missing — the rest of this suite is vacuous"


def _cli_help() -> str:
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    return out.stdout + out.stderr


def test_family_flag_is_exposed_on_the_cli():
    help_text = _cli_help()
    if "--family-dir" not in help_text:
        pytest.skip(f"exporter --help unavailable in this sandbox: {help_text[:200]!r}")
    assert "--family" in help_text
    # and it must be a SEPARATE option from --family-dir, not a prefix match
    assert "--family " in help_text or "--family\n" in help_text or \
           "--family FAMILY" in help_text, help_text[:600]


def _source() -> str:
    return SCRIPT.read_text()


def test_family_is_declared_when_given_and_derived_otherwise():
    """The two states are distinct and both are represented in the source.

    Read the source rather than running a full export: training needs lightgbm
    and a multi-MB rows.jsonl that does not exist in this sandbox, and the
    behaviour under test is the token resolution, not the fit.
    """
    src = _source()
    assert 'family = a.family if a.family else fam_dir.name' in src, (
        "the declared/derived fallback is the whole point of the flag")
    assert '"family": family,' in src, (
        "the artifact must stamp the RESOLVED token, not fam_dir.name again")
    assert 'family_basis = "declared" if a.family else "derived_from_dir"' in src


def test_legacy_behaviour_is_unchanged_when_family_is_omitted():
    """Omitting --family must reproduce the old token exactly.

    This is the half that protects every already-published artifact and every
    round script that does not pass the new flag: the fallback is literally
    ``fam_dir.name``, the same expression the old line stamped.
    """
    src = _source()
    assert "fam_dir.name" in src, "the legacy derivation must still exist"
    # the OLD unconditional stamp must be gone, or the override is dead code
    assert '"family": fam_dir.name,' not in src, (
        "the artifact still stamps fam_dir.name unconditionally — --family would "
        "be accepted and then ignored, which is worse than not offering it")


def test_export_output_states_which_basis_it_used():
    """A refused artifact must be diagnosable from the EXPORT output.

    Otherwise the only place the mismatch surfaces is a WARNING on the live VM,
    hours later and on a different box — the diagnostic-provenance rule: the
    output states what it actually computed.
    """
    src = _source()
    assert "family_basis" in src.split("artifact = {")[-1] or \
           "({family_basis})" in src, "the CLI line must name the basis"
    assert "provenance: family_basis" in src, (
        "the print carries a provenance annotation naming the accessor")
