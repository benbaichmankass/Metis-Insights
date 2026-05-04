import psutil
import time
from typing import Dict, Any

def get_vm_health() -> Dict[str, Any]:
    return {
        "cpu_usage": psutil.cpu_percent(),
        "memory_usage": psutil.virtual_memory().percent,
        "uptime": time.time() - psutil.boot_time()
    }

def get_bot_summary() -> Dict[str, Any]:
    # Placeholder for bot integration - strictly read-only
    return {
        "status": "active",
        "pnl_24h": 0.0,
        "active_trades": 0,
        "timestamp": time.time()
    }
