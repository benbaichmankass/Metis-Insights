"""Phone executor server contract (PHONE-EXEC-1B): per-device auth, atomic claim, one attempt per ticket,
the submit decision, the claim watchdog, account pinning on report, event scrubbing, and the routes."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.prop import phone_executor as pe
from src.prop import prop_journal

TOKEN = "t" * 43
OTHER = "u" * 43


@pytest.fixture(autouse=True)
def _iso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    dev = tmp_path / "devices.yaml"
    dev.write_text(yaml.safe_dump({"devices": [
        {"device_id": "p1", "account_id": "breakout_2", "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()},
        {"device_id": "p2", "account_id": "breakout_2", "token_sha256": hashlib.sha256(OTHER.encode()).hexdigest(),
         "revoked": True},
    ]}))
    monkeypatch.setattr(pe, "DEVICES_PATH", dev)
    monkeypatch.delenv("PROP_PHONE_MODE_BREAKOUT_2", raising=False)
    sent: list = []
    monkeypatch.setattr("src.runtime.notify.send_telegram_direct", lambda m, **k: sent.append(m) or True)
    return sent


def _ticket(tid: str, *, minutes: int = 10, account: str = "breakout_2", meta=None) -> None:
    now = datetime.now(timezone.utc)
    prop_journal.record_ticket({
        "ticket_id": tid, "account_id": account, "strategy": "trend_donchian_eth_prop", "symbol": "ETHUSDT",
        "direction": "long", "entry": 2500.0, "sl": 2450.0, "tp": 2700.0, "qty": 0.5,
        "valid_until": (now + timedelta(minutes=minutes)).isoformat(), "status": "emitted", "meta": meta})


def _dev() -> pe.PhoneDevice:
    return pe.authenticate("Bearer " + TOKEN)


def test_auth_pins_account_and_refuses_unknown_and_revoked():
    assert _dev().account_id == "breakout_2"
    for bad in (None, "Bearer short", "Bearer " + "x" * 43, "Basic " + TOKEN, "Bearer " + OTHER):
        with pytest.raises(pe.PhoneAuthError):
            pe.authenticate(bad)


def test_claim_is_atomic_one_attempt_and_skips_expired():
    _ticket("old", minutes=-1)
    _ticket("t1")
    got = pe.claim_next(_dev())
    assert got["ticket_id"] == "t1" and got["status"] == "claimed"
    assert pe.claim_next(_dev()) is None  # never re-offered, expired one never claimed
    assert prop_journal.get_ticket("old")["status"] == "emitted"


def test_claim_never_crosses_accounts():
    _ticket("x", account="breakout_1")
    assert pe.claim_next(_dev()) is None


def test_submit_mode_dry_unless_account_live_and_not_test(tmp_path: Path, monkeypatch):
    acc = tmp_path / "accounts.yaml"
    acc.write_text(yaml.safe_dump({"accounts": {"breakout_2": {"mode": "dry_run"}}}))
    assert pe.submit_mode("breakout_2", test=False, accounts_path=acc, env={}) == "dry"
    acc.write_text(yaml.safe_dump({"accounts": {"breakout_2": {"mode": "live"}}}))
    assert pe.submit_mode("breakout_2", test=False, accounts_path=acc, env={}) == "live"
    assert pe.submit_mode("breakout_2", test=True, accounts_path=acc, env={}) == "dry"
    assert pe.submit_mode("breakout_2", test=False, accounts_path=acc, env={"PROP_PHONE_MODE_BREAKOUT_2": "dry"}) == "dry"
    assert pe.submit_mode("breakout_2", test=False, accounts_path=acc, env={"PROP_PHONE_MODE_BREAKOUT_2": "typo"}) == "dry"


def test_kill_switch_off_claims_nothing(monkeypatch):
    _ticket("t1")
    monkeypatch.setenv("PROP_PHONE_MODE_BREAKOUT_2", "off")
    assert pe.claim_next(_dev()) is None


def test_watchdog_skips_unreported_claims():
    _ticket("t1")
    pe.claim_next(_dev())
    later = datetime.now(timezone.utc) + timedelta(seconds=pe.CLAIM_TIMEOUT_S + 5)
    assert pe.expire_stale_claims("breakout_2", now=later) == ["t1"]
    assert prop_journal.get_ticket("t1")["status"] == "skipped"


def test_ticket_result_keeps_form_dump_and_needs_a_claim():
    _ticket("t1")
    assert pe.record_report(_dev(), {"kind": "ticket_result", "ticket_id": "t1", "result": "dry_filled"})["updated"] == 0
    pe.claim_next(_dev())
    r = pe.record_report(_dev(), {"kind": "ticket_result", "ticket_id": "t1", "result": "dry_filled",
                                  "reason": "server mode dry", "form": {"inputs": [{"label": "Take profit price"}]},
                                  "account_id": "breakout_1"})
    assert r["updated"] == 1
    t = prop_journal.get_ticket("t1")
    assert t["status"] == "dry_filled" and t["account_id"] == "breakout_2"
    assert t["meta"]["phone"]["result"]["form"]["inputs"][0]["label"] == "Take profit price"
    with pytest.raises(ValueError):
        pe.record_report(_dev(), {"kind": "ticket_result", "ticket_id": "t1", "result": "filled_somehow"})


def test_event_scrubs_links_emails_and_long_numbers(_iso):
    pe.record_event(_dev(), {"event": "login_failed",
                             "reason": "open https://x.y/abc?t=1 for me@x.com acct 12345678"})
    msg = _iso[-1]
    assert "https" not in msg and "@" not in msg and "12345678" not in msg
    with pytest.raises(ValueError):
        pe.record_event(_dev(), {"event": "whatever"})


def test_test_ticket_is_always_dry(tmp_path: Path, monkeypatch):
    acc = tmp_path / "accounts.yaml"
    acc.write_text(yaml.safe_dump({"accounts": {"breakout_2": {"mode": "live"}}}))
    monkeypatch.setattr(pe, "ACCOUNTS_PATH", acc)
    tid = pe.make_test_ticket(_dev(), entry=2500.0)["ticket_id"]
    got = pe.claim_next(_dev())
    assert got["ticket_id"] == tid and got["submit"] == "dry" and got["meta"]["test"] is True


def test_routes_401_without_device_and_claim_with_device(monkeypatch):
    from src.web.api import main as api_main
    c = TestClient(api_main.app, raise_server_exceptions=False)
    assert c.post("/api/bot/prop/phone/claim").status_code == 401
    assert c.post("/api/bot/prop/phone/claim", headers={"Authorization": "Bearer " + OTHER}).status_code == 401
    _ticket("t1")
    r = c.post("/api/bot/prop/phone/claim", headers={"Authorization": "Bearer " + TOKEN})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["account_id"] == "breakout_2" and body["ticket"]["ticket_id"] == "t1"
    assert body["ticket"]["venue_symbol"] == "ETHUSD"


def test_repo_config_declares_breakout_2_as_phone_and_devices_are_fingerprint_only():
    assert pe.is_phone_account("breakout_2")
    assert not pe.is_phone_account("breakout_1")
    raw = yaml.safe_load(Path("config/prop_phone_devices.yaml").read_text())
    for d in raw.get("devices") or []:
        assert len(d["token_sha256"]) == 64 and "token" not in {k for k in d if k != "token_sha256"}
    json.dumps(raw)


def test_test_ticket_geometry_is_valid_for_a_long():
    tid = pe.make_test_ticket(_dev(), entry=2712.0)["ticket_id"]
    t = prop_journal.get_ticket(tid)
    assert t["direction"] == "long"
    assert t["sl"] < t["entry"] < t["tp"], (t["sl"], t["entry"], t["tp"])
    assert t["entry"] < 2712.0  # the limit rests below the last price: a dry fill can never be marketable


def test_ticket_result_reason_is_visible_on_the_outbound_view():
    _ticket("t1")
    pe.claim_next(_dev())
    pe.record_report(_dev(), {"kind": "ticket_result", "ticket_id": "t1", "result": "refused",
                              "reason": "TP price field not unique", "form": {"inputs": [{"label": "Take profit"}]}})
    rows = [r for r in prop_journal.list_outbound_tickets(account_id="breakout_2") if r.get("ticket_id") == "t1"]
    assert rows and rows[0]["phone_result"]["reason"] == "TP price field not unique"


def test_phone_dry_test_action_writes_one_test_ticket_and_refuses_bad_account_and_symbol(tmp_path, monkeypatch):
    """The ``phone-dry-test`` system-action's writer (PI-20261006-APBY4NTV-0009), three proofs:
    (1) the ticket is ``meta.test`` and claims as ``submit=dry`` even on a LIVE account; (2) an account that is
    not a ``phone_accounts`` entry is refused; (3) a symbol outside the account's instruments is refused —
    and a refusal writes NOTHING."""
    plat = tmp_path / "prop_platforms.yaml"
    plat.write_text(yaml.safe_dump({"phone_accounts": {"breakout_2": {
        "platform": "breakout_phone", "instruments": {"ETHUSDT": {"venue": "ETHUSD"}, "SOLUSDT": {"venue": "SOLUSD"}}}}}))
    monkeypatch.setattr(pe, "PLATFORMS_PATH", plat)
    acc = tmp_path / "accounts.yaml"
    acc.write_text(yaml.safe_dump({"accounts": {"breakout_2": {"mode": "live"}, "breakout_1": {"mode": "live"}}}))
    monkeypatch.setattr(pe, "ACCOUNTS_PATH", acc)
    monkeypatch.setattr(pe, "_bybit_last", lambda sym: 2700.0)

    # (1) the SAME writer the Dry test button uses: meta.test, source recorded, forced dry on a live account.
    out = pe.write_dry_test_ticket("breakout_2", "solusdt", source="phone-dry-test#123")
    assert out["submit"] == "dry" and out["symbol"] == "SOLUSDT" and out["account_id"] == "breakout_2"
    got = pe.claim_next(_dev())
    assert got["ticket_id"] == out["ticket_id"] and got["submit"] == "dry"
    assert got["meta"]["test"] is True and got["meta"]["source"] == "phone-dry-test#123"
    assert got["venue_symbol"] == "SOLUSD"

    # (2) a VM-driven prop account (not under phone_accounts) is refused, (3) a non-allowlisted symbol is refused.
    with pytest.raises(ValueError, match="not a phone_accounts entry"):
        pe.write_dry_test_ticket("breakout_1", "ETHUSDT")
    with pytest.raises(ValueError, match="not in phone_accounts.breakout_2.instruments"):
        pe.write_dry_test_ticket("breakout_2", "BTCUSDT")
    # ...and a refusal writes nothing: the only row is the one from (1), now claimed.
    conn = prop_journal._connect()
    try:
        rows = conn.execute("SELECT ticket_id, status, meta FROM prop_tickets").fetchall()
    finally:
        conn.close()
    assert [(r["ticket_id"], r["status"]) for r in rows] == [(out["ticket_id"], "claimed")]
    assert all(json.loads(r["meta"])["test"] is True for r in rows)


def test_phone_dry_test_cli_exit_codes_and_json(tmp_path, monkeypatch, capsys):
    """``scripts/prop/phone_dry_test.py``: exit 0 + a JSON line on a write, exit 1 + ``refused`` on a bad input."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "phone_dry_test", Path(pe._REPO_ROOT) / "scripts" / "prop" / "phone_dry_test.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(pe, "_bybit_last", lambda sym: 2700.0)
    assert cli.main(["--account", "nope", "--symbol", "ETHUSDT"]) == 1
    assert json.loads(capsys.readouterr().out)["refused"]
    assert cli.main(["--account", "breakout_2", "--symbol", "ETHUSDT", "--source", "phone-dry-test#7"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["submit"] == "dry" and out["ticket_id"].startswith("phone-test-")


def test_dry_test_request_serves_exactly_one_dry_ticket(monkeypatch):
    monkeypatch.setattr(pe, "phone_config", lambda acct, path=None: {"dry_test_request": "r1",
                        "instruments": {"ETHUSDT": {"venue": "ETHUSD"}}})
    monkeypatch.setattr(pe, "_bybit_last", lambda sym: 2700.0)
    got = pe.claim_next(_dev())
    assert got and got["meta"]["test"] is True and got["meta"]["dry_test_request"] == "r1" and got["submit"] == "dry"
    assert pe.claim_next(_dev()) is None  # same request id is never served twice


def test_terminal_miss_keeps_latest_controls_scrubbed_and_does_not_ping(_iso):
    n = len(_iso)
    assert pe.last_diag("breakout_2") is None
    pe.record_event(_dev(), {"event": "terminal_miss", "reason": "tabs=0 inputs=1 probe=false",
                             "controls": ["Positions", "acct 12345678", "me@x.com", "", "x" * 99] + ["c"] * 60})
    d = pe.last_diag("breakout_2")
    assert len(_iso) == n  # quiet: diagnostics, not an operator ping
    assert d["reason"] == "tabs=0 inputs=1 probe=false" and d["controls"][0] == "Positions"
    assert all("12345678" not in c and "@" not in c and len(c) <= 40 for c in d["controls"])
    assert len(d["controls"]) <= pe._DIAG_MAX
    from src.web.api import main as api_main
    body = TestClient(api_main.app, raise_server_exceptions=False).get("/api/bot/prop/status?account_id=breakout_2").json()
    assert body.get("phone_diag", {}).get("controls", [None])[0] == "Positions"


def test_heartbeat_keeps_latest_allowlisted_state_and_does_not_ping(_iso):
    n = len(_iso)
    assert pe.last_heartbeat("breakout_2") is None
    pe.record_event(_dev(), {"event": "heartbeat", "reason": "logged in, not on the terminal; acct 12345678",
                             "state": {"st": "logged_in", "hold": True, "tabs": 3, "host": "trade.breakoutprop.com",
                                       "secret": "x", "onAccount": False, "armed": True}})
    hb = pe.last_heartbeat("breakout_2")
    assert len(_iso) == n
    assert "12345678" not in hb["status"] and hb["state"] == {"st": "logged_in", "hold": True, "tabs": 3,
                                                               "host": "trade.breakoutprop.com", "onAccount": False}
    from src.web.api import main as api_main
    body = TestClient(api_main.app, raise_server_exceptions=False).get("/api/bot/prop/status?account_id=breakout_2").json()
    assert body["phone_heartbeat"]["state"]["hold"] is True


def test_pending_peek_counts_without_claiming(monkeypatch):
    from src.web.api import main as api_main
    cfg = pe.phone_config("breakout_2")
    monkeypatch.setattr(pe, "phone_config", lambda acct, path=None: {k: v for k, v in cfg.items() if k != "dry_test_request"})
    c = TestClient(api_main.app, raise_server_exceptions=False)
    assert c.get("/api/bot/prop/phone/pending").status_code == 401
    h = {"Authorization": "Bearer " + TOKEN}
    assert c.get("/api/bot/prop/phone/pending", headers=h).json() == {"ok": True, "pending": 0}
    _ticket("old", minutes=-1)
    _ticket("t1")
    assert c.get("/api/bot/prop/phone/pending", headers=h).json()["pending"] == 1
    assert c.get("/api/bot/prop/phone/pending", headers=h).json()["pending"] == 1  # a peek claims nothing
    assert pe.claim_next(_dev())["ticket_id"] == "t1"
    assert pe.pending_count(_dev()) == 0
    _ticket("t2")
    monkeypatch.setenv("PROP_PHONE_MODE_BREAKOUT_2", "off")
    assert pe.pending_count(_dev()) == 0  # kill switch off: nothing to wake for, like claim_next


def test_pending_counts_an_unserved_dry_test_request_without_serving_it(monkeypatch):
    monkeypatch.setattr(pe, "phone_config", lambda acct, path=None: {"dry_test_request": "r9",
                        "instruments": {"ETHUSDT": {"venue": "ETHUSD"}}})
    monkeypatch.setattr(pe, "_bybit_last", lambda sym: 2700.0)
    assert pe.pending_count(_dev()) == 1
    assert pe.pending_count(_dev()) == 1  # still unserved: the peek wrote no ticket
    assert pe.claim_next(_dev())["meta"]["dry_test_request"] == "r9"
    assert pe.pending_count(_dev()) == 0
