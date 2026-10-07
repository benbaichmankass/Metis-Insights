"""PROP-FLOW-SEPARATION (operator directive 2026-10-07): one prop ticket state
machine per executor type, docs/ARCHITECTURE-CANONICAL.md § "Prop ticket flow".

"In general, if a ticket expires and I haven't logged a trade, the system
should assume that the trade wasnt placed and the ticket should be kept alive.
And the prop accounts should have their own separate flow that isn't
contaminated by the telegram channels activity."

MEASURED cause: velotrade_1's first live ticket (01:59Z) expired unplaced, was
flipped to ``expiry_prompted`` (waiting on a human a REST account never has)
and suppressed four later signals for 17 h.

Every case runs against an isolated ``trade_journal.db`` and a temp
``prop_platforms.yaml`` (velotrade_1 = rest, breakout_2 = phone, breakout_1 =
manual); no Telegram or network I/O.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

NOW = datetime.now(timezone.utc)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import src.prop.phone_executor as phone
    import src.prop.platform as plat
    from src.prop import breakout_notify

    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    monkeypatch.delenv("PROP_EXPIRY_PROMPT_SECONDS", raising=False)
    monkeypatch.delenv("PROP_INVALIDATION_PROMPT_SECONDS", raising=False)
    monkeypatch.delenv("PROP_PHONE_MODE_BREAKOUT_2", raising=False)
    p = tmp_path / "prop_platforms.yaml"
    p.write_text(
        "accounts:\n"
        "  breakout_1: {platform: dxtrade, ticket_flow: manual}\n"
        "  tradeify_1: {platform: dxtrade, ticket_flow: browser}\n"
        "  velotrade_1: {platform: dxtrade_api, login_url: https://x.example/dxsca-web}\n"
        "phone_accounts:\n"
        "  breakout_2:\n"
        "    platform: breakout_phone\n"
        "    instruments: {ETHUSDT: {venue: ETHUSD}}\n")
    monkeypatch.setattr(plat, "PLATFORMS_PATH", p)
    monkeypatch.setattr(phone, "PLATFORMS_PATH", p)
    monkeypatch.setattr(breakout_notify, "emit_prop_fill",
                        lambda fill: {"push": False, "telegram": False})
    return tmp_path


def _ticket(tid: str, account: str, *, status: str = "emitted",
            vu: datetime | None = None) -> None:
    from src.prop import prop_journal

    vu = vu or (NOW - timedelta(hours=1))
    prop_journal.record_ticket({
        "ticket_id": tid, "account_id": account,
        "strategy": "trend_donchian_eth_prop", "symbol": "ETHUSDT",
        "direction": "short", "side": "Sell",
        "entry": 2672.0, "sl": 2701.1, "tp": 2497.4, "qty": 1.7, "risk_usd": 50.0,
        "signal_time": (vu - timedelta(hours=1)).isoformat(),
        "valid_until": vu.isoformat(), "status": status,
    })


def _status(tid: str) -> str:
    from src.prop import prop_journal

    return prop_journal.get_ticket(tid)["status"]


def _suppress(account: str):
    from src.prop.breakout_executor import _reticket_suppress_reason

    return _reticket_suppress_reason(account, "ETHUSDT", "short")


# ── classification ───────────────────────────────────────────────────────

def test_ticket_flow_reads_the_executor_type(env: Path) -> None:
    from src.prop.platform import ticket_flow

    assert ticket_flow("velotrade_1") == "rest"
    assert ticket_flow("breakout_2") == "phone"
    assert ticket_flow("tradeify_1") == "browser"
    assert ticket_flow("breakout_1") == "manual"
    assert ticket_flow("someone_new") == "manual"      # undeclared → manual at runtime


def test_an_invalid_declared_flow_reads_as_manual(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.prop.platform as plat

    p = env / "bad.yaml"
    p.write_text("accounts:\n  x_1: {platform: dxtrade, ticket_flow: robot}\n"
                 "  y_1: {platform: dxtrade_api, login_url: https://x.example/a, ticket_flow: manual}\n")
    monkeypatch.setattr(plat, "PLATFORMS_PATH", p)
    assert plat.ticket_flow("x_1") == "manual"         # the guard below refuses it in git
    assert plat.ticket_flow("y_1") == "rest"           # a REST platform is always rest


def test_unreadable_platform_file_reads_every_account_as_manual(
        env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.prop.platform as plat

    monkeypatch.setattr(plat, "PLATFORMS_PATH", env / "missing.yaml")
    assert plat.ticket_flow("velotrade_1") == "manual"


def test_the_real_config_classifies_the_live_prop_accounts() -> None:
    from src.prop.platform import ticket_flow

    assert ticket_flow("velotrade_1") == "rest"
    assert ticket_flow("breakout_2") == "phone"
    assert ticket_flow("tradeify_1") == "browser"     # browser executor live (MEASURED 2026-10-06)
    assert ticket_flow("breakout_1") == "manual"


def test_every_prop_account_declares_its_ticket_flow() -> None:
    # No prop account may land in `manual` by omission (manager, 2026-10-07):
    # every account routed through the prop bridge (exchange: breakout) in
    # config/accounts.yaml has a flow DECLARED in prop_platforms.yaml.
    import yaml

    from src.prop.platform import ticket_flows

    accts = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "accounts.yaml")
                           .read_text())
    accts = accts.get("accounts", accts)
    prop = sorted(a for a, e in accts.items()
                  if isinstance(e, dict) and str(e.get("exchange") or "") == "breakout")
    assert prop, "found no prop accounts — the probe cannot have checked anything"
    declared = ticket_flows()
    assert [a for a in prop if a not in declared] == []


# ── the general rule, and the re-ticket case on every path ───────────────

@pytest.mark.parametrize("account", ["breakout_1", "velotrade_1", "breakout_2", "tradeify_1"])
def test_expired_unfilled_ticket_is_terminal_and_the_next_signal_tickets(
        env: Path, account: str) -> None:
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    _ticket("prop-manual-old", account)                 # 1 h past validity
    sent = []
    run_prop_expiry_prompts(emitter=lambda t: sent.append(t["ticket_id"]) or True)
    assert _status("prop-manual-old") == "expired"
    assert _suppress(account) is None                   # the opportunity stays alive
    # Telegram is touched for the manual account only.
    assert sent == (["prop-manual-old"] if account == "breakout_1" else [])


@pytest.mark.parametrize("account", ["breakout_1", "velotrade_1", "breakout_2", "tradeify_1"])
def test_a_live_ticket_still_blocks_a_second_one(env: Path, account: str) -> None:
    # fail closed: never two live tickets for one key on one account
    _ticket("prop-manual-old", account)
    _ticket("prop-manual-live", account, vu=NOW + timedelta(minutes=30))
    assert _suppress(account) == "outstanding_ticket:emitted: prop-manual-live"


@pytest.mark.parametrize("account", ["breakout_1", "velotrade_1", "breakout_2", "tradeify_1"])
def test_a_real_open_position_still_blocks(env: Path, account: str) -> None:
    from src.prop.prop_report import ingest_report

    ingest_report({"account_id": account, "symbol": "ETHUSDT", "direction": "short",
                   "status": "open", "entry_price": 2666.11, "qty": 1.3})
    assert str(_suppress(account)).startswith("open_position:")


def test_a_phone_claim_in_flight_blocks(env: Path) -> None:
    _ticket("prop-manual-claimed", "breakout_2", status="claimed",
            vu=NOW - timedelta(hours=2))
    assert _suppress("breakout_2") == "outstanding_ticket:claimed: prop-manual-claimed"


def test_the_sweep_never_overwrites_a_claim_or_a_fill(env: Path) -> None:
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    _ticket("prop-manual-claimed", "breakout_2", status="claimed")
    _ticket("prop-manual-filled", "velotrade_1", status="filled")
    _ticket("prop-manual-placed", "breakout_1", status="placed")
    run_prop_expiry_prompts(emitter=lambda t: True)
    assert _status("prop-manual-claimed") == "claimed"
    assert _status("prop-manual-filled") == "filled"
    assert _status("prop-manual-placed") == "placed"


def test_a_ticket_with_unreadable_validity_is_left_alone(env: Path) -> None:
    from src.prop import prop_journal
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    prop_journal.record_ticket({"ticket_id": "prop-manual-novu", "account_id": "breakout_2",
                                "symbol": "ETHUSDT", "direction": "short",
                                "valid_until": "not-a-date", "status": "emitted"})
    run_prop_expiry_prompts(emitter=lambda t: True)
    assert _status("prop-manual-novu") == "emitted"
    assert _suppress("breakout_2") == "outstanding_ticket:emitted: prop-manual-novu"


def test_a_phone_ticket_reticketed_after_expiry_is_claimable(env: Path) -> None:
    from src.prop import phone_executor
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    _ticket("prop-manual-old", "breakout_2")
    run_prop_expiry_prompts(emitter=lambda t: True)
    _ticket("prop-manual-new", "breakout_2", vu=NOW + timedelta(minutes=30))
    got = phone_executor.claim_next(phone_executor.PhoneDevice("dev-1", "breakout_2"))
    assert got is not None and got["ticket_id"] == "prop-manual-new"
    assert _status("prop-manual-old") == "expired"


# ── machine accounts are not driven from Telegram ────────────────────────

def _ticket_obj():
    from src.prop.breakout_ticket import BreakoutSignal, TicketConfig, build_ticket

    sig = BreakoutSignal(strategy="trend_donchian_eth_prop", symbol="ETHUSDT",
                         direction="short", entry=2672.0, sl=2701.1, tp=2497.4,
                         timeframe="2h", signal_time=NOW)
    return build_ticket(sig, TicketConfig(account_size_usd=5000.0, risk_pct=1.0))


@pytest.mark.parametrize("account,has_keyboard", [
    ("breakout_1", True), ("velotrade_1", False), ("breakout_2", False), ("tradeify_1", False)])
def test_ticket_keyboard_only_on_manual_accounts(
        env: Path, monkeypatch: pytest.MonkeyPatch, account: str, has_keyboard: bool) -> None:
    from src.prop import breakout_notify

    captured = {}

    def _send(text, **kw):
        captured["reply_markup"] = kw.get("reply_markup")
        captured["text"] = text
        return True

    monkeypatch.setattr("src.runtime.notify.send_telegram_direct", _send)
    out = breakout_notify.emit_prop_signal(_ticket_obj(), push=False, account_id=account,
                                           ticket_id="prop-manual-abc")
    assert out["telegram"] is True and captured["text"]       # still notified
    assert (captured["reply_markup"] is not None) is has_keyboard


@pytest.mark.parametrize("account", ["velotrade_1", "breakout_2", "tradeify_1"])
@pytest.mark.parametrize("verb", ["y", "n"])
def test_a_tap_on_a_machine_ticket_is_refused_and_writes_nothing(
        env: Path, account: str, verb: str) -> None:
    from src.prop.prop_expiry_prompt import handle_expiry_callback

    _ticket("prop-manual-m", account, vu=NOW + timedelta(minutes=30))
    res = handle_expiry_callback(f"propexp:{verb}:prop-manual-m")
    assert res["answer"] == "refused" and res["send_prompt"] is False
    assert _status("prop-manual-m") == "emitted"


def test_invalidation_prompter_skips_machine_accounts(
        env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop import prop_invalidation_prompt as pip

    for tid, acct in (("prop-manual-r", "velotrade_1"), ("prop-manual-p", "breakout_2"),
                      ("prop-manual-b", "tradeify_1"), ("prop-manual-h", "breakout_1")):
        _ticket(tid, acct, vu=NOW + timedelta(minutes=30))
    found = {t["ticket_id"] for t in pip.find_tickets_to_check()}
    assert found == {"prop-manual-h"}
    monkeypatch.setattr(pip, "_fetch_current_price", lambda s, st: 2800.0)  # beyond SL
    sent = []
    pip.run_prop_invalidation_prompts(settings={}, emitter=lambda t, p, w: sent.append(
        t["ticket_id"]) or True)
    assert sent == ["prop-manual-h"]
    assert _status("prop-manual-r") == "emitted" and _status("prop-manual-p") == "emitted"


# ── manual accounts: taps are compare-and-set; a late fill reconciles ────

@pytest.mark.parametrize("status", ["filled", "placed", "closed", "skipped"])
@pytest.mark.parametrize("verb", ["y", "n"])
def test_a_tap_never_overwrites_a_later_state(env: Path, status: str, verb: str) -> None:
    from src.prop.prop_expiry_prompt import handle_expiry_callback

    _ticket("prop-manual-x", "breakout_1", status=status)
    res = handle_expiry_callback(f"propexp:{verb}:prop-manual-x")
    assert res["answer"] == "refused"
    assert _status("prop-manual-x") == status


def test_an_unknown_ticket_tap_is_refused(env: Path) -> None:
    from src.prop.prop_expiry_prompt import handle_expiry_callback

    assert handle_expiry_callback("propexp:y:prop-manual-nope")["answer"] == "refused"


def test_late_fill_via_the_notice_button(env: Path) -> None:
    # expired (not placed) → operator taps "I did place it" → awaiting_report,
    # which blocks the key (possibly a live position) until the fill links.
    from src.prop import prop_reconcile
    from src.prop.prop_expiry_prompt import build_late_fill_keyboard, handle_expiry_callback
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    _ticket("prop-manual-late", "breakout_1")
    run_prop_expiry_prompts(emitter=lambda t: True)
    assert _status("prop-manual-late") == "expired"
    cb = build_late_fill_keyboard("prop-manual-late")["inline_keyboard"][0][0]["callback_data"]
    res = handle_expiry_callback(cb)
    assert res["answer"] == "yes" and res["send_prompt"] is True
    assert _status("prop-manual-late") == "awaiting_report"
    assert _suppress("breakout_1") == "outstanding_ticket:awaiting_report: prop-manual-late"
    assert prop_reconcile.match_fill_to_ticket(
        {"account_id": "breakout_1", "symbol": "ETHUSDT", "direction": "short"}) == "prop-manual-late"


def test_late_fill_pasted_with_its_ticket_id(env: Path) -> None:
    # the rendered ticket pre-fills its id: an explicit id links in any status
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts
    from src.prop.prop_report import ingest_report

    _ticket("prop-manual-late", "breakout_1")
    run_prop_expiry_prompts(emitter=lambda t: True)
    out = ingest_report({"account_id": "breakout_1", "ticket_id": "prop-manual-late",
                         "symbol": "ETHUSDT", "direction": "short", "status": "open",
                         "entry_price": 2670.0, "qty": 1.7})
    assert out["ticket_id"] == "prop-manual-late"
    assert _status("prop-manual-late") == "filled"
    assert str(_suppress("breakout_1")).startswith("open_position:")


def test_failed_notice_retries_and_the_ticket_blocks_nothing_meanwhile(env: Path) -> None:
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    _ticket("prop-manual-1", "breakout_1")
    stats = run_prop_expiry_prompts(emitter=lambda t: False)
    assert stats["failed"] == 1 and _status("prop-manual-1") == "emitted"
    assert _suppress("breakout_1") is None
    stats = run_prop_expiry_prompts(emitter=lambda t: True)
    assert stats["prompted"] == 1 and _status("prop-manual-1") == "expired"


def test_the_expiry_notice_has_one_late_fill_button(
        env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop import breakout_notify

    captured = {}

    def _send(text, **kw):
        captured.update(text=text, kb=kw.get("reply_markup"))
        return True

    monkeypatch.setattr("src.runtime.notify.send_telegram_direct", _send)
    assert breakout_notify.emit_prop_expiry_notice(
        {"ticket_id": "prop-manual-1", "account_id": "breakout_1", "symbol": "ETHUSDT",
         "direction": "short"}) is True
    assert "NOT placed" in captured["text"]
    assert [b["callback_data"] for b in captured["kb"]["inline_keyboard"][0]] == \
        ["propexp:y:prop-manual-1"]
