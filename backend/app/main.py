from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.observability.logging import configure_logging, get_logger
from app.api.health import router as health_router
from app.api.agents import router as agents_router
from app.api.documents import router as documents_router

configure_logging()
logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Agent-OS starting up")
    yield
    logger.info("Agent-OS shutting down")

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
