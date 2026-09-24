"""The transaction-log puller, tested WITHOUT the SDK via an injected client.

pybit is a VM-only dependency, so a puller that could only be tested against a
live venue would not be tested at all. The client is injectable for exactly
that reason.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import src.runtime.bybit_wallet_truth as wt
from src.runtime.exchange_fills_store import list_transaction_log

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "pull_txnlog", ROOT / "scripts/ops/pull_bybit_transaction_log.py"
)
puller = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(puller)


class FakeClient:
    """Serves pages the way Bybit's cursor pagination does."""

    def __init__(self, pages, boom=None):
        self.pages = pages
        self.boom = boom
        self.calls = []

    def get_transaction_log(self, **params):
        self.calls.append(params)
        if self.boom:
            raise self.boom
        idx = 0
        if params.get("cursor"):
            idx = int(params["cursor"])
        page = self.pages[idx]
        nxt = str(idx + 1) if idx + 1 < len(self.pages) else ""
        return {"result": {"list": page, "nextPageCursor": nxt}}


def _r(i, typ="TRADE", change="-10.0"):
    return {"id": f"t{i}", "type": typ, "currency": "USDT", "change": change,
            "transactionTime": 1_760_000_000_000 + i}


def test_follows_the_cursor_across_pages():
    c = FakeClient([[_r(1), _r(2)], [_r(3)]])
    rows = puller.fetch_transaction_log(c)
    assert [r["id"] for r in rows] == ["t1", "t2", "t3"]


def test_pagination_is_bounded():
    """An unbounded `while cursor` is how a puller becomes an infinite loop."""
    class Endless:
        def get_transaction_log(self, **p):
            return {"result": {"list": [_r(1)], "nextPageCursor": "0"}}

    rows = puller.fetch_transaction_log(Endless(), max_pages=5)
    assert len(rows) == 5, "must stop at max_pages, not spin"


def test_a_transport_failure_RAISES_rather_than_returning_empty():
    """Returning [] on an exception would collapse 'could not look' into
    'the account was flat' -- the exact conflation this family exists to stop."""
    c = FakeClient([[]], boom=RuntimeError("bybit 10003"))
    with pytest.raises(RuntimeError):
        puller.fetch_transaction_log(c)


def test_end_to_end_pull_then_compute(tmp_path):
    db = tmp_path / "s.sqlite"
    c = FakeClient([[_r(1, change="-100"), _r(2, "TRANSFER_IN", "5000")]])
    n = puller.pull_one_account(
        "bybit_2", "k", "s", demo=False, days=7, store_path=db,
        client_factory=lambda *a, **kw: c, now_ms=1_760_000_500_000,
    )
    assert n == 2
    v = wt.compute_wallet_truth("bybit_2", list_transaction_log("bybit_2", path=db))
    assert v.realized_usd == -100.0, "the deposit must not become profit"


def test_repeated_runs_do_not_move_the_figure(tmp_path):
    """An hourly puller with a 7-day lookback re-sees the same rows constantly."""
    db = tmp_path / "s.sqlite"
    def mk():
        return FakeClient([[_r(1, change="-100"), _r(2, change="-25")]])

    for _ in range(3):
        puller.pull_one_account(
            "bybit_2", "k", "s", demo=False, days=7, store_path=db,
            client_factory=lambda *a, **kw: mk(), now_ms=1_760_000_500_000,
        )
    v = wt.compute_wallet_truth("bybit_2", list_transaction_log("bybit_2", path=db))
    assert v.realized_usd == -125.0, "three runs must not treble the loss"


def test_demo_flag_reaches_the_client_factory(tmp_path):
    seen = {}

    def factory(k, s, demo):
        seen["demo"] = demo
        return FakeClient([[]])

    puller.pull_one_account(
        "bybit_1", "k", "s", demo=True, days=1,
        store_path=tmp_path / "s.sqlite", client_factory=factory,
    )
    assert seen["demo"] is True, "a demo account must not be dialled on mainnet"


def test_window_is_passed_to_the_venue(tmp_path):
    c = FakeClient([[]])
    puller.pull_one_account(
        "bybit_2", "k", "s", demo=False, days=3, store_path=tmp_path / "s.sqlite",
        client_factory=lambda *a, **kw: c, now_ms=1_000_000_000_000,
    )
    p = c.calls[0]
    assert p["endTime"] == 1_000_000_000_000
    assert p["startTime"] == 1_000_000_000_000 - 3 * 86_400_000
    assert p["accountType"] == "UNIFIED"


# ── the Bybit V5 range cap ────────────────────────────────────────────────────

class RangeCappedClient:
    """Models Bybit V5's real behaviour: a call returns only rows inside the
    FIRST 7 days of whatever range you ask for. Asking for 60 days therefore
    yields the slice [start, start+7d] — the window MOVES rather than widening.
    """

    CAP_MS = 7 * 86_400_000

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_transaction_log(self, **p):
        self.calls.append(p)
        lo = p["startTime"]
        hi = min(p["endTime"], lo + self.CAP_MS)
        got = [r for r in self.rows if lo <= r["transactionTime"] <= hi]
        return {"result": {"list": got, "nextPageCursor": ""}}


def _spread(now_ms, days, per_day=1):
    out = []
    for d in range(days):
        for i in range(per_day):
            t = now_ms - d * 86_400_000 - i
            out.append({"id": f"d{d}-{i}", "type": "TRADE", "currency": "USDT",
                        "change": "-1.0", "transactionTime": t})
    return out


def test_a_deep_window_is_WALKED_not_asked_for_in_one_call(tmp_path):
    """The bug this pins: `--days 60` in a single call returns a 7-day slice and
    reports success. The fills puller already paid for it
    (BL-20260808-FILLS-WINDOW-TOO-SHORT-TO-REPAIR-HISTORY); this asserts the
    transaction-log puller does not repeat it."""
    now = 1_760_000_000_000
    rows = _spread(now, days=60)
    c = RangeCappedClient(rows)
    db = tmp_path / "s.sqlite"

    inserted = puller.pull_one_account(
        "bybit_2", "k", "s", demo=False, days=60, store_path=db,
        client_factory=lambda *a, **kw: c, now_ms=now,
    )
    # 60 days walked in <=7-day chunks
    assert len(c.calls) >= 8, f"only {len(c.calls)} call(s) — the range was not walked"
    assert inserted == 60, f"expected all 60 days stored, got {inserted}"


def test_a_single_call_would_have_returned_only_a_slice(tmp_path):
    """The CONTROL: without chunking the same client yields ~7 rows, not 60.
    Without this, the test above could pass for the wrong reason."""
    now = 1_760_000_000_000
    c = RangeCappedClient(_spread(now, days=60))
    one_shot = puller.fetch_transaction_log(
        c, start_ms=now - 60 * 86_400_000, end_ms=now
    )
    assert len(one_shot) <= 8, (
        f"the fake is not modelling the range cap ({len(one_shot)} rows) — "
        "a negative needs a denominator"
    )


def test_chunk_edges_overlap_harmlessly(tmp_path):
    """Chunk boundaries re-see rows; the store keys on the venue id, so a
    re-walk must not double-count the money."""
    now = 1_760_000_000_000
    db = tmp_path / "s.sqlite"
    def mk():
        return RangeCappedClient(_spread(now, days=21))

    for _ in range(2):
        puller.pull_one_account(
            "bybit_2", "k", "s", demo=False, days=21, store_path=db,
            client_factory=lambda *a, **kw: mk(), now_ms=now,
        )
    import src.runtime.bybit_wallet_truth as wt
    from src.runtime.exchange_fills_store import list_transaction_log
    v = wt.compute_wallet_truth("bybit_2", list_transaction_log("bybit_2", path=db))
    assert v.realized_usd == -21.0, f"two walks moved the figure: {v.realized_usd}"


# ── E70: a second Bybit sub-account UID sharing one logical account_id ───────
# bybit_2's real history spans TWO Bybit UIDs (BL-20260831: the live wallet-
# truth pull reproduces one sub-account, MAIN -1.52, to the cent, and cannot
# see the other, SUB -261.01, because a single API key belongs to one UID).
# These tests pin the collision hazard this creates and the fix for it.

def test_two_uids_emitting_the_SAME_raw_id_would_silently_collide_without_a_prefix(tmp_path):
    """THE HAZARD, proven rather than asserted. `bybit_transaction_log.txn_id`
    is a bare PRIMARY KEY on the venue's row `id`; two DIFFERENT Bybit UIDs
    can legitimately emit the identical `id` (each UID has its own sequence),
    so pulling both into the same account_id with no discriminator makes the
    second `INSERT OR IGNORE` silently drop real money as an apparent re-pull
    of the first. This is the CONTROL: two independent credentials sharing
    "t1"/"t2" ids, pulled with NO prefix, undercount."""
    db = tmp_path / "s.sqlite"
    main_client = FakeClient([[_r(1, change="-100"), _r(2, change="-25")]])
    sub_client = FakeClient([[_r(1, change="-9000"), _r(2, change="-50")]])  # same raw ids, different UID

    puller.pull_one_account(
        "bybit_2", "k1", "s1", demo=False, days=7, store_path=db,
        client_factory=lambda *a, **kw: main_client, now_ms=1_760_000_500_000,
    )
    inserted = puller.pull_one_account(
        "bybit_2", "k2", "s2", demo=False, days=7, store_path=db,
        client_factory=lambda *a, **kw: sub_client, now_ms=1_760_000_500_000,
        # deliberately NO sub_id_prefix, to demonstrate the hazard
    )
    assert inserted == 0, "the collision must reproduce here, or this test proves nothing"
    v = wt.compute_wallet_truth("bybit_2", list_transaction_log("bybit_2", path=db))
    assert v.realized_usd == -125.0, "the second UID's -9050 was silently dropped by the collision"


def test_sub_id_prefix_stops_the_collision_and_sums_both_uids(tmp_path):
    """THE FIX: pulling the second UID with `sub_id_prefix` set keeps its rows
    in a disjoint keyspace, so both UIDs' money is counted — no change needed
    to bybit_wallet_truth.compute_wallet_truth, which just sums every row
    stored under the account_id."""
    db = tmp_path / "s.sqlite"
    main_client = FakeClient([[_r(1, change="-100"), _r(2, change="-25")]])
    sub_client = FakeClient([[_r(1, change="-9000"), _r(2, change="-50")]])  # same raw ids as MAIN

    n_main = puller.pull_one_account(
        "bybit_2", "k1", "s1", demo=False, days=7, store_path=db,
        client_factory=lambda *a, **kw: main_client, now_ms=1_760_000_500_000,
    )
    n_sub = puller.pull_one_account(
        "bybit_2", "k2", "s2", demo=False, days=7, store_path=db,
        client_factory=lambda *a, **kw: sub_client, now_ms=1_760_000_500_000,
        sub_id_prefix="BYBIT_API_KEY_2_SUB",
    )
    assert n_main == 2 and n_sub == 2, "both UIDs' rows must land, none dropped"
    v = wt.compute_wallet_truth("bybit_2", list_transaction_log("bybit_2", path=db))
    assert v.realized_usd == -9175.0, "both sub-accounts' wallet deltas must be summed"

    # a THIRD pull of the primary UID (the normal hourly re-pull) must not
    # re-collide with the prefixed rows or move the figure.
    puller.pull_one_account(
        "bybit_2", "k1", "s1", demo=False, days=7, store_path=db,
        client_factory=lambda *a, **kw: FakeClient([[_r(1, change="-100"), _r(2, change="-25")]]),
        now_ms=1_760_000_500_000,
    )
    v2 = wt.compute_wallet_truth("bybit_2", list_transaction_log("bybit_2", path=db))
    assert v2.realized_usd == -9175.0, "a repeat primary pull must not move the combined figure"


def test_main_discovers_and_pulls_a_configured_sub_account(tmp_path, monkeypatch):
    """End-to-end through `main()`: when both `<key_env>_SUB` env vars are set,
    the sub-account is pulled automatically and named in the summary. When
    they are NOT set (every account's normal state today), it is a silent
    no-op — never a warning, since an account with one UID is not a fault.

    `pull_one_account` itself is monkeypatched (rather than the pybit client)
    because `main()` always resolves the real venue client internally; the
    collision-safety and summing behaviour of the real function is already
    pinned end-to-end by the two tests above.
    """
    from src.runtime.exchange_accounts import BybitFillAccount

    acct = BybitFillAccount(
        account_id="bybit_2", key_env="BYBIT_API_KEY_2", secret_env="BYBIT_API_SECRET_2",
        category="linear", symbols=("BTCUSDT",), demo=False,
        key_env_sub="BYBIT_API_KEY_2_SUB", secret_env_sub="BYBIT_API_SECRET_2_SUB",
    )
    monkeypatch.setattr(puller, "live_bybit_fill_accounts", lambda: [acct])

    calls = []

    def fake_pull(account_id, key, secret, **kw):
        calls.append((key, kw.get("sub_id_prefix", "")))
        return 1

    monkeypatch.setattr(puller, "pull_one_account", fake_pull)

    monkeypatch.setenv("BYBIT_API_KEY_2", "primary-key")
    monkeypatch.setenv("BYBIT_API_SECRET_2", "primary-secret")
    monkeypatch.delenv("BYBIT_API_KEY_2_SUB", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET_2_SUB", raising=False)

    rc = puller.main(["--account", "bybit_2", "--json"])
    assert rc == 0
    assert calls == [("primary-key", "")], "no sub-account configured -> primary only, no error"

    monkeypatch.setenv("BYBIT_API_KEY_2_SUB", "sub-key")
    monkeypatch.setenv("BYBIT_API_SECRET_2_SUB", "sub-secret")
    calls.clear()

    rc2 = puller.main(["--account", "bybit_2", "--json"])
    assert rc2 == 0
    assert calls == [("primary-key", ""), ("sub-key", "BYBIT_API_KEY_2_SUB")], (
        "both credential pairs must be dialled once configured, the second "
        "carrying a non-empty sub_id_prefix"
    )
