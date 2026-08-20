FROM python:3.11-slim

WORKDIR /app

# Install backend dependencies first (better layer caching — only
# reinstalls if requirements.txt actually changes)
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the actual application code
COPY backend/ ./backend/

WORKDIR /app/backend

EXPOSE 8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}