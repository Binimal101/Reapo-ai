from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Protocol

from ast_indexer.domain.index_jobs import DeadLetterIndexJob, IndexJob
from ast_indexer.ports.index_job_queue import IndexJobQueuePort


class _RedisClientProtocol(Protocol):
    def rpush(self, key: str, value: str) -> int:
        ...

    def lpop(self, key: str) -> str | bytes | None:
        ...


class RedisIndexJobQueueAdapter(IndexJobQueuePort):
    def __init__(
        self,
        client: _RedisClientProtocol,
        queue_key: str = 'ast_indexer:index_jobs',
        dead_letter_key: str = 'ast_indexer:index_jobs:dead_letter',
    ) -> None:
        self._client = client
        self._queue_key = queue_key
        self._dead_letter_key = dead_letter_key

    @classmethod
    def from_url(
        cls,
        url: str,
        queue_key: str = 'ast_indexer:index_jobs',
        dead_letter_key: str = 'ast_indexer:index_jobs:dead_letter',
    ) -> RedisIndexJobQueueAdapter:
        try:
            import redis  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError('Redis backend requires the "redis" package to be installed') from exc

        client = redis.Redis.from_url(url)
        return cls(client=client, queue_key=queue_key, dead_letter_key=dead_letter_key)

    def enqueue(self, job: IndexJob) -> None:
        self._client.rpush(self._queue_key, json.dumps(asdict(job)))

    def dequeue(self) -> IndexJob | None:
        raw: str | bytes | None = self._client.lpop(self._queue_key)
        if raw is None:
            return None

        payload_text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        payload: dict[str, Any] = json.loads(payload_text)
        return IndexJob(
            repo=payload['repo'],
            changed_paths=tuple(payload['changed_paths']),
            deleted_paths=tuple(payload['deleted_paths']),
            trace_id=payload['trace_id'],
            attempt=payload.get('attempt', 0),
            max_attempts=payload.get('max_attempts', 3),
            source=payload.get('source', 'github_push'),
        )

    def enqueue_dead_letter(self, entry: DeadLetterIndexJob) -> None:
        self._client.rpush(
            self._dead_letter_key,
            json.dumps(
                {
                    'reason': entry.reason,
                    'job': asdict(entry.job),
                }
            ),
        )