"""S-014 M1 PR #2 — UI routes.

Three browser-facing routes that render Jinja2 templates from
``web/templates``:

* ``GET /`` — bounce to ``/home`` (auth.js handles the redirect-to-login
  fallback if no token in localStorage). Public.
* ``GET /login`` — operator sign-in page. Public.
* ``GET /home`` — dashboard shell. Public *server-side* — auth.js gates
  the page client-side by reading ``localStorage["ict_session_token"]``
  and redirecting to ``/login`` if absent. The HTMX fragments the page
  loads (``/ui/fragments/status`` etc.) are auth-gated server-side via
  ``Depends(require_session)`` in M3.

Templates live at the repo root under ``web/templates`` rather than
under ``src/`` so the static + template tree is one directory the
operator can ``ls`` without descending into Python packages.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["ui"])


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/home", status_code=307)


@router.get("/login", include_in_schema=False)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/home", include_in_schema=False)
async def home_page(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})
