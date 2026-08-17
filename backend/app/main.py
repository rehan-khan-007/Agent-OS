from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init DB, Redis, etc.
    yield
    # Shutdown: cleanup

app = FastAPI(title="Agent-OS", version="0.1.0", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}