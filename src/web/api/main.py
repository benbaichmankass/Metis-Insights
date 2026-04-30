"""S-013 M2 — FastAPI app entrypoint for the dashboard backend.

Mounted under uvicorn by ``deploy/ict-web-api.service`` on the
VM staging port (8001). NOT exposed to the public internet until the
S-014 client ships and M3 PR #2 has flipped ``require_session`` from
no-op to real enforcement.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.web.api.routers import auth as auth_router
from src.web.api.routers import pnl as pnl_router
from src.web.api.routers import pnl_history as pnl_history_router
from src.web.api.routers import pnl_fragment as pnl_fragment_router
from src.web.api.routers import status as status_router
from src.web.api.routers import status_fragment as status_fragment_router
from src.web.api.routers import ui as ui_router

REPO_ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = REPO_ROOT / "web" / "static"

app = FastAPI(title="ICT Trading Bot — Dashboard API", version="0.1.0")
app.include_router(status_router.router)
app.include_router(pnl_router.router)
app.include_router(pnl_history_router.router)
app.include_router(auth_router.router)
app.include_router(ui_router.router)
app.include_router(status_fragment_router.router)
app.include_router(pnl_fragment_router.router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/health", tags=["health"])
async def health() -> dict:
    return {"ok": True}
