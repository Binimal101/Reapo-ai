from pathlib import Path
from typing import Any

from ast_indexer.adapters.index_store.in_memory_symbol_index_store_adapter import InMemorySymbolIndexStoreAdapter
from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.adapters.queue.in_memory_index_job_queue_adapter import InMemoryIndexJobQueueAdapter
from ast_indexer.adapters.repository.local_fs_repository_reader_adapter import LocalFsRepositoryReaderAdapter
from ast_indexer.application.github_push_payload_resolver import GithubPushPayloadResolver
from ast_indexer.application.index_job_dispatch_service import IndexJobDispatchService
from ast_indexer.application.index_job_worker_service import IndexJobWorkerService
from ast_indexer.application.index_python_repository_service import IndexPythonRepositoryService
from ast_indexer.domain.index_jobs import IndexJob
from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor


def test_resolver_extracts_python_changes_and_deletions() -> None:
    resolver = GithubPushPayloadResolver()
    payload = {
        'repository': {'name': 'checkout-service'},
        'commits': [
            {
                'added': ['src/new_flow.py', 'README.md'],
                'modified': ['src/orders.py'],
                'removed': ['src/old.py', 'docs/guide.md'],
            }
        ],
    }

    delta = resolver.resolve(payload)
    assert delta.repo == 'checkout-service'
    assert delta.repo_full_name == 'checkout-service'
    assert delta.changed_paths == ('src/new_flow.py', 'src/orders.py')
    assert delta.deleted_paths == ('src/old.py',)


def test_dispatch_enqueues_job_from_push_payload() -> None:
    queue = InMemoryIndexJobQueueAdapter()
    observability = InMemoryObservabilityAdapter()
    dispatch = IndexJobDispatchService(queue, observability, GithubPushPayloadResolver())

    payload = {
        'repository': {'name': 'checkout-service'},
        'commits': [{'modified': ['src/orders.py']}],
    }
    job = dispatch.enqueue_from_github_push(payload, trace_id='trace-push-1')

    assert job.repo == 'checkout-service'
    assert job.repo_full_name == 'checkout-service'
    assert job.changed_paths == ('src/orders.py',)
    assert job.deleted_paths == ()
    assert job.max_attempts == 3

    queued = queue.dequeue()
    assert queued is not None
    assert queued.repo == 'checkout-service'

    spans = observability.list_spans()
    assert spans[-1].name == 'enqueue_index_job'
    assert spans[-1].finished_at is not None


def test_dispatch_applies_custom_max_attempts() -> None:
    queue = InMemoryIndexJobQueueAdapter()
    observability = InMemoryObservabilityAdapter()
    dispatch = IndexJobDispatchService(queue, observability, GithubPushPayloadResolver(), max_attempts=5)

    payload = {
        'repository': {'name': 'checkout-service'},
        'commits': [{'modified': ['src/orders.py']}],
    }
    job = dispatch.enqueue_from_github_push(payload, trace_id='trace-push-2')

    assert job.max_attempts == 5


def test_dispatch_enriches_span_with_session_and_user_context() -> None:
    queue = InMemoryIndexJobQueueAdapter()
    observability = InMemoryObservabilityAdapter()
    dispatch = IndexJobDispatchService(queue, observability, GithubPushPayloadResolver())

    payload = {
        'repository': {'name': 'checkout-service'},
        'sender': {'login': 'octocat'},
        'commits': [{'modified': ['src/orders.py']}],
    }
    _ = dispatch.enqueue_from_github_push_with_context(
        payload=payload,
        trace_id='trace-push-ctx-1',
        correlation_id='corr-ctx-1',
    )

    spans = observability.list_spans()
    enqueue_span = spans[-1]
    assert enqueue_span.name == 'enqueue_index_job'
    assert enqueue_span.session_id == 'corr-ctx-1'
    assert enqueue_span.user_id == 'octocat'


def test_worker_processes_enqueued_job(tmp_path: Path) -> None:
    repo_root = tmp_path / 'checkout-service' / 'src'
    repo_root.mkdir(parents=True)
    (repo_root / 'orders.py').write_text(
        'def process(order_id):\n    return order_id\n',
        encoding='utf-8',
    )

    queue = InMemoryIndexJobQueueAdapter()
    queue.enqueue(
        IndexJob(
            repo='checkout-service',
            repo_full_name='checkout-service',
            changed_paths=('src/orders.py',),
            deleted_paths=(),
            trace_id='trace-worker-1',
        )
    )

    observability = InMemoryObservabilityAdapter()
    index_store = InMemorySymbolIndexStoreAdapter()
    index_service = IndexPythonRepositoryService(
        repository_reader=LocalFsRepositoryReaderAdapter(tmp_path),
        index_store=index_store,
        observability=observability,
        extractor=PythonAstSymbolExtractor(),
    )
    worker = IndexJobWorkerService(queue, index_service)

    processed = worker.process_next()
    assert processed.status == 'processed'
    assert processed.job is not None
    assert processed.metrics is not None
    job = processed.job
    metrics = processed.metrics
    assert job.repo == 'checkout-service'
    assert metrics.files_scanned == 1
    assert metrics.symbols_indexed == 1
    assert len(index_store.list_symbols()) == 1


class _AlwaysFailIndexService:
    def index_repository_subset(self, **kwargs: Any) -> None:  # noqa: ARG002
        raise RuntimeError('synthetic failure')


def test_worker_requeues_job_until_max_attempts_then_dead_letters() -> None:
    queue = InMemoryIndexJobQueueAdapter()
    queue.enqueue(
        IndexJob(
            repo='checkout-service',
            repo_full_name='checkout-service',
            changed_paths=('src/orders.py',),
            deleted_paths=(),
            trace_id='trace-worker-fail-1',
            attempt=0,
            max_attempts=2,
        )
    )
    worker = IndexJobWorkerService(queue, _AlwaysFailIndexService())  # type: ignore[arg-type]

    # First failure should requeue with incremented attempt.
    first = worker.process_next()
    assert first.status == 'retried'
    assert first.job is not None
    retried = queue.dequeue()
    assert retried is not None
    assert retried.attempt == 1
    assert retried.max_attempts == 2

    # Second failure should move to dead letter instead of requeueing again.
    queue.enqueue(retried)
    second = worker.process_next()
    assert second.status == 'dead_lettered'
    assert queue.dequeue() is None

    dead_letters = queue.list_dead_letters()
    assert len(dead_letters) == 1
    assert dead_letters[0].job.trace_id == 'trace-worker-fail-1'
    assert 'synthetic failure' in dead_letters[0].reason
