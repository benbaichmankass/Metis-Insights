"""S-051 — diag router auth + happy path."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import diag as diag_router

_TOKEN = "test-diag-token-not-a-real-secret"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DIAG_READ_TOKEN", _TOKEN)
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")


@pytest.fixture
def client(env):
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def fake_runtime(tmp_path: Path, monkeypatch):
    runtime_logs = tmp_path / "runtime_logs"
    runtime_logs.mkdir()
    db_path = tmp_path / "trade_journal.db"
    audit = runtime_logs / "signal_audit.jsonl"
    status_json = runtime_logs / "status.json"
    heartbeat = runtime_logs / "heartbeat.txt"
    bot_log = tmp_path / "bot.log"
    shadow_predictions = runtime_logs / "shadow_predictions.jsonl"

    monkeypatch.setattr(diag_router, "_DB_PATH", db_path)
    monkeypatch.setattr(diag_router, "_AUDIT_LOG", audit)
    monkeypatch.setattr(diag_router, "_HEARTBEAT", heartbeat)
    monkeypatch.setattr(diag_router, "_STATUS_JSON", status_json)
    monkeypatch.setattr(diag_router, "_BOT_LOG", bot_log)
    monkeypatch.setattr(
        diag_router,
        "_LOG_FILES",
        {
            "audit": audit,
            "status": status_json,
            "heartbeat": heartbeat,
            "bot_log": bot_log,
            "shadow_predictions": shadow_predictions,
        },
    )
    return {
        "runtime_logs": runtime_logs,
        "db_path": db_path,
        "audit": audit,
        "status_json": status_json,
        "heartbeat": heartbeat,
        "bot_log": bot_log,
        "shadow_predictions": shadow_predictions,
    }


def _bearer(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


# ---------------------------------------------------------------------------
# Auth surface
# ---------------------------------------------------------------------------


def test_503_when_token_unset(monkeypatch, fake_runtime):
    monkeypatch.delenv("DIAG_READ_TOKEN", raising=False)
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")
    client = TestClient(api_main.app, raise_server_exceptions=False)
    resp = client.get("/api/diag/snapshot", headers=_bearer(_TOKEN))
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "diag_disabled"


def test_401_no_authorization_header(client, fake_runtime):
    resp = client.get("/api/diag/snapshot")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "missing_token"


def test_401_non_bearer_scheme(client, fake_runtime):
    resp = client.get(
        "/api/diag/snapshot",
        headers={"Authorization": "Basic abc"},
    )
    assert resp.status_code == 401


def test_401_empty_bearer(client, fake_runtime):
    resp = client.get("/api/diag/snapshot", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_401_wrong_token(client, fake_runtime):
    resp = client.get("/api/diag/snapshot", headers=_bearer("not-the-token"))
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_token"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_snapshot_with_empty_runtime_returns_shape(client, fake_runtime):
    resp = client.get("/api/diag/snapshot", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {
        "captured_at",
        "heartbeat",
        "status",
        "audit_tail",
        "order_packages",
        "trades",
        "vm_health",
        "services",
    }
    assert body["heartbeat"]["present"] is False
    assert body["status"] is None
    assert body["audit_tail"] == []
    assert body["order_packages"] == []
    assert body["trades"] == []


def test_audit_returns_tail(client, fake_runtime):
    fake_runtime["audit"].write_text(
        "\n".join(
            [
                json.dumps({"id": 1, "event": "tick", "result": "ok"}),
                json.dumps({"id": 2, "event": "rejected", "reason": "zero_balance"}),
                "",
                "{not valid}",
                json.dumps({"id": 3, "event": "tick", "result": "ok"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    resp = client.get("/api/diag/audit?limit=10", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    # Three valid JSON lines; one blank skipped, one malformed skipped.
    assert len(body) == 3
    assert body[0]["id"] == 1
    assert body[1]["reason"] == "zero_balance"


def test_journal_order_packages_returns_rows_newest_updated_first(client, fake_runtime):
    # Mirror the real schema (database.py): TEXT primary key, updated_at
    # is the chronological ordering field. The endpoint must use
    # datetime(updated_at) DESC — alphabetic ordering of pkg-<hash> ids
    # is essentially random.
    db = sqlite3.connect(str(fake_runtime["db_path"]))
    db.execute(
        "CREATE TABLE order_packages ("
        "order_package_id TEXT PRIMARY KEY, status TEXT, strategy_name TEXT, "
        "updated_at TEXT NOT NULL)"
    )
    db.executemany(
        "INSERT INTO order_packages "
        "(order_package_id, status, strategy_name, updated_at) VALUES (?, ?, ?, ?)",
        [
            ("pkg-aaa", "closed", "vwap", "2026-05-09T01:00:00+00:00"),
            ("pkg-bbb", "open", "vwap", "2026-05-09T03:00:00+00:00"),
            ("pkg-ccc", "closed", "vwap", "2026-05-09T02:00:00+00:00"),
        ],
    )
    db.commit()
    db.close()

    resp = client.get(
        "/api/diag/journal?table=order_packages&limit=10",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [r["order_package_id"] for r in body] == ["pkg-bbb", "pkg-ccc", "pkg-aaa"]


def test_journal_trades_returns_rows_in_desc_id_order(client, fake_runtime):
    db = sqlite3.connect(str(fake_runtime["db_path"]))
    db.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT, symbol TEXT)"
    )
    db.executemany(
        "INSERT INTO trades (id, status, symbol) VALUES (?, ?, ?)",
        [(1, "closed", "BTCUSDT"), (2, "open", "BTCUSDT"), (3, "closed", "BTCUSDT")],
    )
    db.commit()
    db.close()

    resp = client.get(
        "/api/diag/journal?table=trades&limit=10",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body] == [3, 2, 1]


def test_journal_unknown_table_400(client, fake_runtime):
    resp = client.get(
        "/api/diag/journal?table=secrets&limit=10",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "unknown_table"


def test_journalctl_unknown_unit_400(client, fake_runtime):
    resp = client.get(
        "/api/diag/journalctl?unit=arbitrary-attacker-unit",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "unknown_unit"


def test_journalctl_accepts_ssh_service_and_not_the_alias(client, fake_runtime):
    """E27 — the health-review breach sweep's MANDATORY sshd source.

    Two assertions, and the second is the load-bearing one:

    1. ``ssh.service`` is accepted. Before 2026-09-22 it was not, and neither
       was any other spelling, so `/health-review`'s § "Security-breach check"
       source 5 (`journalctl` for sshd/auth) was unsatisfiable on every day
       since the skill was written -- HTTP 400 `unknown_unit`, measured.
    2. ``sshd.service`` is still REFUSED. On ict-bot-arm (Ubuntu 22.04.5) that
       name is a systemd *alias*: `systemctl show` resolves it, but the
       journal match does not (`journalctl -u sshd.service` -> "-- No
       entries --"). Allowlisting it would answer HTTP 200 with `lines: []`
       and `available: true` -- a breach sweep reporting CLEAN having read
       nothing, which is strictly worse than the 400. A loud refusal is the
       correct answer for the alias; do not "helpfully" add it.
    """
    ok = client.get("/api/diag/journalctl?unit=ssh.service&lines=5",
                    headers=_bearer(_TOKEN))
    assert ok.status_code == 200
    assert ok.json()["unit"] == "ssh.service"

    alias = client.get("/api/diag/journalctl?unit=sshd.service&lines=5",
                       headers=_bearer(_TOKEN))
    assert alias.status_code == 400
    assert alias.json()["detail"]["error"] == "unknown_unit"


def test_journalctl_allowlisted_unit_returns_shape(client, fake_runtime):
    # The actual journalctl call may fail in the test env (no journal access),
    # but the route should accept the unit and return a structured response.
    resp = client.get(
        "/api/diag/journalctl?unit=ict-trader-live&lines=10",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["unit"] == "ict-trader-live.service"
    assert "available" in body
    assert "lines" in body


def test_journalctl_since_param_accepts_iso_8601(client, fake_runtime):
    # FU-20260511-001: ?since= forwards to journalctl --since for
    # historical-window pulls. Smoke-tests every accepted shape.
    for ts in (
        "2026-05-10T21:13:00",
        "2026-05-10T21:13:00Z",
        "2026-05-10T21:13:00+00:00",
        "2026-05-10 21:13:00",
    ):
        # Pass via params so the client URL-encodes `+`/space correctly
        # ('+' in a raw query string decodes to a space server-side).
        resp = client.get(
            "/api/diag/journalctl",
            params={"unit": "ict-trader-live", "lines": 10, "since": ts},
            headers=_bearer(_TOKEN),
        )
        assert resp.status_code == 200, f"rejected {ts!r}: {resp.text}"


def test_journalctl_since_param_rejects_garbage(client, fake_runtime):
    # Shape validation — anything not ISO-8601-ish is a 400, not a
    # subprocess argv smuggling vector. Out-of-range numeric components
    # (month=13, etc.) are intentionally NOT rejected here — the shape
    # regex is defense-in-depth against injection; journalctl itself
    # rejects out-of-range values and returns available=False cleanly.
    for ts in (
        "yesterday",
        "; rm -rf /",
        "$(touch /tmp/pwn)",
        "2026-05-10",  # date-only, no time
        "--since=cheat",
    ):
        resp = client.get(
            "/api/diag/journalctl",
            params={"unit": "ict-trader-live", "since": ts},
            headers=_bearer(_TOKEN),
        )
        assert resp.status_code == 400, f"accepted {ts!r}"
        assert resp.json()["detail"]["error"] == "invalid_timestamp"


def test_journalctl_until_param_validated_too(client, fake_runtime):
    # Same regex on the until end of the window.
    resp = client.get(
        "/api/diag/journalctl",
        params={"unit": "ict-trader-live", "until": "tomorrow"},
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["param"] == "until"


def test_log_file_unknown_name_400(client, fake_runtime):
    resp = client.get(
        "/api/diag/log_file?name=/etc/passwd",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "unknown_log_file"


def test_log_file_allowlisted_returns_tail(client, fake_runtime):
    fake_runtime["bot_log"].write_text(
        "\n".join(f"line-{i}" for i in range(50)) + "\n",
        encoding="utf-8",
    )
    resp = client.get(
        "/api/diag/log_file?name=bot_log&lines=5",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["present"] is True
    assert body["lines"] == [f"line-{i}" for i in range(45, 50)]


def test_log_file_shadow_predictions_returns_tail(client, fake_runtime):
    fake_runtime["shadow_predictions"].write_text(
        "\n".join(
            f'{{"model_id": "m-{i}", "stage": "shadow"}}' for i in range(10)
        )
        + "\n",
        encoding="utf-8",
    )
    resp = client.get(
        "/api/diag/log_file?name=shadow_predictions&lines=3",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["present"] is True
    assert body["lines"] == [
        '{"model_id": "m-7", "stage": "shadow"}',
        '{"model_id": "m-8", "stage": "shadow"}',
        '{"model_id": "m-9", "stage": "shadow"}',
    ]


def test_status_endpoint(client, fake_runtime):
    fake_runtime["status_json"].write_text(
        json.dumps({"schema_version": 1, "git_sha": "abc"}),
        encoding="utf-8",
    )
    fake_runtime["heartbeat"].write_text("ok", encoding="utf-8")

    resp = client.get("/api/diag/status", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert body["heartbeat"]["present"] is True
    assert body["status"]["git_sha"] == "abc"


def test_services_returns_one_entry_per_canonical_unit(client, fake_runtime):
    resp = client.get("/api/diag/services", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == len(diag_router._CANONICAL_UNITS)
    units_returned = [entry["unit"] for entry in body]
    assert units_returned == list(diag_router._CANONICAL_UNITS)


# ---------------------------------------------------------------------------
# /api/diag/db_info — DB metadata for trader-vs-web-api cross-reference
# ---------------------------------------------------------------------------


def test_db_info_missing_db_returns_present_false(client, fake_runtime):
    """No DB file at the configured path → ``exists=False``, empty
    tables list, no row counts. Mirrors ``_journal_select``'s
    early-return-empty contract for the same condition."""
    # fake_runtime points _DB_PATH at a tmp path but we never created
    # the file, so it shouldn't exist.
    resp = client.get("/api/diag/db_info", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False
    assert body["tables"] == []
    assert body["row_counts"] == {}


def test_db_info_returns_inode_size_tables_and_counts(client, fake_runtime):
    """Happy path — populated DB returns inode + size + per-table
    row counts. Operator can compare inode across services to confirm
    they read the same file."""
    db = sqlite3.connect(str(fake_runtime["db_path"]))
    try:
        db.execute(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT)"
        )
        db.execute(
            "CREATE TABLE order_packages (order_package_id TEXT PRIMARY KEY)"
        )
        db.execute("INSERT INTO trades(id, status) VALUES (1, 'open')")
        db.execute("INSERT INTO trades(id, status) VALUES (2, 'closed')")
        db.execute("INSERT INTO order_packages(order_package_id) VALUES ('pkg-a')")
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/diag/db_info", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["size_bytes"] is not None and body["size_bytes"] > 0
    assert body["inode"] is not None
    assert sorted(body["tables"]) == ["order_packages", "trades"]
    assert body["row_counts"] == {"trades": 2, "order_packages": 1}
    assert body["error_per_table"] == {}
    assert body["load_error"] is None


def test_db_info_401_without_token(client, fake_runtime):
    resp = client.get("/api/diag/db_info")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /api/diag/version — post-deploy round-trip assertion target
#
# S-067 follow-up #5 — the 2026-05-09 24+h-stale-code incident shipped
# because nothing in the deploy chain confirmed the running web-api
# process had actually picked up the new commit. This endpoint is what
# scripts/deploy_pull_restart.sh now hits to assert the running git
# SHA matches HEAD.
# ---------------------------------------------------------------------------


# ⚠️ THESE TWO TESTS USED TO ENCODE THE DEFECT, not the contract
# (BL-20260823-DIAG-VERSION-REPORTS-DISK-SHA-NOT-RUNNING-CODE). They
# monkeypatched ``_resolve_git_sha`` and asserted ``git_sha`` followed it —
# i.e. they pinned "git_sha is a LIVE resolve of the working tree", which is
# exactly what made the endpoint report DISK rather than the running process,
# and made the deploy assertion compare `git rev-parse HEAD` to itself.
# ``git_sha`` is now bound at import; the live resolve is ``git_sha_on_disk``.


def test_version_reports_the_RUNNING_sha_not_a_live_resolve(
    client, fake_runtime, monkeypatch
):
    """THE REGRESSION. A live resolve must NOT be able to move ``git_sha``."""
    monkeypatch.setattr(diag_router, "_RUNNING_GIT_SHA", "aaaaaaa")
    monkeypatch.setattr(diag_router, "_resolve_git_sha", lambda: "bbbbbbb")
    resp = client.get("/api/diag/version", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert body["git_sha"] == "aaaaaaa", (
        "git_sha must be the sha the PROCESS was loaded from; if a live "
        "resolve can move it, the endpoint is reporting disk again"
    )
    assert body["git_sha_on_disk"] == "bbbbbbb"
    assert body["restart_pending"] is True, (
        "disk ahead of the running process IS the 2026-05-09 stale-code state"
    )
    assert "captured_at" in body
    # ISO-8601 UTC.
    assert body["captured_at"].endswith("+00:00") or body["captured_at"].endswith("Z")


def test_version_reports_no_restart_pending_when_they_agree(
    client, fake_runtime, monkeypatch
):
    monkeypatch.setattr(diag_router, "_RUNNING_GIT_SHA", "abc1234")
    monkeypatch.setattr(diag_router, "_resolve_git_sha", lambda: "abc1234")
    body = client.get("/api/diag/version", headers=_bearer(_TOKEN)).json()
    assert body["git_sha"] == "abc1234"
    assert body["git_sha_on_disk"] == "abc1234"
    assert body["restart_pending"] is False


def test_version_returns_unknown_when_resolver_fails(client, fake_runtime, monkeypatch):
    """``_resolve_git_sha`` returns ``"unknown"`` on a sandbox host
    without git. The deploy script treats ``unknown`` as a soft
    failure rather than a SHA mismatch."""
    monkeypatch.setattr(diag_router, "_RUNNING_GIT_SHA", "unknown")
    monkeypatch.setattr(diag_router, "_resolve_git_sha", lambda: "unknown")
    resp = client.get("/api/diag/version", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    assert resp.json()["git_sha"] == "unknown"


def test_restart_pending_is_none_not_false_when_a_sha_is_unknown(
    client, fake_runtime, monkeypatch
):
    """'We could not look' must never be reported as 'they agree'.

    ``False`` would assert the process matches the tree — the one claim we
    cannot make when either sha is unreadable. Both directions are checked
    because only one of them was ever likely to be written by hand.
    """
    monkeypatch.setattr(diag_router, "_RUNNING_GIT_SHA", "unknown")
    monkeypatch.setattr(diag_router, "_resolve_git_sha", lambda: "abc1234")
    assert client.get(
        "/api/diag/version", headers=_bearer(_TOKEN)
    ).json()["restart_pending"] is None

    monkeypatch.setattr(diag_router, "_RUNNING_GIT_SHA", "abc1234")
    monkeypatch.setattr(diag_router, "_resolve_git_sha", lambda: "unknown")
    assert client.get(
        "/api/diag/version", headers=_bearer(_TOKEN)
    ).json()["restart_pending"] is None


def test_version_401_without_token(client, fake_runtime):
    resp = client.get("/api/diag/version")
    assert resp.status_code == 401


def test_version_401_on_bad_token(client, fake_runtime):
    resp = client.get("/api/diag/version", headers=_bearer("not-the-token"))
    assert resp.status_code == 401


def test_version_503_when_diag_token_unset(monkeypatch, fake_runtime):
    monkeypatch.delenv("DIAG_READ_TOKEN", raising=False)
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")
    client = TestClient(api_main.app, raise_server_exceptions=False)
    resp = client.get("/api/diag/version", headers=_bearer(_TOKEN))
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "diag_disabled"


# ---------------------------------------------------------------------------
# MI-305 — /api/diag/journal paging.
#
# The defect these cover: the route took `table` + `limit` only, FastAPI
# DISCARDED every other query parameter in silence, and the newest-`limit`
# window's FLOOR ROSE as the table grew — so the OLDEST rows walked out of
# reach a little more each day while callers grading before/after populations
# had no way to know. Measured across all four allowlisted tables on the live
# VM 2026-09-18: 83.0%–99.3% of every table unreachable, `offset=1000` and
# `limit=2000` and an invented `bogus_param=zzz` all returning byte-identical
# payloads (sha-equal, not merely same-length).
# ---------------------------------------------------------------------------


def _mk_trades(db_path: Path, n: int) -> None:
    db = sqlite3.connect(str(db_path))
    db.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT, symbol TEXT)")
    db.executemany(
        "INSERT INTO trades (id, status, symbol) VALUES (?, ?, ?)",
        [(i, "closed", "BTCUSDT") for i in range(1, n + 1)],
    )
    db.commit()
    db.close()


def test_journal_offset_is_honoured_and_pages_are_disjoint(client, fake_runtime):
    """THE defect. `offset` must move the window, not be discarded."""
    _mk_trades(fake_runtime["db_path"], 25)

    page1 = client.get(
        "/api/diag/journal?table=trades&limit=10&offset=0", headers=_bearer(_TOKEN)
    ).json()
    page2 = client.get(
        "/api/diag/journal?table=trades&limit=10&offset=10", headers=_bearer(_TOKEN)
    ).json()
    page3 = client.get(
        "/api/diag/journal?table=trades&limit=10&offset=20", headers=_bearer(_TOKEN)
    ).json()

    ids1 = [r["id"] for r in page1]
    ids2 = [r["id"] for r in page2]
    ids3 = [r["id"] for r in page3]

    assert ids1 == list(range(25, 15, -1))
    assert ids2 == list(range(15, 5, -1))
    assert ids3 == list(range(5, 0, -1))
    # Disjoint, and together they are the WHOLE table — the property the
    # research population actually needs.
    assert not set(ids1) & set(ids2)
    assert sorted(ids1 + ids2 + ids3) == list(range(1, 26))
    # The pre-MI-305 behaviour, asserted as a negative control: before the
    # fix page2 was byte-identical to page1. If that ever returns, this fails.
    assert page1 != page2


def test_journal_offset_past_the_end_returns_empty_not_the_first_page(
    client, fake_runtime
):
    _mk_trades(fake_runtime["db_path"], 5)
    body = client.get(
        "/api/diag/journal?table=trades&limit=10&offset=500", headers=_bearer(_TOKEN)
    ).json()
    assert body == []


def test_journal_unsupported_parameter_is_refused_by_name(client, fake_runtime):
    """Silently accepting and ignoring is the one outcome that must not survive."""
    _mk_trades(fake_runtime["db_path"], 3)
    for bad in ("since=2026-08-01", "until=2026-09-01", "bogus_param=zzz"):
        resp = client.get(
            f"/api/diag/journal?table=trades&limit=5&{bad}", headers=_bearer(_TOKEN)
        )
        assert resp.status_code == 400, bad
        detail = resp.json()["detail"]
        assert detail["error"] == "unsupported_parameter"
        # It must NAME the offending parameter, not just refuse.
        assert detail["unsupported"] == [bad.split("=")[0]]
        assert "offset" in detail["supported"]
        assert "audit_query" in detail["hint"]


def test_journal_unsupported_parameter_refusal_is_behind_the_token(
    client, fake_runtime
):
    """An unauthenticated caller must not learn the parameter vocabulary."""
    resp = client.get("/api/diag/journal?table=trades&bogus_param=zzz")
    assert resp.status_code in (401, 403)
    assert "unsupported_parameter" not in resp.text


def test_journal_default_shape_is_still_a_bare_array(client, fake_runtime):
    """~15 in-repo consumers parse this as a JSON array. Do not break them."""
    _mk_trades(fake_runtime["db_path"], 3)
    body = client.get(
        "/api/diag/journal?table=trades&limit=10", headers=_bearer(_TOKEN)
    ).json()
    assert isinstance(body, list)
    assert [r["id"] for r in body] == [3, 2, 1]


def test_journal_envelope_states_what_it_actually_returned(client, fake_runtime):
    _mk_trades(fake_runtime["db_path"], 25)
    body = client.get(
        "/api/diag/journal?table=trades&limit=10&offset=0&envelope=true",
        headers=_bearer(_TOKEN),
    ).json()
    assert isinstance(body, dict)
    assert body["count"] == 10
    assert body["total_rows"] == 25
    assert body["total_rows_state"] == "counted"
    assert body["has_more"] is True
    assert body["returned_range"] == {"column": "id", "first": 25, "last": 16}
    assert body["page_stability"] == "stable_key"
    assert [r["id"] for r in body["rows"]] == list(range(25, 15, -1))

    last = client.get(
        "/api/diag/journal?table=trades&limit=10&offset=20&envelope=true",
        headers=_bearer(_TOKEN),
    ).json()
    assert last["has_more"] is False


def test_journal_envelope_reports_the_limit_clamp_in_three_states(
    client, fake_runtime
):
    _mk_trades(fake_runtime["db_path"], 3)

    def _state(q):
        return client.get(
            f"/api/diag/journal?table=trades&envelope=true&{q}",
            headers=_bearer(_TOKEN),
        ).json()

    asked = _state("limit=2")
    assert asked["limit_state"] == "as_requested" and asked["limit"] == 2

    clamped = _state("limit=5000")
    assert clamped["limit_state"] == "clamped_to_max"
    assert clamped["limit"] == diag_router._MAX_LIMIT
    assert clamped["limit_requested"] == 5000

    defaulted = _state("limit=0")
    assert defaulted["limit_state"] == "defaulted_invalid"
    assert defaulted["limit"] == diag_router._DEFAULT_LIMIT


def test_journal_empty_table_totals_zero_but_missing_db_totals_none(
    client, fake_runtime
):
    """`0` is a real reading; `None` is 'we did not look'. Never conflate."""
    # DB file absent entirely.
    absent = client.get(
        "/api/diag/journal?table=trades&envelope=true", headers=_bearer(_TOKEN)
    ).json()
    assert absent["total_rows"] is None
    assert absent["total_rows_state"] == "db_absent"
    assert absent["has_more"] is None
    assert absent["returned_range"] is None

    _mk_trades(fake_runtime["db_path"], 0)
    empty = client.get(
        "/api/diag/journal?table=trades&envelope=true", headers=_bearer(_TOKEN)
    ).json()
    assert empty["total_rows"] == 0
    assert empty["total_rows_state"] == "counted"
    assert empty["has_more"] is False


def test_journal_order_packages_paging_is_total_even_when_updated_at_ties(
    client, fake_runtime
):
    """The trap inside the fix.

    `order_packages` is ordered by the MUTABLE, NON-UNIQUE
    `datetime(updated_at)`. Adding OFFSET over a non-total order lets a page
    boundary fall inside a tie, so rows are skipped or repeated across pages —
    a NEW silent wrongness inside the fix for a silent wrongness. The
    deterministic `order_package_id` tiebreaker is what prevents it.
    """
    db = sqlite3.connect(str(fake_runtime["db_path"]))
    db.execute(
        "CREATE TABLE order_packages ("
        "order_package_id TEXT PRIMARY KEY, status TEXT, strategy_name TEXT, "
        "updated_at TEXT NOT NULL)"
    )
    # Six rows sharing ONE timestamp — the whole table is a single tie, so a
    # boundary at offset=2 and offset=4 lands inside it.
    db.executemany(
        "INSERT INTO order_packages "
        "(order_package_id, status, strategy_name, updated_at) VALUES (?, ?, ?, ?)",
        [(f"pkg-{c}", "open", "vwap", "2026-05-09T01:00:00+00:00") for c in "abcdef"],
    )
    db.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT)")
    db.commit()
    db.close()

    seen: list[str] = []
    for off in (0, 2, 4):
        page = client.get(
            f"/api/diag/journal?table=order_packages&limit=2&offset={off}",
            headers=_bearer(_TOKEN),
        ).json()
        seen.extend(r["order_package_id"] for r in page)

    assert len(seen) == 6
    assert len(set(seen)) == 6, f"paging skipped or repeated a tied row: {seen}"
    assert sorted(seen) == [f"pkg-{c}" for c in "abcdef"]

    # ⚠️ THE THREE ASSERTIONS ABOVE DO NOT DISCRIMINATE, AND SAYING SO IS THE
    # POINT. Removing the tiebreaker was planted as a mutant and they all
    # still passed: SQLite's order among tied rows is UNSPECIFIED by SQL but
    # is in practice stable for a small single-table scan, so it hands back a
    # consistent sequence here for free. They pin the observable behaviour and
    # they are NOT evidence that the tiebreaker is present.
    #
    # What the fix actually claims is that the emitted ordering is a TOTAL
    # order, so no page boundary can fall inside a tie at any table size or
    # under any query plan. That claim is asserted directly — and on the
    # PUBLIC envelope field, not a private, because `order_by` is part of what
    # a caller is told about its own read.
    meta = client.get(
        "/api/diag/journal?table=order_packages&limit=2&envelope=true",
        headers=_bearer(_TOKEN),
    ).json()
    assert meta["order_by"] == "datetime(updated_at) DESC, order_package_id DESC"
    assert meta["page_key"] == "order_package_id"

    # And the immutable-id tables must NOT grow a redundant second term.
    trades_meta = client.get(
        "/api/diag/journal?table=trades&envelope=true", headers=_bearer(_TOKEN)
    ).json()
    assert trades_meta["order_by"] == "id DESC"


def test_journal_page_stability_distinguishes_mutable_from_immutable_keys(
    client, fake_runtime
):
    """`order_packages` pages over a key that can CHANGE between reads; every
    other table pages over an immutable id. A caller treating a paged pull as
    a census needs that distinction, so it is reported rather than smoothed."""
    db = sqlite3.connect(str(fake_runtime["db_path"]))
    db.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT)")
    db.execute(
        "CREATE TABLE order_packages ("
        "order_package_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
    )
    db.commit()
    db.close()

    def _stab(table):
        return client.get(
            f"/api/diag/journal?table={table}&envelope=true", headers=_bearer(_TOKEN)
        ).json()["page_stability"]

    assert _stab("trades") == "stable_key"
    assert _stab("order_packages") == "mutable_key"


def test_journal_snapshot_still_serves_bare_lists(client, fake_runtime):
    """/snapshot embeds these two tables; its shape must not change."""
    _mk_trades(fake_runtime["db_path"], 3)
    # /snapshot reads BOTH tables, and a missing one is a legitimate 503.
    db = sqlite3.connect(str(fake_runtime["db_path"]))
    db.execute(
        "CREATE TABLE order_packages ("
        "order_package_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
    )
    db.commit()
    db.close()
    body = client.get("/api/diag/snapshot?limit=5", headers=_bearer(_TOKEN)).json()
    assert isinstance(body["trades"], list)
    assert [r["id"] for r in body["trades"]] == [3, 2, 1]
