FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY apps/worker-indexer-py/pyproject.toml /app/apps/worker-indexer-py/pyproject.toml
COPY apps/worker-indexer-py/src /app/apps/worker-indexer-py/src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        openai>=1.40 \
        /app/apps/worker-indexer-py[auth,observability]

WORKDIR /workspace
EXPOSE 8090
