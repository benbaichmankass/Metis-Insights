"""FastAPI app entrypoint for the dashboard backend.

Serves REST endpoints consumed by the Vercel React dashboard.
The HTMX/Streamlit UIs have been removed; only the REST API remains.
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.web.api.routers import auth as auth_router
from src.web.api.routers import dashboard as dashboard_router
from src.web.api.routers import diag as diag_router
from src.web.api.routers import pnl as pnl_router
from src.web.api.routers import pnl_fragment as pnl_fragment_router
from src.web.api.routers import pnl_history as pnl_history_router
from src.web.api.routers import status as status_router
from src.web.api.routers import status_fragment as status_fragment_router

app = FastAPI(title="ICT Trading Bot — Dashboard API", version="0.2.0")

_dashboard_origin = os.environ.get("DASHBOARD_ORIGIN", "")
_origins = ["http://localhost:5173", "http://localhost:3000"]
if _dashboard_origin:
    _origins.append(_dashboard_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(status_router.router)
app.include_router(pnl_router.router)
app.include_router(pnl_history_router.router)
app.include_router(auth_router.router)
app.include_router(status_fragment_router.router)
app.include_router(pnl_fragment_router.router)
app.include_router(dashboard_router.router)
app.include_router(diag_router.router)


@app.get("/api/health", tags=["health"])
async def health() -> dict:
    return {"ok": True}
