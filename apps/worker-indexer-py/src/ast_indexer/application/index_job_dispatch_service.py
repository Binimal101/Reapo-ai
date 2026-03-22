from __future__ import annotations

from ast_indexer.application.github_push_payload_resolver import GithubPushPayloadResolver
from ast_indexer.domain.index_jobs import IndexJob
from ast_indexer.ports.index_job_queue import IndexJobQueuePort
from ast_indexer.ports.observability import ObservabilityPort


class IndexJobDispatchService:
    def __init__(
        self,
        queue: IndexJobQueuePort,
        observability: ObservabilityPort,
        resolver: GithubPushPayloadResolver,
        max_attempts: int = 3,
    ) -> None:
        self._queue = queue
        self._observability = observability
        self._resolver = resolver
        self._max_attempts = max_attempts

    def enqueue_from_github_push(self, payload: dict, trace_id: str) -> IndexJob:
        return self.enqueue_from_github_push_with_context(payload=payload, trace_id=trace_id, correlation_id=None)

    def enqueue_repository_full_index(
        self,
        *,
        owner: str,
        name: str,
        trace_id: str,
        correlation_id: str | None = None,
        user_id: str | None = None,
    ) -> IndexJob:
        job = self.build_repository_full_index_job(
            owner=owner,
            name=name,
            trace_id=trace_id,
        )

        repo_full_name = job.repo
        span = self._observability.start_span(
            name='enqueue_index_job',
            trace_id=trace_id,
            input_payload={
                'event': 'project_repository_linked',
                'repo_full_name': repo_full_name,
                'correlation_id': correlation_id,
            },
            session_id=correlation_id,
            user_id=user_id,
        )

        self._queue.enqueue(job)

        self._observability.end_span(
            span,
            output_payload={
                'repo': job.repo,
                'repo_full_name': job.repo_full_name,
                'changed_files': 0,
                'deleted_files': 0,
                'correlation_id': correlation_id,
                'user_id': user_id,
            },
        )
        return job

    def build_repository_full_index_job(
        self,
        *,
        owner: str,
        name: str,
        trace_id: str,
    ) -> IndexJob:
        owner_clean = owner.strip()
        name_clean = name.strip()
        if not owner_clean or not name_clean:
            raise ValueError('owner and name are required')

        repo_full_name = f'{owner_clean}/{name_clean}'
        return IndexJob(
            repo=repo_full_name,
            repo_full_name=repo_full_name,
            changed_paths=(),
            deleted_paths=(),
            trace_id=trace_id,
            max_attempts=self._max_attempts,
            source='project_repository_linked',
        )

    def enqueue_from_github_push_with_context(
        self,
        payload: dict,
        trace_id: str,
        correlation_id: str | None,
    ) -> IndexJob:
        user_id = self._resolve_user_id(payload)
        span = self._observability.start_span(
            name='enqueue_index_job',
            trace_id=trace_id,
            input_payload={
                'event': 'github_push',
                'correlation_id': correlation_id,
            },
            session_id=correlation_id,
            user_id=user_id,
        )

        delta = self._resolver.resolve(payload)
        job = IndexJob(
            repo=delta.repo,
            repo_full_name=delta.repo_full_name,
            changed_paths=delta.changed_paths,
            deleted_paths=delta.deleted_paths,
            trace_id=trace_id,
            max_attempts=self._max_attempts,
        )
        self._queue.enqueue(job)

        self._observability.end_span(
            span,
            output_payload={
                'repo': job.repo,
                'repo_full_name': job.repo_full_name,
                'changed_files': len(job.changed_paths),
                'deleted_files': len(job.deleted_paths),
                'correlation_id': correlation_id,
                'user_id': user_id,
            },
        )
        return job

    def _resolve_user_id(self, payload: dict) -> str | None:
        sender = payload.get('sender') if isinstance(payload, dict) else None
        if isinstance(sender, dict):
            sender_login = sender.get('login')
            if isinstance(sender_login, str) and sender_login.strip():
                return sender_login

        repository = payload.get('repository') if isinstance(payload, dict) else None
        owner = repository.get('owner') if isinstance(repository, dict) else None
        if isinstance(owner, dict):
            for key in ('login', 'name'):
                value = owner.get(key)
                if isinstance(value, str) and value.strip():
                    return value

        return None
