📡 **MI-158 POST-MERGE LIVE READ-BACK** (`session_016Li3hqzjKh3bAX7HYuGu23`) — the done-condition's last item, run.

**PR [#11232](https://github.com/benbaichmankass/Metis-Insights/pull/11232) MERGED as `3f88bff`.** Content on `main` is exactly what was approved: `gld_pullback_1h` `tp_r: 4.0`, `tp_intent: {mode: r_multiple, basis: e35_cell_tp4, calibrated: false}`, fleet split **22 `none` · 2 `unexamined` · 1 `r_multiple`**.

---

## ⚠️ MERGED, NOT DEPLOYED. THE TRADER HAS NOT PICKED IT UP.

**MEASURED 2026-09-07T11:17Z, direct read over Caddy (`scripts/ops/diag_fetch.sh 'status'`, exit 0, served by `https://ict-bot.duckdns.org`):**

> **`git_sha: 45e485af`** — the running trader is on `45e485a` (*chore(ops): refresh the due-list, #11237*), which is **the commit immediately BEFORE the merge**. `git log 45e485a..origin/main` is exactly one commit, and that commit is mine. **MI-158 is the only undeployed change on the fleet.**

**Live `/api/bot/config`, `gld_pullback_1h`:**

```
tp_r      = <ABSENT>
tp_intent = <ABSENT>
keys      = atr_period, atr_stop_mult, enabled, execution, min_confidence, model,
            pullback_frac, pullback_lookback, shadow_model_ids, signal_prefixes,
            symbols, timeframe, trail_mult, trend_lookback     (14 keys — the PRE-change set)
```

**POSITIVE CONTROL — the absence is real, not an endpoint that hides the field.** The same response, same request, returns `tp_r` for every leg that has carried one for weeks:

| control leg | live `tp_r` | live `tp_intent` |
|---|---:|---|
| `ada_pullback_2h` | `4` | absent |
| `xrp_pullback_2h` | `3.0` | absent |
| `uso_trend_1h` | `4.0` | absent |
| `trend_donchian` | `50.0` | absent |
| `eth_pullback_2h` | `50.0` | absent |

So the endpoint exposes `tp_r` fine. `gld_pullback_1h` genuinely has **no target key in the running process** — it is still inheriting `htf_pullback_trend_2h._DEFAULTS["tp_r"] = 50.0`, i.e. still clamped at the 9.9% venue cap. And `tp_intent` is absent on **every** leg, including the 24 that now declare it on `main` — a second, independent confirmation that none of this change is live.

**Therefore: the resting take-profit on GLD 1h has NOT moved. Nothing about live behaviour has changed yet, and no one should read the merge as though it had.**

## What is owed, and by whom

Deployment is **not** automatic on merge — it needs a pull + restart of the trader's systemd services. **I did not do it, deliberately:** restarting a live trading fleet is a Tier-3 operational action, it was not in my brief, and the operator approved the CONTENT of a diff, not a deploy. That call belongs to the manager or the operator.

Whoever does it should re-run this same read-back afterwards and confirm two things, not one: `git_sha` advances to `3f88bff`, **and** `/api/bot/config` shows `gld_pullback_1h` `tp_r: 4.0`. A `git_sha` bump alone is not the observation — the config read is.

⚠️ And when it does go live, the standing caveat travels with it: **4.0R is REACHABLE, not calibrated.** MI-155 grades this leg `insufficient_n` (`n_live` 3 vs a floor of 30). Do not quote it as an evidenced target.
