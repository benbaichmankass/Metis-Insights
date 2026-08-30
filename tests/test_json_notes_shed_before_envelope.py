"""`_shrink_dict` sheds ONE unprotected value at a time before the envelope.

ROOT CAUSE THIS PINS (measured 2026-08-30 on live trade 4905,
`bybit_portfolio`/ETHUSDT). The operator-flatten marker was stamped on the OPEN
row at 13:58:26; the reconciler closed the row at 14:01:17 and the marker was
GONE. `_close_trade_from_order_status` does merge (`notes =
_decode_notes(row.get("notes"))`), so the loss happened in the
`dump_capped(notes, 500)` that follows.

`_shrink_dict` only ever trims *strings*. The marker was a 5-key DICT, so it
could be neither shortened nor kept: it pushed the blob from 410 to 627 chars
and was then deleted wholesale by the minimal-envelope fallback it had itself
triggered. Reproduced exactly from sibling trade 4887's own notes below.

Two things are asserted here, and the second is what makes the first safe:
  * the ladder now sheds the largest unprotected value BEFORE the envelope, so
    an untrimmable dict costs its own key and not the whole blob;
  * the envelope is still reached when nothing unprotected remains.
"""
import json

from src.utils.json_notes import _DEFAULT_PROTECTED, dump_capped

# Live trade 4887 (`bybit_portfolio`/ETHUSDT), closed by the same reconciler
# path, with the close-time keys stripped back off — i.e. what the OPEN row
# carried. 166 chars.
OPEN_NOTES = {
    "trade_id": "c0fa25f7-7e94-4d9f-a369-2936efe3a99a",
    "is_dry": False,
    "confidence": 0.5266,
    "signal_logic": "",
    "entry_exec_time": "2026-08-21T12:12:29.820000+00:00",
}
CLOSE_UPDATE = {
    "closed_at": "2026-08-30T14:01:17.564681+00:00",
    "closed_by": "monitor_reconciler",
    "closed_reason": "reconciler — Bybit reports order filled and position flat",
    "exit_price_source": "bybit_closed_pnl",
    "exit_reason_source": "unresolved",
}
MARKER = {
    "at": "2026-08-30T13:58:26.123456+00:00",
    "reason": "switching bybit_portfolio to hedge position mode",
    "by": "claude/system-review",
    "account_id": "bybit_portfolio",
    "symbol": "ETHUSDT",
}


def _blob(**extra):
    b = dict(OPEN_NOTES)
    b.update(CLOSE_UPDATE)
    b.update(extra)
    return b


def test_control_the_same_blob_without_the_marker_never_truncates():
    """POSITIVE CONTROL / denominator: the marker is what overflows the cap.

    Without it the payload is 410 chars and every key survives — which is what
    all six `bybit_portfolio` siblings actually show on the live journal. If
    this ever fails, the reproduction below is measuring something else.
    """
    raw = json.dumps(_blob())
    assert len(raw) < 500, f"control payload should fit: {len(raw)}"
    out = json.loads(dump_capped(_blob(), 500))
    assert "_truncated" not in out
    assert set(out) == set(_blob())


def test_untrimmable_dict_costs_its_own_key_not_the_whole_blob():
    """The regression: a big unprotected dict is shed alone."""
    blob = _blob(operator_flatten_intent_detail=MARKER)
    assert len(json.dumps(blob)) > 500, "precondition: must exceed the cap"
    out = json.loads(dump_capped(blob, 500))
    assert "operator_flatten_intent_detail" not in out, "the detail is shed"
    # Everything else survives — this is the whole point of the new rung.
    for k in ("confidence", "is_dry", "entry_exec_time", "trade_id"):
        assert k in out, f"{k} must NOT be collateral damage"
    assert out["_truncated"] is True


def test_a_protected_flag_survives_alongside_the_shed_detail():
    """flag + detail split: the FACT survives, the prose does not."""
    blob = _blob(operator_flatten_intent=True,
                 operator_flatten_intent_detail=MARKER)
    out = json.loads(dump_capped(blob, 500))
    assert out.get("operator_flatten_intent") is True
    assert "operator_flatten_intent_detail" not in out


def test_flag_survives_even_when_the_envelope_is_reached():
    """Belt-and-braces: at a cap tight enough to force the envelope the flag is
    still there, because it is protected AND small."""
    blob = _blob(operator_flatten_intent=True,
                 operator_flatten_intent_detail=MARKER)
    out = json.loads(dump_capped(blob, 360))
    assert out.get("operator_flatten_intent") is True
    assert out["_truncated"] is True
    # Envelope reached: nothing unprotected left.
    assert set(out) - {"_truncated"} <= set(_DEFAULT_PROTECTED)


def test_envelope_is_still_reached_when_nothing_unprotected_remains():
    """The old fallback is NOT removed — only deferred."""
    blob = {k: CLOSE_UPDATE[k] for k in CLOSE_UPDATE}
    blob["closed_reason"] = "x" * 900  # protected, so untrimmable
    out = json.loads(dump_capped(blob, 200))
    assert out["_truncated"] is True


# The protected set as it stood when trade 4905 closed — `operator_flatten_intent`
# was NOT in it. Pinned as a literal so the reproduction below keeps measuring
# the shipped behaviour after the set grows again.
_PROTECTED_AT_4905 = tuple(
    k for k in _DEFAULT_PROTECTED if k != "operator_flatten_intent"
)


#: The 10 keys live trade 4905 actually stored. Three were written AFTER the
#: `dump_capped(notes, 500)` under study — `close_exec_type` (order_monitor
#: re-decodes the capped blob and re-dumps to add it) and `pnl_source` +
#: `contract_value_usd` (the later local-pnl sweep) — so the dump itself
#: produced the other seven.
_LIVE_4905_KEYS = {
    "_truncated", "close_exec_type", "closed_at", "closed_by", "closed_reason",
    "contract_value_usd", "exit_price_source", "exit_reason_source",
    "pnl_source", "trade_id",
}
_WRITTEN_AFTER_THE_DUMP = {"close_exec_type", "pnl_source", "contract_value_usd"}


def test_the_4905_key_set_is_the_pre_fix_minimal_envelope():
    """ARITHMETIC CHECK on the MECHANISM, not a re-read of the row.

    The pre-fix `_shrink_dict` had exactly one fallback: keep the protected keys
    present on the object, plus `_truncated`. That formula is evaluated here
    directly, because the fixed ladder can no longer produce it — which is the
    point. If the two sets match, the minimal envelope is the mechanism; a
    stale-row race would have left the entry-time keys in place, and they are
    absent.
    """
    blob = _blob(operator_flatten_intent=MARKER)  # the shape that SHIPPED
    envelope = {k for k in blob if k in _PROTECTED_AT_4905} | {"_truncated"}
    assert envelope == _LIVE_4905_KEYS - _WRITTEN_AFTER_THE_DUMP
    assert "operator_flatten_intent" not in envelope, (
        "an unprotected dict is dropped whole — this is the bug"
    )
    # And the trigger: WITH the marker the payload exceeds the cap, WITHOUT it
    # it does not. The marker caused the overflow that then deleted it.
    assert len(json.dumps(blob)) > 500 >= len(json.dumps(_blob()))


def test_the_fixed_ladder_no_longer_produces_that_envelope():
    """The fix, stated as the difference from the row above.

    Same blob, same (pre-fix) protected set, new ladder: the marker is shed
    alone and the entry-time keys the live row lost are retained.
    """
    blob = _blob(operator_flatten_intent=MARKER)
    out = json.loads(dump_capped(blob, 500, protected=_PROTECTED_AT_4905))
    assert set(out) != _LIVE_4905_KEYS - _WRITTEN_AFTER_THE_DUMP
    for k in ("confidence", "is_dry", "entry_exec_time"):
        assert k in out, f"{k} was collateral damage before the fix"


def test_protecting_the_big_dict_alone_would_have_been_WORSE():
    """Why the fix is a flag/detail SPLIT and not just a protected key.

    Protect the 217-char marker as-is and the protected set itself overflows
    500, so `_shrink_dict` falls all the way to the barest `{"_truncated":true}`
    marker — losing `closed_at`, which `closed_flat_invariant` reads via
    `json_extract` and which is the whole reason the protected set exists.

    A protected key must be SMALL. This is the control for that claim.
    """
    blob = _blob(operator_flatten_intent=MARKER)
    out = json.loads(dump_capped(blob, 500))
    assert out == {"_truncated": True}, (
        "protecting a large value moves the overflow INTO the envelope"
    )
    # And the shipped split does not have that problem:
    split = json.loads(dump_capped(
        _blob(operator_flatten_intent=True,
              operator_flatten_intent_detail=MARKER), 500))
    assert split["closed_at"] == CLOSE_UPDATE["closed_at"]
    assert split["operator_flatten_intent"] is True
