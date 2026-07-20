"""Forensics extraction for BL-20260720-ICTSCALP-PASTSTOP-EXITS.

Run on the trainer VM (repo root) against the synced trade_journal.db.
Dumps, for the suspect Jun-21..23 bybit BTCUSDT rows, the FULL notes JSON
(closed_by / closed_reason / exit_price_source / reconcile markers), the
row timestamps, and every other bybit_2/bybit_1 BTCUSDT trade row in the
window (any strategy) so the netted-position timeline can be rebuilt.
Also dumps the matching order_packages meta close markers.
"""
import json
import sqlite3

conn = sqlite3.connect('file:data/trade_journal.db?mode=ro', uri=True)
conn.row_factory = sqlite3.Row

SUSPECTS = (2530, 2757, 2764, 2765, 2783, 2796, 2797, 2768)

out = {"suspects": [], "window_rows": [], "packages": []}

for tid in SUSPECTS:
    r = conn.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone()
    if r is None:
        continue
    d = dict(r)
    try:
        d["notes"] = json.loads(d.get("notes") or "{}")
    except Exception:
        pass
    for k in ("entry_reason",):
        d.pop(k, None)
    out["suspects"].append(d)

q = ("SELECT id, strategy_name, account_id, direction, position_size, "
     "entry_price, exit_price, stop_loss, take_profit_1, pnl, status, "
     "exit_reason, timestamp, created_at, closed_at, setup_type, "
     "reconcile_status, order_package_id "
     "FROM trades WHERE symbol='BTCUSDT' "
     "AND timestamp >= '2026-06-21' AND timestamp < '2026-06-24' "
     "ORDER BY timestamp")
out["window_rows"] = [dict(r) for r in conn.execute(q)]

pkg_ids = {d.get("order_package_id") for d in out["suspects"] if d.get("order_package_id")}
for pid in sorted(p for p in pkg_ids if p):
    r = conn.execute(
        "SELECT order_package_id, status, close_reason, created_at, updated_at, meta "
        "FROM order_packages WHERE order_package_id=?", (pid,)).fetchone()
    if r is None:
        continue
    d = dict(r)
    try:
        m = json.loads(d.pop("meta") or "{}")
        d["meta_close_keys"] = {k: m.get(k) for k in (
            "rejected_at", "rejected_by", "rejected_reason", "closed_at",
            "closed_by", "close_reason", "stuck_alert_emitted_at",
        ) if m.get(k) is not None}
    except Exception:
        pass
    out["packages"].append(d)

print("JSON_START")
print(json.dumps(out, default=str, separators=(",", ":")))
print("JSON_END")
