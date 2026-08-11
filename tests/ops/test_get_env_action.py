"""`get-env` — the READ half of `set-env` (Tier-1, read-only).

`get_env.py --self-test` covers the pure classifiers, but the part that can
actually mislead an operator is `build_report`: it joins TWO sources (the
running process vs the unit's declared EnvironmentFiles) and asserts whether
they agree. These tests pin that join, with the sources faked so no VM is
needed.

The contract under test (BL-20260810-CONVICTION-SIZING-APPLY-LIVE-VS-DOC):
  * four states per source, never collapsed — `set` / `set_empty` / `unset` /
    `unreadable`;
  * `set_empty` is NOT `unset` — for an allowlist var, empty is the WIDEST
    setting, so collapsing them would invert the operator's reading;
  * agreement is only asserted when BOTH sides were readable — an unreadable
    side yields `undetermined`, never `agree`;
  * a secret-NAMED key never yields its value, on either side.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GET_ENV = REPO / "scripts" / "ops" / "get_env.py"


def _load():
    spec = importlib.util.spec_from_file_location("get_env_under_test", GET_ENV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ge():
    return _load()


def _fake_sources(ge, monkeypatch, *, process, declared,
                  process_why=None, declared_why=None):
    """Fake both readers. `None` on a side means 'we could not look'."""
    monkeypatch.setattr(ge, "read_process_env", lambda unit: (process, process_why))
    monkeypatch.setattr(ge, "read_file_env",
                        lambda unit: (declared, ["/fake/.env"], declared_why))


def _entry(report, key):
    return next(e for e in report["entries"] if e["key"] == key)


# --- the state vocabulary -------------------------------------------------


def test_empty_value_is_set_empty_on_both_sides_not_unset(ge, monkeypatch):
    """The distinction the tool exists for: an empty allowlist var is the
    WIDEST scope, not an absent setting."""
    _fake_sources(ge, monkeypatch,
                  process={"CONVICTION_SIZING_ACCOUNTS": ""},
                  declared={"CONVICTION_SIZING_ACCOUNTS": ""})
    e = _entry(ge.build_report("u", ["CONVICTION_SIZING_ACCOUNTS"]),
               "CONVICTION_SIZING_ACCOUNTS")
    assert e["process"]["state"] == ge.SET_EMPTY
    assert e["declared"]["state"] == ge.SET_EMPTY
    assert e["process"]["state"] != ge.UNSET
    assert e["agreement"] == "agree"


def test_absent_key_is_unset_not_unreadable(ge, monkeypatch):
    _fake_sources(ge, monkeypatch, process={"OTHER": "x"}, declared={"OTHER": "x"})
    e = _entry(ge.build_report("u", ["FLIP_POLICY"]), "FLIP_POLICY")
    assert e["process"]["state"] == ge.UNSET
    assert e["declared"]["state"] == ge.UNSET


def test_unreadable_process_is_never_reported_as_unset(ge, monkeypatch):
    """'We could not look' and 'it is not set' are opposite claims."""
    _fake_sources(ge, monkeypatch, process=None, declared={"FLIP_POLICY": "hold"},
                  process_why="/proc/123/environ permission denied")
    e = _entry(ge.build_report("u", ["FLIP_POLICY"]), "FLIP_POLICY")
    assert e["process"]["state"] == ge.UNREADABLE
    assert e["process"]["state"] != ge.UNSET
    assert "permission denied" in e["process"]["unreadable_reason"]


# --- the join -------------------------------------------------------------


def test_file_edited_without_restart_reads_as_pending_restart(ge, monkeypatch):
    """The condition that is otherwise invisible: .env changed, service did not
    re-read it. The PROCESS side is the one that governs behaviour."""
    _fake_sources(ge, monkeypatch,
                  process={"CONVICTION_SIZING_MODE": "annotate"},
                  declared={"CONVICTION_SIZING_MODE": "apply"})
    e = _entry(ge.build_report("u", ["CONVICTION_SIZING_MODE"]),
               "CONVICTION_SIZING_MODE")
    assert e["agreement"] == "pending_restart"
    assert e["process"]["value"] == "annotate"   # what is actually running
    assert e["declared"]["value"] == "apply"     # what the next restart takes


def test_agreement_is_undetermined_when_a_side_could_not_be_read(ge, monkeypatch):
    """A failed measurement must not be able to manufacture either verdict."""
    _fake_sources(ge, monkeypatch, process={"FLIP_POLICY": "hold"}, declared=None,
                  declared_why="unit declares no EnvironmentFiles")
    e = _entry(ge.build_report("u", ["FLIP_POLICY"]), "FLIP_POLICY")
    assert e["agreement"] == "undetermined"
    assert e["agreement"] != "agree"


def test_set_empty_versus_unset_across_sides_is_a_difference(ge, monkeypatch):
    _fake_sources(ge, monkeypatch,
                  process={"NETTING_ATTRIBUTION_ACCOUNTS": ""}, declared={})
    e = _entry(ge.build_report("u", ["NETTING_ATTRIBUTION_ACCOUNTS"]),
               "NETTING_ATTRIBUTION_ACCOUNTS")
    assert e["agreement"] == "pending_restart"


# --- secrets --------------------------------------------------------------


def test_secret_named_key_never_yields_its_value_on_either_side(ge, monkeypatch):
    """This action's stdout is commented onto a PUBLIC issue."""
    _fake_sources(ge, monkeypatch,
                  process={"DASHBOARD_API_TOKEN": "s3cret-value"},
                  declared={"DASHBOARD_API_TOKEN": "s3cret-value"})
    report = ge.build_report("u", ["DASHBOARD_API_TOKEN"])
    e = _entry(report, "DASHBOARD_API_TOKEN")
    assert e["secret_name"] is True
    for side in ("process", "declared"):
        assert "s3cret-value" not in str(e[side]["value"])
        assert e[side]["value"].startswith("sha256:")
    # And it must not leak through the human-readable rendering either.
    assert "s3cret-value" not in ge.render_text(report)


def test_secret_presence_is_still_answerable(ge, monkeypatch):
    """The question that actually gets asked (BL-20260705): is it SET at all?"""
    _fake_sources(ge, monkeypatch, process={}, declared={})
    e = _entry(ge.build_report("u", ["DASHBOARD_API_TOKEN"]), "DASHBOARD_API_TOKEN")
    assert e["process"]["state"] == ge.UNSET


# --- the allowlist is the contract ----------------------------------------


def test_the_motivating_key_is_allowlisted(ge):
    assert "CONVICTION_SIZING_ACCOUNTS" in ge.ALLOWED_KEYS


def test_every_allowlisted_secret_named_key_is_fingerprint_only(ge):
    """A key may be added to ALLOWED_KEYS only if its value is safe to publish;
    the secret-name net is the belt-and-braces on that judgement. Assert the net
    actually engages for every key it should."""
    for key in ge.ALLOWED_KEYS:
        if ge.SECRET_NAME.search(key):
            rendered = ge._render(key, ge.SET, "some-real-value")
            assert rendered.startswith("sha256:"), key
            assert "some-real-value" not in rendered, key


def test_self_test_passes(ge):
    """The script's own embedded self-test, run under pytest so a regression in
    it is caught by CI rather than only by a human remembering to run it."""
    assert ge._self_test() == 0


# --- `declared` must include the unit's INLINE Environment=, not just files ---
#
# Regression for the first real run (issue #8755): reading only
# EnvironmentFiles made two inline-pinned keys report `declared: <unset>`
# against a live process holding a value, and the tool announced a pending
# restart that did not exist.


def test_unit_environment_parsing_handles_systemd_quoting(ge):
    parsed = ge._parse_unit_environment('A=1 B=two C="three four" D=')
    assert parsed["A"] == "1"
    assert parsed["B"] == "two"
    assert parsed["C"] == "three four", "quoted value with a space was split"
    assert parsed["D"] == "", "an explicitly-empty inline value is set_empty, not absent"


def test_unit_environment_parsing_never_raises_on_bad_quoting(ge):
    # An unbalanced quote must degrade, not explode — this runs on the live VM.
    parsed = ge._parse_unit_environment('A=1 B="unterminated')
    assert parsed.get("A") == "1"


def test_inline_pinned_key_is_not_reported_as_a_pending_restart(ge, monkeypatch):
    """The exact #8755 false positive: process holds 60 from the unit's
    `Environment=`, the .env says nothing, and that is AGREEMENT, not drift."""
    monkeypatch.setattr(ge, "_systemctl_show", lambda unit, prop: {
        "EnvironmentFiles": "/fake/.env (ignore_errors=no)",
        "Environment": "TICK_INTERVAL_SECONDS=60",
        "MainPID": "0",
    }.get(prop))
    monkeypatch.setattr(ge, "_parse_env_file", lambda path: {})  # .env lacks it
    declared, _paths, _why = ge.read_file_env("u")
    assert declared == {"TICK_INTERVAL_SECONDS": "60"}

    monkeypatch.setattr(ge, "read_process_env",
                        lambda unit: ({"TICK_INTERVAL_SECONDS": "60"}, None))
    monkeypatch.setattr(ge, "read_file_env", lambda unit: (declared, ["/fake/.env"], None))
    e = _entry(ge.build_report("u", ["TICK_INTERVAL_SECONDS"]), "TICK_INTERVAL_SECONDS")
    assert e["agreement"] == "agree", "inline-pinned key still reads as pending_restart"
    assert e["declared_source"] == "unit_environment"


def test_env_file_and_inline_are_both_merged(ge, monkeypatch):
    monkeypatch.setattr(ge, "_systemctl_show", lambda unit, prop: {
        "EnvironmentFiles": "/fake/.env (ignore_errors=no)",
        "Environment": "FROM_UNIT=a",
    }.get(prop))
    monkeypatch.setattr(ge, "_parse_env_file", lambda path: {"FROM_FILE": "b"})
    declared, _p, _w = ge.read_file_env("u")
    assert declared == {"FROM_FILE": "b", "FROM_UNIT": "a"}
    src = ge.declared_sources("u")
    assert src["FROM_UNIT"] == "unit_environment"
    assert src["FROM_FILE"] == "env_file"


def test_a_key_defined_in_both_with_different_values_is_flagged_not_resolved(ge, monkeypatch):
    """systemd resolves by DIRECTIVE ORDER, which `systemctl show` does not
    expose. Picking a winner would be a guess dressed as a fact."""
    monkeypatch.setattr(ge, "_systemctl_show", lambda unit, prop: {
        "EnvironmentFiles": "/fake/.env (ignore_errors=no)",
        "Environment": "K=from_unit",
    }.get(prop))
    monkeypatch.setattr(ge, "_parse_env_file", lambda path: {"K": "from_file"})
    assert ge.declared_sources("u")["K"] == "both_conflict"


def test_agreeing_definitions_are_not_flagged_as_conflict(ge, monkeypatch):
    monkeypatch.setattr(ge, "_systemctl_show", lambda unit, prop: {
        "EnvironmentFiles": "/fake/.env (ignore_errors=no)",
        "Environment": "K=same",
    }.get(prop))
    monkeypatch.setattr(ge, "_parse_env_file", lambda path: {"K": "same"})
    assert ge.declared_sources("u")["K"] == "both_agree"


def test_unit_with_neither_source_is_unreadable_not_empty(ge, monkeypatch):
    """No Environment= and no EnvironmentFiles is 'nothing declared here' —
    which must not masquerade as 'every key is unset'."""
    monkeypatch.setattr(ge, "_systemctl_show", lambda unit, prop: "")
    declared, _p, why = ge.read_file_env("u")
    assert declared is None
    assert why and "no Environment" in why
