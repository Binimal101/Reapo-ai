import hashlib
import hmac
import json

from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.adapters.queue.in_memory_index_job_queue_adapter import InMemoryIndexJobQueueAdapter
from ast_indexer.adapters.webhooks.hmac_github_signature_verifier_adapter import HmacGithubSignatureVerifierAdapter
from ast_indexer.application.github_push_payload_resolver import GithubPushPayloadResolver
from ast_indexer.application.github_webhook_http_handler import GithubWebhookHttpHandler
from ast_indexer.application.index_job_dispatch_service import IndexJobDispatchService


def _signature(secret: str, body: bytes) -> str:
    return 'sha256=' + hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()


def test_signature_verifier_validates_expected_signature() -> None:
    verifier = HmacGithubSignatureVerifierAdapter('secret-1')
    body = b'{"ok": true}'
    signature = _signature('secret-1', body)

    assert verifier.verify(body, signature) is True
    assert verifier.verify(body, 'sha256=wrong') is False


def test_http_handler_queues_push_event_when_signature_is_valid() -> None:
    queue = InMemoryIndexJobQueueAdapter()
    dispatch = IndexJobDispatchService(
        queue=queue,
        observability=InMemoryObservabilityAdapter(),
        resolver=GithubPushPayloadResolver(),
    )
    handler = GithubWebhookHttpHandler(
        verifier=HmacGithubSignatureVerifierAdapter('secret-2'),
        dispatch=dispatch,
    )

    payload = {
        'repository': {'name': 'checkout-service'},
        'sender': {'login': 'octocat'},
        'commits': [{'modified': ['src/orders.py']}],
    }
    body = json.dumps(payload).encode('utf-8')
    headers = {
        'X-GitHub-Event': 'push',
        'X-GitHub-Delivery': 'delivery-123',
        'X-Hub-Signature-256': _signature('secret-2', body),
    }

    response = handler.handle(headers=headers, body=body)
    assert response.status_code == 202
    assert response.payload['status'] == 'queued'
    assert response.payload['repo'] == 'checkout-service'
    assert response.payload['repo_full_name'] == 'checkout-service'
    assert response.payload['correlation_id'] == 'corr-delivery-123'
    assert response.payload['user_id'] == 'octocat'

    queued = queue.dequeue()
    assert queued is not None
    assert queued.repo == 'checkout-service'
    assert queued.trace_id == 'push-delivery-123'


def test_http_handler_ignores_duplicate_delivery_id_when_replay_guard_is_enabled() -> None:
    class _ReplayGuard:
        def __init__(self) -> None:
            self._seen: set[str] = set()

        def seen_before_then_mark(self, delivery_id: str) -> bool:
            if delivery_id in self._seen:
                return True
            self._seen.add(delivery_id)
            return False

    queue = InMemoryIndexJobQueueAdapter()
    dispatch = IndexJobDispatchService(
        queue=queue,
        observability=InMemoryObservabilityAdapter(),
        resolver=GithubPushPayloadResolver(),
    )
    handler = GithubWebhookHttpHandler(
        verifier=HmacGithubSignatureVerifierAdapter('secret-dup'),
        dispatch=dispatch,
        replay_guard=_ReplayGuard(),
    )

    payload = {
        'repository': {'name': 'checkout-service'},
        'commits': [{'modified': ['src/orders.py']}],
    }
    body = json.dumps(payload).encode('utf-8')
    headers = {
        'X-GitHub-Event': 'push',
        'X-GitHub-Delivery': 'delivery-dup-1',
        'X-Hub-Signature-256': _signature('secret-dup', body),
    }

    first = handler.handle(headers=headers, body=body)
    second = handler.handle(headers=headers, body=body)

    assert first.payload['status'] == 'queued'
    assert second.payload['status'] == 'ignored_duplicate'


def test_http_handler_rejects_invalid_signature() -> None:
    queue = InMemoryIndexJobQueueAdapter()
    dispatch = IndexJobDispatchService(
        queue=queue,
        observability=InMemoryObservabilityAdapter(),
        resolver=GithubPushPayloadResolver(),
    )
    handler = GithubWebhookHttpHandler(
        verifier=HmacGithubSignatureVerifierAdapter('secret-3'),
        dispatch=dispatch,
    )

    body = b'{}'
    response = handler.handle(
        headers={
            'X-GitHub-Event': 'push',
            'X-Hub-Signature-256': 'sha256=bad',
        },
        body=body,
    )

    assert response.status_code == 401
    assert response.payload['reason'] == 'invalid_signature'
    assert response.payload['correlation_id'] == 'corr-github-delivery-missing'
    assert queue.dequeue() is None


def test_http_handler_ignores_non_push_event() -> None:
    queue = InMemoryIndexJobQueueAdapter()
    dispatch = IndexJobDispatchService(
        queue=queue,
        observability=InMemoryObservabilityAdapter(),
        resolver=GithubPushPayloadResolver(),
    )
    handler = GithubWebhookHttpHandler(
        verifier=HmacGithubSignatureVerifierAdapter('secret-4'),
        dispatch=dispatch,
    )

    body = b'{}'
    response = handler.handle(
        headers={
            'X-GitHub-Event': 'issues',
            'X-Hub-Signature-256': _signature('secret-4', body),
            'X-Correlation-ID': 'corr-manual-1',
        },
        body=body,
    )

    assert response.status_code == 202
    assert response.payload['status'] == 'ignored'
    assert response.payload['correlation_id'] == 'corr-manual-1'
    assert queue.dequeue() is None
