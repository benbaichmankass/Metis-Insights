"""The disjoint-OCA stop-over-cover must reach an OPERATOR surface.

MEASURED GAP (2026-08-25). The DETECTION has been correct since
BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS -- the sweep counts
`over_covered` and emits a `logger.error`. But `logger.error` writes to the
systemd journal and nothing else: it never reaches `outcomes.jsonl`, which is
what feeds Telegram, the `/api/bot/notifications` banner, and
`/api/bot/logs?level=error`.

Measured live, both halves in one session:
  - `/api/bot/logs?level=error&limit=1000` -> 388 rows spanning
    2026-08-20T07:01Z-2026-08-25T09:28Z. Rows mentioning over-cover: **0**.
  - `/api/diag/ib_open_orders?account_id=ib_paper` -> MHG 29-lot position with
    TWO disjoint OCA groups (`oca-protect-416`, `oca-protect-432`), each
    holding a 29-lot STP and a 29-lot LMT. 58 of stop against 29 of position
    = **200%**.

So the condition was live, correctly detected, and invisible on every surface
a human reads. OCA cancels only WITHIN a group, so one stop firing flattens the
position and leaves the other group resting to sell 29 more into a naked SHORT.
"""
from __future__ import annotations

import pytest

import src.runtime.order_monitor as om


@pytest.fixture()
def latched(tmp_path, monkeypatch):
    monkeypatch.setattr(om, "_alert_state_path",
                        lambda kind: tmp_path / f"{kind}_alert_state.json")
    return tmp_path / "stop_over_cover_alert_state.json"


@pytest.fixture()
def pages(monkeypatch):
    """Capture what reaches `outcomes.report`, the operator-facing path."""
    sent = []
    import src.runtime.outcomes as outcomes
    monkeypatch.setattr(outcomes, "report",
                        lambda *a, **k: sent.append((a, k)))
    return sent


def _emit(symbol="MHG"):
    return om._emit_stop_over_cover_alert(
        account_id="ib_paper", symbol=symbol, size=29.0, stop_qty=58.0,
        oca_groups={"oca-protect-432": 29.0, "oca-protect-416": 29.0},
    )


def test_it_reaches_the_operator_path_at_critical(latched, pages):
    """A journal line is not an operator surface. CRITICAL is what Telegrams."""
    assert _emit() is True
    assert len(pages) == 1, "the page must go through outcomes.report"
    args, kwargs = pages[0]
    from src.runtime.outcomes import Level
    assert kwargs["level"] is Level.CRITICAL
    assert args[0] == "ib_stop_over_cover"


def test_the_page_states_the_measurement_and_the_consequence(latched, pages):
    """A page naming neither the numbers nor the failure mode cannot be acted on."""
    _emit()
    reason = pages[0][1]["reason"]
    assert "29.0" in reason and "58.0" in reason and "200%" in reason
    assert "naked SHORT" in reason, "the consequence is the reason it is CRITICAL"
    # Both group names, sorted, so the operator can go cancel one by id.
    assert "oca-protect-416" in reason and "oca-protect-432" in reason
    kw = pages[0][1]
    assert kw["over_cover_pct"] == pytest.approx(200.0)
    assert kw["oca_groups"] == ["oca-protect-416", "oca-protect-432"]


def test_cooldown_is_durable_across_a_simulated_restart(latched, pages):
    """The target-naked latch failed EXACTLY here (per-process monotonic), and a
    copy-pasted latch would have failed the same way. Same gate, same proof."""
    assert _emit() is True
    assert _emit() is False, "inside 6h -> suppressed"
    for name in dir(om):
        obj = getattr(om, name)
        if isinstance(obj, dict) and "OVER_COVER" in name:
            obj.clear()
    assert _emit() is False, "a restart must NOT re-arm the page"
    assert len(pages) == 1


def test_a_different_symbol_is_not_suppressed(latched, pages):
    """Keyed per (account, symbol): MHG paging must not mute MES."""
    assert _emit("MHG") is True
    assert _emit("MES") is True
    assert len(pages) == 2


def test_unreadable_latch_alerts_rather_than_suppressing(latched, pages):
    assert _emit() is True
    latched.write_text("{ not json", encoding="utf-8")
    assert _emit() is True, (
        "'we could not look' must never be read as 'already paged' on a "
        "money-at-risk page"
    )


def test_a_failing_report_never_aborts_the_sweep(latched, monkeypatch):
    """This runs inside the broker sweep; an alert failure must not propagate."""
    import src.runtime.outcomes as outcomes

    def boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(outcomes, "report", boom)
    assert _emit() is True, "the sweep continues and the cooldown still commits"


def test_the_state_file_is_readable_on_the_diag_surface():
    """A latch that suppresses a CRITICAL and cannot be inspected is worse than
    none. #8778 shipped a writer with no allowlist entry; not again."""
    from src.web.api.routers.diag import _LOG_FILES
    assert "stop_over_cover_alert_state" in _LOG_FILES
    assert (_LOG_FILES["stop_over_cover_alert_state"].name
            == om._alert_state_path("stop_over_cover").name)
