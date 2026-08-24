"""
Health check endpoints.

/health — kept as-is for backward compatibility; the deployed
frontend already polls this for its connection-status indicator.

/health/live — liveness probe: is the process itself alive and able
to respond at all. Deliberately checks nothing else — a liveness
probe that depends on external services (database, Redis) can cause
a healthy process to be needlessly killed/restarted just because a
downstream dependency had a blip, which is the wrong failure mode
for liveness specifically.

/health/ready — readiness probe: is the app actually ready to serve
real traffic. Checks the things that were previously invisible:
PostgreSQL reachability, Redis reachability, required config
presence, and whether the background worker tasks are still running
(not silently dead — see the worker_survives_unexpected_dequeue_exception
test for the exact failure mode this catches: a worker task that
died silently with no visible symptom other than jobs never being
processed).
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.worker_state import worker_tasks

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@router.get("/health/live")
async def liveness():
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness():
    checks: dict[str, str] = {}
    healthy = True

    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
        healthy = False

    if not settings.redis_url or "localhost" in settings.redis_url:
        checks["redis"] = "not configured"
    else:
        try:
            from app.queue.redis_queue import get_client
            client = get_client()
            await client.ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"error: {e}"
            healthy = False

    checks["openrouter_api_key"] = "configured" if settings.openrouter_api_key else "missing"
    if not settings.openrouter_api_key:
        healthy = False

    alive_workers = sum(1 for t in worker_tasks if not t.done())
    total_workers = len(worker_tasks)
    checks["workers"] = f"{alive_workers}/{total_workers} running"
    if total_workers > 0 and alive_workers == 0:
        healthy = False

    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if healthy else "not_ready", "checks": checks},
    )
