"""Per-endpoint prompt builders for the AI Analyst.

Each builder returns ``(system_blocks, user_text)``, where
``system_blocks`` is a list of dicts compatible with the Anthropic
Messages API's ``system: [...]`` parameter. The static portion of
each system prompt carries ``"cache_control": {"type": "ephemeral"}``
so it benefits from prompt caching across calls; the per-call data
goes in the user message (uncached, must be fresh every run).

The static blocks are intentionally substantial — that's what makes
prompt caching pay. They encode the grounding rules ("cite every claim
by id"), the grading rubric, and the output envelope. The per-call
user block carries the joined data dict as compact JSON.
"""
from __future__ import annotations

import json
from typing import Any

# The output contract every endpoint enforces. The LLM is told to
# return a JSON object with these exact keys; the generator
# validates and slots them into the response envelope the router
# serves.
_OUTPUT_CONTRACT = """\
Return a SINGLE JSON object with these exact keys:

  {
    "summary_md": "<markdown text, 2-6 sentences, every claim citing an id from the input>",
    "grade": "good" | "mixed" | "concerning",
    "signals": [
      {"kind": "<short label>", "severity": "low" | "med" | "high", "note": "<<=120 chars, cites id(s)>"}
    ]
  }

Rules — these are load-bearing:

1. CITE EVERY CLAIM. Use the ids from the input rows
   (trade.id, order_package_id, signal_id). Do not invent ids. Do not
   refer to trades you cannot point at by id.
2. NO HALLUCINATION. If the input rows are empty or thin, say so —
   "no closed trades in the window" is a complete, honest answer.
   Never describe trades, prices, or outcomes that are not in the
   input data.
3. KEEP signals[] SHORT — at most 5 entries, smallest first. ``severity``:
   "low" = informational, "med" = worth a glance, "high" = operator
   should look now. If nothing is unusual, return signals: [].
4. GRADE is your overall read of the window:
   - "good" = nothing meaningful broken or surprising
   - "mixed" = some watch-items but not actionable
   - "concerning" = operator should investigate
5. Output JSON ONLY. No prose before or after. No code fences.
"""


def _system_blocks(static_text: str) -> list[dict[str, Any]]:
    """Wrap the static system text with the cache_control marker.

    The cache_control breakpoint applies to everything before it in
    the system block — which is the whole block here, since we have
    only one. That's the simple cacheable case.
    """
    return [
        {
            "type": "text",
            "text": static_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _data_user_text(label: str, data: dict[str, Any]) -> str:
    """Build the per-call user message with the joined data inline.

    Compact JSON keeps the per-call portion small so the cached
    static block dominates the prompt.
    """
    return (
        f"# {label}\n\n"
        f"Below is the joined data window. Every id in your output must "
        f"come from these rows. If the data is thin, say so honestly — "
        f"do not invent trades.\n\n"
        f"## data\n```json\n{json.dumps(data, default=str, indent=2)}\n```\n\n"
        f"Return the JSON object now."
    )


_SUMMARY_STATIC = f"""\
You are the AI Analyst for the ICT trading bot. Your job is to give
the operator a one-glance narrative of how the live trading system has
been doing over the most recent rolling window.

You will receive a joined data bundle covering the last 24 hours:
counts of trades and order packages, a tail of the signal audit log,
and the most recent closed trades and packages.

The narrative should answer three questions:
  1. Did the bot trade at all? How many decisions, how many fills?
  2. Did anything notable happen — sizable PnL, repeated rejects,
     strategy silence, an unusual symbol?
  3. Should the operator look at anything specific?

{_OUTPUT_CONTRACT}
"""


_RECENT_STATIC = f"""\
You are the AI Analyst for the ICT trading bot. The operator wants a
short narrative on the last batch of closed trades.

You will receive the most recent closed trades, each joined to its
``order_package`` (entry/SL/TP, confidence, signal_logic) and — when
available — the historical Claude strategy-decision score for the
same package.

Your narrative should:
  - Note the PnL trend (positive / negative / mixed).
  - Flag any single bad outcome worth understanding (large loss,
    quick stop-out, suspicious entry).
  - Where a historical claude_score exists for the trade's package,
    let that inform your grade — repeat-low-grade setups are a
    pattern worth surfacing.

{_OUTPUT_CONTRACT}
"""


_STRATEGY_STATIC = f"""\
You are the AI Analyst for the ICT trading bot. The operator is asking
about one specific strategy's recent session — typically the last
~7 days.

You will receive that strategy's trades and order packages over the
window, plus aggregate counts (closed / wins / losses / total_pnl).

Your narrative should:
  - Whether the strategy fired at all (silence is a legitimate finding).
  - The win-rate and PnL direction over the window.
  - Any rejection / close-reason clusters that suggest a regime mismatch
    or a stop placement issue.
  - Whether the operator should consider re-tuning, pausing to
    ``shadow``, or leaving as-is.

{_OUTPUT_CONTRACT}
"""


_HEALTH_STATIC = f"""\
You are the AI Analyst for the ICT trading bot. You will receive the
latest health snapshot (``artifacts/health/latest.json``) — a
structured object with per-check status, the snapshot timestamp, and
the overall summary.

Your job is to translate the snapshot into a few sentences a tired
operator can read on their phone: what's green, what's yellow, what's
red, and what (if anything) they should do.

If the snapshot is missing or empty, say so plainly — that itself is
a finding worth surfacing.

{_OUTPUT_CONTRACT}
"""


def summary_prompt(data: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    return _system_blocks(_SUMMARY_STATIC), _data_user_text("summary (24h)", data)


def recent_prompt(
    data: dict[str, Any], limit: int
) -> tuple[list[dict[str, Any]], str]:
    return (
        _system_blocks(_RECENT_STATIC),
        _data_user_text(f"recent closed trades (limit={limit})", data),
    )


def strategy_prompt(
    name: str, data: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    return (
        _system_blocks(_STRATEGY_STATIC),
        _data_user_text(f"strategy = {name}", data),
    )


def health_prompt(data: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    return _system_blocks(_HEALTH_STATIC), _data_user_text("latest health snapshot", data)
