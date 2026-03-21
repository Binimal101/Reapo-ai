from datetime import datetime, timezone

from ast_indexer.adapters.oauth.in_memory_oauth_token_store_adapter import InMemoryOAuthTokenStoreAdapter
from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.application.oauth_session_service import OAuthSessionService
from ast_indexer.ports.oauth import OAuthTokenRecord


def test_save_and_get_valid_token() -> None:
    observability = InMemoryObservabilityAdapter()
    store = InMemoryOAuthTokenStoreAdapter()
    service = OAuthSessionService(store, observability)

    token = service.save_token(
        trace_id='trace-2',
        user_id='u-1',
        access_token='token',
        scopes=('contents:read',),
        expires_in_seconds=300,
    )

    assert token.user_id == 'u-1'
    assert service.get_valid_token(trace_id='trace-2', user_id='u-1') is not None


def test_expired_token_returns_none() -> None:
    observability = InMemoryObservabilityAdapter()
    store = InMemoryOAuthTokenStoreAdapter()
    expired = OAuthTokenRecord(
        user_id='u-2',
        access_token='expired',
        expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        scopes=('contents:read',),
    )
    store.save(expired)

    service = OAuthSessionService(store, observability)
    assert service.get_valid_token(trace_id='trace-3', user_id='u-2') is None
