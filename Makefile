.PHONY: install run dev

install:
	pip install -r backend/requirements.txt

run:
	uvicorn backend.app.main:app --reload --port 8000

dev: install run