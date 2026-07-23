"""M28 P2 — event-outcome resolver (the non-price decision machinery).

The core of "events we watch and make decisions based on their **results**": a
thesis registers ``watched_events`` each carrying an ``on_outcome`` rule list
(M28-P0 schema §2). When an event resolves with a realized outcome, this module
evaluates each rule's predicate against that outcome and returns the matched
**action** (``enter | add | trim | exit | flip | hold | extend``).

Deliberately a **small, explicit, auditable predicate DSL — NOT ``eval``.** A
predicate is ``{"field": <name>, "op": <cmp>, "value": <v>}`` compared against the
event's ``realized_outcome`` dict, so every decision is reconstructable from data
and safe to run on untrusted rule content. Pure / fail-closed: a missing field,
unknown op, or bad rule evaluates **false** (never fires an action on ambiguity),
and nothing here raises or touches an order path — the resolver returns the
*would-be* action; enacting it is the gated executor (P3+).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

# The actions a thesis rule may request (M28-P0 §1a / §2).
VALID_ACTIONS = frozenset(
    {"enter", "add", "trim", "exit", "flip", "hold", "extend"}
)


def _num(x: Any) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float, str)):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _op_gt(a, b):  # noqa: ANN001
    na, nb = _num(a), _num(b)
    return na is not None and nb is not None and na > nb


def _op_lt(a, b):  # noqa: ANN001
    na, nb = _num(a), _num(b)
    return na is not None and nb is not None and na < nb


def _op_gte(a, b):  # noqa: ANN001
    na, nb = _num(a), _num(b)
    return na is not None and nb is not None and na >= nb


def _op_lte(a, b):  # noqa: ANN001
    na, nb = _num(a), _num(b)
    return na is not None and nb is not None and na <= nb


def _op_eq(a, b):  # noqa: ANN001
    return a == b


def _op_ne(a, b):  # noqa: ANN001
    return a != b


def _op_in(a, b):  # noqa: ANN001
    try:
        return a in b
    except TypeError:
        return False


def _op_not_in(a, b):  # noqa: ANN001
    return not _op_in(a, b)


_OPS = {
    "gt": _op_gt, "lt": _op_lt, "gte": _op_gte, "lte": _op_lte,
    "eq": _op_eq, "ne": _op_ne, "in": _op_in, "not_in": _op_not_in,
}


def eval_predicate(pred: Mapping[str, Any], outcome: Mapping[str, Any]) -> bool:
    """Evaluate one ``{field, op, value}`` predicate against a realized outcome.

    Fail-closed: unknown op, missing field, or a malformed predicate ⇒ ``False``
    (never fire an action on ambiguity)."""
    if not isinstance(pred, Mapping):
        return False
    field, op, value = pred.get("field"), pred.get("op"), pred.get("value")
    fn = _OPS.get(str(op))
    if fn is None or field is None:
        return False
    if field not in outcome:
        return False
    try:
        return bool(fn(outcome[field], value))
    except Exception:  # noqa: BLE001
        return False


def resolve_action(
    on_outcome: Sequence[Mapping[str, Any]], outcome: Mapping[str, Any]
) -> Optional[dict]:
    """Evaluate a thesis's ``on_outcome`` rule list against a realized outcome and
    return the **first** matched ``{"action", "matched_rule"}`` (rule order = priority).

    A rule is ``{"if": <predicate>, "action": <valid action>}``; a rule whose
    action isn't in :data:`VALID_ACTIONS` is skipped (never fires an unknown
    action). Returns ``None`` when no rule matches (the thesis holds)."""
    for rule in on_outcome or []:
        if not isinstance(rule, Mapping):
            continue
        action = rule.get("action")
        if action not in VALID_ACTIONS:
            continue
        if eval_predicate(rule.get("if", {}), outcome):
            return {"action": action, "matched_rule": dict(rule)}
    return None


def resolve_event_for_theses(
    event: Mapping[str, Any], thesis_links: Sequence[Mapping[str, Any]]
) -> list[dict]:
    """For a **resolved** event (carrying ``realized_outcome``), evaluate every
    linked thesis's ``on_outcome`` and return the matched would-be actions.

    Each result: ``{thesis_id, event_id, action, matched_rule}``. Observe-only —
    the caller (P3 executor, gated) decides whether to enact. An event with no
    ``realized_outcome`` (still scheduled) yields ``[]`` (nothing to resolve).
    """
    outcome = event.get("realized_outcome")
    if not isinstance(outcome, Mapping):
        return []
    event_id = event.get("event_id")
    out: list[dict] = []
    for link in thesis_links or []:
        if not isinstance(link, Mapping):
            continue
        if link.get("event_id") != event_id:
            continue
        matched = resolve_action(link.get("on_outcome", []), outcome)
        if matched is not None:
            out.append({
                "thesis_id": link.get("thesis_id"),
                "event_id": event_id,
                "action": matched["action"],
                "matched_rule": matched["matched_rule"],
            })
    return out
