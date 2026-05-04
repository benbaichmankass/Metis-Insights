# Sprint Doc: Web App V1 Implementation Plan
**Project**: ICT Trading Bot
**Status**: Tier 1 (Read-Only)

## 1. Architecture
- **Backend**: FastAPI (Python) serving read-only data from existing dashboard logic.
- **Frontend**: React + TradingView Lightweight Charts for per-tick monitoring.
- **Data Flow**: Dashboards Unit -> FastAPI -> WebSockets -> UI.

## 2. API Endpoints
- `GET /api/v1/health`: VM CPU, RAM, and Bot Uptime.
- `GET /api/v1/overview`: Live status, 24h PnL, open positions count.
- `WS /ws/v1/ticks`: Real-time price feed for hollow-candle charts.

## 3. Roadmap
- **S1**: Backend API Scaffolding (This PR).
- **S2**: Live Monitor UI (React + Charts).
- **S3**: Performance Analytics & Strategy Overlays.
- **S4**: Deployment via Nginx/Systemd.
