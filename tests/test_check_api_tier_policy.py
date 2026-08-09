"""The API tier-inventory guard (`scripts/check_api_tier_policy.py`).

This guard exists because `docs/api-tier-policy.md` called itself the single
source of truth for route tiers while being **60% incomplete** — 54 of 90
routes had no row — for three months, because nothing enforced it. Every
sibling inventory that stays correct has a CI check behind it; this one did
not, so routes landed unannounced from 2026-05-09 to 2026-08-09.

Two properties are load-bearing here, and they pull in opposite directions:

* ``TestFailsLoudly`` — a route with no row must produce a NON-ZERO exit. A
  checker that prints a warning and exits 0 would be another green-but-doing-
  nothing check, which is the bug guarding the bug.
* ``TestCreditsTheFamilyRowConvention`` — the Tier-2.5 section documents the
  diag surface as family rows (bare leaves after a sibling full path). A naive
  exact-string matcher credits none of it and reports 76% missing against a
  true 69%: it MANUFACTURES a gap and pressures a contributor into reformatting
  a doc that was already correct. A guard that picks a fight with the thing it
  protects gets silenced wholesale.

``TestStatedCoverageIsTrue`` is the one that would have caught the original
defect: the file's own headline number must equal a freshly computed one. That
number was miscounted twice by hand while the warning banner was being written
(77% → 69% → 60%), which is the whole argument for computing it.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

_spec = importlib.util.spec_from_file_location(
    "check_api_tier_policy", os.path.join(REPO, "scripts", "check_api_tier_policy.py")
)
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)


ROUTER_SRC = (
    'from fastapi import APIRouter\n'
    'router = APIRouter(prefix="/api/bot/thing", tags=["t"])\n'
    '@router.get("/alpha")\n'
    'def a():\n'
    '    return {}\n'
    '@router.post("/beta/{item_id}")\n'
    'def b(item_id: str):\n'
    '    return {}\n'
)


class TestEnumeration:
    """Routes are the PREFIX joined to the decorator, not either alone."""

    def test_joins_prefix_to_decorator_path(self):
        routes = g.routes_in_source("src/web/api/routers/x.py", ROUTER_SRC)
        assert {r.key for r in routes} == {
            "GET /api/bot/thing/alpha",
            "POST /api/bot/thing/beta/{}",
        }

    def test_records_the_decorator_line_not_the_function_line(self):
        # Diff scoping keys on this; pointing at the `def` would miss a PR that
        # adds only a decorator to an existing handler.
        routes = {r.key: r.lineno for r in g.routes_in_source("x.py", ROUTER_SRC)}
        assert routes["GET /api/bot/thing/alpha"] == 3

    def test_router_without_a_prefix_still_yields_routes(self):
        # `market_ws.py` declares APIRouter(tags=[...]) with no prefix. An
        # enumerator that required `prefix=` would drop /ws/market entirely --
        # the one route in the original inventory whose absence was least
        # visible, because it is not a GET.
        src = ('from fastapi import APIRouter\n'
               'router = APIRouter(tags=["m"])\n'
               '@router.websocket("/ws/market")\n'
               'def w():\n'
               '    return {}\n')
        routes = g.routes_in_source("x.py", src)
        assert [r.key for r in routes] == ["WEBSOCKET /ws/market"]

    def test_a_file_that_does_not_parse_yields_nothing_rather_than_claiming_coverage(self):
        assert g.routes_in_source("x.py", "def broken( :\n") == []

    def test_matches_the_live_fastapi_route_table(self):
        """The population claim, verified against the app itself.

        Skipped where FastAPI is not importable (the guard is stdlib-only by
        design so CI can run it anywhere); where it IS importable this is the
        check that the AST walk has neither missed nor invented a route.
        """
        pytest.importorskip("fastapi")
        try:
            from src.web.api.main import app
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"app not importable here: {exc}")
        live = set()
        for r in app.routes:
            path = getattr(r, "path", None)
            if path is None:
                continue
            for method in (getattr(r, "methods", None) or {"WEBSOCKET"}):
                if method in ("HEAD", "OPTIONS"):
                    continue
                live.add(f"{method} {g.normalise_path(path)}")
        mine = {r.key for r in g.enumerate_routes()}
        # Everything the enumerator reports must really be mounted.
        assert not (mine - live), f"enumerated but not live: {sorted(mine - live)}"
        # And the only live routes it omits are the ones outside routers/.
        outside = {"GET /api/health", "GET /docs", "GET /docs/oauth2-redirect",
                   "GET /openapi.json", "GET /redoc"}
        assert (live - mine) <= outside, f"missed live routes: {sorted(live - mine - outside)}"


class TestNormalisation:
    def test_path_params_collapse_so_a_rename_is_not_a_missing_row(self):
        assert g.normalise_path("/a/{table}") == g.normalise_path("/a/{name}")

    def test_query_string_is_stripped(self):
        assert g.normalise_path("/a/b?limit=N&x=Y") == "/a/b"

    def test_trailing_slash_does_not_split_a_route_from_its_row(self):
        assert g.normalise_path("/a/b/") == "/a/b"


class TestDocParsing:
    def test_reads_a_plain_table_row(self):
        doc = "| `GET /api/bot/stats` | src | notes |\n"
        assert "GET /api/bot/stats" in g.documented_keys(doc)

    def test_verb_is_significant(self):
        """GET and POST on one path are two different tier judgements.

        `/api/bot/learning/progress` is exactly this: the GET is a plain read,
        the POST is an unauthenticated write. Crediting one for the other would
        hide the half that actually needed thinking about.
        """
        doc = "| `GET /api/bot/learning/progress` | src | notes |\n"
        keys = g.documented_keys(doc)
        assert "GET /api/bot/learning/progress" in keys
        assert "POST /api/bot/learning/progress" not in keys

    def test_prose_mentions_do_not_count_as_documentation(self):
        # Only table rows count. Otherwise name-dropping an endpoint in a
        # paragraph makes the inventory look complete.
        doc = "See `GET /api/bot/stats` for details.\n"
        assert g.documented_keys(doc) == set()

    def test_header_underline_is_not_parsed_as_a_row(self):
        assert g.documented_keys("|---|---|---|\n") == set()


class TestCreditsTheFamilyRowConvention:
    """The Tier-2.5 shorthand must count as documentation, exactly as written."""

    def test_bare_leaves_inherit_the_sibling_full_path_directory(self):
        doc = "| `GET /api/diag/snapshot`, `audit`, `journal` | src | notes |\n"
        keys = g.documented_keys(doc)
        assert keys == {
            "GET /api/diag/snapshot",
            "GET /api/diag/audit",
            "GET /api/diag/journal",
        }

    def test_a_deeper_leaf_resolves_too(self):
        doc = "| `GET /api/bot/insights/summary`, `strategy/{name}` | src | n |\n"
        assert "GET /api/bot/insights/strategy/{}" in g.documented_keys(doc)

    def test_the_real_doc_credits_every_diag_route(self):
        # The regression that matters: if this convention ever stops being
        # credited, the guard reports ~16 phantom gaps and the fix a
        # contributor reaches for is reformatting a correct document.
        doc = open(os.path.join(REPO, "docs/api-tier-policy.md"), encoding="utf-8").read()
        documented = g.documented_keys(doc)
        diag = [r for r in g.enumerate_routes() if r.path.startswith("/api/diag/")]
        assert diag, "no diag routes found - the probe itself is broken"
        assert [r.key for r in diag if r.key not in documented] == []


class TestDiffScoping:
    def test_flags_a_newly_added_route(self, tmp_path, monkeypatch):
        planted = os.path.join(REPO, "src/web/api/routers/_pytest_tmp_route.py")
        with open(planted, "w", encoding="utf-8") as fh:
            fh.write(ROUTER_SRC)
        try:
            diff = ("--- /dev/null\n"
                    "+++ b/src/web/api/routers/_pytest_tmp_route.py\n"
                    "@@ -0,0 +1,8 @@\n"
                    + "".join(f"+{ln}\n" for ln in ROUTER_SRC.splitlines()))
            scoped = {r.key for r in g.routes_in_scope(diff)}
            assert "GET /api/bot/thing/alpha" in scoped
        finally:
            os.remove(planted)

    def test_ignores_a_diff_that_touches_no_router(self):
        diff = ("--- a/README.md\n+++ b/README.md\n@@ -1,1 +1,1 @@\n+hello\n")
        assert g.routes_in_scope(diff) == []

    def test_touching_the_APIRouter_line_pulls_in_every_route_in_the_file(self):
        """Editing a prefix silently rewrites every path in the file.

        A decorator-line-only scope would wave that through while invalidating
        a whole section of the inventory at once.
        """
        planted = os.path.join(REPO, "src/web/api/routers/_pytest_tmp_prefix.py")
        with open(planted, "w", encoding="utf-8") as fh:
            fh.write(ROUTER_SRC)
        try:
            diff = ("--- a/src/web/api/routers/_pytest_tmp_prefix.py\n"
                    "+++ b/src/web/api/routers/_pytest_tmp_prefix.py\n"
                    "@@ -2,1 +2,1 @@\n"
                    '+router = APIRouter(prefix="/api/bot/thing", tags=["t"])\n')
            scoped = {r.key for r in g.routes_in_scope(diff)}
            assert scoped == {
                "GET /api/bot/thing/alpha",
                "POST /api/bot/thing/beta/{}",
            }
        finally:
            os.remove(planted)


class TestFailsLoudly:
    """The load-bearing property: an unrowed route must exit NON-ZERO."""

    def test_undocumented_route_is_reported(self):
        routes = g.routes_in_source("x.py", ROUTER_SRC)
        assert len(g.undocumented(routes, documented=set())) == 2

    def test_documented_route_is_not_reported(self):
        routes = g.routes_in_source("x.py", ROUTER_SRC)
        documented = {"GET /api/bot/thing/alpha", "POST /api/bot/thing/beta/{}"}
        assert g.undocumented(routes, documented) == []

    def test_grandfathered_routes_are_exempt(self, monkeypatch):
        monkeypatch.setattr(g, "_GRANDFATHERED", {"GET /api/bot/thing/alpha"})
        routes = g.routes_in_source("x.py", ROUTER_SRC)
        assert [r.key for r in g.undocumented(routes, set())] == [
            "POST /api/bot/thing/beta/{}"
        ]

    def test_grandfather_list_is_empty_so_gaps_are_decisions_not_backlog(self):
        # It exists as an escape hatch, not as a place for debt to accumulate.
        # An entry here should be a reviewed decision with a stated reason.
        assert g._GRANDFATHERED == set()


class TestStatedCoverageIsTrue:
    """The file's headline number must equal a freshly computed one.

    This is the test that would have caught the original defect. The figure was
    miscounted twice BY HAND while the previous warning banner was being
    written, so the banner shipped stating a number its own file contradicted.
    """

    def test_every_live_route_carries_a_row(self):
        doc_path = os.path.join(REPO, "docs/api-tier-policy.md")
        documented = g.documented_keys(open(doc_path, encoding="utf-8").read())
        missing = g.undocumented(g.enumerate_routes(), documented)
        assert missing == [], (
            "routes with no tier row: " + ", ".join(r.render() for r in missing)
        )

    def test_the_documented_coverage_claim_matches_the_computed_one(self):
        doc_path = os.path.join(REPO, "docs/api-tier-policy.md")
        text = open(doc_path, encoding="utf-8").read()
        m = re.search(r"\*\*Coverage[^:]*:\s*(\d+)\s+of\s+(\d+)\s+routes", text)
        assert m, "the coverage line is missing or reworded - keep it machine-checkable"
        claimed_covered, claimed_total = int(m.group(1)), int(m.group(2))

        routes = g.enumerate_routes()
        documented = g.documented_keys(text)
        actual_total = len(routes)
        actual_covered = actual_total - len(g.undocumented(routes, documented))

        assert (claimed_covered, claimed_total) == (actual_covered, actual_total), (
            f"the doc claims {claimed_covered}/{claimed_total}; the code says "
            f"{actual_covered}/{actual_total}. Run "
            f"`python3 scripts/check_api_tier_policy.py --list` and restate it."
        )
