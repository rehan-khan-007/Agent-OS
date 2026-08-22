import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.observability.logging import configure_logging, get_logger
from app.api.health import router as health_router
from app.api.agents import router as agents_router
from app.api.documents import router as documents_router
from app.queue.worker import worker_loop, reclaim_loop, DOCUMENT_QUEUE

configure_logging()
logger = get_logger(__name__)

NUM_WORKERS = 2

_worker_tasks: list[asyncio.Task] = []
_stop_event = asyncio.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Agent-OS starting up")

    for i in range(NUM_WORKERS):
        task = asyncio.create_task(
            worker_loop(DOCUMENT_QUEUE, worker_id=f"doc-worker-{i}", stop_event=_stop_event)
        )
        _worker_tasks.append(task)
    logger.info("Started background workers", extra={"extra_fields": {"count": NUM_WORKERS}})

    reclaim_task = asyncio.create_task(reclaim_loop(DOCUMENT_QUEUE, stop_event=_stop_event))
    _worker_tasks.append(reclaim_task)

    yield

    logger.info("Agent-OS shutting down, stopping workers")
    _stop_event.set()
    await asyncio.gather(*_worker_tasks, return_exceptions=True)

app = FastAPI(title="Agent-OS", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_origin_regex=r"https://agent-os.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(agents_router)
app.include_router(documents_router)
