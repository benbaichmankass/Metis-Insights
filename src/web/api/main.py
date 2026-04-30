"""S-013 M2 — FastAPI app entrypoint for the dashboard backend.

Mounted under uvicorn by ``deploy/ict-web-api.service`` on the
VM staging port (8001). NOT exposed to the public internet until the
S-014 client ships and M3 PR #2 has flipped ``require_session`` from
no-op to real enforcement.
"""
from __future__ import annotations

from fastapi import FastAPI

from src.web.api.routers import pnl as pnl_router
from src.web.api.routers import status as status_router

app = FastAPI(title="ICT Trading Bot — Dashboard API", version="0.1.0")
app.include_router(status_router.router)
app.include_router(pnl_router.router)


@app.get("/api/health", tags=["health"])
async def health() -> dict:
    return {"ok": True}
