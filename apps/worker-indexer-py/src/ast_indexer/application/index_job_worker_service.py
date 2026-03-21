from __future__ import annotations

import traceback
from dataclasses import dataclass

from ast_indexer.application.index_python_repository_service import IndexPythonRepositoryService
from ast_indexer.domain.index_jobs import DeadLetterIndexJob, IndexJob
from ast_indexer.domain.models import IndexRunMetrics
from ast_indexer.ports.index_job_queue import IndexJobQueuePort


@dataclass(frozen=True)
class IndexJobProcessOutcome:
    status: str
    job: IndexJob | None = None
    metrics: IndexRunMetrics | None = None
    reason: str | None = None


class IndexJobWorkerService:
    def __init__(self, queue: IndexJobQueuePort, index_service: IndexPythonRepositoryService) -> None:
        self._queue = queue
        self._index_service = index_service

    def process_next(self) -> IndexJobProcessOutcome:
        job = self._queue.dequeue()
        if job is None:
            return IndexJobProcessOutcome(status='no_job')

        try:
            metrics = self._index_service.index_repository_subset(
                repo=job.repo,
                trace_id=job.trace_id,
                file_paths=list(job.changed_paths),
                deleted_paths=list(job.deleted_paths),
            )
            return IndexJobProcessOutcome(status='processed', job=job, metrics=metrics)
        except Exception as exc:
            if job.attempt + 1 < job.max_attempts:
                retried_job = IndexJob(
                    repo=job.repo,
                    changed_paths=job.changed_paths,
                    deleted_paths=job.deleted_paths,
                    trace_id=job.trace_id,
                    attempt=job.attempt + 1,
                    max_attempts=job.max_attempts,
                    source=job.source,
                )
                self._queue.enqueue(
                    retried_job
                )
                return IndexJobProcessOutcome(status='retried', job=retried_job, reason=str(exc))

            reason = '\n'.join(traceback.format_exception_only(type(exc), exc)).strip()
            self._queue.enqueue_dead_letter(
                DeadLetterIndexJob(
                    job=job,
                    reason=reason,
                )
            )
            return IndexJobProcessOutcome(status='dead_lettered', job=job, reason=reason)
