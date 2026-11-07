import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.config import Settings, default_settings
from app.database import (
    get_config,
    get_latest_snapshot_payload,
    get_recent_snapshots,
    init_db,
    legacy_count,
    save_snapshot,
    update_config,
)
from app.valuation_engine import compute_full_valuation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mstr_monitor")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
STATE = {
    "latest_valuation": None,
    "last_refresh_utc": None,
    "next_refresh_utc": None,
    "is_refreshing": False,
    "last_error": None,
    "refresh_count": 0,
}
LOCK = asyncio.Lock()
WAKE = asyncio.Event()


def next_refresh():
    STATE["next_refresh_utc"] = (
        datetime.now(timezone.utc)
        + timedelta(seconds=get_config()["refresh_interval_seconds"])
    ).isoformat()


async def perform_refresh():
    async with LOCK:
        STATE["is_refreshing"] = True
        try:
            valuation = await asyncio.to_thread(compute_full_valuation)
            await asyncio.to_thread(save_snapshot, valuation)
            STATE.update(
                latest_valuation=valuation,
                last_refresh_utc=valuation["timestamp_utc"],
                last_error=None,
                refresh_count=STATE["refresh_count"] + 1,
            )
        except Exception as exc:
            logger.exception("Refresh failed; retaining previous observation")
            STATE["last_error"] = str(exc)
            raise
        finally:
            STATE["is_refreshing"] = False
            next_refresh()
            WAKE.set()


async def hourly_scheduler_loop():
    while True:
        WAKE.clear()
        target = datetime.fromisoformat(STATE["next_refresh_utc"])
        delay = max(0, (target - datetime.now(timezone.utc)).total_seconds())
        try:
            await asyncio.wait_for(WAKE.wait(), timeout=delay)
            continue
        except asyncio.TimeoutError:
            pass
        try:
            await perform_refresh()
        except Exception:
            pass  # Error exposed in API; retry at the next scheduled interval.


@asynccontextmanager
async def lifespan(app):
    init_db()
    cached = get_latest_snapshot_payload()
    STATE.update(
        latest_valuation=cached,
        last_refresh_utc=cached["timestamp_utc"] if cached else None,
    )
    next_refresh()

    async def startup_refresh():
        try:
            await perform_refresh()
        except Exception:
            pass

    initial = asyncio.create_task(startup_refresh())
    scheduler = asyncio.create_task(hourly_scheduler_loop())
    yield
    for task in (initial, scheduler):
        task.cancel()
    await asyncio.gather(initial, scheduler, return_exceptions=True)


app = FastAPI(title="MSTR Observatory", version="2.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def protect_browser_writes(request: Request, call_next):
    # Public reads remain available. Cross-site browser writes are rejected.
    if request.method in ("POST", "PUT", "DELETE"):
        from urllib.parse import urlsplit

        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"detail": "Cross-origin writes are not allowed"}, status_code=403
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


def scheduler_meta():
    return {k: v for k, v in STATE.items() if k != "latest_valuation"} | {
        "refresh_interval_seconds": get_config()["refresh_interval_seconds"]
    }


@app.get("/health")
async def health():
    latest = STATE["latest_valuation"]
    aged = not latest or (
        datetime.now(timezone.utc) - datetime.fromisoformat(latest["timestamp_utc"])
    ).total_seconds() > max(7200, get_config()["refresh_interval_seconds"] * 2)
    return {
        "status": "degraded"
        if STATE["last_error"] or aged or latest.get("quality") == "stale"
        else "ok",
        "service": "mstr-fair-value-monitor",
        "scheduler": scheduler_meta(),
    }


@app.get("/api/valuation")
async def valuation():
    return {
        "valuation": STATE["latest_valuation"],
        "scheduler": scheduler_meta(),
        "hourly_logs": get_recent_snapshots(),
        "legacy_snapshot_count": legacy_count(),
        "config": get_config(),
    }


@app.post("/api/refresh")
async def refresh():
    if STATE["is_refreshing"]:
        raise HTTPException(409, "Refresh is already running")
    try:
        await perform_refresh()
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc
    return await valuation()


@app.post("/api/config")
async def configure(payload: Settings):
    if STATE["is_refreshing"]:
        raise HTTPException(
            409, "Wait for the active refresh before changing assumptions"
        )
    # Only mark a configuration saved; market refresh failure does not imply save failure.
    update_config(payload.model_dump(mode="json"))
    try:
        await perform_refresh()
    except Exception:
        pass
    return await valuation()


@app.get("/api/config/reviewed")
async def reviewed_config():
    return default_settings()


@app.get("/api/history")
async def history(limit: int = Query(default=168, ge=1, le=8760)):
    rows = get_recent_snapshots(limit)
    return {
        "count": len(rows),
        "model_version": "2.0",
        "snapshots": rows,
        "legacy_snapshot_count": legacy_count(),
    }
