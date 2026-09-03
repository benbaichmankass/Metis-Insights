#!/usr/bin/env python3
# wiring: .github/workflows/error-feed-digest.yml (--write) + scripts/ci/run_guards.py::error-feed-digest-guard (--self-test, --check)
"""Render the trader's ERROR FEED into a triage-ready digest the `duty` pass owns.

WHY (the operator's ask, 2026-09-02)
------------------------------------
    "can the error feed that's in the trader bot be fed directly to the manager
     session, so you can decide what should be resolved immediately vs.
     backlogged?"

Every piece already existed and none of them were joined up:

* ``runtime_logs/operator_alerts.jsonl`` is the durable ring behind the
  ``/api/bot/notifications`` banner — **the only surface from which a page RATE
  is recoverable** — and it is read by nothing on a cadence.
* ``/api/bot/logs?level=error`` is the ``outcomes.jsonl`` ERROR+/CRITICAL feed.
* ``.claude/skills/duty/SKILL.md`` is a triage pass whose entire job is *"give
  every detected signal an OWNER"* with a written disposition — and it was
  never pointed at either feed.

So this does not invent triage. It renders the two feeds into the shape
``render_due_list.py`` already consumes, and the existing `duty` pass gives each
group an owner. The measured cost of the gap: ``ict_scalp_avax_5m`` on
``bybit_1`` signalled 8 times in one day and placed **zero** orders — every one
rejected by the venue for an oversized qty — for the third time across three
backlog rows, and it was caught only because the operator pasted the feed by
hand.

WHAT IT IS NOT
--------------
**It decides nothing.** It has no notion of severity beyond the level the
producer already stamped, it never files, and it never closes. Ranking rows by
"importance" here would be this script deciding what the `duty` pass exists to
decide.

THE THREE READ STATES, WHICH ARE THE WHOLE DESIGN
-------------------------------------------------
Each feed reports ``read`` / ``unreachable`` / ``absent``:

    read         we fetched it; the rows are what it holds
    unreachable  WE COULD NOT LOOK — a transport failure, never "quiet"
    absent       we fetched it and it genuinely holds nothing

``CLAUDE.md`` § "Diagnostic provenance" sub-class **C** is the exact defect this
prevents: a ``curl … || echo '{}'`` poller turned an HTTP 403 into ``0 checks``
and a watcher reported a green having checked nothing. A digest that renders an
unreachable feed as a quiet one is worse than no digest, because the flood it
summarises is precisely what a reader would then believe had stopped.

ALWAYS STATE THE POPULATION
---------------------------
``operator_alerts.jsonl`` is **NOT a fixed window**: ``_OPERATOR_ALERTS_KEEP``
is 300 but the trim only fires past 2x, so the file holds anywhere from 300 to
600 rows and its oldest row's age is not a constant. Every feed therefore
carries its OWN measured ``oldest_ts``/``newest_ts`` span and row count, and a
page that hit the request cap is stamped ``truncated`` so a short window can
never read as a complete one. No rate is computed over an assumed window.

GROUPING, AND WHY IT IS NOT A ROW DUMP
--------------------------------------
The measured failure mode is ONE condition flooding the feed: 202 of 376
CRITICALs in one window were a single un-latched alarm, and 240 of 401 rows were
one leg's ``no candle data``. Measured again for this script on 2026-09-02 over
the full 1000-row page: **866 of 1000 rows were six MGC/MES no-candle causes**,
and the AVAX venue rejection was 18 rows that a row-dump would bury. So rows are
grouped by DIGIT-NORMALISED cause and emitted as ``cause -> count, first_seen,
last_seen, facets``.

Groups are ordered by three facts already on the row: the level the PRODUCER
stamped (errors before warnings), whether the group is NEW since the last
digest, then count. None is a severity this script assigns — and the level key
alone is what lifts the 17-row AVAX rejection above a 171-row no-candle flood.

⚠️ THE WATERMARK MARKS WHAT IS NEW; IT DOES NOT FILTER WHAT IS SHOWN. Every
group on the page is emitted, carrying `is_new`. Filtering to the delta was the
first design and it was wrong: measured on the second consecutive live run, it
covered 2 rows and dropped the AVAX rejection while it was still unresolved. A
digest that forgets a STANDING condition between two `duty` passes loses exactly
the signal it exists to carry — and *is this new or still happening* is much
closer to the operator's immediate-vs-backlog question than any row count.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

OUT = Path("docs/claude/ERROR-FEED-DIGEST.json")
#: The markdown twin, for a session that reads rather than parses.
OUT_MD = Path("docs/claude/ERROR-FEED-DIGEST.md")

#: The canonical HTTPS base. Both feeds ride Caddy; `diag_fetch.sh` documents
#: why a raw `http://IP:port` is dropped by the sandbox proxy and this host is
#: not. Overridable for the self-test and for a future host move.
DEFAULT_BASE = "https://ict-bot.duckdns.org"

#: Request caps. Each is the LIMIT WE ASKED FOR, echoed into the population
#: block, so `rows_returned == limit_requested` is visible as a possible
#: truncation rather than inferred.
ALERTS_LIMIT = 1000
LOGS_LIMIT = 1000

#: Feed read states. Never collapsed — see the module docstring.
#: `absent` is "we looked and it holds nothing"; `unreachable` is "we could not
#: look". Reading the second as the first is the whole defect class.
FEED_STATES = ("read", "unreachable", "absent")

#: Envelope verdicts, mirroring `render_due_list.py` deliberately: a session
#: reads both artifacts and two vocabularies for one idea is how a reader stops
#: checking either.
VERDICTS = ("all_feeds_read", "partial", "no_feeds_read")

_HTTP_TIMEOUT_S = 30.0


# ── population + read state ────────────────────────────────────────────────

@dataclass
class FeedRead:
    """One feed's rows plus the population they were drawn from.

    `state` and `population` travel together on purpose: a count without its
    denominator is the thing this repo keeps paying for, so there is no way to
    get the rows from this object without also getting the span they cover.
    """

    name: str
    state: str
    rows: list = field(default_factory=list)
    note: str = ""
    population: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in FEED_STATES:
            raise ValueError(f"{self.name}: bad state {self.state!r}")


def _population(rows: list, limit_requested: int) -> dict:
    """Measure the window the rows actually cover — never assume one.

    `truncated` is `rows_returned >= limit_requested`: the page hit the cap, so
    the span below is a LOWER BOUND on what the feed holds and older rows exist
    that this digest did not see.
    """
    stamps = sorted(t for t in (_ts(r) for r in rows) if t)
    return {
        "rows_returned": len(rows),
        "limit_requested": limit_requested,
        "truncated": len(rows) >= limit_requested,
        "oldest_ts": stamps[0] if stamps else None,
        "newest_ts": stamps[-1] if stamps else None,
        "undateable_rows": sum(1 for r in rows if not _ts(r)),
    }


def _ts(row: dict) -> str | None:
    for key in ("ts", "timestamp"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _parse_ts(s: str | None) -> datetime | None:
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ── transport ──────────────────────────────────────────────────────────────

def _get(url: str, token: str | None = None) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_operator_alerts(base: str, token: str | None) -> FeedRead:
    """`runtime_logs/operator_alerts.jsonl` via the diag `log_file` allowlist.

    Needs the diag bearer. NO TOKEN IS `unreachable`, NOT `absent`: an
    unauthenticated call cannot distinguish an empty ring from a closed door,
    and calling that "quiet" is the failure this module exists to refuse.
    """
    if not token:
        return FeedRead("operator_alerts", "unreachable",
                        note="DIAG_READ_TOKEN unset — the diag surface serves 503 "
                             "without it, so we could not look")
    url = f"{base.rstrip('/')}/api/diag/log_file?name=operator_alerts&lines={ALERTS_LIMIT}"
    try:
        env = _get(url, token)
    except Exception as exc:  # noqa: BLE001 — a transport failure is a STATE
        return FeedRead("operator_alerts", "unreachable",
                        note=f"{type(exc).__name__}: {exc}")

    if not env.get("present"):
        # The route reached us and said the file is not there. That is a real
        # reading on the trader (no alert has ever been recorded) and a
        # different fact from a failed fetch.
        return FeedRead("operator_alerts", "absent",
                        note="diag reports the log file is not present on the host",
                        population=_population([], ALERTS_LIMIT))

    rows = []
    undecodable = 0
    for line in env.get("lines") or []:
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            undecodable += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    pop = _population(rows, ALERTS_LIMIT)
    pop["undecodable_lines"] = undecodable
    if not rows:
        return FeedRead("operator_alerts", "absent",
                        note="the ring is present and holds no decodable rows",
                        population=pop)
    return FeedRead("operator_alerts", "read", rows, population=pop)


def fetch_bot_logs(base: str) -> FeedRead:
    """`/api/bot/logs` — the `outcomes.jsonl` operator feed.

    Unauthenticated by design (Tier-1 read surface), so it carries no bearer.
    ERROR and WARN are both pulled: the level split is what orders the digest,
    and dropping WARN would hide the denominator that makes an ERROR group's
    size meaningful.
    """
    url = (f"{base.rstrip('/')}/api/bot/logs"
           f"?level=error,warn&limit={LOGS_LIMIT}")
    try:
        payload = _get(url)
    except Exception as exc:  # noqa: BLE001
        return FeedRead("bot_logs", "unreachable", note=f"{type(exc).__name__}: {exc}")

    rows = payload if isinstance(payload, list) else payload.get("logs") or []
    rows = [r for r in rows if isinstance(r, dict)]
    pop = _population(rows, LOGS_LIMIT)
    if not rows:
        return FeedRead("bot_logs", "absent",
                        note="the route answered and returned no rows",
                        population=pop)
    return FeedRead("bot_logs", "read", rows, population=pop)


# ── grouping ───────────────────────────────────────────────────────────────

# ⚠️ THE LONG-BLOB BRANCH MUST REQUIRE A HEX LETTER, and the first version of
# this did not. `\b[0-9a-f]{12,}\b` matches a long DECIMAL number too, so the
# AVAX rejection's `order_qty:2299510000000 > max_qty:2200000000000` — the two
# numbers the whole finding is about — were replaced by `<hex>` before the digit
# pass could turn them into the `N` a reader recognises as "a quantity varied
# here". Measured on the live feed 2026-09-02: the digest's top-5 error group
# read `order_qty:<hex> > max_qty:<hex>`, which says a comparison happened and
# hides that it was a size cap. The lookahead requires at least one a-f digit,
# so a decimal of any length falls through to `_NUM_RE` where it belongs.
_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b|\b(?=[0-9a-f]*[a-f])[0-9a-f]{12,}\b")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                      re.IGNORECASE)
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_WS_RE = re.compile(r"\s+")

#: How much of a normalised message forms the cause key. Long enough that two
#: genuinely different conditions do not collide, short enough that a trailing
#: payload does not split one condition into many keys.
_CAUSE_KEY_CHARS = 160


def normalise_cause(text: str) -> str:
    """Collapse a digit-varying repeat of one condition into a single key.

    Order matters: uuids and hex blobs are eaten BEFORE the bare-number pass,
    or a uuid becomes a string of `N`-separated fragments and two occurrences of
    the same condition still key differently — which is the flood this grouping
    exists to collapse.
    """
    s = _UUID_RE.sub("<id>", text or "")
    s = _HEX_RE.sub("<hex>", s)
    s = _NUM_RE.sub("N", s)
    return _WS_RE.sub(" ", s).strip()[:_CAUSE_KEY_CHARS]


_FACET_RES: tuple[tuple[str, re.Pattern], ...] = (
    # `"symbol": "AVAXUSDT"` (a JSON body) as well as `symbol=MGC` and
    # `Symbol: GLD` (prose). The optional closing quote before the separator is
    # load-bearing: without it the AVAX rejection — whose symbol appears ONLY in
    # the JSON request body it echoes — extracted no symbol at all, so the one
    # group that most needed attributing was the one that carried none.
    ("symbols", re.compile(
        r"""\bsymbol["']?\s*[=:]\s*["']?([A-Z][A-Z0-9/:._-]{1,20})""",
        re.IGNORECASE)),
    ("strategies", re.compile(
        r"""\b((?:ict_scalp|trend_donchian|turtle_soup|pairs|vwap|squeeze_breakout)"""
        r"""[a-z0-9_]*|[a-z][a-z0-9]*_(?:trend|pullback|scalp|breakout|fade|orb)"""
        r"""_[a-z0-9]+)\b""")),
)

_ACCOUNTS_YAML = Path("config/accounts.yaml")
#: Matches an account id declared as a two-space key under `accounts:`.
_ACCOUNT_KEY_RE = re.compile(r"^  ([a-z][a-z0-9_]*):\s*$", re.MULTILINE)


def account_roster(root: Path) -> tuple[set, str]:
    """The declared account ids, PROJECTED OVER THE CANONICAL SOURCE.

    ⚠️ THE FIRST VERSION GUESSED A SHAPE AND MANUFACTURED ACCOUNTS THAT DO NOT
    EXIST. It matched `(?:bybit|ib|alpaca|…)_[a-z0-9_]+`, which is also the
    shape of an EVENT NAME — so on the live feed 2026-09-02 the AVAX venue
    rejection was filed under an account called `bybit_place_order_failed`, and
    the IB target-naked pages under `ib_target_naked`. A false facet is worse
    than a missing one: it reads as attribution and sends a triage session to an
    account that is not there. `config/accounts.yaml` already declares the
    roster, so the ids are READ rather than inferred.

    Returns `(ids, state)` where state is `read` / `unreadable`. On
    `unreadable` the caller reports accounts as UNKNOWN, never as `[]` — we
    could not look is not "no account was involved".
    """
    p = root / _ACCOUNTS_YAML
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return set(), f"unreadable: {type(exc).__name__}"
    body = text.split("\naccounts:", 1)
    if len(body) != 2:
        return set(), "unreadable: no top-level `accounts:` block"
    ids = set(_ACCOUNT_KEY_RE.findall(body[1]))
    return (ids, "read") if ids else (set(), "unreadable: no ids under `accounts:`")


def extract_facets(text: str, roster: set, roster_state: str) -> dict:
    """Pull account / symbol / strategy names out of a message's TEXT.

    ⚠️ An empty list means NOTHING WAS EXTRACTABLE FROM THE TEXT — never that
    no account or strategy was involved. These feeds carry prose bodies, not
    typed columns, so a facet is evidence when present and says nothing when
    absent. `facets_found` states which keys actually matched so a reader can
    tell the two apart rather than inferring it from an empty list, and
    `accounts_state` separates *no declared account appeared in this text* from
    *we could not read the roster to check*.
    """
    out: dict = {}
    for name, rx in _FACET_RES:
        hits = set()
        for m in rx.finditer(text or ""):
            hits.update(g for g in m.groups() if g)
        out[name] = sorted(hits)
    # An account id is matched as a whole word against the DECLARED roster, so
    # an event name that merely looks account-shaped can never become one.
    out["accounts"] = sorted(
        a for a in roster if re.search(rf"\b{re.escape(a)}\b", text or ""))
    out["accounts_state"] = "matched" if roster_state == "read" else roster_state
    out["facets_found"] = sorted(
        k for k in ("accounts", "symbols", "strategies") if out.get(k))
    return out


def _row_text(row: dict) -> str:
    """The human-facing text of a row, whichever feed it came from."""
    for key in ("message", "body"):
        v = row.get(key)
        if isinstance(v, str):
            return v
    return json.dumps(row, sort_keys=True)


def _row_level(row: dict, feed: str) -> str:
    """The level the PRODUCER stamped — never a severity this script assigns.

    `operator_alerts` rows carry `priority`, not `level`; a `high` priority is
    mapped to `error` so the two feeds share one ordering axis. Anything
    unrecognised stays `unknown` rather than being defaulted into `warn`: a row
    whose level we could not read is not a row we graded as low.
    """
    lvl = (row.get("level") or "").strip().lower()
    if lvl in ("critical", "error", "warn", "warning", "info"):
        return "error" if lvl == "critical" else ("warn" if lvl == "warning" else lvl)
    if feed == "operator_alerts":
        prio = (row.get("priority") or "").strip().lower()
        if prio in ("high", "critical"):
            return "error"
        if prio in ("normal", "medium", "low"):
            return "warn"
    return "unknown"


def group_rows(feed: FeedRead, since: datetime | None,
               roster: set | None = None, roster_state: str = "read") -> tuple[list, dict]:
    """Group one feed's rows by normalised cause over the WHOLE page.

    ⚠️ THE WATERMARK MARKS WHAT IS NEW; IT DOES NOT FILTER WHAT IS SHOWN, and
    the first version of this got that wrong. Filtering to the delta meant a
    condition that fired between two `duty` passes vanished from the due-list
    while still being unresolved — measured on the second live run, which
    covered 2 rows and dropped the AVAX venue rejection the whole change exists
    to surface. A digest that forgets a STANDING condition loses exactly the
    signal it is there to carry.

    So every group on the page is emitted, and each carries `is_new` (nothing in
    it predates the watermark). That is also the axis the operator actually
    asked about — *resolve immediately vs. backlog* is much closer to "did this
    start since we last looked" than to "how many rows".

    Returns `(groups, coverage)`. `coverage` records how the watermark was
    applied — including `undateable_dropped`, because a row we could not date
    cannot be shown to be new and silently discarding it would understate the
    digest with nothing saying so.
    """
    buckets: dict[tuple[str, str], dict] = {}
    considered = kept = undateable = 0
    for row in feed.rows:
        considered += 1
        stamp = _parse_ts(_ts(row))
        if stamp is None:
            undateable += 1
            continue
        if since is None or stamp > since:
            kept += 1
        text = _row_text(row)
        key = (_row_level(row, feed.name), normalise_cause(text))
        b = buckets.setdefault(key, {
            "feed": feed.name, "level": key[0], "cause": key[1],
            "count": 0, "first_seen": stamp, "last_seen": stamp,
            "_texts": [],
        })
        b["count"] += 1
        b["first_seen"] = min(b["first_seen"], stamp)
        b["last_seen"] = max(b["last_seen"], stamp)
        if len(b["_texts"]) < 3:
            b["_texts"].append(text)

    groups = []
    for b in buckets.values():
        facets = extract_facets(" \n".join(b["_texts"]), roster or set(), roster_state)
        # `is_new` is a property of the GROUP's own earliest row, so a
        # long-standing condition that fired again this hour reads as
        # continuing rather than as new — which is the distinction a triage
        # session needs and a per-row test would destroy.
        is_new = since is None or b["first_seen"] > since
        groups.append({
            "feed": b["feed"], "level": b["level"], "cause": b["cause"],
            "count": b["count"], "is_new": is_new,
            "first_seen": b["first_seen"].isoformat(),
            "last_seen": b["last_seen"].isoformat(),
            "sample": b["_texts"][0][:600],
            **facets,
        })
    coverage = {
        "rows_considered": considered,
        # Rows NEWER than the watermark. This is a MARKING count, not a
        # filtering one — every row on the page is grouped regardless.
        "rows_after_watermark": kept,
        "undateable_dropped": undateable,
        "watermark_applied": since.isoformat() if since else None,
    }
    return groups, coverage


#: Level ordering. `unknown` sorts with the errors, deliberately: a row whose
#: level could not be read must not be filed below the warnings, because "we
#: could not grade it" is not "it is minor".
_LEVEL_RANK = {"error": 0, "unknown": 0, "warn": 1, "info": 2}


def order_groups(groups: list) -> list:
    """Level, then NEW before continuing, then count.

    All three keys are facts already on the row — the level the producer
    stamped, whether the group predates the watermark, and how many rows it
    holds. None of them is a severity this module assigns; the disposition
    stays the `duty` pass's.
    """
    return sorted(groups, key=lambda g: (_LEVEL_RANK.get(g["level"], 3),
                                         not g.get("is_new"),
                                         -g["count"], g["cause"]))


# ── envelope ───────────────────────────────────────────────────────────────

def verdict_for(feeds: Iterable[FeedRead]) -> str:
    feeds = list(feeds)
    if not feeds:
        return "no_feeds_read"
    unreachable = [f for f in feeds if f.state == "unreachable"]
    if not unreachable:
        return "all_feeds_read"
    if len(unreachable) == len(feeds):
        return "no_feeds_read"
    return "partial"


def read_watermark(root: Path) -> tuple[datetime | None, str]:
    """The watermark lives INSIDE the committed digest, and that is deliberate.

    It must survive a re-provision of either VM, so `runtime_logs/` is out —
    that path is `.gitignore`d and VM-local, and a fresh box would replay the
    entire feed as if it were new. It must also never disagree with the artifact
    it bounds, which a second file is free to do, so it is a field on the digest
    rather than a sidecar: the run that publishes the rows publishes the mark in
    the same commit, and a rollback of one is a rollback of both.
    """
    p = root / OUT
    if not p.exists():
        return None, "no prior digest — this run covers the full feed page"
    try:
        prev = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, f"prior digest unreadable ({type(exc).__name__}) — covering the full page"
    mark = _parse_ts((prev.get("watermark") or {}).get("next_since"))
    if mark is None:
        return None, "prior digest carries no readable watermark — covering the full page"
    return mark, f"resuming from the prior digest's watermark {mark.isoformat()}"


def next_watermark(feeds: list, previous: datetime | None) -> tuple[datetime | None, str]:
    """Advance to the newest row we actually SAW, and never past it.

    ⚠️ THE WATERMARK ONLY ADVANCES ON A FEED WE READ. If every feed was
    `unreachable` the mark is carried forward unchanged — advancing it on a
    failed fetch would mark as covered a window nobody looked at, which is the
    one way this design can lose a signal permanently.
    """
    seen = []
    for f in feeds:
        if f.state != "read":
            continue
        newest = _parse_ts(f.population.get("newest_ts"))
        if newest:
            seen.append(newest)
    if not seen:
        return previous, ("no feed was read — the watermark is HELD so the "
                          "unread window is re-covered next run")
    advanced = max(seen)
    if previous and advanced <= previous:
        return previous, "no row newer than the prior watermark — mark unchanged"
    return advanced, f"advanced to the newest row read ({advanced.isoformat()})"


def build(feeds: list, *, now: datetime, since: datetime | None,
          since_note: str, roster: set | None = None,
          roster_state: str = "read") -> dict:
    groups: list = []
    coverage: dict = {}
    for f in feeds:
        g, cov = group_rows(f, since, roster, roster_state)
        groups.extend(g)
        coverage[f.name] = cov
    groups = order_groups(groups)
    mark, mark_note = next_watermark(feeds, since)

    # A page that hit its cap did not show us the whole feed, and a reader must
    # not take the span below as the whole story.
    truncated = sorted(f.name for f in feeds if f.population.get("truncated"))

    return {
        "schema_version": 1,
        "what_this_is": (
            "The trader's error feed, grouped by digit-normalised cause and "
            "rendered for the `duty` pass. It COLLECTS and ORDERS; it decides "
            "nothing. Read `verdict` before reading `groups`: on `partial` at "
            "least one feed could not be read and this digest is a LOWER "
            "BOUND, not a statement that the rest is quiet."
        ),
        "generated_at": now.replace(microsecond=0).isoformat(),
        "covers_since": since.isoformat() if since else None,
        "covers_since_note": since_note,
        "verdict": verdict_for(feeds),
        "unreachable_feeds": [f.name for f in feeds if f.state == "unreachable"],
        "truncated_feeds": truncated,
        "feeds": {
            f.name: {"state": f.state, "note": f.note, "population": f.population}
            for f in feeds
        },
        "coverage": coverage,
        "account_roster": {"state": roster_state, "ids": sorted(roster or set())},
        "counts": {
            "groups": len(groups),
            "new_groups": sum(1 for g in groups if g.get("is_new")),
            "error_groups": sum(1 for g in groups if g["level"] in ("error", "unknown")),
            "rows_grouped": sum(g["count"] for g in groups),
        },
        "watermark": {
            "next_since": mark.isoformat() if mark else None,
            "note": mark_note,
        },
        "groups": groups,
    }


def render_markdown(env: dict) -> str:
    L = ["# Trader error feed — grouped for triage", "",
         f"_Generated {env['generated_at']} · covers rows after "
         f"`{env['covers_since'] or '(everything on the page)'}` · verdict "
         f"**{env['verdict']}**_", ""]
    if env["verdict"] != "all_feeds_read":
        L += [f"> ⚠️ **LOWER BOUND.** Could not read: "
              f"`{'`, `'.join(env['unreachable_feeds']) or '(none)'}`. An empty "
              f"section below may mean nothing fired, or may mean nobody looked.", ""]
    if env["truncated_feeds"]:
        L += [f"> ⚠️ **Page cap hit** on `{'`, `'.join(env['truncated_feeds'])}` — "
              f"older rows exist that this digest did not see.", ""]

    L += ["## Population", ""]
    for name, f in env["feeds"].items():
        p = f["population"]
        span = (f"{p.get('oldest_ts')} → {p.get('newest_ts')}"
                if p.get("oldest_ts") else "no dated rows")
        L.append(f"- **{name}** — state `{f['state']}` · "
                 f"{p.get('rows_returned', 0)} of {p.get('limit_requested', 0)} "
                 f"requested · span {span}"
                 + (f" · {f['note']}" if f["note"] else ""))
    L += ["", f"## Groups ({env['counts']['groups']}, covering "
              f"{env['counts']['rows_grouped']} rows)", ""]
    if not env["groups"]:
        L += ["No rows newer than the watermark on the feeds that answered.", ""]
    for g in env["groups"]:
        facets = " · ".join(
            f"{k}: {', '.join(g[k])}" for k in ("accounts", "symbols", "strategies")
            if g.get(k))
        L.append(f"- **[{g['level']}] x{g['count']}** `{g['feed']}` — {g['cause']}")
        L.append(f"  - {g['first_seen']} → {g['last_seen']}"
                 + (f" · {facets}" if facets else ""))
    L += ["", "---", "",
          f"_Watermark: `{env['watermark']['next_since']}` — "
          f"{env['watermark']['note']}_", ""]
    return "\n".join(L)


# ── entry points ───────────────────────────────────────────────────────────

def run(root: Path, *, base: str, token: str | None,
        now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since, since_note = read_watermark(root)
    roster, roster_state = account_roster(root)
    feeds = [fetch_operator_alerts(base, token), fetch_bot_logs(base)]
    return build(feeds, now=now, since=since, since_note=since_note,
                 roster=roster, roster_state=roster_state)


def _self_test() -> int:
    """Executable controls for the five properties this digest is claimed to have."""
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

    # 1. THE THREE READ STATES ARE DISTINGUISHABLE, and an unreachable feed
    #    does not render as an empty one.
    unreachable = FeedRead("bot_logs", "unreachable", note="ConnectionError")
    absent = FeedRead("operator_alerts", "absent", population=_population([], 10))
    assert verdict_for([unreachable, absent]) == "partial"
    assert verdict_for([unreachable]) == "no_feeds_read"
    assert verdict_for([absent]) == "all_feeds_read", \
        "an ABSENT feed was read — only `unreachable` degrades the verdict"
    env = build([unreachable, absent], now=now, since=None, since_note="")
    assert env["counts"]["groups"] == 0
    assert "LOWER BOUND" in render_markdown(env), \
        "an unreachable feed must be stated, not rendered as quiet"
    assert "bot_logs" in env["unreachable_feeds"]

    # 2. GROUPING collapses a digit-varying repeat of one condition into ONE
    #    row — the flood-summarising property.
    rows = [{"timestamp": f"2026-09-02T0{i}:00:00+00:00", "level": "warn",
             "message": f"ict_scalp_mgc_15m: no candle data, attempt {i}, took {i*7}ms"}
            for i in range(1, 9)]
    feed = FeedRead("bot_logs", "read", rows, population=_population(rows, 1000))
    groups, cov = group_rows(feed, None)
    assert len(groups) == 1, f"digit-varying repeat split into {len(groups)} groups"
    assert groups[0]["count"] == 8
    assert cov["rows_considered"] == 8 and cov["rows_after_watermark"] == 8

    # ... and does NOT collapse two genuinely different conditions.
    rows2 = rows + [{"timestamp": "2026-09-02T09:00:00+00:00", "level": "error",
                     "message": "bybit_place_order_failed: order_qty:22 > max_qty:11"}]
    feed2 = FeedRead("bot_logs", "read", rows2, population=_population(rows2, 1000))
    g2, _ = group_rows(feed2, None)
    assert len(g2) == 2, "two different conditions must not share a cause key"

    # 3. ORDERING puts the smaller ERROR group above the larger WARN flood.
    #    This is the property that surfaces a venue rejection buried under a
    #    no-candle flood; without it the digest reproduces the flood.
    ordered = order_groups(g2)
    assert ordered[0]["level"] == "error" and ordered[0]["count"] == 1, \
        "a 1-row error must outrank an 8-row warn — level is the producer's own stamp"
    assert ordered[1]["count"] == 8

    # 4. THE WATERMARK advances on a read feed and does not replay.
    mark, note = next_watermark([feed2], None)
    assert mark == datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc), note
    g3, cov3 = group_rows(feed2, mark)
    assert cov3["rows_after_watermark"] == 0, "a second pass counted rows it had covered as new"

    # 4a. ... BUT THE GROUPS ARE STILL EMITTED, marked `is_new: False`. The
    #     watermark MARKS what is new; it must not FILTER what is shown.
    #     Measured on the second live run, filtering dropped the AVAX venue
    #     rejection from the due-list while it was still unresolved — a digest
    #     that forgets a STANDING condition loses the signal it carries.
    assert len(g3) == 2, "a standing condition vanished from the digest"
    assert all(not g["is_new"] for g in g3), g3
    assert cov3["rows_considered"] == 9

    # 4b. A NEW error group outranks a CONTINUING one of the same level, which
    #     is the immediate-vs-backlog axis the operator asked about.
    mixed = order_groups([
        {"level": "error", "count": 99, "is_new": False, "cause": "old"},
        {"level": "error", "count": 1, "is_new": True, "cause": "new"},
    ])
    assert mixed[0]["cause"] == "new", mixed

    # 5. ... and is HELD when nothing was read, so an unread window is
    #    re-covered rather than marked done.
    held, note5 = next_watermark([unreachable], mark)
    assert held == mark, f"watermark advanced past a window nobody read: {note5}"

    # 6. An UNDATEABLE row is dropped LOUDLY, never silently.
    rows4 = [{"level": "error", "message": "no timestamp here"}]
    feed4 = FeedRead("bot_logs", "read", rows4, population=_population(rows4, 10))
    _, cov4 = group_rows(feed4, None)
    assert cov4["undateable_dropped"] == 1

    # 7. FACETS: an empty list is "nothing extractable", and `facets_found`
    #    is what says so.
    roster = {"bybit_1", "bybit_2", "ib_paper", "alpaca_paper"}
    f = extract_facets('Account: bybit_1 | symbol="AVAXUSDT" | ict_scalp_avax_5m',
                       roster, "read")
    assert f["accounts"] == ["bybit_1"], f
    assert f["symbols"] == ["AVAXUSDT"], f
    assert "ict_scalp_avax_5m" in f["strategies"], f
    assert extract_facets("nothing here", roster, "read")["facets_found"] == []

    # 7a. AN EVENT NAME IS NOT AN ACCOUNT. The first version matched a
    #     venue-prefixed SHAPE and filed the AVAX rejection under an account
    #     called `bybit_place_order_failed`, and the IB pages under
    #     `ib_target_naked`. A false facet reads as attribution and sends a
    #     triage session somewhere that does not exist.
    ev = extract_facets("api_call bybit_place_order_failed: ib_target_naked detected",
                        roster, "read")
    assert ev["accounts"] == [], f"an event name was extracted as an account: {ev}"

    # 7b. AN UNREADABLE ROSTER IS SAID, NOT SILENTLY EMPTIED — otherwise "no
    #     declared account appeared" and "we could not check" render alike.
    unk = extract_facets("Account: bybit_1", set(), "unreadable: boom")
    assert unk["accounts"] == [] and unk["accounts_state"].startswith("unreadable"), unk

    # 7c. THE JSON-BODY SYMBOL FORM. The AVAX rejection carries its symbol ONLY
    #     inside the request body it echoes, so a prose-only pattern attributed
    #     nothing on the one group that most needed it.
    js = extract_facets('POST /v5/order/create: {"symbol": "AVAXUSDT", "side": "Buy"}',
                        roster, "read")
    assert js["symbols"] == ["AVAXUSDT"], js

    # 7d. A LONG DECIMAL IS NOT A HEX BLOB. `\b[0-9a-f]{12,}\b` also matches a
    #     13-digit decimal, so the AVAX cause key read `order_qty:<hex> >
    #     max_qty:<hex>` — hiding that the comparison was a SIZE CAP.
    cause = normalise_cause("too large, order_qty:2299510000000 > max_qty:2200000000000")
    assert "<hex>" not in cause, cause
    assert cause.count("N") >= 2, cause
    assert "<hex>" in normalise_cause("pkg-6a8e3fb325464be3"), "a real hex blob must collapse"

    # 7e. THE ROSTER IS PROJECTED OVER THE CANONICAL SOURCE, not guessed. This
    #     asserts against the repo's own config, so a rename that breaks the
    #     extractor fails here rather than silently emptying every facet.
    ids, state = account_roster(Path("."))
    if state == "read":
        assert "bybit_1" in ids and "alpaca_live" in ids, sorted(ids)
        assert not any(i.endswith("_failed") or i.endswith("_naked") for i in ids), \
            f"an event name reached the roster: {sorted(ids)}"

    # 8. POPULATION: a page at the cap is stamped truncated.
    assert _population([{"ts": "2026-09-02T00:00:00+00:00"}], 1)["truncated"] is True
    assert _population([{"ts": "2026-09-02T00:00:00+00:00"}], 5)["truncated"] is False

    print("error-feed-digest self-test: 8 property groups "
          "(13 controls) OK")
    return 0


def _check(root: Path) -> int:
    """CI: the committed digest parses and states its own limits."""
    p = root / OUT
    if not p.exists():
        print(f"error-feed-digest: OK — {OUT} not yet written (no run has landed)")
        return 0
    try:
        env = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"error-feed-digest: FAIL — {OUT} is unreadable ({exc})")
        return 1
    if env.get("verdict") not in VERDICTS:
        print(f"error-feed-digest: FAIL — verdict {env.get('verdict')!r} "
              f"is not one of {VERDICTS}")
        return 1
    if env["verdict"] != "all_feeds_read" and not env.get("unreachable_feeds"):
        print("error-feed-digest: FAIL — verdict is not `all_feeds_read` but no feed "
              "is named as unreachable. A partial digest must say WHICH feed it "
              "could not read.")
        return 1
    if not env.get("generated_at"):
        print("error-feed-digest: FAIL — no `generated_at`; a digest that cannot be "
              "dated cannot be shown to be current.")
        return 1
    print(f"error-feed-digest: OK — verdict={env['verdict']} "
          f"groups={env.get('counts', {}).get('groups')} "
          f"generated_at={env['generated_at']}")
    return 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help=f"fetch both feeds and write {OUT} + {OUT_MD}")
    ap.add_argument("--check", action="store_true",
                    help="CI: validate the committed digest")
    ap.add_argument("--self-test", action="store_true",
                    help="run the executable controls")
    ap.add_argument("--base", default=os.environ.get("ERROR_FEED_BASE", DEFAULT_BASE),
                    help="API base (default: the canonical Caddy host)")
    ap.add_argument("--root", default=".", help="repo root")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if args.self_test:
        return _self_test()
    if args.check:
        return _check(root)

    env = run(root, base=args.base, token=os.environ.get("DIAG_READ_TOKEN"))
    if args.write:
        (root / OUT).parent.mkdir(parents=True, exist_ok=True)
        (root / OUT).write_text(json.dumps(env, indent=2) + "\n", encoding="utf-8")
        (root / OUT_MD).write_text(render_markdown(env), encoding="utf-8")
        print(f"error-feed-digest: wrote {OUT} + {OUT_MD} — "
              f"verdict={env['verdict']} groups={env['counts']['groups']} "
              f"rows={env['counts']['rows_grouped']} "
              f"unreachable={env['unreachable_feeds'] or '[]'}")
    else:
        print(render_markdown(env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
