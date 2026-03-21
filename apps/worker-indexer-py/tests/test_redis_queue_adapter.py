from __future__ import annotations

import json

from ast_indexer.adapters.queue.redis_index_job_queue_adapter import RedisIndexJobQueueAdapter
from ast_indexer.domain.index_jobs import DeadLetterIndexJob, IndexJob


class _FakeRedisClient:
    def __init__(self) -> None:
        self._stores: dict[str, list[str]] = {}

    def rpush(self, key: str, value: str) -> int:
        if key not in self._stores:
            self._stores[key] = []
        self._stores[key].append(value)
        return len(self._stores[key])

    def lpop(self, key: str) -> str | None:
        if key not in self._stores or not self._stores[key]:
            return None
        return self._stores[key].pop(0)

    def list_entries(self, key: str) -> list[str]:
        return list(self._stores.get(key, []))


def test_redis_queue_adapter_round_trip() -> None:
    queue = RedisIndexJobQueueAdapter(client=_FakeRedisClient(), queue_key='test:index:jobs')
    job = IndexJob(
        repo='checkout-service',
        changed_paths=('src/orders.py',),
        deleted_paths=('src/legacy.py',),
        trace_id='trace-redis-1',
    )

    queue.enqueue(job)
    queued = queue.dequeue()

    assert queued is not None
    assert queued.repo == 'checkout-service'
    assert queued.changed_paths == ('src/orders.py',)
    assert queued.deleted_paths == ('src/legacy.py',)
    assert queued.trace_id == 'trace-redis-1'


def test_redis_queue_adapter_dequeue_empty() -> None:
    queue = RedisIndexJobQueueAdapter(client=_FakeRedisClient())
    assert queue.dequeue() is None


def test_redis_queue_adapter_writes_dead_letter_payload() -> None:
    client = _FakeRedisClient()
    queue = RedisIndexJobQueueAdapter(
        client=client,
        queue_key='test:index:jobs',
        dead_letter_key='test:index:jobs:dlq',
    )

    queue.enqueue_dead_letter(
        DeadLetterIndexJob(
            job=IndexJob(
                repo='checkout-service',
                changed_paths=('src/orders.py',),
                deleted_paths=(),
                trace_id='trace-redis-dead-1',
                attempt=2,
                max_attempts=3,
            ),
            reason='RuntimeError: parse failure',
        )
    )

    rows = client.list_entries('test:index:jobs:dlq')
    assert len(rows) == 1
    payload = json.loads(rows[0])
    assert payload['reason'] == 'RuntimeError: parse failure'
    assert payload['job']['trace_id'] == 'trace-redis-dead-1'
    assert payload['job']['attempt'] == 2