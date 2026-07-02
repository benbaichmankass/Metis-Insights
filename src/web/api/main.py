"""FastAPI app entrypoint for the dashboard backend.

Serves REST endpoints consumed by the Streamlit dashboard
(benbaichmankass/ict-trader-dashboard, streamlit_app.py).
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.web.api.routers import accounts as accounts_router
from src.web.api.routers import auth as auth_router
from src.web.api.routers import attribution as attribution_router
from src.web.api.routers import backtests as backtests_router
from src.web.api.routers import bot_config as bot_config_router
from src.web.api.routers import candles as candles_router
from src.web.api.routers import performance as performance_router
from src.web.api.routers import dashboard as dashboard_router
from src.web.api.routers import db_explorer as db_explorer_router
from src.web.api.routers import devices as devices_router
from src.web.api.routers import diag as diag_router
from src.web.api.routers import health_snapshots as health_snapshots_router
from src.web.api.routers import insights as insights_router
from src.web.api.routers import gpu_spend as gpu_spend_router
from src.web.api.routers import exit_ladder as exit_ladder_router
from src.web.api.routers import allocator as allocator_router
from src.web.api.routers import liquidity as liquidity_router
from src.web.api.routers import news as news_router
from src.web.api.routers import order_packages as order_packages_router
from src.web.api.routers import pnl as pnl_router
from src.web.api.routers import prop as prop_router
from src.web.api.routers import pnl_exchange as pnl_exchange_router
from src.web.api.routers import pnl_history as pnl_history_router
from src.web.api.routers import reports as reports_router
from src.web.api.routers import roadmap as roadmap_router
from src.web.api.routers import shadow as shadow_router
from src.web.api.routers import status as status_router
from src.web.api.routers import strategies as strategies_router
from src.web.api.routers import strategy_review as strategy_review_router
from src.web.api.routers import strategy_tune as strategy_tune_router
from src.web.api.routers import trade_scores as trade_scores_router
from src.web.api.routers import trades_closed as trades_closed_router
from src.web.api.routers import training_center as training_center_router

app = FastAPI(title="ICT Trading Bot — Dashboard API", version="0.2.0")

_dashboard_origin = os.environ.get("DASHBOARD_ORIGIN", "")
_origins = ["http://localhost:5173", "http://localhost:3000"]
if _dashboard_origin:
    _origins.append(_dashboard_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(status_router.router)
app.include_router(pnl_router.router)
app.include_router(pnl_history_router.router)
app.include_router(auth_router.router)
app.include_router(dashboard_router.router)
app.include_router(bot_config_router.router)
app.include_router(accounts_router.router)
app.include_router(db_explorer_router.router)
app.include_router(liquidity_router.router)
app.include_router(trades_closed_router.router)
app.include_router(order_packages_router.router)
app.include_router(candles_router.router)
app.include_router(performance_router.router)
app.include_router(backtests_router.router)
app.include_router(pnl_exchange_router.router)
app.include_router(diag_router.router)
app.include_router(shadow_router.router)
app.include_router(health_snapshots_router.router)
app.include_router(trade_scores_router.router)
app.include_router(strategies_router.router)
app.include_router(strategy_review_router.router)
app.include_router(strategy_tune_router.router)
app.include_router(training_center_router.router)
app.include_router(attribution_router.router)
app.include_router(devices_router.router)
app.include_router(insights_router.router)
app.include_router(gpu_spend_router.router)
app.include_router(news_router.router)
app.include_router(exit_ladder_router.router)
app.include_router(allocator_router.router)
app.include_router(prop_router.router)
app.include_router(reports_router.router)
app.include_router(roadmap_router.router)


@app.get("/api/health", tags=["health"])
async def health() -> dict:
    return {"ok": True}
