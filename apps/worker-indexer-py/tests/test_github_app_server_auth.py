from __future__ import annotations

from pathlib import Path

import pytest

from ast_indexer.server import GithubWebhookServerApp


class _FakeGithubAppAuthService:
    def __init__(self) -> None:
        self.last_installation_id: int | None = None

    def build_oauth_start_url(self, state: str, redirect_uri: str | None = None) -> str:
        suffix = f'&redirect_uri={redirect_uri}' if redirect_uri else ''
        return f'https://github.com/login/oauth/authorize?state={state}{suffix}'

    def exchange_oauth_code(self, trace_id: str, code: str, state: str | None = None, redirect_uri: str | None = None):  # noqa: ANN001
        class _Token:
            user_id = 'octocat'
            scopes = ('repo',)

            @staticmethod
            def _expires_at_text() -> str:
                return '2030-01-01T00:00:00+00:00'

            @property
            def expires_at(self):  # noqa: ANN201
                class _Date:
                    @staticmethod
                    def isoformat() -> str:
                        return _Token._expires_at_text()

                return _Date()

        _ = (trace_id, code, state, redirect_uri)
        return _Token()

    def resolve_installation_id_for_repo(self, trace_id: str, owner: str, repo: str) -> int:
        _ = (trace_id, owner, repo)
        return 12345

    def create_installation_access_token(self, trace_id: str, installation_id: int) -> dict:
        _ = trace_id
        self.last_installation_id = installation_id
        return {
            'token': 'v1.installation.token',
            'expires_at': '2030-01-01T00:00:00+00:00',
            'permissions': {'contents': 'read'},
            'repository_selection': 'all',
        }


def _build_app(tmp_path: Path, auth_service: _FakeGithubAppAuthService | None = None) -> GithubWebhookServerApp:
    workspace_root = tmp_path / 'workspace'
    (workspace_root / 'worker-indexer-py').mkdir(parents=True)
    return GithubWebhookServerApp(
        workspace_root=workspace_root,
        state_root=tmp_path / 'state',
        webhook_secret='whsec',
        github_app_auth_service=auth_service,
    )


def test_github_auth_start_returns_503_when_env_not_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('GITHUB_APP_ID', raising=False)
    monkeypatch.delenv('GITHUB_APP_CLIENT_ID', raising=False)
    monkeypatch.delenv('GITHUB_APP_CLIENT_SECRET', raising=False)
    monkeypatch.delenv('GITHUB_APP_PRIVATE_KEY_PATH', raising=False)
    monkeypatch.delenv('GITHUB_APP_WEBHOOK_SECRET', raising=False)

    app = _build_app(tmp_path)
    code, payload = app.github_auth_start(state='state-1', redirect_uri='http://localhost/callback')

    assert code == 503
    assert payload['reason'] == 'github_app_not_configured'
    assert 'missing_fields' in payload


def test_github_auth_flows_use_injected_service(tmp_path: Path) -> None:
    fake = _FakeGithubAppAuthService()
    app = _build_app(tmp_path, auth_service=fake)

    start_code, start_payload = app.github_auth_start(state='state-2', redirect_uri='http://localhost/callback')
    assert start_code == 200
    assert 'authorize_url' in start_payload

    callback_code, callback_payload = app.github_auth_callback(
        code='code-1',
        state='state-2',
        redirect_uri='http://localhost/callback',
    )
    assert callback_code == 200
    assert callback_payload['user_id'] == 'octocat'

    token_code, token_payload = app.github_installation_token(owner='matth', repo='reapo-ai')
    assert token_code == 200
    assert token_payload['installation_id'] == 12345
    assert token_payload['token'] == 'v1.installation.token'
    assert fake.last_installation_id == 12345
