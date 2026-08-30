#!/usr/bin/env python3
"""Flatten a compat-matrix run into a durable, landable per-account corpus.

WHY THIS EXISTS
---------------
`gld-compat-matrix.yml` ends at `actions/upload-artifact`, and a PM-side session
has **no artifact download** (root `CLAUDE.md`). So every verdict it has ever
computed is unreachable the moment the run ends —
`BL-20260827-EIGHTEEN-EVIDENCE-WORKFLOWS-UPLOAD-AND-LAND-NOTHING`, of which this
workflow is one instance.

That became blocking on 2026-08-30: `research/queue/RQ-20260827-001.yaml` routes
to this workflow and declares `lands.store:
docs/research/gld-compat-matrix-verdicts.jsonl` with `min_rows: 1` — and that
file **has never existed on `main`**. It is the FIRST job an armed cron would
fire, so arming without this would have produced, on day one, exactly the
failure the disposition ledger was built to detect: a job that exits 0 having
landed nothing. R2: *"A job that exits 0 having landed nothing is a FAILED job."*

⚠️ **THE PRODUCER WRITES `account`; THIS EMITS `account_id`.** Not an oversight —
the queue job's `assert_field` is `account_id`, and every other store in the repo
keys accounts that way (`trades.account_id`, `/api/bot/...`). The rename happens
HERE, once, at the corpus boundary; `account_compat_matrix.py` is unchanged, so
nobody reading the producer should expect to find `account_id` in it.

⚠️ **EVERY ROW CARRIES `run_generated_at`, AND THE RUN STAMP IS PRINTED.** The
store is CUMULATIVE, so an assertion like `--field account_id --contains bybit`
would be satisfied by history and can never fail — measured on the e35 corpus,
which reported `landed - 6624 rows, exit 0` for a run whose rows were entirely on
an unmerged branch. Scope the assertion on THIS run's stamp or it is theatre.

⚠️ **AN EMPTY SCAN IS A REFUSAL, NOT A CLEAN NO-OP** (exit 2). A silent zero-row
success is how a broken producer reads as a quiet one.

Tier-1: reads run artifacts, writes one committed JSONL. No live path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STORE = REPO / "docs/research/gld-compat-matrix-verdicts.jsonl"

#: Payload keys that describe the RUN, copied onto every row so a row is
#: self-describing once it leaves the file it came from.
_RUN_KEYS = ("strategy", "symbol", "asset_class", "fee_bps_roundtrip",
             "n_ledger_trades", "horizon_months", "overrides", "data")


def rows_from(payload: dict) -> list[dict]:
    """One flat row per account in a compat payload."""
    stamp = payload.get("generated_at")
    if not stamp:
        raise ValueError("payload carries no generated_at — a row that cannot be "
                         "dated cannot be scoped to its run")
    out = []
    for r in payload.get("rows") or []:
        row = {"corpus": "gld_compat", "run_generated_at": stamp}
        for k in _RUN_KEYS:
            row[k] = payload.get(k)
        # the rename, at the boundary — see the module docstring
        row["account_id"] = r.get("account")
        for k, v in r.items():
            if k != "account":
                row[k] = v
        out.append(row)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in-dir", default="artifacts/gld")
    ap.add_argument("--store", default=str(STORE))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()

    src = sorted(Path(a.in_dir).glob("compat_*.json"))
    if not src:
        print(f"::error::no compat_*.json under {a.in_dir} — refusing to report a "
              "clean no-op for a scan that found nothing", file=sys.stderr)
        return 2

    incoming: list[dict] = []
    for p in src:
        try:
            incoming.extend(rows_from(json.loads(p.read_text())))
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"::error::{p}: {exc}", file=sys.stderr)
            return 2

    if not incoming:
        print("::error::compat payloads carried zero account rows — a verdict set "
              "with no accounts is a producer failure, not an empty result",
              file=sys.stderr)
        return 2

    stamps = sorted({r["run_generated_at"] for r in incoming})
    for s in stamps:
        print(f"run-stamp: {s}")

    store = Path(a.store)
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a") as fh:
        for r in incoming:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    # READ IT BACK. `exit 0` from a write is not evidence the write landed.
    back = [json.loads(l) for l in store.read_text().splitlines() if l.strip()]
    mine = [r for r in back if r.get("run_generated_at") in set(stamps)]
    if len(mine) != len(incoming):
        print(f"::error::wrote {len(incoming)} rows but read back {len(mine)} for "
              "this run — refusing to report success", file=sys.stderr)
        return 2
    print(f"appended {len(incoming)} row(s) from {len(src)} payload(s); "
          f"store now {len(back)} rows")
    return 0


def _selftest() -> int:
    ok = fail = 0

    def check(name, cond):
        nonlocal ok, fail
        if cond: ok += 1
        else:
            fail += 1; print(f"  FAIL: {name}")

    p = {"generated_at": "2026-08-30T10:00:00+00:00", "strategy": "gld_pullback_1h",
         "symbol": "GLD", "rows": [{"account": "alpaca_portfolio", "verdict": "ROUTE",
                                    "kind": "standard", "class": "paper"}]}
    r = rows_from(p)[0]
    check("account -> account_id at the boundary", r["account_id"] == "alpaca_portfolio")
    check("the producer's key is not carried through", "account" not in r)
    check("the run stamp rides on the row", r["run_generated_at"] == p["generated_at"])
    check("the verdict survives", r["verdict"] == "ROUTE")
    check("rows are tagged with their corpus", r["corpus"] == "gld_compat")

    try:
        rows_from({"rows": [{"account": "x"}]})
        check("an undateable payload is REFUSED", False)
    except ValueError:
        check("an undateable payload is REFUSED", True)

    check("a payload with no rows yields none", rows_from({"generated_at": "t"}) == [])
    print(f"selftest: {ok}/{ok + fail} passed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
