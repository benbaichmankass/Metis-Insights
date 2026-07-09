"""BL-20260628-PROP-ISPROP-PREDICATE-DRIFT — the merge-gate half.

Two layers on top of ``test_prop_identity.py`` (which pins the predicate's
semantics):

1. Self-test for the ``prop-identity`` CI guard
   (``scripts/ci/check_prop_identity_single_home.py``): a planted
   ``account_class == "prop"`` classifier anywhere in ``src/`` and a planted
   ``exchange == "breakout"`` classifier inside ``src/prop/`` must fail; the
   seam, connector-dispatch in ``src/core``/executor, a ruleset-name compare,
   and an allow-marked line must pass; and — the live invariant — the real
   ``src/`` tree must currently be clean.

2. A cross-site delegation check: the three sites that used to hold divergent
   copies still route through ``is_prop_account`` (static source assertion, so
   a future re-inline is caught even if it happened to classify identically).
"""
from __future__ import annotations

from pathlib import Path

from scripts.ci.check_prop_identity_single_home import (
    find_violations_in_source,
    scan_paths,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


# --- guard self-test ---------------------------------------------------------

def test_account_class_classifier_flagged_anywhere():
    src = 'if str(a.get("account_class", "")).lower() == "prop":\n    pass\n'
    hits = find_violations_in_source(src, "src/web/api/routers/x.py")
    assert len(hits) == 1 and "prop classifier" in hits[0][1]


def test_type_classifier_flagged_anywhere():
    hits = find_violations_in_source('if account.type == "prop":\n    pass\n', "src/core/x.py")
    assert len(hits) == 1


def test_exchange_breakout_flagged_inside_prop_pkg():
    src = 'if a.get("exchange") == "breakout":\n    pass\n'
    hits = find_violations_in_source(src, "src/prop/foo.py")
    assert len(hits) == 1 and "breakout" in hits[0][1]


def test_connector_dispatch_outside_prop_pkg_is_clean():
    # exchange == "breakout" in the coordinator/executor is a broker-integration
    # switch, not a funding classifier — must NOT be flagged.
    src = 'if (account.exchange or "").lower() == "breakout":\n    pass\n'
    assert find_violations_in_source(src, "src/core/coordinator.py") == []
    assert find_violations_in_source(src, "src/units/accounts/execute.py") == []


def test_ruleset_name_compare_is_clean():
    # ``ruleset == "breakout"`` names the prop ruleset, not an account field.
    src = 'if u.ruleset.ruleset == "breakout":\n    pass\n'
    assert find_violations_in_source(src, "src/prop/account_rulesets.py") == []


def test_seam_is_exempt():
    src = 'if str(account.get("account_class", "")).lower() == "prop":\n    pass\n'
    assert find_violations_in_source(src, "src/prop/prop_identity.py") == []


def test_allow_marker_suppresses():
    src = 'if a.account_class == "prop":  # prop-identity-allow: legacy shim\n    pass\n'
    assert find_violations_in_source(src, "src/core/x.py") == []


def test_live_src_tree_is_clean():
    """The real invariant: no out-of-seam prop classifier in src/."""
    violations = scan_paths([_REPO_ROOT / "src"], _REPO_ROOT)
    assert violations == [], "prop-identity drift:\n" + "\n".join(violations)


# --- cross-site delegation ---------------------------------------------------

def test_all_former_copies_delegate_to_is_prop_account():
    """The 3 sites that once held divergent copies still call the seam."""
    for rel in (
        "src/prop/account_rulesets.py",
        "src/prop/telegram_report_handler.py",
        "src/prop/prop_journal.py",
    ):
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "is_prop_account(" in text, f"{rel} no longer delegates to is_prop_account"
