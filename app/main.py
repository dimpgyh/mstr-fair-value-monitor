import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.database import (
    DEFAULT_CONFIG,
    get_config,
    get_latest_snapshot_payload,
    get_recent_snapshots,
    init_db,
    save_snapshot,
    update_config,
)
from app.valuation_engine import compute_full_valuation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mstr_monitor.main")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# In-memory state for fast serving & scheduler status
STATE: Dict[str, Any] = {
    "latest_valuation": None,
    "last_refresh_utc": None,
    "next_refresh_utc": None,
    "refresh_interval_seconds": 3600,
    "refresh_count": 0,
    "is_refreshing": False,
    "last_error": None,
}

REFRESH_LOCK = asyncio.Lock()


async def perform_refresh(reason: str = "scheduled") -> Dict[str, Any]:
    """Run the valuation calculation in a thread pool and persist snapshot."""
    async with REFRESH_LOCK:
        STATE["is_refreshing"] = True
        try:
            logger.info("Executing MSTR fair value refresh (reason=%s)...", reason)
            valuation = await asyncio.to_thread(compute_full_valuation)
            await asyncio.to_thread(save_snapshot, valuation)

            now_utc = datetime.now(timezone.utc)
            cfg = valuation.get("config", get_config())
            interval_sec = int(cfg.get("refresh_interval_seconds", 3600))
            next_utc = now_utc + timedelta(seconds=interval_sec)

            STATE["latest_valuation"] = valuation
            STATE["last_refresh_utc"] = now_utc.isoformat()
            STATE["next_refresh_utc"] = next_utc.isoformat()
            STATE["refresh_interval_seconds"] = interval_sec
            STATE["refresh_count"] += 1
            STATE["last_error"] = None
            logger.info(
                "Refresh #%d complete: MSTR=$%.2f, BTC=$%.2f, Composite Fair Value=$%.2f (%s)",
                STATE["refresh_count"],
                valuation["market"]["mstr_price"],
                valuation["market"]["btc_price"],
                valuation["composite"]["fair_value"],
                valuation["composite"]["verdict_code"],
            )
            return valuation
        except Exception as exc:
            logger.exception("Error during MSTR valuation refresh: %s", exc)
            STATE["last_error"] = str(exc)
            raise
        finally:
            STATE["is_refreshing"] = False


async def hourly_scheduler_loop() -> None:
    """Background loop that triggers perform_refresh every refresh_interval_seconds (default 1 hour)."""
    while True:
        try:
            interval = int(STATE.get("refresh_interval_seconds") or 3600)
            interval = max(60, interval)
            await asyncio.sleep(interval)
            await perform_refresh(reason="hourly_timer")
        except asyncio.CancelledError:
            logger.info("Hourly scheduler loop cancelled.")
            break
        except Exception as exc:
            logger.error("Hourly scheduler loop encountered error: %s", exc)
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Load cached snapshot first if available
    cached = get_latest_snapshot_payload()
    if cached:
        STATE["latest_valuation"] = cached
        STATE["last_refresh_utc"] = cached.get("timestamp_utc")

    # Run immediate live refresh on startup
    try:
        await perform_refresh(reason="startup")
    except Exception as exc:
        logger.warning("Initial startup refresh failed, using cached/fallback state: %s", exc)

    scheduler_task = asyncio.create_task(hourly_scheduler_loop())
    try:
        yield
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="MSTR Fair Value Monitor",
    description="Real-Time & Hourly MSTR Multi-Model Composite Fair Value Monitor",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_Methods=["*"],
    allow_headers=["*"],
) if False else None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ConfigUpdateRequest(BaseModel):
    btc_holdings: Optional[float] = None
    btc_avg_cost: Optional[float] = None
    basic_shares: Optional[float] = None
    diluted_shares: Optional[float] = None
    total_debt: Optional[float] = None
    preferred_stock: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    software_ev: Optional[float] = None
    target_basic_mnav: Optional[float] = None
    target_ev_mnav: Optional[float] = None
    annual_btc_yield_pct: Optional[float] = None
    discount_rate_pct: Optional[float] = None
    yield_horizon_years: Optional[float] = None
    optionality_premium_pct: Optional[float] = None
    auto_sync_fundamentals: Optional[bool] = None
    refresh_interval_seconds: Optional[int] = None
    weights: Optional[Dict[str, float]] = None


def _build_scheduler_meta() -> Dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    next_str = STATE.get("next_refresh_utc")
    sec_remaining = STATE.get("refresh_interval_seconds", 3600)
    if next_str:
        try:
            next_dt = datetime.fromisoformat(next_str)
            sec_remaining = max(0, int((next_dt - now_utc).total_seconds()))
        except Exception:
            pass
    return {
        "last_refresh_utc": STATE.get("last_refresh_utc"),
        "next_refresh_utc": STATE.get("next_refresh_utc"),
        "seconds_until_next_refresh": sec_remaining,
        "refresh_interval_seconds": STATE.get("refresh_interval_seconds", 3600),
        "refresh_count": STATE.get("refresh_count", 0),
        "is_refreshing": STATE.get("is_refreshing", False),
        "last_error": STATE.get("last_error"),
    }


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "mstr-fair-value-monitor",
        "scheduler": _build_scheduler_meta(),
    }


@app.get("/api/valuation")
async def api_get_valuation():
    if not STATE["latest_valuation"]:
        await perform_refresh(reason="on_demand_init")
    snapshots = await asyncio.to_thread(get_recent_snapshots, 168)
    return JSONResponse(
        {
            "valuation": STATE["latest_valuation"],
            "scheduler": _build_scheduler_meta(),
            "hourly_logs": snapshots,
        }
    )


@app.post("/api/refresh")
async def api_trigger_refresh():
    try:
        valuation = await perform_refresh(reason="manual_user_trigger")
        snapshots = await asyncio.to_thread(get_recent_snapshots, 168)
        return JSONResponse(
            {
                "status": "refreshed",
                "valuation": valuation,
                "scheduler": _build_scheduler_meta(),
                "hourly_logs": snapshots,
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gagal melakukan refresh data: {exc}")


@app.post("/api/config")
async def api_update_config(payload: ConfigUpdateRequest):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    await asyncio.to_thread(update_config, updates)
    valuation = await perform_refresh(reason="config_updated")
    snapshots = await asyncio.to_thread(get_recent_snapshots, 168)
    return JSONResponse(
        {
            "status": "updated",
            "valuation": valuation,
            "scheduler": _build_scheduler_meta(),
            "hourly_logs": snapshots,
        }
    )


@app.post("/api/config/reset")
async def api_reset_config():
    await asyncio.to_thread(update_config, dict(DEFAULT_CONFIG))
    valuation = await perform_refresh(reason="config_reset")
    snapshots = await asyncio.to_thread(get_recent_snapshots, 168)
    return JSONResponse(
        {
            "status": "reset",
            "valuation": valuation,
            "scheduler": _build_scheduler_meta(),
            "hourly_logs": snapshots,
        }
    )


@app.get("/api/history")
async def api_get_history(limit: int = 168):
    snapshots = await asyncio.to_thread(get_recent_snapshots, limit)
    return JSONResponse({"count": len(snapshots), "snapshots": snapshots})
