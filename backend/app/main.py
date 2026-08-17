from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.agents import router as agents_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(title="Agent-OS", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(agents_router)