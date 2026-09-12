#!/usr/bin/env python3
"""WHERE IS THE DIGEST RECEIPT, IF NOT ON MAIN?

Split out of `check_digest_liveness.py` on 2026-09-12 so that guard is a real
CONSUMER of this vocabulary rather than its own. `collapsed-state-guard`
refuses a contract whose only reader is its producer — correctly: a module
branching on its own constants proves nothing about whether anyone acts on the
distinction, which is the whole property the contract encodes.

⚠️ IT IS ALSO AN HONEST SPLIT, NOT A GUARD-SHAPED ONE. `check_digest_liveness`
   grades a timestamp and must stay fast, deterministic and socket-free; this
   module makes a bounded network read. Keeping them in one file made the
   guard's own docstring claim "offline" while it wasn't.

⚠️ EVERY FAILURE PATH RETURNS `could_not_look`, NEVER `no_fresher_receipt`.
   A missing token, a 403, a rate limit, a timeout and a malformed payload are
   all *we did not look*. Reporting any of them as *we looked and found nothing*
   is the `curl ... || echo '{}'` class this repo has already paid for, where a
   403 read as `0 checks` made a CI watcher report TIMEOUT having checked
   nothing.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

from digest_due import _parse_ts  # noqa: E402

#: What the carrier lookup found. THREE VALUES, NEVER COLLAPSED — folding
#: `could_not_look` into `none_fresher` would report *we did not look* as *we
#: looked and the carrier is dead*, which is the exact substitution this repo
#: names as a defect class.
# ⚠️ ONE ASSIGNMENT PER CONSTANT, NOT A TUPLE UNPACK. `collapsed-state-guard`
#    derives `{state: [CONSTANT NAMES]}` from module-level `NAME = "<state>"`
#    assignments only, so the tuple form these were written in first was
#    INVISIBLE to it — the contract then reported all three states as read by
#    nobody while `check_digest_liveness` was branching on every one of them.
#    Written out so the registry and the code agree.
CARRIER_FRESHER = "fresher_receipt_on_open_pr"
CARRIER_NONE = "no_fresher_receipt"
CARRIER_UNKNOWN = "could_not_look"


#: Only heads under this prefix are searched for a fresher receipt. The digest
#: receipt is written by `work-digest.yml` through `commit-to-main`, which opens
#: `automation/work-digest-<run>-<n>` branches — so this is where a stuck
#: receipt actually sits.
#: ⚠️ STATED LIMIT: a fresher receipt riding some OTHER branch is not found, and
#: the verdict then stays `stale`, i.e. today's behaviour. The filter can only
#: ever make the guard fail MORE, never less, which is the safe direction.
CARRIER_HEAD_PREFIX = "automation/work-digest"


def _api(path: str, token: str, repo: str, timeout: float) -> Any:
    """One bounded GitHub read. Raises on anything that is not a clean 200."""
    import json as _json
    import urllib.request

    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "check-digest-liveness"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return _json.loads(resp.read().decode("utf-8"))


def look_for_fresher_receipt(main_ts: datetime,
                             receipt_rel: str,
                             token: Optional[str] = None,
                             repo: Optional[str] = None,
                             timeout: float = 10.0,
                             max_heads: int = 8) -> Dict[str, Any]:
    """Is a receipt NEWER than main's sitting on an open PR?

    ⚠️ CALLED ONLY WHEN THE VERDICT WOULD OTHERWISE BE `stale`. A healthy repo
       never reaches this function, so the guard's cost on a normal PR is
       unchanged and no socket is opened.

    ⚠️ IT NEVER RAISES AND IT NEVER RETURNS `no_fresher_receipt` ON A FAILED
       READ. Every error path returns `could_not_look`, because the
       `curl ... || echo '{}'` idiom that turns a 403 into a clean negative is a
       named failure class in this repo — there, a 403 read as `0 checks` made a
       watcher report a confident wrong answer. `no_fresher_receipt` here means
       *we looked at every candidate head and none carried a newer receipt*.
    """
    import base64
    import json as _json
    import os as _os

    token = token or _os.environ.get("GITHUB_TOKEN") or _os.environ.get("GH_TOKEN")
    repo = repo or _os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return {"state": CARRIER_UNKNOWN,
                "detail": "no GITHUB_TOKEN/GITHUB_REPOSITORY in the environment — "
                          "this runs offline outside CI, which is not evidence "
                          "either way"}
    try:
        prs = _api("pulls?state=open&per_page=100", token, repo, timeout)
        if not isinstance(prs, list):
            raise RuntimeError("open-PR list was not a list")
    except Exception as exc:  # noqa: BLE001 — every failure is `could_not_look`
        return {"state": CARRIER_UNKNOWN, "detail": f"open-PR list unreadable: {exc}"}

    heads = [(p.get("number"), (p.get("head") or {}).get("sha"))
             for p in prs
             if str((p.get("head") or {}).get("ref", "")).startswith(CARRIER_HEAD_PREFIX)]
    heads = [(n, sha) for n, sha in heads if n and sha][:max_heads]
    if not heads:
        return {"state": CARRIER_NONE,
                "detail": f"no open PR with a `{CARRIER_HEAD_PREFIX}*` head, "
                          f"over {len(prs)} open PR(s)"}

    best: Dict[str, Any] = {}
    for number, sha in heads:
        try:
            blob = _api(f"contents/{receipt_rel}?ref={sha}", token, repo, timeout)
            raw = base64.b64decode(blob["content"]).decode("utf-8")
            ts = _parse_ts(_json.loads(raw).get("generated_at"))
        except Exception:  # noqa: BLE001
            # ⚠️ ONE unreadable head does NOT make the whole answer unknown —
            #    but it must not silently shrink the population either, so it is
            #    counted and reported beside the verdict.
            best.setdefault("unreadable_heads", 0)
            best["unreadable_heads"] = best.get("unreadable_heads", 0) + 1
            continue
        if ts is not None and ts > main_ts:
            if not best.get("ts") or ts > best["ts"]:
                best.update(ts=ts, pr=number,
                            generated_at=ts.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if best.get("pr"):
        return {"state": CARRIER_FRESHER, "pr": best["pr"],
                "generated_at": best["generated_at"],
                "heads_checked": len(heads),
                "unreadable_heads": best.get("unreadable_heads", 0)}
    if best.get("unreadable_heads") == len(heads):
        return {"state": CARRIER_UNKNOWN,
                "detail": f"all {len(heads)} candidate head(s) were unreadable"}
    return {"state": CARRIER_NONE,
            "detail": f"checked {len(heads)} candidate head(s); none carries a "
                      f"receipt newer than main's",
            "heads_checked": len(heads),
            "unreadable_heads": best.get("unreadable_heads", 0)}


