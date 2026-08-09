"""Explode the M20 coverage matrix's bundled rows — one row per leg.

BL-20260809-COVERAGE-MATRIX-MULTILEG-ROW-ONE-STATUS.

v2 fixes two bugs in v1, both caught by arithmetic rather than by re-reading:
  * v1 matched bundles with startswith(), which also hit the STANDALONE
    `xauusd_trend_1h` row -> duplicated legs (48 rows where 46 was expected).
    Matching is now on the EXACT strategy string.
  * v1's bundle scan keyed on "/" and "fleet" and missed
    `trend_donchian_{eth,sol,xrp,ada,avax}_4h` -> 6 of 7 bundles.

Per-leg statuses come ONLY from what each ref explicitly says (the file's own
rule: "Statuses only from verified evidence — never inferred"):
  * ref unqualified / "all symbols fail"  -> bundle status applies to every leg
  * ref names passes and fails            -> split accordingly
  * ref silent on a leg, or the run ERRORED/TIMED OUT -> `pending`
    (an un-run cell is not a negative, however plausible the neighbours)
"""
import json
from collections import Counter

P = "docs/research/exit-refinement-coverage.json"
with open(P, encoding="utf-8") as _fh:
    d = json.load(_fh)
COLS = d["lever_columns"]
NOTE = (" || EXPLODED 2026-08-09 from bundled row {!r} "
        "(BL-20260809-COVERAGE-MATRIX-MULTILEG-ROW-ONE-STATUS). Per-leg status from "
        "this ref's explicit wording only; silent or un-run -> `pending`, never an "
        "inherited verdict.")

BUNDLES = {
 "sol_pullback_2h / xrp_pullback_2h / ada_pullback_2h / avax_pullback_2h / eth_pullback_prop_2h": (
   [("sol_pullback_2h","SOLUSDT","2h","live"), ("xrp_pullback_2h","XRPUSDT","2h","live"),
    ("ada_pullback_2h","ADAUSDT","2h","live"), ("avax_pullback_2h","AVAXUSDT","2h","live"),
    ("eth_pullback_prop_2h","ETHUSDT","2h","live")],
   {"trail_decay": {"sol_pullback_2h":"shipped","xrp_pullback_2h":"honest_negative",
                    "ada_pullback_2h":"honest_negative","avax_pullback_2h":"honest_negative",
                    "eth_pullback_prop_2h":"honest_negative"}}),

 "trend_donchian_{eth,sol,xrp,ada,avax}_4h": (
   [("trend_donchian_eth_4h","ETHUSDT","4h","live"), ("trend_donchian_sol_4h","SOLUSDT","4h","live"),
    ("trend_donchian_xrp_4h","XRPUSDT","4h","live"), ("trend_donchian_ada_4h","ADAUSDT","4h","live"),
    ("trend_donchian_avax_4h","AVAXUSDT","4h","live")],
   {# "XRP-4h stale8 PASS 5/6 — Tier-3 PR #6229; eth/sol/ada/avax fail"
    "stale_stop": {"trend_donchian_xrp_4h":"shipped","trend_donchian_eth_4h":"honest_negative",
                   "trend_donchian_sol_4h":"honest_negative","trend_donchian_ada_4h":"honest_negative",
                   "trend_donchian_avax_4h":"honest_negative"},
    # "eth/sol/xrp all cells is_oos_fail; ada ... ERRORED, avax harness TIMEOUT" -> not measured
    "trail_decay": {"trend_donchian_eth_4h":"honest_negative","trend_donchian_sol_4h":"honest_negative",
                    "trend_donchian_xrp_4h":"honest_negative","trend_donchian_ada_4h":"pending",
                    "trend_donchian_avax_4h":"pending"}}),

 "mes_trend_long_1d / mgc_pullback_1d / mhg_pullback_1d": (
   [("mes_trend_long_1d","MES","1d","live"), ("mgc_pullback_1d","MGC","1d","live"),
    ("mhg_pullback_1d","MHG","1d","live")],
   {"trail_geometry": {"mes_trend_long_1d":"shipped","mhg_pullback_1d":"shipped",
                       "mgc_pullback_1d":"honest_negative"},
    "stale_stop": {"mhg_pullback_1d":"passed_unshipped","mes_trend_long_1d":"honest_negative",
                   "mgc_pullback_1d":"honest_negative"},
    "giveback_stop": {"mgc_pullback_1d":"honest_negative","mes_trend_long_1d":"pending",
                      "mhg_pullback_1d":"pending"},
    "trail_decay": {"mhg_pullback_1d":"shipped","mes_trend_long_1d":"honest_negative",
                    "mgc_pullback_1d":"honest_negative"},
    "vol_trail": {"mgc_pullback_1d":"honest_negative","mhg_pullback_1d":"honest_negative",
                  "mes_trend_long_1d":"pending"}}),

 "equities 1d fleet (spy/qqq/tqqq/qld/iwm/splg/scha trend; gld/tlt/ief/slv/gdx/iaum pullback)": (
   [("spy_trend_1d","SPY","1d","live"),("qqq_trend_1d","QQQ","1d","live"),
    ("tqqq_trend_1d","TQQQ","1d","live"),("qld_trend_1d","QLD","1d","live"),
    ("iwm_trend_1d","IWM","1d","live"),("splg_trend_1d","SPLG","1d","live"),
    ("scha_trend_1d","SCHA","1d","live"),("gld_pullback_1d","GLD","1d","live"),
    ("tlt_pullback_1d","TLT","1d","live"),("ief_pullback_1d","IEF","1d","live"),
    ("slv_pullback_1d","SLV","1d","live"),("gdx_pullback_1d","GDX","1d","live"),
    ("iaum_pullback_1d","IAUM","1d","live")],
   {"trail_decay": dict(
      **{k:"shipped" for k in ("scha_trend_1d","slv_pullback_1d","iwm_trend_1d",
                               "gld_pullback_1d","splg_trend_1d","iaum_pullback_1d")},
      **{k:"honest_negative" for k in ("spy_trend_1d","qqq_trend_1d","tqqq_trend_1d",
                                       "qld_trend_1d","tlt_pullback_1d","ief_pullback_1d",
                                       "gdx_pullback_1d")}),
    "vol_trail": dict(
      **{k:"honest_negative" for k in ("gld_pullback_1d","tlt_pullback_1d","ief_pullback_1d",
                                       "slv_pullback_1d","gdx_pullback_1d","iaum_pullback_1d")},
      **{k:"pending" for k in ("spy_trend_1d","qqq_trend_1d","tqqq_trend_1d","qld_trend_1d",
                               "iwm_trend_1d","splg_trend_1d","scha_trend_1d")})}),

 "equities 1h fleet (gld/spy/qqq/tlt pullback; slv/uso trend)": (
   [("gld_pullback_1h","GLD","1h","live"),("spy_pullback_1h","SPY","1h","live"),
    ("qqq_pullback_1h","QQQ","1h","live"),("tlt_pullback_1h","TLT","1h","live"),
    ("slv_trend_1h","SLV","1h","live"),("uso_trend_1h","USO","1h","live")],
   {"trail_geometry": {"tlt_pullback_1h":"shipped","gld_pullback_1h":"honest_negative",
                       "spy_pullback_1h":"honest_negative","qqq_pullback_1h":"honest_negative",
                       "slv_trend_1h":"honest_negative","uso_trend_1h":"pending"},
    "giveback_stop": dict(uso_trend_1h="shipped",
      **{k:"pending" for k in ("gld_pullback_1h","spy_pullback_1h","qqq_pullback_1h",
                               "tlt_pullback_1h","slv_trend_1h")}),
    "trail_decay": dict(tlt_pullback_1h="shipped", uso_trend_1h="honest_negative",
      **{k:"pending" for k in ("gld_pullback_1h","spy_pullback_1h","qqq_pullback_1h","slv_trend_1h")}),
    "vol_trail": {"qqq_pullback_1h":"passed_unshipped","gld_pullback_1h":"honest_negative",
                  "spy_pullback_1h":"honest_negative","tlt_pullback_1h":"honest_negative",
                  "slv_trend_1h":"pending","uso_trend_1h":"pending"}}),

 "xauusd_trend_1h / mgc_trend_1h": (
   # execution VERIFIED in config/strategies.yaml 2026-08-09:
   #   xauusd_trend_1h  enabled: false  (disabled 2026-07-04; only account shelved)
   #   mgc_trend_1h     execution: shadow (Tier-3 demotion 2026-06-18)
   [("xauusd_trend_1h","XAUUSD","1h","disabled"), ("mgc_trend_1h","MGC","1h","shadow")],
   # xauusd trail_mult: 4.0 IS declared in config (M20 sweep wf 6/6 cited inline), so
   # `passed_unshipped` ("awaiting implementation") is false. The lever shipped; the LEG
   # is off, which the row's own execution:disabled records. Field beats inference.
   {"trail_geometry": {"mgc_trend_1h":"shipped","xauusd_trend_1h":"shipped"}}),
}

# The standalone xauusd_trend_1h row is STALE and is merged into the exploded one.
# Verified: its blocked cells cite "candle coverage (task #27)", superseded by the
# 2026-07-12 GC=F-proxy fleet sweep; and config carries `trail_mult: 4.0` with that
# sweep's citation, so `shipped` is the field-backed value. It does hold one verdict
# the bundle lacks (vol_trail #6507), so the merge is per-cell, not row-replacement.
STANDALONE_STALE = "xauusd_trend_1h"
KEEP_FROM_STANDALONE = {"vol_trail", "exit_head_ml"}  # exit_head_ml: `blocked` names a real reason

before = Counter()
for r in d["rows"]:
    for c in COLS:
        v = r.get(c)
        before[v.get("status") if isinstance(v, dict) else v] += 1

standalone = next(r for r in d["rows"] if r["strategy"] == STANDALONE_STALE)

n_expected_legs = sum(len(v[0]) for v in BUNDLES.values())
new_rows, seen_bundles = [], set()
for r in d["rows"]:
    strat = r["strategy"]
    if strat == STANDALONE_STALE:
        continue                                   # merged into the exploded leg below
    if strat not in BUNDLES:
        new_rows.append(r)
        continue
    seen_bundles.add(strat)
    legs, mapping = BUNDLES[strat]
    for name, sym, tf, ex in legs:
        row = {"strategy": name, "symbol": sym, "tf": tf, "execution": ex,
               "exploded_from": strat}
        for c in COLS:
            src = r.get(c)
            src_status = src.get("status") if isinstance(src, dict) else src
            src_ref = src.get("ref") if isinstance(src, dict) else None
            rule = mapping.get(c)
            status = src_status if rule is None else rule[name]
            if name == STANDALONE_STALE and c in KEEP_FROM_STANDALONE:
                sv = standalone.get(c) or {}
                status = sv.get("status")
                src_ref = (sv.get("ref") or "") + (
                    " || MERGED 2026-08-09: kept from the stale standalone "
                    "`xauusd_trend_1h` row, which carried a verdict the bundled row "
                    "did not. Its other cells were `blocked: candle coverage (task #27)`, "
                    "superseded by the 2026-07-12 GC=F-proxy fleet sweep.")
            row[c] = {"status": status,
                      "ref": (src_ref or "") + NOTE.format(strat[:58])}
        new_rows.append(row)

missing = set(BUNDLES) - seen_bundles
if missing:
    raise SystemExit(f"BUNDLE NEVER MATCHED (exact-string drift): {missing}")

# --- arithmetic cross-check (RULE ONE #4): counts must reconcile exactly -------
expected = len(d["rows"]) - len(BUNDLES) - 1 + n_expected_legs   # -1 = merged standalone
if len(new_rows) != expected:
    raise SystemExit(f"ROW COUNT MISMATCH: got {len(new_rows)}, expected {expected}")
names = [r["strategy"] for r in new_rows]
dups = {n for n in names if names.count(n) > 1}
if dups:
    raise SystemExit(f"DUPLICATE LEGS: {dups}")

d["rows"] = new_rows
d["_doc"] += (
    " ROWS ARE ONE LEG EACH as of 2026-08-09 "
    "(BL-20260809-COVERAGE-MATRIX-MULTILEG-ROW-ONE-STATUS): "
    "bundled rows carried ONE status for a whole family, so the status "
    "described only the leg that passed and the roll-up over-counted `shipped`. Exploded "
    "rows carry `exploded_from`. The `shadow fleet` row is deliberately NOT exploded — its "
    "label does not enumerate its legs and it is execution:shadow, outside the live-leg "
    "done-condition. DO NOT ADD A NEW BUNDLED ROW.")
d["updated_at"] = "2026-08-09T00:00:00+00:00"
with open(P, "w", encoding="utf-8") as _out:
    json.dump(d, _out, indent=2, ensure_ascii=False)
    _out.write("\n")

after = Counter()
for r in d["rows"]:
    for c in COLS:
        v = r.get(c)
        after[v.get("status") if isinstance(v, dict) else v] += 1
print(f"rows {len(new_rows)} (expected {expected}) · bundles exploded {len(seen_bundles)}/7 "
      f"· standalone merged 1 · duplicate legs 0")
print(f"cells {sum(after.values())}\n")
print(f"{'status':30}{'before':>8}{'after':>8}{'delta':>8}")
for k in sorted(set(before) | set(after), key=lambda x: -after.get(x, 0)):
    b, a = before.get(k, 0), after.get(k, 0)
    print(f"{k!s:30}{b:8}{a:8}{a-b:+8}")
