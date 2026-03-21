from __future__ import annotations

import argparse
import json
import os
from json import JSONDecodeError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from ast_indexer.adapters.oauth.in_memory_oauth_token_store_adapter import InMemoryOAuthTokenStoreAdapter
from ast_indexer.adapters.oauth.encrypted_file_oauth_token_store_adapter import EncryptedFileOAuthTokenStoreAdapter
from ast_indexer.adapters.queue.in_memory_index_job_queue_adapter import InMemoryIndexJobQueueAdapter
from ast_indexer.adapters.queue.redis_index_job_queue_adapter import RedisIndexJobQueueAdapter
from ast_indexer.adapters.webhooks.hmac_github_signature_verifier_adapter import HmacGithubSignatureVerifierAdapter
from ast_indexer.application.github_app_auth_service import GithubAppAuthService, GithubAppConfig
from ast_indexer.application.github_push_payload_resolver import GithubPushPayloadResolver
from ast_indexer.application.github_webhook_http_handler import GithubWebhookHttpHandler, WebhookHttpResponse
from ast_indexer.application.index_job_dispatch_service import IndexJobDispatchService
from ast_indexer.application.index_job_worker_service import IndexJobWorkerService
from ast_indexer.application.oauth_session_service import OAuthSessionService
from ast_indexer.main import build_persistent_index_service, build_persistent_observability_adapter
from ast_indexer.ports.index_job_queue import IndexJobQueuePort
from ast_indexer.ports.oauth import OAuthTokenStorePort


class GithubWebhookServerApp:
    def __init__(
        self,
        workspace_root: Path,
        state_root: Path,
        webhook_secret: str,
        queue_backend: Literal['memory', 'redis'] = 'memory',
        redis_url: str | None = None,
        redis_key: str = 'ast_indexer:index_jobs',
        redis_dead_letter_key: str = 'ast_indexer:index_jobs:dead_letter',
        max_attempts: int = 3,
        embedding_backend: Literal['hash', 'sentence-transformers', 'openai'] = 'hash',
        embedding_model: str = 'sentence-transformers/all-MiniLM-L6-v2',
        embedding_device: str | None = None,
        normalize_embeddings: bool = True,
        openai_api_key: str | None = None,
        openai_base_url: str | None = None,
        openai_dimensions: int | None = None,
        observability_backend: Literal['jsonl', 'langfuse'] = 'jsonl',
        langfuse_host: str | None = None,
        langfuse_public_key: str | None = None,
        langfuse_secret_key: str | None = None,
        observability_strict: bool = False,
        queue: IndexJobQueuePort | None = None,
        github_app_auth_service: GithubAppAuthService | None = None,
    ) -> None:
        observability = build_persistent_observability_adapter(
            state_root=state_root,
            backend=observability_backend,
            langfuse_host=langfuse_host,
            langfuse_public_key=langfuse_public_key,
            langfuse_secret_key=langfuse_secret_key,
            strict=observability_strict,
        )
        self._observability = observability
        self._queue_backend = queue_backend
        run_queue = queue or _build_queue(
            backend=queue_backend,
            redis_url=redis_url,
            redis_key=redis_key,
            redis_dead_letter_key=redis_dead_letter_key,
        )
        self._queue = run_queue
        self._oauth_token_store = self._build_oauth_token_store(state_root)
        self._oauth_session_service = OAuthSessionService(
            token_store=self._oauth_token_store,
            observability=observability,
        )
        resolver = GithubPushPayloadResolver()
        dispatch = IndexJobDispatchService(
            queue=run_queue,
            observability=observability,
            resolver=resolver,
            max_attempts=max_attempts,
        )
        verifier = HmacGithubSignatureVerifierAdapter(webhook_secret)

        self._http_handler = GithubWebhookHttpHandler(verifier=verifier, dispatch=dispatch)
        self._worker = IndexJobWorkerService(
            queue=run_queue,
            index_service=build_persistent_index_service(
                workspace_root=workspace_root,
                state_root=state_root,
                embedding_backend=embedding_backend,
                embedding_model=embedding_model,
                embedding_device=embedding_device,
                normalize_embeddings=normalize_embeddings,
                openai_api_key=openai_api_key,
                openai_base_url=openai_base_url,
                openai_dimensions=openai_dimensions,
                observability_backend=observability_backend,
                langfuse_host=langfuse_host,
                langfuse_public_key=langfuse_public_key,
                langfuse_secret_key=langfuse_secret_key,
                observability_strict=observability_strict,
            ),
        )
        self._github_app_auth = github_app_auth_service or self._build_github_app_auth_service()

    def _build_oauth_token_store(self, state_root: Path) -> OAuthTokenStorePort:
        token_store_path = os.getenv('AST_INDEXER_OAUTH_TOKEN_STORE_PATH')
        encryption_key = os.getenv('AST_INDEXER_OAUTH_ENCRYPTION_KEY')

        if token_store_path and encryption_key:
            target_path = Path(token_store_path)
            if not target_path.is_absolute():
                target_path = state_root / target_path
            return EncryptedFileOAuthTokenStoreAdapter(target_path, encryption_key)

        return InMemoryOAuthTokenStoreAdapter()

    def _build_github_app_auth_service(self) -> GithubAppAuthService | None:
        config = GithubAppConfig.from_env()
        if config.missing_fields():
            return None
        return GithubAppAuthService(
            config=config,
            oauth_session_service=self._oauth_session_service,
            observability=self._observability,
        )

    def github_auth_status(self) -> tuple[int, dict]:
        config = GithubAppConfig.from_env()
        missing_fields = config.missing_fields()
        configured = len(missing_fields) == 0
        return (
            200,
            {
                'status': 'ok',
                'configured': configured,
                'missing_fields': missing_fields,
                'oauth_tokens_cached': len(self._oauth_token_store._records),  # noqa: SLF001
            },
        )

    def github_auth_start(self, state: str, redirect_uri: str | None) -> tuple[int, dict]:
        if self._github_app_auth is None:
            _, status = self.github_auth_status()
            return (
                503,
                {
                    'status': 'error',
                    'reason': 'github_app_not_configured',
                    'missing_fields': status['missing_fields'],
                },
            )

        return (
            200,
            {
                'status': 'ok',
                'authorize_url': self._github_app_auth.build_oauth_start_url(
                    state=state,
                    redirect_uri=redirect_uri,
                ),
                'state': state,
            },
        )

    def github_auth_callback(self, code: str, state: str | None, redirect_uri: str | None) -> tuple[int, dict]:
        if self._github_app_auth is None:
            _, status = self.github_auth_status()
            return (
                503,
                {
                    'status': 'error',
                    'reason': 'github_app_not_configured',
                    'missing_fields': status['missing_fields'],
                },
            )

        token = self._github_app_auth.exchange_oauth_code(
            trace_id=uuid4().hex,
            code=code,
            state=state,
            redirect_uri=redirect_uri,
        )
        return (
            200,
            {
                'status': 'ok',
                'user_id': token.user_id,
                'expires_at': token.expires_at.isoformat(),
                'scopes': list(token.scopes),
            },
        )

    def github_installation_token(
        self,
        installation_id: int | None = None,
        owner: str | None = None,
        repo: str | None = None,
    ) -> tuple[int, dict]:
        if self._github_app_auth is None:
            _, status = self.github_auth_status()
            return (
                503,
                {
                    'status': 'error',
                    'reason': 'github_app_not_configured',
                    'missing_fields': status['missing_fields'],
                },
            )

        resolved_installation_id = installation_id
        trace_id = uuid4().hex
        if resolved_installation_id is None:
            if not owner or not repo:
                return (
                    400,
                    {
                        'status': 'error',
                        'reason': 'installation_id or owner/repo is required',
                    },
                )
            resolved_installation_id = self._github_app_auth.resolve_installation_id_for_repo(
                trace_id=trace_id,
                owner=owner,
                repo=repo,
            )

        token_payload = self._github_app_auth.create_installation_access_token(
            trace_id=trace_id,
            installation_id=resolved_installation_id,
        )
        return (
            200,
            {
                'status': 'ok',
                'installation_id': resolved_installation_id,
                'expires_at': token_payload.get('expires_at'),
                'token': token_payload.get('token'),
                'permissions': token_payload.get('permissions', {}),
                'repository_selection': token_payload.get('repository_selection'),
            },
        )

    def handle_github_webhook(self, headers: dict[str, str], body: bytes) -> WebhookHttpResponse:
        response = self._http_handler.handle(headers=headers, body=body)
        if response.status_code != 202 or response.payload.get('status') != 'queued':
            return response

        trace_id = str(response.payload.get('trace_id', 'webhook-worker-missing-trace'))
        span = self._observability.start_span(
            name='process_index_job',
            trace_id=trace_id,
            input_payload={
                'repo': response.payload.get('repo'),
                'changed_files': response.payload.get('changed_files'),
                'deleted_files': response.payload.get('deleted_files'),
            },
            session_id=response.payload.get('correlation_id'),
            user_id=response.payload.get('user_id'),
        )
        outcome = self._worker.process_next()

        enriched_payload = dict(response.payload)
        enriched_payload['worker_outcome'] = outcome.status

        if outcome.status == 'processed' and outcome.metrics is not None and outcome.job is not None:
            enriched_payload.update(
                {
                    'processed': True,
                    'files_scanned': outcome.metrics.files_scanned,
                    'symbols_indexed': outcome.metrics.symbols_indexed,
                    'attempt': outcome.job.attempt + 1,
                }
            )
        elif outcome.status == 'retried' and outcome.job is not None:
            enriched_payload.update(
                {
                    'processed': False,
                    'retry_scheduled': True,
                    'attempt': outcome.job.attempt,
                    'max_attempts': outcome.job.max_attempts,
                    'worker_reason': outcome.reason,
                }
            )
        elif outcome.status == 'dead_lettered' and outcome.job is not None:
            enriched_payload.update(
                {
                    'processed': False,
                    'dead_lettered': True,
                    'attempt': outcome.job.attempt + 1,
                    'max_attempts': outcome.job.max_attempts,
                    'worker_reason': outcome.reason,
                }
            )
        else:
            enriched_payload['processed'] = False

        self._observability.end_span(
            span,
            output_payload={
                'worker_outcome': outcome.status,
                'processed': enriched_payload.get('processed', False),
                'retry_scheduled': enriched_payload.get('retry_scheduled', False),
                'dead_lettered': enriched_payload.get('dead_lettered', False),
            },
            metadata={'correlation_id': enriched_payload.get('correlation_id')},
        )
        return WebhookHttpResponse(status_code=response.status_code, payload=enriched_payload)

    def readiness(self) -> tuple[int, dict]:
        checks: dict[str, bool] = {
            'queue': True,
            'observability': True,
        }

        if self._queue_backend == 'redis':
            redis_client = getattr(self._queue, '_client', None)
            if redis_client is not None and hasattr(redis_client, 'ping'):
                try:
                    checks['queue'] = bool(redis_client.ping())
                except Exception:
                    checks['queue'] = False

        check_health = getattr(self._observability, 'check_health', None)
        if callable(check_health):
            checks['observability'] = bool(check_health())

        ready = all(checks.values())
        return (
            200 if ready else 503,
            {
                'status': 'ready' if ready else 'not_ready',
                'checks': checks,
            },
        )


def _build_queue(
    backend: Literal['memory', 'redis'],
    redis_url: str | None,
    redis_key: str,
    redis_dead_letter_key: str,
) -> IndexJobQueuePort:
    if backend == 'memory':
        return InMemoryIndexJobQueueAdapter()

    if not redis_url:
        raise ValueError('redis_url is required when queue_backend=redis')
    return RedisIndexJobQueueAdapter.from_url(
        url=redis_url,
        queue_key=redis_key,
        dead_letter_key=redis_dead_letter_key,
    )


def _make_handler(app: GithubWebhookServerApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == '/auth/github/installation-token':
                content_length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(content_length)
                try:
                    body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                except JSONDecodeError:
                    self._send_json(400, {'status': 'error', 'reason': 'invalid_json'})
                    return
                try:
                    installation_id = body.get('installation_id')
                    owner = body.get('owner')
                    repo = body.get('repo')
                    response_code, payload = app.github_installation_token(
                        installation_id=int(installation_id) if isinstance(installation_id, int | str) and str(installation_id).isdigit() else None,
                        owner=owner if isinstance(owner, str) else None,
                        repo=repo if isinstance(repo, str) else None,
                    )
                    self._send_json(response_code, payload)
                except Exception as exc:
                    self._send_json(500, {'status': 'error', 'reason': str(exc)})
                return

            if parsed.path != '/webhooks/github':
                self._send_json(404, {'status': 'error', 'reason': 'not_found'})
                return

            content_length = int(self.headers.get('Content-Length', '0'))
            body = self.rfile.read(content_length)
            headers = {key: value for key, value in self.headers.items()}
            correlation_id = headers.get('X-Correlation-ID') or headers.get('X-Request-ID') or f'corr-{uuid4().hex}'
            headers['X-Correlation-ID'] = correlation_id
            response = app.handle_github_webhook(headers=headers, body=body)
            payload = dict(response.payload)
            payload.setdefault('correlation_id', correlation_id)
            self._send_json(
                response.status_code,
                payload,
                extra_headers={'X-Correlation-ID': correlation_id},
            )

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == '/healthz':
                self._send_json(200, {'status': 'ok'})
                return
            if parsed.path == '/readyz':
                status_code, payload = app.readiness()
                self._send_json(status_code, payload)
                return
            if parsed.path == '/auth/github/status':
                status_code, payload = app.github_auth_status()
                self._send_json(status_code, payload)
                return
            if parsed.path == '/auth/github/start':
                query = parse_qs(parsed.query)
                state = query.get('state', [f'state-{uuid4().hex}'])[0]
                redirect_uri = query.get('redirect_uri', [None])[0]
                status_code, payload = app.github_auth_start(state=state, redirect_uri=redirect_uri)
                self._send_json(status_code, payload)
                return
            if parsed.path == '/auth/github/callback':
                query = parse_qs(parsed.query)
                code = query.get('code', [None])[0]
                if not isinstance(code, str) or not code.strip():
                    self._send_json(400, {'status': 'error', 'reason': 'missing_code'})
                    return
                state = query.get('state', [None])[0]
                redirect_uri = query.get('redirect_uri', [None])[0]
                try:
                    status_code, payload = app.github_auth_callback(
                        code=code,
                        state=state if isinstance(state, str) else None,
                        redirect_uri=redirect_uri if isinstance(redirect_uri, str) else None,
                    )
                    self._send_json(status_code, payload)
                except Exception as exc:
                    self._send_json(500, {'status': 'error', 'reason': str(exc)})
                return
            self._send_json(404, {'status': 'error', 'reason': 'not_found'})

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

        def _send_json(self, status_code: int, payload: dict, extra_headers: dict[str, str] | None = None) -> None:
            raw = json.dumps(payload).encode('utf-8')
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)

    return Handler


def run_webhook_server(
    workspace_root: Path,
    state_root: Path,
    webhook_secret: str,
    host: str = '127.0.0.1',
    port: int = 8080,
    queue_backend: Literal['memory', 'redis'] = 'memory',
    redis_url: str | None = None,
    redis_key: str = 'ast_indexer:index_jobs',
    redis_dead_letter_key: str = 'ast_indexer:index_jobs:dead_letter',
    max_attempts: int = 3,
    embedding_backend: Literal['hash', 'sentence-transformers', 'openai'] = 'hash',
    embedding_model: str = 'sentence-transformers/all-MiniLM-L6-v2',
    embedding_device: str | None = None,
    normalize_embeddings: bool = True,
    openai_api_key: str | None = None,
    openai_base_url: str | None = None,
    openai_dimensions: int | None = None,
    observability_backend: Literal['jsonl', 'langfuse'] = 'jsonl',
    langfuse_host: str | None = None,
    langfuse_public_key: str | None = None,
    langfuse_secret_key: str | None = None,
    observability_strict: bool = False,
) -> None:
    app = GithubWebhookServerApp(
        workspace_root=workspace_root,
        state_root=state_root,
        webhook_secret=webhook_secret,
        queue_backend=queue_backend,
        redis_url=redis_url,
        redis_key=redis_key,
        redis_dead_letter_key=redis_dead_letter_key,
        max_attempts=max_attempts,
        embedding_backend=embedding_backend,
        embedding_model=embedding_model,
        embedding_device=embedding_device,
        normalize_embeddings=normalize_embeddings,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        openai_dimensions=openai_dimensions,
        observability_backend=observability_backend,
        langfuse_host=langfuse_host,
        langfuse_public_key=langfuse_public_key,
        langfuse_secret_key=langfuse_secret_key,
        observability_strict=observability_strict,
    )
    server = ThreadingHTTPServer((host, port), _make_handler(app))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='ast-indexer-webhook-server')
    parser.add_argument('--workspace-root', type=Path, required=True)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--webhook-secret', type=str, required=True)
    parser.add_argument('--host', type=str, default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--queue-backend', type=str, choices=['memory', 'redis'], default='memory')
    parser.add_argument('--redis-url', type=str, required=False)
    parser.add_argument('--redis-key', type=str, default='ast_indexer:index_jobs')
    parser.add_argument('--redis-dead-letter-key', type=str, default='ast_indexer:index_jobs:dead_letter')
    parser.add_argument('--max-attempts', type=int, default=3)
    parser.add_argument('--embedding-backend', type=str, choices=['hash', 'sentence-transformers', 'openai'], default='hash')
    parser.add_argument('--embedding-model', type=str, default='sentence-transformers/all-MiniLM-L6-v2')
    parser.add_argument('--embedding-device', type=str, required=False)
    parser.add_argument('--openai-api-key', type=str, required=False)
    parser.add_argument('--openai-base-url', type=str, required=False)
    parser.add_argument('--openai-dimensions', type=int, required=False)
    parser.add_argument('--observability-backend', type=str, choices=['jsonl', 'langfuse'], default='jsonl')
    parser.add_argument('--langfuse-host', type=str, required=False)
    parser.add_argument('--langfuse-public-key', type=str, required=False)
    parser.add_argument('--langfuse-secret-key', type=str, required=False)
    parser.add_argument('--observability-strict', action='store_true')
    parser.add_argument('--no-normalize-embeddings', action='store_true')

    args = parser.parse_args(argv)
    run_webhook_server(
        workspace_root=args.workspace_root,
        state_root=args.state_root,
        webhook_secret=args.webhook_secret,
        host=args.host,
        port=args.port,
        queue_backend=args.queue_backend,
        redis_url=args.redis_url,
        redis_key=args.redis_key,
        redis_dead_letter_key=args.redis_dead_letter_key,
        max_attempts=args.max_attempts,
        embedding_backend=args.embedding_backend,
        embedding_model=args.embedding_model,
        embedding_device=args.embedding_device,
        normalize_embeddings=not args.no_normalize_embeddings,
        openai_api_key=args.openai_api_key,
        openai_base_url=args.openai_base_url,
        openai_dimensions=args.openai_dimensions,
        observability_backend=args.observability_backend,
        langfuse_host=args.langfuse_host,
        langfuse_public_key=args.langfuse_public_key,
        langfuse_secret_key=args.langfuse_secret_key,
        observability_strict=args.observability_strict,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
