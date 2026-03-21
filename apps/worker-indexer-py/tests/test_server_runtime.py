import hashlib
import hmac
import json
from pathlib import Path

import pytest

from ast_indexer.adapters.queue.in_memory_index_job_queue_adapter import InMemoryIndexJobQueueAdapter
from ast_indexer.application.index_job_worker_service import IndexJobProcessOutcome
from ast_indexer.application.writer_pr_service import WriterFileChange
from ast_indexer.domain.index_jobs import IndexJob
from ast_indexer.server import GithubWebhookServerApp


def _signature(secret: str, body: bytes) -> str:
    return 'sha256=' + hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()


def test_server_app_handles_valid_push_and_processes_job(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    repo_root = workspace_root / 'checkout-service' / 'src'
    repo_root.mkdir(parents=True)
    (repo_root / 'orders.py').write_text('def process(order_id):\n    return order_id\n', encoding='utf-8')

    state_root = tmp_path / 'state'
    secret = 'server-secret'
    app = GithubWebhookServerApp(workspace_root=workspace_root, state_root=state_root, webhook_secret=secret)

    payload = {
        'repository': {'name': 'checkout-service'},
        'commits': [{'modified': ['src/orders.py']}],
    }
    body = json.dumps(payload).encode('utf-8')
    response = app.handle_github_webhook(
        headers={
            'X-GitHub-Event': 'push',
            'X-GitHub-Delivery': 'server-test-1',
            'X-Hub-Signature-256': _signature(secret, body),
        },
        body=body,
    )

    assert response.status_code == 202
    assert response.payload['status'] == 'queued'
    assert response.payload['processed'] is True
    assert response.payload['worker_outcome'] == 'processed'
    assert response.payload['files_scanned'] == 1
    assert response.payload['symbols_indexed'] == 1
    assert response.payload['correlation_id'] == 'corr-server-test-1'


def test_server_app_rejects_invalid_signature(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'checkout-service').mkdir(parents=True)

    state_root = tmp_path / 'state'
    app = GithubWebhookServerApp(workspace_root=workspace_root, state_root=state_root, webhook_secret='server-secret')

    body = b'{}'
    response = app.handle_github_webhook(
        headers={
            'X-GitHub-Event': 'push',
            'X-Hub-Signature-256': 'sha256=invalid',
        },
        body=body,
    )

    assert response.status_code == 401
    assert response.payload['reason'] == 'invalid_signature'


def test_server_app_requires_redis_url_for_redis_backend(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'checkout-service').mkdir(parents=True)

    with pytest.raises(ValueError, match='redis_url is required'):
        GithubWebhookServerApp(
            workspace_root=workspace_root,
            state_root=tmp_path / 'state',
            webhook_secret='server-secret',
            queue_backend='redis',
        )


def test_server_app_uses_redis_queue_factory_when_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_root = tmp_path / 'workspace'
    repo_root = workspace_root / 'checkout-service' / 'src'
    repo_root.mkdir(parents=True)
    (repo_root / 'orders.py').write_text('def process(order_id):\n    return order_id\n', encoding='utf-8')

    called: dict[str, str] = {}
    queue = InMemoryIndexJobQueueAdapter()

    def _fake_from_url(
        cls: object,
        url: str,
        queue_key: str = 'ast_indexer:index_jobs',
        dead_letter_key: str = 'ast_indexer:index_jobs:dead_letter',
    ) -> InMemoryIndexJobQueueAdapter:
        called['url'] = url
        called['queue_key'] = queue_key
        called['dead_letter_key'] = dead_letter_key
        return queue

    monkeypatch.setattr(
        'ast_indexer.server.RedisIndexJobQueueAdapter.from_url',
        classmethod(_fake_from_url),
    )

    app = GithubWebhookServerApp(
        workspace_root=workspace_root,
        state_root=tmp_path / 'state',
        webhook_secret='server-secret',
        queue_backend='redis',
        redis_url='redis://localhost:6379/0',
        redis_key='index:jobs:test',
    )

    payload = {
        'repository': {'name': 'checkout-service'},
        'commits': [{'modified': ['src/orders.py']}],
    }
    body = json.dumps(payload).encode('utf-8')
    response = app.handle_github_webhook(
        headers={
            'X-GitHub-Event': 'push',
            'X-GitHub-Delivery': 'server-test-redis-1',
            'X-Hub-Signature-256': _signature('server-secret', body),
        },
        body=body,
    )

    assert called['url'] == 'redis://localhost:6379/0'
    assert called['queue_key'] == 'index:jobs:test'
    assert called['dead_letter_key'] == 'ast_indexer:index_jobs:dead_letter'
    assert response.status_code == 202
    assert response.payload['processed'] is True


def test_server_app_exposes_retry_outcome_and_logs_span(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'checkout-service').mkdir(parents=True)
    state_root = tmp_path / 'state'
    secret = 'server-secret'
    app = GithubWebhookServerApp(workspace_root=workspace_root, state_root=state_root, webhook_secret=secret)

    class _FakeRetryWorker:
        def process_next(self) -> IndexJobProcessOutcome:
            return IndexJobProcessOutcome(
                status='retried',
                job=IndexJob(
                    repo='checkout-service',
                    repo_full_name='checkout-service',
                    changed_paths=('src/orders.py',),
                    deleted_paths=(),
                    trace_id='push-server-test-retry',
                    attempt=1,
                    max_attempts=3,
                ),
                reason='RuntimeError: synthetic failure',
            )

    app._worker = _FakeRetryWorker()  # type: ignore[assignment]
    payload = {
        'repository': {'name': 'checkout-service'},
        'commits': [{'modified': ['src/orders.py']}],
    }
    body = json.dumps(payload).encode('utf-8')

    response = app.handle_github_webhook(
        headers={
            'X-GitHub-Event': 'push',
            'X-GitHub-Delivery': 'server-test-retry',
            'X-Hub-Signature-256': _signature(secret, body),
        },
        body=body,
    )

    assert response.status_code == 202
    assert response.payload['worker_outcome'] == 'retried'
    assert response.payload['retry_scheduled'] is True
    assert response.payload['processed'] is False

    spans_file = state_root / 'observability' / 'spans.jsonl'
    rows = [json.loads(line) for line in spans_file.read_text(encoding='utf-8').splitlines()]
    process_spans = [row for row in rows if row.get('name') == 'process_index_job']
    assert len(process_spans) == 1
    assert process_spans[0]['output_payload']['worker_outcome'] == 'retried'


def test_server_app_exposes_dead_letter_outcome(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'checkout-service').mkdir(parents=True)
    state_root = tmp_path / 'state'
    secret = 'server-secret'
    app = GithubWebhookServerApp(workspace_root=workspace_root, state_root=state_root, webhook_secret=secret)

    class _FakeDeadLetterWorker:
        def process_next(self) -> IndexJobProcessOutcome:
            return IndexJobProcessOutcome(
                status='dead_lettered',
                job=IndexJob(
                    repo='checkout-service',
                    repo_full_name='checkout-service',
                    changed_paths=('src/orders.py',),
                    deleted_paths=(),
                    trace_id='push-server-test-dead',
                    attempt=2,
                    max_attempts=3,
                ),
                reason='RuntimeError: terminal failure',
            )

    app._worker = _FakeDeadLetterWorker()  # type: ignore[assignment]
    payload = {
        'repository': {'name': 'checkout-service'},
        'commits': [{'modified': ['src/orders.py']}],
    }
    body = json.dumps(payload).encode('utf-8')

    response = app.handle_github_webhook(
        headers={
            'X-GitHub-Event': 'push',
            'X-GitHub-Delivery': 'server-test-dead',
            'X-Hub-Signature-256': _signature(secret, body),
        },
        body=body,
    )

    assert response.status_code == 202
    assert response.payload['worker_outcome'] == 'dead_lettered'
    assert response.payload['dead_lettered'] is True
    assert response.payload['processed'] is False


def test_server_app_readiness_reports_ready_for_memory_backend(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'checkout-service').mkdir(parents=True)
    app = GithubWebhookServerApp(
        workspace_root=workspace_root,
        state_root=tmp_path / 'state',
        webhook_secret='server-secret',
    )

    status_code, payload = app.readiness()
    assert status_code == 200
    assert payload['status'] == 'ready'
    assert payload['checks']['queue'] is True
    assert payload['checks']['observability'] is True


def test_server_app_readiness_reports_not_ready_when_redis_ping_fails(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'checkout-service').mkdir(parents=True)

    class _DownRedisClient:
        def ping(self) -> bool:
            raise RuntimeError('redis unavailable')

    class _QueueWithRedisClient:
        def __init__(self) -> None:
            self._client = _DownRedisClient()

        def enqueue(self, job: object) -> None:  # noqa: ARG002
            return

        def dequeue(self) -> None:
            return None

        def enqueue_dead_letter(self, entry: object) -> None:  # noqa: ARG002
            return

    app = GithubWebhookServerApp(
        workspace_root=workspace_root,
        state_root=tmp_path / 'state',
        webhook_secret='server-secret',
        queue_backend='redis',
        redis_url='redis://localhost:6379/0',
        queue=_QueueWithRedisClient(),  # type: ignore[arg-type]
    )

    status_code, payload = app.readiness()
    assert status_code == 503
    assert payload['status'] == 'not_ready'
    assert payload['checks']['queue'] is False


def test_server_app_issues_and_validates_session_bearer_token(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'checkout-service').mkdir(parents=True)
    app = GithubWebhookServerApp(
        workspace_root=workspace_root,
        state_root=tmp_path / 'state',
        webhook_secret='server-secret',
    )

    token = app._issue_session_token(user_id='alice', provider='github')
    status_code, payload = app.authenticate_bearer_token(token)

    assert status_code == 200
    assert payload['status'] == 'ok'
    assert payload['user_id'] == 'alice'
    assert payload['provider'] == 'github'


def test_server_app_rejects_invalid_session_bearer_token(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'checkout-service').mkdir(parents=True)
    app = GithubWebhookServerApp(
        workspace_root=workspace_root,
        state_root=tmp_path / 'state',
        webhook_secret='server-secret',
    )

    status_code, payload = app.authenticate_bearer_token('invalid.token')
    assert status_code == 401
    assert payload['reason'] == 'invalid_or_expired_token'


def test_server_app_oauth_signup_start_rejects_unknown_provider(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'checkout-service').mkdir(parents=True)
    app = GithubWebhookServerApp(
        workspace_root=workspace_root,
        state_root=tmp_path / 'state',
        webhook_secret='server-secret',
    )

    status_code, payload = app.oauth_signup_start(provider='google', state='state-1', redirect_uri=None)
    assert status_code == 400
    assert payload['reason'] == 'unsupported_provider'


def test_chat_session_access_is_scoped_to_authenticated_user(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'checkout-service').mkdir(parents=True)
    app = GithubWebhookServerApp(
        workspace_root=workspace_root,
        state_root=tmp_path / 'state',
        webhook_secret='server-secret',
    )

    create_code, create_payload = app.chat_create_session(user_id='owner')
    assert create_code == 200
    session_id = str(create_payload['session']['session_id'])

    owner_code, _ = app.chat_get_session(session_id, requesting_user_id='owner')
    assert owner_code == 200

    intruder_code, intruder_payload = app.chat_get_session(session_id, requesting_user_id='intruder')
    assert intruder_code == 403
    assert intruder_payload['reason'] == 'session_access_denied'


def test_server_app_writer_open_pr_uses_writer_service(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'checkout-service').mkdir(parents=True)
    app = GithubWebhookServerApp(
        workspace_root=workspace_root,
        state_root=tmp_path / 'state',
        webhook_secret='server-secret',
    )

    class _FakeWriterService:
        def open_pull_request(self, **kwargs: object) -> dict:
            return {
                'status': 'ok',
                'mode': 'dry_run' if bool(kwargs.get('dry_run')) else 'applied',
                'owner': kwargs.get('owner'),
                'repo': kwargs.get('repo'),
                'files_changed': 1,
                'pull_request': {'number': 9, 'html_url': 'https://example/pull/9', 'reused': False},
            }

    app._writer_service = _FakeWriterService()  # type: ignore[assignment]
    code, payload = app.writer_open_pr(
        requesting_user_id='alice',
        owner='acme',
        repo='checkout',
        base_branch='main',
        title='Fix checkout',
        body='Body',
        files=[WriterFileChange(path='src/checkout.py', content='print(1)')],
        branch_name='reapo-ai/fix-checkout',
        commit_message='fix: checkout',
        draft=False,
        dry_run=True,
    )

    assert code == 200
    assert payload['status'] == 'ok'
    assert payload['mode'] == 'dry_run'


def test_server_app_writer_open_pr_handles_permission_error(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'checkout-service').mkdir(parents=True)
    app = GithubWebhookServerApp(
        workspace_root=workspace_root,
        state_root=tmp_path / 'state',
        webhook_secret='server-secret',
    )

    class _DenyWriterService:
        def open_pull_request(self, **kwargs: object) -> dict:  # noqa: ARG002
            raise PermissionError('insufficient_repo_permission')

    app._writer_service = _DenyWriterService()  # type: ignore[assignment]
    code, payload = app.writer_open_pr(
        requesting_user_id='alice',
        owner='acme',
        repo='checkout',
        base_branch='main',
        title='Fix checkout',
        body='Body',
        files=[WriterFileChange(path='src/checkout.py', content='print(1)')],
        branch_name='reapo-ai/fix-checkout',
        commit_message='fix: checkout',
        draft=False,
        dry_run=False,
    )

    assert code == 403
    assert payload['status'] == 'error'
