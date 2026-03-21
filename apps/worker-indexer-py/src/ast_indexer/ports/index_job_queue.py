from __future__ import annotations

from typing import Protocol

from ast_indexer.domain.index_jobs import DeadLetterIndexJob, IndexJob


class IndexJobQueuePort(Protocol):
    def enqueue(self, job: IndexJob) -> None:
        """Push a new index job onto the queue."""

    def dequeue(self) -> IndexJob | None:
        """Pop the next index job or None when queue is empty."""

    def enqueue_dead_letter(self, entry: DeadLetterIndexJob) -> None:
        """Move a permanently failed job to dead-letter storage."""
