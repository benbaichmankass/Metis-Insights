from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.dashboards.web_getters import get_vm_health, get_bot_summary

app = FastAPI(title="ICT Bot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
)

@app.get("/api/v1/health")
async def health():
    return get_vm_health()

@app.get("/api/v1/overview")
async def overview():
    return get_bot_summary()
