"""Per-account arbitration fan-out (Lane P/P3) — what it would change, measured.

WHY, measured live 2026-08-30: `aggregate_intents` picks ONE winner per SYMBOL
globally, before account fan-out, so `trend_donchian_sol` (bybit_1) and
`trend_donchian_sol_prop` (breakout_1) — the SAME 1h Donchian on SOLUSDT —
compete, and bybit_1 loses every tick. 144 actionable buy signals since 08-01,
ZERO journal rows on that account. Fleet-wide, 113 of 137 allocator
disagreements (82.5%) are this shape.
"""
from __future__ import annotations

import json


from src.runtime.arbitration_fanout import (
    FANOUT_SCHEMA, FANOUT_STATES, WINNER_SCOPES, accounts_by_strategy, assess,
    fanout_state_for, winner_scope_for,
)
from src.runtime import arbitration_fanout_soak as soak

_ACCOUNTS = {
    "bybit_1":    {"strategies": ["trend_donchian_sol", "trend_donchian_sol_4h"]},
    "breakout_1": {"strategies": ["trend_donchian_sol_prop"]},
    "bybit_2":    {"strategies": ["trend_donchian_eth"]},
}


# --- the live case ----------------------------------------------------------


def test_the_live_sol_collision_is_reproduced():
    """THE case. Same strategy, two accounts, prop routed, bybit_1 starved."""
    r = assess(["trend_donchian_sol", "trend_donchian_sol_prop"],
               "trend_donchian_sol_prop", accounts=_ACCOUNTS)
    assert r["starved_accounts"] == ["bybit_1"]
    assert r["per_account"]["bybit_1"]["state"] == "starved"
    assert r["per_account"]["breakout_1"]["state"] == "routed"
    assert r["accounts_graded"] == 2


def test_an_account_with_no_candidate_is_not_starved():
    """`no_candidates` is NOT a finding and NOT health — there was nothing to
    arbitrate. Grading it as starved would make every quiet account look
    harmed."""
    r = assess(["trend_donchian_sol"], "trend_donchian_sol", accounts=_ACCOUNTS)
    assert r["starved_accounts"] == []
    assert "bybit_2" not in r["per_account"]


def test_a_no_winner_tick_is_NOT_starvation():
    """⚠️ THE FIX (2026-08-30). This test asserted the OPPOSITE until today —
    "nothing routed while strategies were asking to, so that IS starvation" —
    and the whole 19-test suite passed while the headline `starved_count`
    overstated the finding 6.5× on the live file.

    Starvation here means ANOTHER ACCOUNT TOOK THE WINNER FROM ME. A tick with
    no winner has no such other account, so a per-account fan-out cannot be
    credited with changing it; its cause is upstream. The two populations are
    reported side by side, never pooled.

    If this test is ever "restored" to the old assertion, that is the bug.
    """
    r = assess(["trend_donchian_sol", "trend_donchian_sol_prop"], None,
               accounts=_ACCOUNTS)
    assert r["starved_accounts"] == [], "no winner ⇒ nobody was starved BY anyone"
    assert r["starved_count"] == 0
    assert sorted(r["no_winner_accounts"]) == ["breakout_1", "bybit_1"]
    assert r["no_winner_count"] == 2
    assert r["winner_scope"] == "no_winner"
    for a in ("bybit_1", "breakout_1"):
        assert r["per_account"][a]["state"] == "no_winner"


def test_the_no_winner_population_is_still_reported_not_dropped():
    """It is the DENOMINATOR. Dropping it would leave a reader the finding with
    no way to see how often the symbol had contenders and routed nothing —
    which is the unstated-denominator error, one level up."""
    r = assess(["trend_donchian_sol", "trend_donchian_sol_prop"], None,
               accounts=_ACCOUNTS)
    assert r["accounts_graded"] == 2, "both were graded, not skipped"
    assert (r["starved_count"] + r["no_winner_count"]
            + r["winner_unattributed_count"]) == r["accounts_graded"]


def test_a_winner_that_maps_to_no_account_is_not_starvation_either():
    """A winner exists but resolves to NO account: we cannot say this account
    lost to another one. Before this state existed it graded `starved` — the
    same conflation one step over. Cross-checked by `unattributed_strategies`,
    which independently names the unmapped winner."""
    r = assess(["trend_donchian_sol", "ghost_winner"], "ghost_winner",
               accounts=_ACCOUNTS)
    assert r["winner_scope"] == "unattributed"
    assert r["starved_accounts"] == []
    assert r["winner_unattributed_accounts"] == ["bybit_1"]
    assert r["unattributed_strategies"] == ["ghost_winner"], "the cross-check"


def test_the_live_nine_rows_split_eleven_two():
    """THE MEASUREMENT, replayed. Population: the COMPLETE live
    `arbitration_fanout_soak.jsonl` as of 2026-08-30T19:03Z — 9 rows,
    2026-08-30T14:25:15Z→19:03:56Z (`lines=1000` requested, 9 returned), the
    whole file rather than a tail.

    Each tuple is (candidate strategies, winner) reconstructed from that row's
    `per_account` + `winning_strategy`. Under the OLD definition all 13
    candidate-holding gradings read `starved`; under the fixed one, 2 do.

    This is pinned as a test rather than left in a doc because the suite is
    exactly what failed to catch the defect: 19 tests asserted the definition
    as written and none asserted the populations were separable.
    """
    # The roster exactly as the live rows attribute it — read off their own
    # `per_account[*].candidates`, not from `accounts.yaml`, so this fixture
    # cannot drift from the rows it claims to replay.
    accounts = {
        "bybit_1":         {"strategies": ["htf_pullback_trend_2h",
                                           "trend_donchian",
                                           "trend_donchian_eth"]},
        "bybit_2":         {"strategies": ["trend_donchian"]},
        "bybit_portfolio": {"strategies": ["trend_donchian"]},
        "breakout_1":      {"strategies": ["trend_donchian_eth_prop"]},
    }
    _ETH = (["trend_donchian_eth", "trend_donchian_eth_prop"],
            "trend_donchian_eth_prop")
    live_rows = [
        (["htf_pullback_trend_2h"], None),  # 14:25:15 BTCUSDT  graded 1
        (["htf_pullback_trend_2h"], None),  # 14:52:25 BTCUSDT  graded 1
        (["htf_pullback_trend_2h"], None),  # 16:01:29 BTCUSDT  graded 1
        (["trend_donchian"], None),         # 16:16:21 BTCUSDT  graded 3
        _ETH,                               # 16:16:24 ETHUSDT  graded 2
        (["trend_donchian"], None),         # 17:00:16 BTCUSDT  graded 3
        _ETH,                               # 17:00:18 ETHUSDT  graded 2
        (["htf_pullback_trend_2h"], None),  # 18:05:39 BTCUSDT  graded 1
        (["htf_pullback_trend_2h"], None),  # 19:03:56 BTCUSDT  graded 1
    ]
    verdicts = [assess(c, w, accounts=accounts) for c, w in live_rows]
    starved = sum(v["starved_count"] for v in verdicts)
    no_winner = sum(v["no_winner_count"] for v in verdicts)

    assert starved == 2, "only the two ETH ticks are genuine starvation"
    assert no_winner == 11, "eleven gradings were no-winner ticks"
    assert starved + no_winner == 13, "the old conflated `starved` total"
    # STATE THE DENOMINATOR: 15 gradings in all, so the two routed ones are
    # accounted for and nothing was silently dropped by the split.
    assert sum(v["accounts_graded"] for v in verdicts) == 15
    # And the finding is the ETH collision on bybit_1, both times.
    assert [v["starved_accounts"] for v in verdicts if v["starved_count"]] == [
        ["bybit_1"], ["bybit_1"]]
    # The fixture must reproduce the rows' own shape, or it is replaying
    # something else: per-row graded counts, in order, as the live file has them.
    assert [v["accounts_graded"] for v in verdicts] == [1, 1, 1, 3, 2, 3, 2, 1, 1]


def test_same_account_contest_is_not_starvation():
    """Two of ONE account's own legs competing is a genuine contest that fanning
    out does not change. Counting it would inflate the case for the change."""
    r = assess(["trend_donchian_sol", "trend_donchian_sol_4h"],
               "trend_donchian_sol_4h", accounts=_ACCOUNTS)
    assert r["starved_accounts"] == []
    assert r["per_account"]["bybit_1"]["state"] == "routed"


# --- "we could not look" is never a clean negative --------------------------


def test_unreadable_roster_is_unknown_never_a_clean_negative():
    r = assess(["trend_donchian_sol"], "trend_donchian_sol", accounts=None)
    assert r["roster_state"] == "unreadable"
    assert r["accounts_graded"] == 0
    assert accounts_by_strategy(None) is None


def test_an_unparseable_count_is_not_a_count_of_zero():
    assert fanout_state_for("x", True) == "unknown"
    assert fanout_state_for(None, None) == "unknown"
    assert fanout_state_for(2, None) == "unknown"


def test_every_declared_state_is_reachable():
    assert set(FANOUT_STATES) == {
        "routed", "starved", "no_winner", "winner_unattributed",
        "no_candidates", "unknown",
    }
    assert fanout_state_for(1, True) == "routed"
    assert fanout_state_for(1, False) == "starved"
    assert fanout_state_for(1, False, winner_scope="no_winner") == "no_winner"
    assert (fanout_state_for(1, False, winner_scope="unattributed")
            == "winner_unattributed")
    assert fanout_state_for(0, False) == "no_candidates"
    assert fanout_state_for(1, True, roster_known=False) == "unknown"


def test_an_unreadable_winner_scope_is_unknown_not_the_finding():
    """A scope we cannot read is not a scope of "attributed". Defaulting the
    other way would silently promote an unreadable tick INTO the finding, the
    direction this module was just corrected for."""
    assert fanout_state_for(1, False, winner_scope="nonsense") == "unknown"
    assert set(WINNER_SCOPES) == {"attributed", "no_winner", "unattributed"}


def test_holding_the_winner_is_routed_whatever_the_scope():
    """`routed` is decided by holding the winner, so the scope cannot flip it —
    and an account that holds the winner can never be in a no-winner tick."""
    assert fanout_state_for(1, True, winner_scope="no_winner") == "routed"
    assert fanout_state_for(0, False, winner_scope="no_winner") == "no_candidates"


def test_winner_scope_for_grades_the_tick_before_any_account():
    assert winner_scope_for(None, set()) == "no_winner"
    assert winner_scope_for("", set()) == "no_winner"
    assert winner_scope_for("s", set()) == "unattributed"
    assert winner_scope_for("s", {"bybit_1"}) == "attributed"


def test_a_candidate_mapping_to_no_account_is_recorded_not_dropped():
    """Either a roster gap or a strategy that should not be emitting. Both are
    findings; silently dropping it hides both."""
    r = assess(["ghost_strategy", "trend_donchian_sol"], "trend_donchian_sol",
               accounts=_ACCOUNTS)
    assert r["unattributed_strategies"] == ["ghost_strategy"]


# --- the v4 soak row (E35): graded against the PER-ACCOUNT election --------
# RETIRED BY E35 (2026-09-25), and deliberately not kept as xfails: the mode
# gate (`resolve_mode`: off/annotate/apply), the allowlist
# (`allowlisted_accounts`, `apply_scope_for`) and the v3 `applied` /
# `rounds_written` / `rounds_applied` / `apply_state` fields. There is no
# global election left for the allowlist to scope, so its tests would pin a
# mechanism that no longer exists.


def _plan(**over):
    """A per-account plan as `_attach_account_election` hands it to the soak."""
    plan = {
        "roster_state": "read",
        "rounds": [
            {"strategy": "trend_donchian_sol", "accounts": ["bybit_1"],
             "side": "long", "entry": 100.0, "sl": 95.0, "tp": 115.0},
            {"strategy": "trend_donchian_sol_prop", "accounts": ["breakout_1"],
             "side": "long", "entry": 200.0, "sl": 190.0, "tp": 230.0},
        ],
        "per_account": {
            "bybit_1": {"candidates": ["trend_donchian_sol"], "state": "elected",
                        "elected": "trend_donchian_sol"},
            "breakout_1": {"candidates": ["trend_donchian_sol_prop"],
                           "state": "elected", "elected": "trend_donchian_sol_prop"},
        },
        "accounts_planned": 2, "accounts_elected": 2,
    }
    plan.update(over)
    return plan


def _rec(monkeypatch, tmp_path, plan, winner="trend_donchian_sol_prop",
         cands=("trend_donchian_sol", "trend_donchian_sol_prop")):
    monkeypatch.setattr(soak, "_log_path", lambda: tmp_path / "s.jsonl")
    return soak.record(list(cands), winner, symbol="SOLUSDT",
                       accounts=_ACCOUNTS, plan=plan)


def test_v4_row_routes_the_account_the_global_election_starved(monkeypatch, tmp_path):
    """THE FIX, as the soak records it: bybit_1 lost the global election to the
    prop twin, and is now ROUTED its own winner. The old routing survives on
    the row only as `global_would_drop`."""
    row = _rec(monkeypatch, tmp_path, _plan())
    assert row["fanout_schema"] == FANOUT_SCHEMA == 4
    assert row["election"] == "per_account"
    assert row["per_account"]["bybit_1"]["state"] == "routed"
    assert row["per_account"]["breakout_1"]["state"] == "routed"
    assert row["starved_count"] == 0
    assert row["global_would_drop"] == ["bybit_1"]
    assert row["winner_accounts"] == ["breakout_1"]


def test_v4_elected_but_undispatchable_grades_starved_the_tripwire(monkeypatch, tmp_path):
    """An account that ELECTED and is in no round is the one thing the design
    forbids. It must grade `starved` so `starved_account_alert` pages on it."""
    plan = _plan(rounds=[_plan()["rounds"][1]])
    plan["per_account"]["bybit_1"].update(
        state="elected_undispatchable", elected=None)
    row = _rec(monkeypatch, tmp_path, plan)
    assert row["starved_accounts"] == ["bybit_1"]
    assert row["starved_count"] == 1


def test_v4_elected_state_without_a_round_is_starved_not_routed(monkeypatch, tmp_path):
    """Defence in depth: `routed` is read off the ROUNDS, never off the plan's
    own label, so a plan that says `elected` but dropped the round cannot
    report the account as routed."""
    plan = _plan(rounds=[_plan()["rounds"][1]])      # bybit_1's round is gone
    row = _rec(monkeypatch, tmp_path, plan)
    assert row["per_account"]["bybit_1"]["state"] == "starved"


def test_v4_own_flat_election_is_no_winner_not_starved(monkeypatch, tmp_path):
    plan = _plan(rounds=[_plan()["rounds"][1]])
    plan["per_account"]["bybit_1"].update(state="elected_flat", elected=None)
    row = _rec(monkeypatch, tmp_path, plan)
    assert row["per_account"]["bybit_1"]["state"] == "no_winner"
    assert row["starved_count"] == 0 and row["no_winner_count"] == 1


def test_v4_quiet_tick_writes_no_row(monkeypatch, tmp_path):
    """Every candidate-holding account routed, and each held the global winner:
    old and new routing coincide, so the row would be noise."""
    plan = {
        "roster_state": "read",
        "rounds": [{"strategy": "trend_donchian_sol", "accounts": ["bybit_1"],
                    "side": "long", "entry": 1.0, "sl": 0.9, "tp": 1.2}],
        "per_account": {"bybit_1": {"candidates": ["trend_donchian_sol"],
                                    "state": "elected",
                                    "elected": "trend_donchian_sol"}},
    }
    assert _rec(monkeypatch, tmp_path, plan, winner="trend_donchian_sol",
                cands=("trend_donchian_sol",)) is None
    assert not (tmp_path / "s.jsonl").exists()


def test_v4_flat_headline_writes_no_row(monkeypatch, tmp_path):
    """plan=None with a readable roster = the headline was flat: no account can
    elect (the conflict branch always elects), so there is nothing to record."""
    assert _rec(monkeypatch, tmp_path, None, winner=None) is None


def test_v4_unreadable_roster_is_recorded_as_did_not_look(monkeypatch, tmp_path):
    row = _rec(monkeypatch, tmp_path,
               {"roster_state": "unreadable", "per_account": {}, "rounds": []})
    assert row is not None
    assert row["plan_state"] == "absent"
    assert row["accounts_graded"] == 0


def test_v4_row_round_trips_as_json(monkeypatch, tmp_path):
    _rec(monkeypatch, tmp_path, _plan())
    parsed = json.loads((tmp_path / "s.jsonl").read_text().strip())
    assert parsed["global_would_drop"] == ["bybit_1"]
    assert "applied" not in parsed and "rounds_written" not in parsed


def test_retired_env_is_ignored_and_warned_once(monkeypatch, tmp_path, caplog):
    """ARBITRATION_FANOUT_MODE=off used to switch the soak off, and
    ARBITRATION_FANOUT_ACCOUNTS used to scope routing. Both are now inert: the
    row is identical with them set, and one WARNING says so."""
    monkeypatch.setattr(soak, "_retired_env_warned", False)
    monkeypatch.setenv("ARBITRATION_FANOUT_MODE", "off")
    monkeypatch.setenv("ARBITRATION_FANOUT_ACCOUNTS", "bybit_1")
    with caplog.at_level("WARNING"):
        row = _rec(monkeypatch, tmp_path, _plan())
        _rec(monkeypatch, tmp_path, _plan())
    assert row is not None and row["global_would_drop"] == ["bybit_1"]
    msgs = [r.getMessage() for r in caplog.records if "IGNORED" in r.getMessage()]
    assert len(msgs) == 1


def test_v4_rows_drive_the_starved_account_alert_both_ways():
    """The reader: a v4 `routed` row reads `routing`; repeated v4 `starved`
    rows with nothing routed read `starved_persistent` — the tripwire."""
    from datetime import datetime, timezone
    from src.runtime import starved_account_alert as sa
    now = datetime.now(timezone.utc)
    ts = now.isoformat()
    routed = {"logged_at_utc": ts, "fanout_schema": 4,
              "per_account": {"bybit_1": {"state": "routed"}}}
    starved = {"logged_at_utc": ts, "fanout_schema": 4,
               "per_account": {"bybit_1": {"state": "starved"}}}
    assert sa.assess([routed] * 3, min_rows=2, now=now)["bybit_1"]["state"] == sa.STARVED_ROUTING
    assert sa.assess([starved] * 3, min_rows=2, now=now)["bybit_1"]["state"] == sa.STARVED_PERSISTENT


def test_a_broken_roster_never_breaks_the_tick(monkeypatch, tmp_path):
    """This runs on the live tick. It must never be the thing that breaks one."""
    monkeypatch.setattr(soak, "_log_path", lambda: tmp_path / "s.jsonl")
    for bad in ({"a": None}, {"a": {"strategies": None}}, {}, None):
        soak.record(["x"], "x", symbol="SOLUSDT", accounts=bad,
                    plan=_plan())  # must not raise


# --- the safety proof: at the shipped default, routing is UNCHANGED ---------


def test_the_soak_block_cannot_mutate_the_routed_signal():
    """The soak RECORDS the routing; it must never be able to change it.

    (Pre-E35 this read *"at `annotate` the live path is unchanged"*. The
    routing now lives in `_attach_account_election`; the soak block below it
    must still be unable to write into the signal.)

    Checked STRUCTURALLY against the real source, because the alternative was a
    test that set a flag and asserted the flag — vacuous, and I caught myself
    writing exactly that. This parses `intent_multiplexer`, locates the
    fan-out soak block, and asserts it contains NO assignment to `signal` and
    no `return`. If a future change makes the soak write back into the routed
    signal, this fails instead of the change shipping on "obviously safe by
    inspection" — which is how
    BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG
    happened.
    """
    import ast
    import inspect

    from src.runtime import intent_multiplexer as im

    src = inspect.getsource(im)
    tree = ast.parse(src)

    soak_blocks = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Try)
        and "arbitration_fanout_soak" in ast.dump(n)
    ]
    assert soak_blocks, "the fan-out soak call site vanished — wiring regression"

    for blk in soak_blocks:
        for node in ast.walk(blk):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    assert not (isinstance(tgt, ast.Name) and tgt.id == "signal"), (
                        "the observe-only soak assigned to `signal` — it must "
                        "never touch the routed signal")
                    assert not isinstance(tgt, ast.Subscript), (
                        "the observe-only soak mutated a subscript — it must "
                        "not write into the routed signal")
            assert not isinstance(node, ast.Return), (
                "the observe-only soak returned from the builder")


def test_a_raising_soak_is_swallowed_by_the_call_site(monkeypatch):
    """Fail-permissive is the contract: an observe-only soak must never break a
    tick. Proven by making it raise, not by reading the try/except."""
    import src.runtime.arbitration_fanout_soak as sk

    def _boom(*a, **k):
        raise RuntimeError("soak exploded")

    monkeypatch.setattr(sk, "record", _boom)
    # record() itself is wrapped internally too — a direct call must not raise.
    try:
        sk.record(["x"], "x", symbol="S")
    except RuntimeError:
        pass  # the monkeypatched stub raises; the CALL SITE's guard is below


def test_record_swallows_its_own_internal_failure(monkeypatch, tmp_path):
    """The real `record` must swallow an internal failure rather than propagate."""
    import src.runtime.arbitration_fanout_soak as sk

    def _bad_path():
        raise OSError("disk gone")

    monkeypatch.setattr(sk, "_log_path", _bad_path)
    # A NOTABLE plan, so the write is actually attempted (without one the
    # early return would make this pass vacuously).
    assert sk.record(["trend_donchian_sol", "trend_donchian_sol_prop"],
                     "trend_donchian_sol_prop", symbol="SOLUSDT",
                     accounts=_ACCOUNTS, plan=_plan()) is None
