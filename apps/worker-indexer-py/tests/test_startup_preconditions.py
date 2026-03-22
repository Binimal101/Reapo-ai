from pathlib import Path

import pytest

from ast_indexer.application.startup_preconditions import (
    validate_embedding_openai,
    validate_github_app_if_required,
    validate_index_or_research,
    validate_langfuse_observability,
    validate_oauth_encrypted_file_store,
    validate_redis_queue,
    validate_serve_webhook,
)


def test_openai_embedding_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('AST_INDEXER_OPENAI_API_KEY', raising=False)
    with pytest.raises(ValueError, match='OPENAI_API_KEY'):
        validate_embedding_openai('openai', None)


def test_openai_embedding_ok_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    validate_embedding_openai('openai', None)


def test_langfuse_observability_requires_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('LANGFUSE_HOST', raising=False)
    monkeypatch.delenv('LANGFUSE_PUBLIC_KEY', raising=False)
    monkeypatch.delenv('LANGFUSE_SECRET_KEY', raising=False)
    with pytest.raises(ValueError, match='LANGFUSE'):
        validate_langfuse_observability('langfuse', None, None, None)


def test_redis_queue_requires_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('AST_INDEXER_REDIS_URL', raising=False)
    with pytest.raises(ValueError, match='redis'):
        validate_redis_queue('redis', None)


def test_redis_queue_memory_skips_url() -> None:
    validate_redis_queue('memory', None)


def test_oauth_token_path_without_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('AST_INDEXER_OAUTH_TOKEN_STORE_PATH', 'oauth/tokens.enc')
    monkeypatch.delenv('AST_INDEXER_OAUTH_ENCRYPTION_KEY', raising=False)
    with pytest.raises(ValueError, match='AST_INDEXER_OAUTH_ENCRYPTION_KEY'):
        validate_oauth_encrypted_file_store()


def test_serve_webhook_runs_full_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    monkeypatch.setenv('LANGFUSE_HOST', 'http://langfuse:3000')
    monkeypatch.setenv('LANGFUSE_PUBLIC_KEY', 'pk')
    monkeypatch.setenv('LANGFUSE_SECRET_KEY', 'sk')
    monkeypatch.setenv('AST_INDEXER_REDIS_URL', 'redis://redis:6379/0')
    validate_serve_webhook(
        'openai',
        None,
        'langfuse',
        None,
        None,
        None,
        'redis',
        None,
    )


def test_index_or_research_hash_jsonl_no_keys_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    validate_index_or_research('hash', None, 'jsonl', None, None, None)


def test_require_github_app_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('AST_INDEXER_REQUIRE_GITHUB_APP', 'true')
    monkeypatch.delenv('GITHUB_APP_ID', raising=False)
    with pytest.raises(ValueError, match='AST_INDEXER_REQUIRE_GITHUB_APP'):
        validate_github_app_if_required()


def test_require_github_app_skipped_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('AST_INDEXER_REQUIRE_GITHUB_APP', raising=False)
    monkeypatch.delenv('GITHUB_APP_ID', raising=False)
    validate_github_app_if_required()


def test_cli_serve_webhook_returns_2_without_openai_when_embedding_openai(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr('ast_indexer.cli._load_environment', lambda: None)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('AST_INDEXER_OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('AST_INDEXER_WEBHOOK_SECRET', 'test-webhook-secret')
    workspace = tmp_path / 'w'
    state = tmp_path / 's'
    workspace.mkdir()
    state.mkdir()
    from ast_indexer.cli import main

    code = main(
        [
            'serve-webhook',
            '--workspace-root',
            str(workspace),
            '--state-root',
            str(state),
            '--embedding-backend',
            'openai',
            '--queue-backend',
            'memory',
        ]
    )
    assert code == 2
