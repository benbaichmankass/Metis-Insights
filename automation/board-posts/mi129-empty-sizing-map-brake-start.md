▶️ START — MI-129 · empty-sizing-map brake (Tier-2, operator-approved scope 2026-09-05)

Branch: `claude/mi129-empty-sizing-map-brake`
Object: `WO-20260905-EMPTY-SIZING-MAP-IS-RE-EMITTED-INSTEAD-OF-REFUSED` (landing via PR #11031)
Row: `BL-20260905-MES-TREND-LONG-1D-RE-EMITTED-ONE-DAILY-SIGNAL-SEVEN-TIMES-IN-49-MINUTES-WHEN-SIZING-RETURNED-EMPTY`

Files touched:
- `src/core/coordinator.py` — `multi_account_execute`: the eligibility predicate returns the exclusion REASON instead of a bool; a new post-loop refusal names the cause when `sized_qty_by_account` comes back empty.
- `src/runtime/strategy_monocle.py` — new `_empty_sizing_refusal_for_signal` gate helper + `STRATEGY_EMPTY_SIZING_BRAKE_DISABLED` kill-switch.
- `src/runtime/pipeline.py` — the gate, as the last check before dispatch.
- `src/runtime/order_bridge.py` — signal-identity helpers.
- `tests/test_bl20260905_empty_sizing_map_refused_once.py` (new), `.github/pr-landing/`, `docs/claude/health-review-backlog.json` (3 rows appended through `backlog_append.py`).

No VM mutation. No `config/`, no `deploy/`, no account files, no risk caps, no strategy params.

⚠️ Landing is **hold** — Tier-2. The operator approved the SCOPE, not a self-land.

Posted through `board-post.yml`: `add_issue_comment` returned `403 Resource not accessible by integration` from this session, which is the write-scope boundary CLAUDE.md documents, not the transient MCP drop (`issue_read` on the same object succeeds).

Late post, and that is a miss rather than a judgement call: the board claim belongs before the first substantive tool call, and this session's repo context arrived only after the clone. Flagging it rather than quietly backdating.
