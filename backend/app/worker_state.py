"""
Shared, minimal module holding a reference to the running background
worker tasks — exists purely so app/api/health.py can check worker
liveness without importing from app/main.py, which would create a
circular import (main.py already imports the health router).
"""

import asyncio

worker_tasks: list[asyncio.Task] = []
