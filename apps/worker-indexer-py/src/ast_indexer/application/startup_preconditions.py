"""Fail-fast checks so missing API keys and required secrets fail at startup with clear errors."""

from __future__ import annotations

import os
from typing import Literal


def _truthy_env(name: str) -> bool:
    return os.getenv(name, '').strip().lower() in ('1', 'true', 'yes', 'on')


def resolved_openai_api_key(openai_api_key: str | None) -> str | None:
    for candidate in (
        (openai_api_key or '').strip(),
        (os.getenv('OPENAI_API_KEY') or '').strip(),
        (os.getenv('AST_INDEXER_OPENAI_API_KEY') or '').strip(),
    ):
        if candidate:
            return candidate
    return None


def validate_embedding_openai(
    embedding_backend: Literal['hash', 'sentence-transformers', 'openai'],
    openai_api_key: str | None,
) -> None:
    if embedding_backend != 'openai':
        return
    if resolved_openai_api_key(openai_api_key):
        return
    raise ValueError(
        'embedding-backend=openai requires a non-empty OPENAI_API_KEY or AST_INDEXER_OPENAI_API_KEY'
    )


def validate_langfuse_observability(
    observability_backend: Literal['jsonl', 'langfuse'],
    langfuse_host: str | None,
    langfuse_public_key: str | None,
    langfuse_secret_key: str | None,
) -> None:
    if observability_backend != 'langfuse':
        return
    host = (langfuse_host or os.getenv('LANGFUSE_HOST') or '').strip()
    public_key = (langfuse_public_key or os.getenv('LANGFUSE_PUBLIC_KEY') or '').strip()
    secret_key = (langfuse_secret_key or os.getenv('LANGFUSE_SECRET_KEY') or '').strip()
    missing: list[str] = []
    if not host:
        missing.append('LANGFUSE_HOST')
    if not public_key:
        missing.append('LANGFUSE_PUBLIC_KEY')
    if not secret_key:
        missing.append('LANGFUSE_SECRET_KEY')
    if missing:
        raise ValueError(
            'observability-backend=langfuse requires non-empty values for: ' + ', '.join(missing)
        )


def validate_redis_queue(
    queue_backend: Literal['memory', 'redis'],
    redis_url: str | None,
) -> None:
    if queue_backend != 'redis':
        return
    url = (redis_url or os.getenv('AST_INDEXER_REDIS_URL') or '').strip()
    if not url:
        raise ValueError('queue-backend=redis requires --redis-url or AST_INDEXER_REDIS_URL')


def validate_oauth_encrypted_file_store() -> None:
    token_path = (os.getenv('AST_INDEXER_OAUTH_TOKEN_STORE_PATH') or '').strip()
    enc_key = (os.getenv('AST_INDEXER_OAUTH_ENCRYPTION_KEY') or '').strip()
    if token_path and not enc_key:
        raise ValueError(
            'AST_INDEXER_OAUTH_TOKEN_STORE_PATH is set but AST_INDEXER_OAUTH_ENCRYPTION_KEY is missing; '
            'set the encryption key or remove AST_INDEXER_OAUTH_TOKEN_STORE_PATH'
        )


def validate_github_app_if_required() -> None:
    if not _truthy_env('AST_INDEXER_REQUIRE_GITHUB_APP'):
        return
    from ast_indexer.application.github_app_auth_service import GithubAppConfig

    config = GithubAppConfig.from_env()
    missing = config.missing_fields()
    if missing:
        raise ValueError(
            'AST_INDEXER_REQUIRE_GITHUB_APP is set but GitHub App is not fully configured. '
            'Missing: ' + ', '.join(missing)
        )


def validate_index_or_research(
    embedding_backend: Literal['hash', 'sentence-transformers', 'openai'],
    openai_api_key: str | None,
    observability_backend: Literal['jsonl', 'langfuse'],
    langfuse_host: str | None,
    langfuse_public_key: str | None,
    langfuse_secret_key: str | None,
) -> None:
    validate_embedding_openai(embedding_backend, openai_api_key)
    validate_langfuse_observability(
        observability_backend,
        langfuse_host,
        langfuse_public_key,
        langfuse_secret_key,
    )


def validate_serve_webhook(
    embedding_backend: Literal['hash', 'sentence-transformers', 'openai'],
    openai_api_key: str | None,
    observability_backend: Literal['jsonl', 'langfuse'],
    langfuse_host: str | None,
    langfuse_public_key: str | None,
    langfuse_secret_key: str | None,
    queue_backend: Literal['memory', 'redis'],
    redis_url: str | None,
) -> None:
    validate_index_or_research(
        embedding_backend,
        openai_api_key,
        observability_backend,
        langfuse_host,
        langfuse_public_key,
        langfuse_secret_key,
    )
    validate_redis_queue(queue_backend, redis_url)
    validate_oauth_encrypted_file_store()
    validate_github_app_if_required()
