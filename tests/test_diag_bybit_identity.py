"""BL-20260821-NO-BYBIT-ACCOUNT-IDENTITY-READ-SURFACE — workplan 0.6.

THE GATING QUESTION for T.2 (pairs hedge mode): `bybit_portfolio` is also demo.
If it shares a demo UID with `bybit_1`, switching a symbol's position mode on
`bybit_1` hits BOTH books — so the design's "scope it per-symbol on bybit_1"
safety argument would not hold.

`config/accounts.yaml` cannot settle it. Distinct key ENV VARS prove two key
PAIRS exist; they do not prove two ACCOUNTS. Two keys can be issued under one
UID, and a sub-account key carries its own key id while sharing its parent's
book. So the question was answerable only by asking the venue, and nothing did —
which is why a prior handoff carried it as "operator-blocked" when it is a
missing Tier-1 read.

⚠️ THE ASSERTION THAT CARRIES THE WEIGHT is `test_a_blank_uid_is_not_an_identity`.
Bybit hands back an EMPTY STRING for a field it declines to populate. Two
accounts both reporting `""` would compare EQUAL and be grouped as sharing an
identity — manufacturing precisely the wrong answer to the question this route
exists to settle, and manufacturing it in the DANGEROUS direction (it would
argue against a change that is in fact safe, or for one that is not).
"""
from __future__ import annotations

import pytest

from src.web.api.routers import diag


class _Client:
    def __init__(self, result=None, raises=False):
        self._result, self._raises = result, raises

    def get_api_key_information(self):
        if self._raises:
            raise RuntimeError("api down")
        return {"result": self._result}


def test_a_clean_read_reports_both_ids():
    r = diag._bybit_identity(_Client({"userID": "111", "parentUid": "111"}))
    assert r["read_state"] == "identity_read"
    assert r["user_id"] == "111" and r["parent_uid"] == "111"
    assert r["is_sub_account"] is False


def test_a_sub_account_is_flagged():
    r = diag._bybit_identity(_Client({"userID": "222", "parentUid": "111"}))
    assert r["read_state"] == "identity_read"
    assert r["is_sub_account"] is True


@pytest.mark.parametrize("blank", ["", "  ", "0", 0, None])
def test_a_blank_uid_is_not_an_identity(blank):
    """`""` / `0` must normalise to None, NEVER be reported as a UID.

    Two accounts both reporting a falsy UID would compare equal and group as
    sharing an identity. That is the wrong answer to the only question this
    route exists to answer.
    """
    assert diag._clean_uid(blank) is None
    r = diag._bybit_identity(_Client({"userID": blank, "parentUid": blank}))
    assert r["read_state"] == "could_not_look"
    assert r["user_id"] is None and r["parent_uid"] is None


def test_a_raising_client_is_could_not_look():
    r = diag._bybit_identity(_Client(raises=True))
    assert r["read_state"] == "could_not_look"
    assert r["user_id"] is None
    assert "RuntimeError" in (r["error"] or "")


def test_a_partial_read_keeps_the_half_the_venue_gave_us():
    """One field blank must not discard the UID that WAS returned."""
    r = diag._bybit_identity(_Client({"userID": "333", "parentUid": ""}))
    assert r["read_state"] == "identity_read"
    assert r["user_id"] == "333" and r["parent_uid"] is None
    assert r["is_sub_account"] is None, "must not be guessed from one half"


def _summary(rows):
    """Re-run the route's grouping over synthetic rows."""
    uid_groups, unread = {}, []
    for r in rows:
        if r["exchange"] != "bybit":
            continue
        ident = r.get("identity") or {}
        if ident.get("read_state") != diag._IDENTITY_READ:
            unread.append(r["account_id"])
            continue
        if ident.get("user_id"):
            uid_groups.setdefault(ident["user_id"], []).append(r["account_id"])
    return {"uid_groups": uid_groups,
            "shared_uid_groups": {u: a for u, a in uid_groups.items() if len(a) > 1},
            "unread_bybit_accounts": unread}


def _row(aid, state, uid, exchange="bybit"):
    return {"account_id": aid, "exchange": exchange,
            "identity": {"read_state": state, "user_id": uid}}


def test_shared_uid_is_reported_as_shared():
    """The answer T.2 STEP 0 needs, stated from a read rather than inferred."""
    s = _summary([_row("bybit_1", diag._IDENTITY_READ, "111"),
                  _row("bybit_portfolio", diag._IDENTITY_READ, "111")])
    assert s["shared_uid_groups"] == {"111": ["bybit_1", "bybit_portfolio"]}


def test_distinct_uids_are_not_reported_as_shared():
    s = _summary([_row("bybit_1", diag._IDENTITY_READ, "111"),
                  _row("bybit_portfolio", diag._IDENTITY_READ, "222")])
    assert s["shared_uid_groups"] == {}
    assert len(s["uid_groups"]) == 2


def test_an_unread_account_is_in_NEITHER_group():
    """It cannot be shown to share OR not share a UID — so it counts as neither.

    Placing it in either direction would manufacture the answer;
    `unread_bybit_accounts` is the denominator that keeps a partial grouping
    from reading as a complete one.
    """
    s = _summary([_row("bybit_1", diag._IDENTITY_READ, "111"),
                  _row("bybit_2", diag._IDENTITY_COULD_NOT_LOOK, None)])
    assert s["unread_bybit_accounts"] == ["bybit_2"]
    assert s["shared_uid_groups"] == {}
    assert s["uid_groups"] == {"111": ["bybit_1"]}
