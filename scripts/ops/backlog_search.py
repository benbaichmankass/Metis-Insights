#!/usr/bin/env python3
# wiring: scripts/ops/backlog_append.py (duplicate pre-check) + manual CLI
"""Find rows already in the backlogs that look like the one you are about to file.

WHY THIS EXISTS
---------------
Operator directive, 2026-08-26: *"We aren't using the backlog/lessons learned
logs correctly if we still keep running into the same fuck ups and aren't
implementing better practices."*

They are right, and the mechanism is simple: **the backlogs are write-only in
practice.** `health-review-backlog.json` is 951 rows and 5.1 MB. Nothing stops
a session filing a row that already exists, because nothing makes checking
cheap — so the lessons-learned log accumulates lessons and teaches none.

Measured the same day, on this very session: a row was filed as a fresh
discovery about exit labelling that duplicated **two** 2026-08-22 rows whose
mechanism was already named and half of which was already fixed. It was caught
by accident while writing a work plan, not by any check.

WHAT THIS IS AND IS NOT
-----------------------
It is a **lexical** search — token overlap over `id` + `title` + `detail`, with
the id's own date and prefix stripped so two rows about the same thing filed
weeks apart still collide. It knows nothing about meaning.

So it is deliberately a **PROMPT, not a verdict**. It cannot tell a duplicate
from a genuine recurrence, and those want opposite handling: a duplicate should
be dropped, a recurrence is *evidence the first fix did not hold* and is one of
the most valuable rows you can file. Only a human reading both can tell. The
guard therefore surfaces candidates and asks you to look — it never decides.

⚠️ **A silent zero-match is not proof of novelty.** The probe is token overlap;
a row phrased in entirely different words WILL score zero. Treat an empty
result as "this cheap check found nothing", never as "this is new" — the
denominator rule from `RULE ONE`.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

BACKLOGS = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
)

#: Words that appear in nearly every row and so carry no discriminating signal.
#: Kept short on purpose: an over-eager stop list silently suppresses matches,
#: which is the failure direction that hurts (a missed duplicate looks exactly
#: like a novel finding).
_STOP = {
    "the", "a", "an", "and", "or", "but", "is", "was", "are", "were", "be",
    "been", "to", "of", "in", "on", "for", "with", "that", "this", "it", "its",
    "as", "at", "by", "not", "no", "so", "if", "then", "than", "from", "into",
    "which", "when", "what", "how", "why", "we", "our", "they", "their",
    "bl", "pb", "mb", "oi", "fu",
}
_DATEISH = re.compile(r"\b20\d{6}\b|\b20\d{2}-\d{2}-\d{2}\b")
_TOKEN = re.compile(r"[a-z0-9_]+")


def tokens(text: str) -> set[str]:
    """Discriminating lowercase tokens. Dates and boilerplate stripped."""
    text = _DATEISH.sub(" ", str(text or "").lower())
    out = set()
    for t in _TOKEN.findall(text):
        t = t.strip("_")
        if len(t) < 3 or t in _STOP or t.isdigit():
            continue
        out.add(t)
    return out


def _rows(paths: Iterable[str]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for p in paths:
        path = Path(p)
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A backlog we cannot read is NOT a backlog with nothing in it.
            # Say so rather than silently shrinking the denominator.
            out.append((p, {"id": "<UNREADABLE>", "title": f"{p} did not parse",
                            "detail": "", "_unreadable": True}))
            continue
        for row in data.get("items") or []:
            if isinstance(row, dict):
                out.append((p, row))
    return out


def search(query: str, *, paths: Iterable[str] = BACKLOGS,
           limit: int = 8, min_score: float = 0.12) -> list[dict[str, Any]]:
    """Rank existing rows by token overlap with *query*. Highest first."""
    q = tokens(query)
    if not q:
        return []
    hits: list[dict[str, Any]] = []
    for source, row in _rows(paths):
        if row.get("_unreadable"):
            hits.append({"score": 1.0, "source": source, "id": row["id"],
                         "status": "unreadable", "title": row["title"]})
            continue
        blob = " ".join(str(row.get(k) or "") for k in ("id", "title", "detail"))
        r = tokens(blob)
        if not r:
            continue
        # Overlap normalised by the QUERY, not by the union: a long existing
        # row that fully contains a short new one is a strong signal, and
        # Jaccard would bury it under the length difference.
        score = len(q & r) / len(q)
        if score >= min_score:
            hits.append({
                "score": round(score, 3),
                "source": source,
                "id": row.get("id"),
                "status": row.get("status"),
                "title": (str(row.get("title") or ""))[:160],
            })
    # provenance: score — |query tokens ∩ row tokens| / |query tokens|
    hits.sort(key=lambda h: (-h["score"], str(h["id"])))
    return hits[:limit]


def format_hits(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return ("no lexical match — but this probe is TOKEN OVERLAP only, so a "
                "row phrased differently scores zero. 'nothing found' is not "
                "'nothing exists'.")
    # State what the number IS. A bare "0.80" reads as a confidence or a
    # probability; it is neither. Printing a value under a label that does not
    # describe what was computed is the A-class defect in CLAUDE.md
    # § "Diagnostic provenance" — and a reader who thinks this is semantic
    # similarity will over-trust a high score AND under-trust a zero.
    lines = [f"{len(hits)} existing row(s) overlap this text.",
             "overlap = |shared tokens| / |your tokens| over id+title+detail — "
             "LEXICAL only, no semantics:"]
    for h in hits:
        # provenance: score — |query tokens ∩ row tokens| / |query tokens|
        lines.append(f"  overlap {h['score']:.2f}  [{h.get('status')}] {h['id']}")
        lines.append(f"         {h['title']}")
    lines.append("")
    lines.append("READ THEM BEFORE FILING. A DUPLICATE should be dropped; a "
                 "RECURRENCE is evidence the first fix did not hold and is one "
                 "of the most valuable rows you can file. This tool cannot "
                 "tell them apart — only you can.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", nargs="*", help="text of the row you want to file")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--min-score", type=float, default=0.12)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.query:
        ap.error("give the title/detail text you are about to file")
    print(format_hits(search(" ".join(args.query), limit=args.limit,
                             min_score=args.min_score)))
    return 0


def _self_test() -> int:
    """Show the probe finds a positive before its silence is trusted."""
    import tempfile

    rows = {"schema_version": 1, "items": [
        {"id": "ZZ-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE",
         "status": "open", "title": "exit reason frozen when price arrives late",
         "detail": "the monitor stamps the exit label before the fill lands"},
        {"id": "ZZ-20260101-UNRELATED-TRAINER-DISK",
         "status": "resolved", "title": "trainer disk filled",
         "detail": "dataset retention never ran"},
    ]}
    ok = True
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "b.json"
        p.write_text(json.dumps(rows))

        pos = search("exit reason is frozen when the price arrives late",
                     paths=[str(p)])
        hit = bool(pos) and pos[0]["id"].startswith("ZZ-20260822-EXIT-REASON")
        print(f"  self-test (finds the near-duplicate): {'PASS' if hit else 'FAIL'}")
        ok &= hit

        # The date in the id must not be what matches, or two rows about the
        # same thing filed weeks apart would never collide.
        # Shares ONLY the date with the fixture row. If the date were a
        # matchable token, two unrelated rows filed the same day would collide
        # — and worse, two rows about the SAME thing filed weeks apart would
        # not, which is the case this tool exists for.
        dated = search("20260822 kubernetes ingress certificate rotation",
                       paths=[str(p)])
        no_date_match = not any(h["id"].startswith("ZZ-20260822-EXIT") for h in dated)
        print(f"  self-test (the id's DATE does not drive a match): "
              f"{'PASS' if no_date_match else 'FAIL'}")
        ok &= no_date_match

        neg = search("kubernetes ingress certificate rotation", paths=[str(p)])
        print(f"  self-test (unrelated text is quiet): {'PASS' if not neg else 'FAIL'}")
        ok &= not neg

        missing = search("anything", paths=[str(Path(d) / "nope.json")])
        print(f"  self-test (a missing backlog is not a match): "
              f"{'PASS' if not missing else 'FAIL'}")
        ok &= not missing

        bad = Path(d) / "bad.json"
        bad.write_text("{ not json")
        unread = search("anything at all", paths=[str(bad)])
        flagged = bool(unread) and unread[0]["status"] == "unreadable"
        print(f"  self-test (an UNREADABLE backlog is surfaced, not silently "
              f"dropped): {'PASS' if flagged else 'FAIL'}")
        ok &= flagged

    print("backlog-search self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
