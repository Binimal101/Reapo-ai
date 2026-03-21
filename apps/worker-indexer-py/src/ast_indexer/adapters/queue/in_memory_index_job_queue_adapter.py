from __future__ import annotations

from ast_indexer.domain.index_jobs import DeadLetterIndexJob, IndexJob
from ast_indexer.ports.index_job_queue import IndexJobQueuePort


class InMemoryIndexJobQueueAdapter(IndexJobQueuePort):
    def __init__(self) -> None:
        self._queue: list[IndexJob] = []
        self._dead_letters: list[DeadLetterIndexJob] = []

    def enqueue(self, job: IndexJob) -> None:
        self._queue.append(job)

    def dequeue(self) -> IndexJob | None:
        if not self._queue:
            return None
        return self._queue.pop(0)

    def enqueue_dead_letter(self, entry: DeadLetterIndexJob) -> None:
        self._dead_letters.append(entry)

    def list_dead_letters(self) -> list[DeadLetterIndexJob]:
        return list(self._dead_letters)
