from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from json import JSONDecodeError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from ast_indexer.adapters.oauth.encrypted_file_oauth_token_store_adapter import EncryptedFileOAuthTokenStoreAdapter
from ast_indexer.adapters.oauth.sqlite_multitenant_oauth_store_adapter import SqliteMultiTenantOAuthStoreAdapter
from ast_indexer.adapters.orchestrator.json_file_orchestrator_state_store_adapter import (
    JsonFileOrchestratorStateStoreAdapter,
)
from ast_indexer.adapters.index_store.json_file_symbol_index_store_adapter import JsonFileSymbolIndexStoreAdapter
from ast_indexer.adapters.access.json_file_repo_capability_store_adapter import JsonFileRepoCapabilityStoreAdapter
from ast_indexer.adapters.queue.in_memory_index_job_queue_adapter import InMemoryIndexJobQueueAdapter
from ast_indexer.adapters.queue.redis_index_job_queue_adapter import RedisIndexJobQueueAdapter
from ast_indexer.adapters.webhooks.hmac_github_signature_verifier_adapter import HmacGithubSignatureVerifierAdapter
from ast_indexer.adapters.webhooks.json_file_webhook_replay_guard_adapter import JsonFileWebhookReplayGuardAdapter
from ast_indexer.application.chat_orchestrator_service import ChatOrchestratorService
from ast_indexer.application.github_app_auth_service import GithubAppAuthService, GithubAppConfig
from ast_indexer.application.github_push_payload_resolver import GithubPushPayloadResolver
from ast_indexer.application.github_webhook_http_handler import GithubWebhookHttpHandler, WebhookHttpResponse
from ast_indexer.application.index_job_dispatch_service import IndexJobDispatchService
from ast_indexer.application.index_job_worker_service import IndexJobWorkerService
from ast_indexer.application.orchestrator_loop_service import GrepRepoMatch, GrepRepoResult, OrchestratorLoopService
from ast_indexer.application.oauth_session_service import OAuthSessionService
from ast_indexer.application.writer_pr_service import WriterFileChange, WriterPrService
from ast_indexer.main import (
    build_persistent_index_service,
    build_persistent_observability_adapter,
    build_persistent_research_pipeline,
)
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
        chat_orchestrator_service: ChatOrchestratorService | None = None,
    ) -> None:
        self._session_secret = os.getenv('AST_INDEXER_SESSION_SECRET', webhook_secret)
        self._session_ttl_seconds = max(300, int(os.getenv('AST_INDEXER_SESSION_TTL_SECONDS', '604800')))
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
        self._repo_capability_store = JsonFileRepoCapabilityStoreAdapter(state_root / 'auth' / 'repo_capabilities.json')
        self._webhook_replay_guard = JsonFileWebhookReplayGuardAdapter(state_root / 'webhooks' / 'delivery_ids.json')
        resolver = GithubPushPayloadResolver()
        dispatch = IndexJobDispatchService(
            queue=run_queue,
            observability=observability,
            resolver=resolver,
            max_attempts=max_attempts,
        )
        verifier = HmacGithubSignatureVerifierAdapter(webhook_secret)

        self._http_handler = GithubWebhookHttpHandler(
            verifier=verifier,
            dispatch=dispatch,
            replay_guard=self._webhook_replay_guard,
        )
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
        self._research_pipeline = build_persistent_research_pipeline(
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
        )
        self._github_app_auth = github_app_auth_service or self._build_github_app_auth_service()
        self._writer_service = WriterPrService(github_auth=self._github_app_auth) if self._github_app_auth else None
        self._chat_orchestrator = chat_orchestrator_service or self._build_chat_orchestrator_service(state_root)

    def _build_chat_orchestrator_service(self, state_root: Path) -> ChatOrchestratorService:
        store = JsonFileOrchestratorStateStoreAdapter(state_root / 'orchestrator' / 'chat_state.json')
        symbol_index_store = JsonFileSymbolIndexStoreAdapter(state_root / 'index' / 'symbols.json')

        def _search_tool(
            *,
            trace_id: str,
            prompt: str,
            repos_in_scope: tuple[str, ...],
            top_k: int,
            candidate_pool_multiplier: int,
            relevancy_threshold: float,
            relevancy_workers: int,
            reducer_token_budget: int,
            reducer_max_contexts: int | None,
        ):
            return self._research_pipeline.run(
                trace_id=trace_id,
                prompt=prompt,
                repos_in_scope=repos_in_scope,
                top_k=top_k,
                candidate_pool_multiplier=candidate_pool_multiplier,
                relevancy_threshold=relevancy_threshold,
                relevancy_workers=relevancy_workers,
                reducer_token_budget=reducer_token_budget,
                reducer_max_contexts=reducer_max_contexts,
            )

        def _truncate_signature(signature: str, max_chars: int) -> str:
            if max_chars <= 0:
                return ''
            if len(signature) <= max_chars:
                return signature
            if max_chars <= 3:
                return signature[:max_chars]
            return signature[: max_chars - 3] + '...'

        def _grep_repo_tool(
            *,
            query: str,
            repos_in_scope: tuple[str, ...],
            page: int = 1,
            page_size: int = 10,
            signature_max_chars: int = 120,
        ) -> GrepRepoResult:
            # Pagination is 1-based. has_more indicates if subsequent pages are available.
            page_clean = max(1, int(page))
            page_size_clean = max(1, min(100, int(page_size)))
            max_chars_clean = max(8, min(500, int(signature_max_chars)))

            symbols = symbol_index_store.list_symbols()
            query_lower = query.strip().lower()
            filtered = []
            for symbol in symbols:
                if repos_in_scope and symbol.repo not in repos_in_scope:
                    continue
                haystack = f'{symbol.path} {symbol.symbol} {symbol.signature}'.lower()
                if query_lower and query_lower not in haystack:
                    continue
                filtered.append(symbol)

            filtered.sort(key=lambda item: (item.repo, item.path, item.line, item.symbol))
            total = len(filtered)
            start = (page_clean - 1) * page_size_clean
            end = start + page_size_clean
            page_slice = filtered[start:end]
            matches: list[GrepRepoMatch] = [
                {
                    'repo': item.repo,
                    'path': item.path,
                    'symbol': item.symbol,
                    'kind': item.kind,
                    'line': item.line,
                    'signature': _truncate_signature(item.signature, max_chars_clean),
                }
                for item in page_slice
            ]

            payload: GrepRepoResult = {
                'query': query,
                'page': page_clean,
                'page_size': page_size_clean,
                'total_matches': total,
                'has_more': end < total,
                'matches': matches,
            }
            return payload

        orchestrator = OrchestratorLoopService(
            observability=self._observability,
            search_tool=_search_tool,
            grep_repo_tool=_grep_repo_tool,
            memory_threshold_messages=20,
            max_tool_iterations=5,
        )
        return ChatOrchestratorService(state_store=store, orchestrator=orchestrator)

    def chat_create_session(self, user_id: str) -> tuple[int, dict]:
        try:
            session = self._chat_orchestrator.create_session(user_id=user_id)
        except ValueError as exc:
            return (400, {'status': 'error', 'reason': str(exc)})
        return (200, {'status': 'ok', 'session': session})

    def chat_get_session(self, session_id: str, requesting_user_id: str | None = None) -> tuple[int, dict]:
        session = self._chat_orchestrator.get_session(session_id=session_id)
        if session is None:
            return (404, {'status': 'error', 'reason': 'session_not_found', 'session_id': session_id})
        if requesting_user_id is not None and str(session.get('user_id', '')) != requesting_user_id:
            return (403, {'status': 'error', 'reason': 'session_access_denied'})
        return (200, {'status': 'ok', 'session': session})

    def chat_get_run(self, run_id: str, requesting_user_id: str | None = None) -> tuple[int, dict]:
        run = self._chat_orchestrator.get_run(run_id=run_id)
        if run is None:
            return (404, {'status': 'error', 'reason': 'run_not_found', 'run_id': run_id})
        if requesting_user_id is not None and str(run.get('user_id', '')) != requesting_user_id:
            return (403, {'status': 'error', 'reason': 'run_access_denied'})
        return (200, {'status': 'ok', 'run': run})

    def chat_send_message(
        self,
        *,
        session_id: str,
        user_id: str,
        message: str,
        repos_in_scope: tuple[str, ...] = (),
        top_k: int = 8,
        candidate_pool_multiplier: int = 6,
        relevancy_threshold: float = 0.35,
        relevancy_workers: int = 6,
        reducer_token_budget: int = 2500,
        reducer_max_contexts: int | None = None,
    ) -> tuple[int, dict]:
        try:
            payload = self._chat_orchestrator.send_message(
                session_id=session_id,
                user_id=user_id,
                message=message,
                repos_in_scope=repos_in_scope,
                top_k=top_k,
                candidate_pool_multiplier=candidate_pool_multiplier,
                relevancy_threshold=relevancy_threshold,
                relevancy_workers=relevancy_workers,
                reducer_token_budget=reducer_token_budget,
                reducer_max_contexts=reducer_max_contexts,
            )
        except KeyError as exc:
            return (404, {'status': 'error', 'reason': str(exc)})
        except PermissionError as exc:
            return (403, {'status': 'error', 'reason': str(exc)})
        except ValueError as exc:
            return (400, {'status': 'error', 'reason': str(exc)})
        except Exception as exc:  # noqa: BLE001
            return (500, {'status': 'error', 'reason': str(exc)})
        return (200, {'status': 'ok', **payload})

    def observability_trace_stack(self, *, trace_id: str | None = None, limit: int = 80) -> tuple[int, dict]:
        spans = self._observability.list_spans()
        if not spans:
            return (
                200,
                {
                    'status': 'ok',
                    'trace_id': trace_id,
                    'max_depth': 0,
                    'active_depth': 0,
                    'spans': [],
                    'events': [],
                },
            )

        selected_trace_id = trace_id
        if not selected_trace_id:
            selected_trace_id = spans[-1].trace_id

        trace_spans = [span for span in spans if span.trace_id == selected_trace_id]
        if not trace_spans:
            return (
                404,
                {
                    'status': 'error',
                    'reason': 'trace_not_found',
                    'trace_id': selected_trace_id,
                },
            )

        events: list[tuple[datetime, str, str]] = []
        open_span_ids: set[str] = set()
        for span in trace_spans:
            events.append((span.started_at, 'start', span.span_id))
            if span.finished_at is not None:
                events.append((span.finished_at, 'end', span.span_id))
            else:
                open_span_ids.add(span.span_id)

        events.sort(key=lambda item: (item[0], 0 if item[1] == 'start' else 1))

        current_depth = 0
        max_depth = 0
        depth_by_span: dict[str, int] = {}
        event_rows: list[dict] = []
        for timestamp, kind, span_id in events:
            if kind == 'start':
                current_depth += 1
                depth_by_span[span_id] = current_depth
                if current_depth > max_depth:
                    max_depth = current_depth
                event_rows.append(
                    {
                        'at': timestamp.isoformat(),
                        'kind': 'start',
                        'span_id': span_id,
                        'depth': current_depth,
                    }
                )
            else:
                depth = depth_by_span.get(span_id, current_depth)
                event_rows.append(
                    {
                        'at': timestamp.isoformat(),
                        'kind': 'end',
                        'span_id': span_id,
                        'depth': max(1, depth),
                    }
                )
                current_depth = max(0, current_depth - 1)

        span_rows = []
        for span in trace_spans[-max(1, min(200, int(limit))) :]:
            duration_ms = None
            if span.finished_at is not None:
                duration_ms = int((span.finished_at - span.started_at).total_seconds() * 1000)
            span_rows.append(
                {
                    'span_id': span.span_id,
                    'name': span.name,
                    'started_at': span.started_at.isoformat(),
                    'finished_at': span.finished_at.isoformat() if span.finished_at is not None else None,
                    'duration_ms': duration_ms,
                    'session_id': span.session_id,
                    'user_id': span.user_id,
                }
            )

        return (
            200,
            {
                'status': 'ok',
                'trace_id': selected_trace_id,
                'max_depth': max_depth,
                'active_depth': len(open_span_ids),
                'span_count': len(trace_spans),
                'spans': span_rows,
                'events': event_rows[-200:],
            },
        )

    def observability_write_event(
        self,
        *,
        user_id: str,
        name: str,
        trace_id: str | None,
        input_payload: dict | None,
        output_payload: dict | None,
    ) -> tuple[int, dict]:
        cleaned_name = name.strip()
        if not cleaned_name:
            return (400, {'status': 'error', 'reason': 'name is required'})

        resolved_trace_id = (trace_id or '').strip() or uuid4().hex
        span = self._observability.start_span(
            name=cleaned_name,
            trace_id=resolved_trace_id,
            input_payload=input_payload,
            user_id=user_id,
        )
        self._observability.end_span(
            span,
            output_payload=output_payload,
            metadata={
                'source': 'frontend_observability_event',
                'received_at': datetime.now(timezone.utc).isoformat(),
            },
        )
        return (
            200,
            {
                'status': 'ok',
                'trace_id': resolved_trace_id,
                'span_id': span.span_id,
                'name': cleaned_name,
            },
        )

    def _build_oauth_token_store(self, state_root: Path) -> OAuthTokenStorePort:
        token_store_path = os.getenv('AST_INDEXER_OAUTH_TOKEN_STORE_PATH')
        encryption_key = os.getenv('AST_INDEXER_OAUTH_ENCRYPTION_KEY')
        sqlite_path = os.getenv('AST_INDEXER_OAUTH_SQLITE_PATH', 'auth/multitenant_auth.db')

        if token_store_path and encryption_key:
            target_path = Path(token_store_path)
            if not target_path.is_absolute():
                target_path = state_root / target_path
            return EncryptedFileOAuthTokenStoreAdapter(target_path, encryption_key)

        sqlite_target = Path(sqlite_path)
        if not sqlite_target.is_absolute():
            sqlite_target = state_root / sqlite_target
        return SqliteMultiTenantOAuthStoreAdapter(sqlite_target)

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
                'oauth_tokens_cached': len(self._oauth_token_store.list_user_ids()),
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

    def oauth_signup_start(self, *, provider: str, state: str, redirect_uri: str | None) -> tuple[int, dict]:
        return self._oauth_flow_start(flow='signup', provider=provider, state=state, redirect_uri=redirect_uri)

    def oauth_signin_start(self, *, provider: str, state: str, redirect_uri: str | None) -> tuple[int, dict]:
        return self._oauth_flow_start(flow='signin', provider=provider, state=state, redirect_uri=redirect_uri)

    def _oauth_flow_start(self, *, flow: str, provider: str, state: str, redirect_uri: str | None) -> tuple[int, dict]:
        if provider != 'github':
            return (400, {'status': 'error', 'reason': 'unsupported_provider', 'provider': provider})

        status_code, payload = self.github_auth_start(state=state, redirect_uri=redirect_uri)
        if status_code != 200:
            return status_code, payload
        return (
            200,
            {
                'status': 'ok',
                'flow': flow,
                'provider': provider,
                'authorize_url': payload.get('authorize_url'),
                'state': payload.get('state'),
            },
        )

    def oauth_callback(
        self,
        *,
        flow: str,
        provider: str,
        code: str,
        state: str | None,
        redirect_uri: str | None,
    ) -> tuple[int, dict]:
        if provider != 'github':
            return (400, {'status': 'error', 'reason': 'unsupported_provider', 'provider': provider})
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
        session_token = self._issue_session_token(user_id=token.user_id, provider=provider)
        return (
            200,
            {
                'status': 'ok',
                'flow': flow,
                'provider': provider,
                'token_type': 'Bearer',
                'session_token': session_token,
                'expires_in': self._session_ttl_seconds,
                'user': {
                    'user_id': token.user_id,
                    'scopes': list(token.scopes),
                    'oauth_expires_at': token.expires_at.isoformat(),
                },
            },
        )

    def authenticate_bearer_token(self, token: str) -> tuple[int, dict]:
        payload = self._verify_session_token(token)
        if payload is None:
            return (401, {'status': 'error', 'reason': 'invalid_or_expired_token'})
        return (
            200,
            {
                'status': 'ok',
                'user_id': payload.get('sub'),
                'provider': payload.get('provider'),
                'exp': payload.get('exp'),
            },
        )

    def _issue_session_token(self, *, user_id: str, provider: str) -> str:
        now = int(time.time())
        payload = {
            'sub': user_id,
            'provider': provider,
            'iat': now,
            'exp': now + self._session_ttl_seconds,
        }
        payload_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        payload_encoded = self._b64url_encode(payload_bytes)
        signature = hmac.new(
            self._session_secret.encode('utf-8'),
            payload_encoded.encode('ascii'),
            hashlib.sha256,
        ).digest()
        signature_encoded = self._b64url_encode(signature)
        return f'{payload_encoded}.{signature_encoded}'

    def _verify_session_token(self, token: str) -> dict | None:
        if '.' not in token:
            return None
        payload_encoded, signature_encoded = token.split('.', 1)
        expected_signature = hmac.new(
            self._session_secret.encode('utf-8'),
            payload_encoded.encode('ascii'),
            hashlib.sha256,
        ).digest()
        provided_signature = self._b64url_decode(signature_encoded)
        if provided_signature is None or not hmac.compare_digest(provided_signature, expected_signature):
            return None

        payload_raw = self._b64url_decode(payload_encoded)
        if payload_raw is None:
            return None
        try:
            payload = json.loads(payload_raw.decode('utf-8'))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        exp = payload.get('exp')
        sub = payload.get('sub')
        if not isinstance(exp, int) or not isinstance(sub, str) or not sub.strip():
            return None
        if exp <= int(time.time()):
            return None
        return payload

    def _b64url_encode(self, raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')

    def _b64url_decode(self, encoded: str) -> bytes | None:
        try:
            padding = '=' * ((4 - len(encoded) % 4) % 4)
            return base64.urlsafe_b64decode((encoded + padding).encode('ascii'))
        except Exception:
            return None

    def github_installation_token(
        self,
        installation_id: int | None = None,
        owner: str | None = None,
        repo: str | None = None,
        operation: str = 'read',
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
            try:
                resolved_installation_id = self._github_app_auth.resolve_installation_id_for_repo(
                    trace_id=trace_id,
                    owner=owner,
                    repo=repo,
                )
            except RuntimeError as exc:
                if 'HTTP 404' in str(exc):
                    return (
                        404,
                        {
                            'status': 'needs_installation',
                            'owner': owner,
                            'repo': repo,
                            'operation': operation,
                            'reason': 'github_app_not_installed_for_repo',
                            'next_action': 'install_github_app_for_owner_or_repo',
                        },
                    )
                raise

        token_payload = self._github_app_auth.create_installation_access_token(
            trace_id=trace_id,
            installation_id=resolved_installation_id,
        )
        permissions = token_payload.get('permissions', {})
        if operation == 'write':
            contents_permission = permissions.get('contents') if isinstance(permissions, dict) else None
            if contents_permission not in ('write', 'admin'):
                return (
                    403,
                    {
                        'status': 'error',
                        'reason': 'insufficient_repo_permission',
                        'required': 'contents:write',
                        'actual': contents_permission or 'unknown',
                    },
                )

        if owner and repo:
            self._repo_capability_store.upsert(
                owner=owner,
                repo=repo,
                installation_id=resolved_installation_id,
                permissions=permissions if isinstance(permissions, dict) else {},
                repository_selection=token_payload.get('repository_selection') if isinstance(token_payload.get('repository_selection'), str) else None,
            )
        return (
            200,
            {
                'status': 'ok',
                'installation_id': resolved_installation_id,
                'expires_at': token_payload.get('expires_at'),
                'token': token_payload.get('token'),
                'permissions': permissions if isinstance(permissions, dict) else {},
                'repository_selection': token_payload.get('repository_selection'),
            },
        )

    def github_repo_access(self, owner: str, repo: str) -> tuple[int, dict]:
        record = self._repo_capability_store.get(owner=owner, repo=repo)
        if record is None:
            return (
                404,
                {
                    'status': 'error',
                    'reason': 'access_record_not_found',
                    'owner': owner,
                    'repo': repo,
                },
            )
        return (200, {'status': 'ok', 'record': record})

    def github_user_repositories(self, *, user_id: str, per_page: int = 100) -> tuple[int, dict]:
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

        try:
            rows = self._github_app_auth.list_user_repositories(
                trace_id=uuid4().hex,
                user_id=user_id,
                per_page=per_page,
            )
        except Exception as exc:
            return (500, {'status': 'error', 'reason': str(exc)})

        repositories: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            owner_raw = row.get('owner')
            owner = owner_raw if isinstance(owner_raw, dict) else {}
            owner_login = owner.get('login') if isinstance(owner.get('login'), str) else None
            name = row.get('name') if isinstance(row.get('name'), str) else None
            full_name = row.get('full_name') if isinstance(row.get('full_name'), str) else None
            if not owner_login or not name or not full_name:
                continue
            repositories.append(
                {
                    'id': row.get('id'),
                    'owner': owner_login,
                    'name': name,
                    'full_name': full_name,
                    'private': bool(row.get('private')),
                    'visibility': row.get('visibility'),
                    'default_branch': row.get('default_branch'),
                }
            )

        repositories.sort(key=lambda item: str(item.get('full_name', '')).lower())
        return (200, {'status': 'ok', 'repositories': repositories})

    def projects_list(self, *, user_id: str) -> tuple[int, dict]:
        list_projects = getattr(self._oauth_token_store, 'list_user_projects', None)
        if not callable(list_projects):
            return (501, {'status': 'error', 'reason': 'projects_not_supported_for_current_token_store'})
        return (200, {'status': 'ok', 'projects': list_projects(user_id=user_id)})

    def projects_get(self, *, user_id: str, project_id: str, require_owner: bool = False) -> tuple[int, dict]:
        list_projects = getattr(self._oauth_token_store, 'list_user_projects', None)
        if not callable(list_projects):
            return (501, {'status': 'error', 'reason': 'projects_not_supported_for_current_token_store'})

        projects_raw = list_projects(user_id=user_id)
        projects = projects_raw if isinstance(projects_raw, list) else []
        project = next((item for item in projects if str(item.get('project_id', '')) == project_id), None)
        if project is None:
            return (404, {'status': 'error', 'reason': 'project_not_found', 'project_id': project_id})

        owner_user_id = str(project.get('owner_user_id', ''))
        if require_owner and owner_user_id != user_id:
            return (403, {'status': 'error', 'reason': 'project_owner_required', 'project_id': project_id})

        return (200, {'status': 'ok', 'project': project})

    def projects_create(self, *, user_id: str, name: str, description: str | None) -> tuple[int, dict]:
        create_project = getattr(self._oauth_token_store, 'create_project', None)
        if not callable(create_project):
            return (501, {'status': 'error', 'reason': 'projects_not_supported_for_current_token_store'})
        try:
            project = create_project(owner_user_id=user_id, name=name, description=description)
        except Exception as exc:
            return (400, {'status': 'error', 'reason': str(exc)})
        return (201, {'status': 'ok', 'project': project})

    def projects_update(self, *, user_id: str, project_id: str, name: str, description: str | None) -> tuple[int, dict]:
        update_project = getattr(self._oauth_token_store, 'update_project', None)
        if not callable(update_project):
            return (501, {'status': 'error', 'reason': 'projects_not_supported_for_current_token_store'})
        try:
            project = update_project(
                project_id=project_id,
                owner_user_id=user_id,
                name=name,
                description=description,
            )
        except KeyError:
            return (404, {'status': 'error', 'reason': 'project_not_found', 'project_id': project_id})
        except PermissionError:
            return (403, {'status': 'error', 'reason': 'project_owner_required', 'project_id': project_id})
        except Exception as exc:
            return (400, {'status': 'error', 'reason': str(exc)})
        return (200, {'status': 'ok', 'project': project})

    def projects_delete(self, *, user_id: str, project_id: str) -> tuple[int, dict]:
        delete_project = getattr(self._oauth_token_store, 'delete_project', None)
        if not callable(delete_project):
            return (501, {'status': 'error', 'reason': 'projects_not_supported_for_current_token_store'})
        try:
            deleted = delete_project(project_id=project_id, owner_user_id=user_id)
        except KeyError:
            return (404, {'status': 'error', 'reason': 'project_not_found', 'project_id': project_id})
        except PermissionError:
            return (403, {'status': 'error', 'reason': 'project_owner_required', 'project_id': project_id})
        except Exception as exc:
            return (400, {'status': 'error', 'reason': str(exc)})
        return (200, {'status': 'ok', 'deleted': bool(deleted), 'project_id': project_id})

    def projects_add_repository(
        self,
        *,
        user_id: str,
        project_id: str,
        owner: str,
        name: str,
        github_repo_id: int | None,
        visibility: str | None,
    ) -> tuple[int, dict]:
        add_repo = getattr(self._oauth_token_store, 'add_repository_to_project', None)
        if not callable(add_repo):
            return (501, {'status': 'error', 'reason': 'projects_not_supported_for_current_token_store'})

        project_code, project_payload = self.projects_get(user_id=user_id, project_id=project_id, require_owner=True)
        if project_code != 200:
            return (project_code, project_payload)

        try:
            link = add_repo(
                project_id=project_id,
                owner=owner,
                name=name,
                added_by_user_id=user_id,
                github_repo_id=github_repo_id,
                visibility=visibility,
            )
        except Exception as exc:
            return (400, {'status': 'error', 'reason': str(exc)})

        code, listing = self.projects_list_repositories(user_id=user_id, project_id=project_id, require_owner=True)
        if code != 200:
            return (200, {'status': 'ok', 'link': link, 'repositories': []})
        return (200, {'status': 'ok', 'link': link, 'repositories': listing.get('repositories', [])})

    def projects_list_repositories(self, *, user_id: str, project_id: str, require_owner: bool = True) -> tuple[int, dict]:
        list_repos = getattr(self._oauth_token_store, 'list_project_repositories', None)
        if not callable(list_repos):
            return (501, {'status': 'error', 'reason': 'projects_not_supported_for_current_token_store'})

        project_code, project_payload = self.projects_get(
            user_id=user_id,
            project_id=project_id,
            require_owner=require_owner,
        )
        if project_code != 200:
            return (project_code, project_payload)

        return (200, {'status': 'ok', 'repositories': list_repos(project_id=project_id)})

    def projects_remove_repository(self, *, user_id: str, project_id: str, repository_id: int) -> tuple[int, dict]:
        remove_repo = getattr(self._oauth_token_store, 'remove_repository_from_project', None)
        if not callable(remove_repo):
            return (501, {'status': 'error', 'reason': 'projects_not_supported_for_current_token_store'})

        project_code, project_payload = self.projects_get(user_id=user_id, project_id=project_id, require_owner=True)
        if project_code != 200:
            return (project_code, project_payload)

        try:
            removed = remove_repo(project_id=project_id, repository_id=repository_id)
        except Exception as exc:
            return (400, {'status': 'error', 'reason': str(exc)})
        code, listing = self.projects_list_repositories(user_id=user_id, project_id=project_id, require_owner=True)
        if code != 200:
            return (200, {'status': 'ok', 'removed': bool(removed), 'repository_id': repository_id})
        return (
            200,
            {
                'status': 'ok',
                'removed': bool(removed),
                'repository_id': repository_id,
                'repositories': listing.get('repositories', []),
            },
        )

    def writer_open_pr(
        self,
        *,
        requesting_user_id: str,
        owner: str,
        repo: str,
        base_branch: str,
        title: str,
        body: str,
        files: list[WriterFileChange],
        branch_name: str | None,
        commit_message: str,
        draft: bool,
        dry_run: bool,
    ) -> tuple[int, dict]:
        if self._writer_service is None:
            _, status = self.github_auth_status()
            return (
                503,
                {
                    'status': 'error',
                    'reason': 'github_app_not_configured',
                    'missing_fields': status['missing_fields'],
                },
            )

        list_accessible = getattr(self._oauth_token_store, 'list_user_accessible_repositories', None)
        if callable(list_accessible):
            rows = list_accessible(user_id=requesting_user_id)
            accessible_rows = rows if isinstance(rows, list) else []
            accessible = {
                str(row.get('full_name', '')).lower()
                for row in accessible_rows
                if isinstance(row, dict) and isinstance(row.get('full_name'), str)
            }
            if accessible and f'{owner}/{repo}'.lower() not in accessible:
                return (
                    403,
                    {
                        'status': 'error',
                        'reason': 'repo_not_accessible_for_user',
                        'owner': owner,
                        'repo': repo,
                    },
                )

        try:
            payload = self._writer_service.open_pull_request(
                trace_id=uuid4().hex,
                owner=owner,
                repo=repo,
                base_branch=base_branch,
                title=title,
                body=body,
                files=files,
                branch_name=branch_name,
                commit_message=commit_message,
                draft=draft,
                dry_run=dry_run,
            )
        except PermissionError as exc:
            return (403, {'status': 'error', 'reason': str(exc)})
        except ValueError as exc:
            return (400, {'status': 'error', 'reason': str(exc)})
        except RuntimeError as exc:
            return (502, {'status': 'error', 'reason': str(exc)})
        except Exception as exc:  # noqa: BLE001
            return (500, {'status': 'error', 'reason': str(exc)})
        return (200, payload)

    def github_register_webhook(self, owner: str, repo: str, webhook_url: str) -> tuple[int, dict]:
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

        result = self._github_app_auth.ensure_repository_webhook(
            trace_id=uuid4().hex,
            owner=owner,
            repo=repo,
            webhook_url=webhook_url,
            events=('push',),
        )
        return (200, {'status': 'ok', **result})

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
        def _route_path(self, raw_path: str) -> str:
            if raw_path == '/api':
                return '/'
            if raw_path.startswith('/api/'):
                return raw_path[4:]
            return raw_path

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = self._route_path(parsed.path)
            if path == '/auth/oauth/signup/start' or path == '/auth/oauth/signin/start':
                content_length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(content_length)
                try:
                    body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                except JSONDecodeError:
                    self._send_json(400, {'status': 'error', 'reason': 'invalid_json'})
                    return
                provider_raw = body.get('provider') if isinstance(body, dict) else None
                provider = provider_raw if isinstance(provider_raw, str) and provider_raw else 'github'
                redirect_uri_raw = body.get('redirect_uri') if isinstance(body, dict) else None
                redirect_uri = redirect_uri_raw if isinstance(redirect_uri_raw, str) and redirect_uri_raw else None
                state_raw = body.get('state') if isinstance(body, dict) else None
                state = state_raw if isinstance(state_raw, str) and state_raw else f'state-{uuid4().hex}'

                if path == '/auth/oauth/signup/start':
                    code, payload = app.oauth_signup_start(provider=provider, state=state, redirect_uri=redirect_uri)
                else:
                    code, payload = app.oauth_signin_start(provider=provider, state=state, redirect_uri=redirect_uri)
                self._send_json(code, payload)
                return

            if path == '/auth/oauth/callback':
                content_length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(content_length)
                try:
                    body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                except JSONDecodeError:
                    self._send_json(400, {'status': 'error', 'reason': 'invalid_json'})
                    return
                provider_raw = body.get('provider') if isinstance(body, dict) else None
                provider = provider_raw if isinstance(provider_raw, str) and provider_raw else 'github'
                flow_raw = body.get('flow') if isinstance(body, dict) else None
                flow = flow_raw if isinstance(flow_raw, str) and flow_raw else 'signin'
                code_raw = body.get('code') if isinstance(body, dict) else None
                code = code_raw if isinstance(code_raw, str) and code_raw else None
                state_raw = body.get('state') if isinstance(body, dict) else None
                state = state_raw if isinstance(state_raw, str) and state_raw else None
                redirect_uri_raw = body.get('redirect_uri') if isinstance(body, dict) else None
                redirect_uri = redirect_uri_raw if isinstance(redirect_uri_raw, str) and redirect_uri_raw else None
                if not code:
                    self._send_json(400, {'status': 'error', 'reason': 'code is required'})
                    return
                try:
                    response_code, payload = app.oauth_callback(
                        flow=flow,
                        provider=provider,
                        code=code,
                        state=state,
                        redirect_uri=redirect_uri,
                    )
                    self._send_json(response_code, payload)
                except Exception as exc:
                    self._send_json(500, {'status': 'error', 'reason': str(exc)})
                return

            if path == '/auth/session/validate':
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                self._send_json(200, {'status': 'ok', 'user_id': user_id})
                return

            if path == '/writer/pr':
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                content_length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(content_length)
                try:
                    body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                except JSONDecodeError:
                    self._send_json(400, {'status': 'error', 'reason': 'invalid_json'})
                    return

                owner = body.get('owner') if isinstance(body.get('owner'), str) else None
                repo = body.get('repo') if isinstance(body.get('repo'), str) else None
                title = body.get('title') if isinstance(body.get('title'), str) else None
                if not owner or not repo or not title:
                    self._send_json(400, {'status': 'error', 'reason': 'owner, repo, and title are required'})
                    return

                files_raw = body.get('files')
                if not isinstance(files_raw, list) or not files_raw:
                    self._send_json(400, {'status': 'error', 'reason': 'files must be a non-empty list'})
                    return

                files: list[WriterFileChange] = []
                for item in files_raw:
                    if not isinstance(item, dict):
                        self._send_json(400, {'status': 'error', 'reason': 'each file entry must be an object'})
                        return
                    path = item.get('path')
                    content = item.get('content')
                    if not isinstance(path, str) or not path.strip() or not isinstance(content, str):
                        self._send_json(400, {'status': 'error', 'reason': 'file path and content are required'})
                        return
                    files.append(WriterFileChange(path=path, content=content))

                base_branch_raw = body.get('base_branch')
                base_branch = base_branch_raw if isinstance(base_branch_raw, str) and base_branch_raw else 'main'
                body_raw = body.get('body')
                pr_body = body_raw if isinstance(body_raw, str) else ''
                branch_name_raw = body.get('branch_name')
                branch_name = branch_name_raw if isinstance(branch_name_raw, str) and branch_name_raw else None
                commit_message_raw = body.get('commit_message')
                commit_message = (
                    commit_message_raw
                    if isinstance(commit_message_raw, str) and commit_message_raw
                    else 'chore: apply automated code changes'
                )

                code, payload = app.writer_open_pr(
                    requesting_user_id=user_id,
                    owner=owner,
                    repo=repo,
                    base_branch=base_branch,
                    title=title,
                    body=pr_body,
                    files=files,
                    branch_name=branch_name,
                    commit_message=commit_message,
                    draft=bool(body.get('draft', False)),
                    dry_run=bool(body.get('dry_run', False)),
                )
                self._send_json(code, payload)
                return

            if path == '/projects':
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                content_length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(content_length)
                try:
                    body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                except JSONDecodeError:
                    self._send_json(400, {'status': 'error', 'reason': 'invalid_json'})
                    return
                name_raw = body.get('name') if isinstance(body, dict) else None
                description_raw = body.get('description') if isinstance(body, dict) else None
                name = name_raw if isinstance(name_raw, str) else ''
                description = description_raw if isinstance(description_raw, str) else None
                if not name:
                    self._send_json(400, {'status': 'error', 'reason': 'project name is required'})
                    return
                code, payload = app.projects_create(user_id=user_id, name=name, description=description)
                self._send_json(code, payload)
                return

            if path.startswith('/projects/') and path.endswith('/repositories'):
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                parts = [part for part in path.split('/') if part]
                if len(parts) != 3:
                    self._send_json(404, {'status': 'error', 'reason': 'not_found'})
                    return
                project_id = parts[1]
                content_length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(content_length)
                try:
                    body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                except JSONDecodeError:
                    self._send_json(400, {'status': 'error', 'reason': 'invalid_json'})
                    return
                owner_raw = body.get('owner') if isinstance(body, dict) else None
                name_raw = body.get('name') if isinstance(body, dict) else None
                repo_id_raw = body.get('id') if isinstance(body, dict) else None
                visibility_raw = body.get('visibility') if isinstance(body, dict) else None
                owner = owner_raw if isinstance(owner_raw, str) else ''
                name = name_raw if isinstance(name_raw, str) else ''
                github_repo_id = repo_id_raw if isinstance(repo_id_raw, int) else None
                visibility = visibility_raw if isinstance(visibility_raw, str) else None
                if not owner or not name:
                    self._send_json(400, {'status': 'error', 'reason': 'owner and name are required'})
                    return
                code, payload = app.projects_add_repository(
                    user_id=user_id,
                    project_id=project_id,
                    owner=owner,
                    name=name,
                    github_repo_id=github_repo_id,
                    visibility=visibility,
                )
                self._send_json(code, payload)
                return

            if path == '/chat/sessions':
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                content_length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(content_length)
                try:
                    body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                except JSONDecodeError:
                    self._send_json(400, {'status': 'error', 'reason': 'invalid_json'})
                    return
                body_user_id = body.get('user_id')
                if isinstance(body_user_id, str) and body_user_id != user_id:
                    self._send_json(403, {'status': 'error', 'reason': 'user_id does not match auth token'})
                    return
                code, payload = app.chat_create_session(user_id=user_id)
                self._send_json(code, payload)
                return

            if path == '/chat/messages':
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                content_length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(content_length)
                try:
                    body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                except JSONDecodeError:
                    self._send_json(400, {'status': 'error', 'reason': 'invalid_json'})
                    return

                session_id = body.get('session_id')
                message = body.get('message')
                if not isinstance(session_id, str) or not isinstance(message, str):
                    self._send_json(400, {'status': 'error', 'reason': 'session_id and message are required'})
                    return

                repos_value = body.get('repos_in_scope', [])
                repos_in_scope = tuple(item for item in repos_value if isinstance(item, str)) if isinstance(repos_value, list) else ()
                code, payload = app.chat_send_message(
                    session_id=session_id,
                    user_id=user_id,
                    message=message,
                    repos_in_scope=repos_in_scope,
                    top_k=int(body.get('top_k', 8)),
                    candidate_pool_multiplier=int(body.get('candidate_pool_multiplier', 6)),
                    relevancy_threshold=float(body.get('relevancy_threshold', 0.35)),
                    relevancy_workers=int(body.get('relevancy_workers', 6)),
                    reducer_token_budget=int(body.get('reducer_token_budget', 2500)),
                    reducer_max_contexts=(
                        int(body['reducer_max_contexts']) if isinstance(body.get('reducer_max_contexts'), int) else None
                    ),
                )
                self._send_json(code, payload)
                return

            if path == '/observability/events':
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                content_length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(content_length)
                try:
                    body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                except JSONDecodeError:
                    self._send_json(400, {'status': 'error', 'reason': 'invalid_json'})
                    return
                name_raw = body.get('name') if isinstance(body, dict) else None
                trace_id_raw = body.get('trace_id') if isinstance(body, dict) else None
                input_payload_raw = body.get('input') if isinstance(body, dict) else None
                output_payload_raw = body.get('output') if isinstance(body, dict) else None
                name = name_raw if isinstance(name_raw, str) else ''
                trace_id = trace_id_raw if isinstance(trace_id_raw, str) else None
                input_payload = input_payload_raw if isinstance(input_payload_raw, dict) else None
                output_payload = output_payload_raw if isinstance(output_payload_raw, dict) else None
                code, payload = app.observability_write_event(
                    user_id=user_id,
                    name=name,
                    trace_id=trace_id,
                    input_payload=input_payload,
                    output_payload=output_payload,
                )
                self._send_json(code, payload)
                return

            if path == '/auth/github/installation-token':
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
                    operation = body.get('operation')
                    response_code, payload = app.github_installation_token(
                        installation_id=int(installation_id) if isinstance(installation_id, int | str) and str(installation_id).isdigit() else None,
                        owner=owner if isinstance(owner, str) else None,
                        repo=repo if isinstance(repo, str) else None,
                        operation=operation if isinstance(operation, str) and operation in ('read', 'write') else 'read',
                    )
                    self._send_json(response_code, payload)
                except Exception as exc:
                    self._send_json(500, {'status': 'error', 'reason': str(exc)})
                return

            if path == '/auth/github/webhook/register':
                content_length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(content_length)
                try:
                    body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                except JSONDecodeError:
                    self._send_json(400, {'status': 'error', 'reason': 'invalid_json'})
                    return
                owner = body.get('owner')
                repo = body.get('repo')
                webhook_url = body.get('webhook_url')
                if not isinstance(owner, str) or not isinstance(repo, str) or not isinstance(webhook_url, str):
                    self._send_json(400, {'status': 'error', 'reason': 'owner, repo, webhook_url are required'})
                    return
                try:
                    code, payload = app.github_register_webhook(owner=owner, repo=repo, webhook_url=webhook_url)
                    self._send_json(code, payload)
                except Exception as exc:
                    self._send_json(500, {'status': 'error', 'reason': str(exc)})
                return

            if path != '/webhooks/github':
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
            path = self._route_path(parsed.path)
            if path == '/auth/github/user-repos':
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                query = parse_qs(parsed.query)
                per_page_raw = query.get('per_page', ['100'])[0]
                per_page = int(per_page_raw) if isinstance(per_page_raw, str) and per_page_raw.isdigit() else 100
                code, payload = app.github_user_repositories(user_id=user_id, per_page=per_page)
                self._send_json(code, payload)
                return

            if path == '/projects':
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                code, payload = app.projects_list(user_id=user_id)
                self._send_json(code, payload)
                return

            if path.startswith('/projects/') and not path.endswith('/repositories'):
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                parts = [part for part in path.split('/') if part]
                if len(parts) != 2:
                    self._send_json(404, {'status': 'error', 'reason': 'not_found'})
                    return
                project_id = parts[1]
                code, payload = app.projects_get(user_id=user_id, project_id=project_id, require_owner=True)
                self._send_json(code, payload)
                return

            if path.startswith('/projects/') and path.endswith('/repositories'):
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                parts = [part for part in path.split('/') if part]
                if len(parts) != 3:
                    self._send_json(404, {'status': 'error', 'reason': 'not_found'})
                    return
                project_id = parts[1]
                code, payload = app.projects_list_repositories(user_id=user_id, project_id=project_id, require_owner=True)
                self._send_json(code, payload)
                return

            if path.startswith('/chat/sessions/'):
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                session_id = path.rsplit('/', 1)[-1]
                code, payload = app.chat_get_session(session_id, requesting_user_id=user_id)
                self._send_json(code, payload)
                return
            if path.startswith('/chat/runs/'):
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                run_id = path.rsplit('/', 1)[-1]
                code, payload = app.chat_get_run(run_id, requesting_user_id=user_id)
                self._send_json(code, payload)
                return
            if path == '/observability/trace-stack':
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                query = parse_qs(parsed.query)
                trace_id_value = query.get('trace_id', [None])[0]
                trace_id = trace_id_value if isinstance(trace_id_value, str) and trace_id_value.strip() else None
                limit_raw = query.get('limit', ['80'])[0]
                limit = int(limit_raw) if isinstance(limit_raw, str) and limit_raw.isdigit() else 80
                code, payload = app.observability_trace_stack(trace_id=trace_id, limit=limit)
                self._send_json(code, payload)
                return
            if path == '/healthz':
                self._send_json(200, {'status': 'ok'})
                return
            if path == '/readyz':
                status_code, payload = app.readiness()
                self._send_json(status_code, payload)
                return
            if path == '/auth/github/status':
                status_code, payload = app.github_auth_status()
                self._send_json(status_code, payload)
                return
            if path == '/auth/github/start':
                query = parse_qs(parsed.query)
                state = query.get('state', [f'state-{uuid4().hex}'])[0]
                redirect_uri = query.get('redirect_uri', [None])[0]
                status_code, payload = app.github_auth_start(state=state, redirect_uri=redirect_uri)
                self._send_json(status_code, payload)
                return
            if path == '/auth/github/callback':
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
            if path == '/auth/github/access':
                query = parse_qs(parsed.query)
                owner = query.get('owner', [None])[0]
                repo = query.get('repo', [None])[0]
                if not isinstance(owner, str) or not isinstance(repo, str):
                    self._send_json(400, {'status': 'error', 'reason': 'owner and repo are required'})
                    return
                code, payload = app.github_repo_access(owner=owner, repo=repo)
                self._send_json(code, payload)
                return
            self._send_json(404, {'status': 'error', 'reason': 'not_found'})

        def do_PATCH(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = self._route_path(parsed.path)
            if path.startswith('/projects/') and not path.endswith('/repositories'):
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                parts = [part for part in path.split('/') if part]
                if len(parts) != 2:
                    self._send_json(404, {'status': 'error', 'reason': 'not_found'})
                    return
                project_id = parts[1]
                content_length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(content_length)
                try:
                    body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                except JSONDecodeError:
                    self._send_json(400, {'status': 'error', 'reason': 'invalid_json'})
                    return
                name_raw = body.get('name') if isinstance(body, dict) else None
                description_raw = body.get('description') if isinstance(body, dict) else None
                name = name_raw if isinstance(name_raw, str) else ''
                description = description_raw if isinstance(description_raw, str) else None
                if not name:
                    self._send_json(400, {'status': 'error', 'reason': 'project name is required'})
                    return
                code, payload = app.projects_update(
                    user_id=user_id,
                    project_id=project_id,
                    name=name,
                    description=description,
                )
                self._send_json(code, payload)
                return

            self._send_json(404, {'status': 'error', 'reason': 'not_found'})

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = self._route_path(parsed.path)

            if path.startswith('/projects/') and '/repositories/' in path:
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                parts = [part for part in path.split('/') if part]
                if len(parts) != 4 or parts[2] != 'repositories':
                    self._send_json(404, {'status': 'error', 'reason': 'not_found'})
                    return
                project_id = parts[1]
                repository_id_raw = parts[3]
                if not repository_id_raw.isdigit():
                    self._send_json(400, {'status': 'error', 'reason': 'repository_id must be an integer'})
                    return
                code, payload = app.projects_remove_repository(
                    user_id=user_id,
                    project_id=project_id,
                    repository_id=int(repository_id_raw),
                )
                self._send_json(code, payload)
                return

            if path.startswith('/projects/') and not path.endswith('/repositories'):
                user_id = self._require_authenticated_user()
                if user_id is None:
                    return
                parts = [part for part in path.split('/') if part]
                if len(parts) != 2:
                    self._send_json(404, {'status': 'error', 'reason': 'not_found'})
                    return
                project_id = parts[1]
                code, payload = app.projects_delete(user_id=user_id, project_id=project_id)
                self._send_json(code, payload)
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

        def _require_authenticated_user(self) -> str | None:
            auth_header = self.headers.get('Authorization')
            if not isinstance(auth_header, str) or not auth_header.lower().startswith('bearer '):
                self._send_json(
                    401,
                    {'status': 'error', 'reason': 'missing_bearer_token'},
                    extra_headers={'WWW-Authenticate': 'Bearer'},
                )
                return None
            token = auth_header[7:].strip()
            code, payload = app.authenticate_bearer_token(token)
            if code != 200:
                self._send_json(code, payload, extra_headers={'WWW-Authenticate': 'Bearer'})
                return None
            user_id = payload.get('user_id')
            if not isinstance(user_id, str) or not user_id:
                self._send_json(401, {'status': 'error', 'reason': 'invalid_bearer_claims'})
                return None
            return user_id

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
