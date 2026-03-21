from __future__ import annotations

from pathlib import Path

from ast_indexer.adapters.oauth.in_memory_oauth_token_store_adapter import InMemoryOAuthTokenStoreAdapter
from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.application.github_app_auth_service import GithubAppAuthService, GithubAppConfig
from ast_indexer.application.oauth_session_service import OAuthSessionService


def test_github_app_config_detects_missing_fields(tmp_path: Path) -> None:
    config = GithubAppConfig(
        app_id='',
        client_id='abc',
        client_secret='',
        private_key_path=tmp_path / 'missing.pem',
        webhook_secret='',
    )

    missing = config.missing_fields()
    assert 'GITHUB_APP_ID' in missing
    assert 'GITHUB_APP_CLIENT_SECRET' in missing
    assert 'GITHUB_APP_PRIVATE_KEY_PATH (file missing)' in missing
    assert 'GITHUB_APP_WEBHOOK_SECRET' in missing


def test_build_oauth_start_url_contains_expected_query_values(tmp_path: Path) -> None:
    key_path = tmp_path / 'app.pem'
    key_path.write_text('dummy', encoding='utf-8')
    config = GithubAppConfig(
        app_id='1',
        client_id='client-123',
        client_secret='secret-123',
        private_key_path=key_path,
        webhook_secret='whsec',
    )
    service = GithubAppAuthService(
        config=config,
        oauth_session_service=OAuthSessionService(InMemoryOAuthTokenStoreAdapter(), InMemoryObservabilityAdapter()),
        observability=InMemoryObservabilityAdapter(),
        http_json=lambda *_: {},
    )

    url = service.build_oauth_start_url(state='state-1', redirect_uri='http://localhost:8090/auth/github/callback')
    assert 'client_id=client-123' in url
    assert 'state=state-1' in url
    assert 'redirect_uri=' in url


def test_exchange_oauth_code_saves_token_for_user(tmp_path: Path) -> None:
    key_path = tmp_path / 'app.pem'
    key_path.write_text('dummy', encoding='utf-8')

    token_store = InMemoryOAuthTokenStoreAdapter()
    oauth_service = OAuthSessionService(token_store, InMemoryObservabilityAdapter())

    def _fake_http(method: str, url: str, body: dict | None, headers: dict[str, str]) -> dict:  # noqa: ARG001
        if url.endswith('/login/oauth/access_token'):
            return {
                'access_token': 'gho_test_token',
                'scope': 'repo,read:user',
                'expires_in': 7200,
            }
        if url.endswith('/user'):
            return {'login': 'octocat'}
        raise AssertionError(f'Unexpected url: {url}')

    service = GithubAppAuthService(
        config=GithubAppConfig(
            app_id='1',
            client_id='client-123',
            client_secret='secret-123',
            private_key_path=key_path,
            webhook_secret='whsec',
        ),
        oauth_session_service=oauth_service,
        observability=InMemoryObservabilityAdapter(),
        http_json=_fake_http,
    )

    token = service.exchange_oauth_code(
        trace_id='trace-oauth-1',
        code='code-123',
        state='state-123',
        redirect_uri='http://localhost:8090/auth/github/callback',
    )

    assert token.user_id == 'octocat'
    assert token.access_token == 'gho_test_token'
    assert token.scopes == ('repo', 'read:user')
    assert token_store.get('octocat') is not None
