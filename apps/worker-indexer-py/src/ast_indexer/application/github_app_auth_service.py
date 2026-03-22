from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from ast_indexer.application import runtime_config
from ast_indexer.application.oauth_session_service import OAuthSessionService
from ast_indexer.ports.oauth import OAuthTokenRecord
from ast_indexer.ports.observability import ObservabilityPort


@dataclass(frozen=True)
class GithubAppConfig:
    app_id: str
    client_id: str
    client_secret: str
    private_key_path: Path
    webhook_secret: str

    @classmethod
    def from_env(cls) -> GithubAppConfig:
        return cls(
            app_id=os.getenv('GITHUB_APP_ID', ''),
            client_id=os.getenv('GITHUB_APP_CLIENT_ID', ''),
            client_secret=os.getenv('GITHUB_APP_CLIENT_SECRET', ''),
            private_key_path=Path(os.getenv('GITHUB_APP_PRIVATE_KEY_PATH', '')),
            webhook_secret=os.getenv('GITHUB_APP_WEBHOOK_SECRET', ''),
        )

    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.app_id:
            missing.append('GITHUB_APP_ID')
        if not self.client_id:
            missing.append('GITHUB_APP_CLIENT_ID')
        if not self.client_secret:
            missing.append('GITHUB_APP_CLIENT_SECRET')
        if not str(self.private_key_path):
            missing.append('GITHUB_APP_PRIVATE_KEY_PATH')
        elif not self.private_key_path.exists():
            missing.append('GITHUB_APP_PRIVATE_KEY_PATH (file missing)')
        if not self.webhook_secret:
            missing.append('GITHUB_APP_WEBHOOK_SECRET')
        return missing


class GithubAppAuthService:
    def __init__(
        self,
        config: GithubAppConfig,
        oauth_session_service: OAuthSessionService,
        observability: ObservabilityPort,
        http_json: Callable[[str, str, dict | None, dict[str, str]], dict | list | str] | None = None,
    ) -> None:
        self._config = config
        self._oauth_session_service = oauth_session_service
        self._observability = observability
        self._http_json = http_json or _http_json_request
        self._github_api_base_url = runtime_config.github_api_base_url()
        self._github_oauth_base_url = runtime_config.github_oauth_base_url()

    def _http_json_dict(self, method: str, url: str, body: dict | None, headers: dict[str, str]) -> dict:
        payload = self._http_json(method, url, body, headers)
        if not isinstance(payload, dict):
            raise RuntimeError(f'GitHub API returned unexpected payload type: {type(payload).__name__}')
        return payload

    def missing_fields(self) -> list[str]:
        return self._config.missing_fields()

    def is_configured(self) -> bool:
        return not self.missing_fields()

    def build_oauth_start_url(self, state: str, redirect_uri: str | None = None) -> str:
        params: dict[str, str] = {
            'client_id': self._config.client_id,
            'state': state,
        }
        if redirect_uri:
            params['redirect_uri'] = redirect_uri

        return f"{self._github_oauth_base_url}/login/oauth/authorize?{urlencode(params)}"

    def exchange_oauth_code(
        self,
        trace_id: str,
        code: str,
        state: str | None = None,
        redirect_uri: str | None = None,
    ) -> OAuthTokenRecord:
        span = self._observability.start_span(
            name='github_app_exchange_oauth_code',
            trace_id=trace_id,
            input_payload={
                'has_code': bool(code),
                'has_state': bool(state),
                'has_redirect_uri': bool(redirect_uri),
            },
        )

        payload: dict[str, str] = {
            'client_id': self._config.client_id,
            'client_secret': self._config.client_secret,
            'code': code,
        }
        if state:
            payload['state'] = state
        if redirect_uri:
            payload['redirect_uri'] = redirect_uri

        token_response = self._http_json_dict(
            'POST',
            f'{self._github_oauth_base_url}/login/oauth/access_token',
            payload,
            {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            },
        )

        access_token = token_response.get('access_token')
        if not access_token or not isinstance(access_token, str):
            error = token_response.get('error', 'missing_access_token')
            description = token_response.get('error_description', '')
            raise ValueError(f'GitHub OAuth exchange failed: {error} {description}'.strip())

        user_response = self._http_json_dict(
            'GET',
            f'{self._github_api_base_url}/user',
            None,
            {
                'Accept': 'application/vnd.github+json',
                'Authorization': f'Bearer {access_token}',
                'X-GitHub-Api-Version': '2022-11-28',
            },
        )

        user_id = user_response.get('login') if isinstance(user_response.get('login'), str) else None
        if not user_id:
            user_id = f'github-user-{uuid4().hex[:8]}'

        scope_raw = token_response.get('scope')
        scopes = tuple(scope.strip() for scope in scope_raw.split(',') if scope.strip()) if isinstance(scope_raw, str) else ()
        expires_in = token_response.get('expires_in')
        expires_in_seconds = int(expires_in) if isinstance(expires_in, int) else 3600
        refresh_token = token_response.get('refresh_token') if isinstance(token_response.get('refresh_token'), str) else None

        token = self._oauth_session_service.save_token(
            trace_id=trace_id,
            user_id=user_id,
            access_token=access_token,
            scopes=scopes,
            expires_in_seconds=expires_in_seconds,
            refresh_token=refresh_token,
        )

        self._observability.end_span(
            span,
            output_payload={
                'user_id': token.user_id,
                'scopes': list(token.scopes),
            },
        )
        return token

    def refresh_oauth_token(self, trace_id: str, refresh_token: str, user_id: str) -> OAuthTokenRecord:
        token_response = self._http_json_dict(
            'POST',
            f'{self._github_oauth_base_url}/login/oauth/access_token',
            {
                'client_id': self._config.client_id,
                'client_secret': self._config.client_secret,
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
            },
            {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            },
        )

        access_token = token_response.get('access_token')
        if not isinstance(access_token, str) or not access_token:
            raise ValueError('GitHub OAuth refresh failed: missing access_token')

        scope_raw = token_response.get('scope')
        scopes = tuple(scope.strip() for scope in scope_raw.split(',') if scope.strip()) if isinstance(scope_raw, str) else ()
        expires_in = token_response.get('expires_in')
        expires_in_seconds = int(expires_in) if isinstance(expires_in, int) else 3600
        next_refresh_token = token_response.get('refresh_token') if isinstance(token_response.get('refresh_token'), str) else refresh_token

        return self._oauth_session_service.save_token(
            trace_id=trace_id,
            user_id=user_id,
            access_token=access_token,
            scopes=scopes,
            expires_in_seconds=expires_in_seconds,
            refresh_token=next_refresh_token,
        )

    def fetch_user_with_retry(self, trace_id: str, user_id: str) -> dict:
        token = self._oauth_session_service.get_valid_token_with_refresh(
            trace_id=trace_id,
            user_id=user_id,
            refresh=lambda value: self._http_json_dict(
                'POST',
                f'{self._github_oauth_base_url}/login/oauth/access_token',
                {
                    'client_id': self._config.client_id,
                    'client_secret': self._config.client_secret,
                    'grant_type': 'refresh_token',
                    'refresh_token': value,
                },
                {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                },
            ),
        )
        if token is None:
            raise ValueError('No valid OAuth token available for user')

        try:
            return self._http_json_dict(
                'GET',
                f'{self._github_api_base_url}/user',
                None,
                {
                    'Accept': 'application/vnd.github+json',
                    'Authorization': f'Bearer {token.access_token}',
                    'X-GitHub-Api-Version': '2022-11-28',
                },
            )
        except RuntimeError as exc:
            if 'HTTP 401' not in str(exc) or not token.refresh_token:
                raise
            refreshed = self.refresh_oauth_token(trace_id=trace_id, refresh_token=token.refresh_token, user_id=user_id)
            return self._http_json_dict(
                'GET',
                f'{self._github_api_base_url}/user',
                None,
                {
                    'Accept': 'application/vnd.github+json',
                    'Authorization': f'Bearer {refreshed.access_token}',
                    'X-GitHub-Api-Version': '2022-11-28',
                },
            )

    def list_user_repositories(self, trace_id: str, user_id: str, per_page: int = 100) -> list[dict]:
        token = self._oauth_session_service.get_valid_token_with_refresh(
            trace_id=trace_id,
            user_id=user_id,
            refresh=lambda value: self._http_json_dict(
                'POST',
                f'{self._github_oauth_base_url}/login/oauth/access_token',
                {
                    'client_id': self._config.client_id,
                    'client_secret': self._config.client_secret,
                    'grant_type': 'refresh_token',
                    'refresh_token': value,
                },
                {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                },
            ),
        )
        if token is None:
            raise ValueError('No valid OAuth token available for user')

        page_size = max(1, min(100, int(per_page)))
        url = f'{self._github_api_base_url}/user/repos?per_page={page_size}&sort=updated&type=owner,public,private'
        headers = {
            'Accept': 'application/vnd.github+json',
            'Authorization': f'Bearer {token.access_token}',
            'X-GitHub-Api-Version': '2022-11-28',
        }

        payload = self._http_json('GET', url, None, headers)
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        raise RuntimeError(f'GitHub API returned unexpected payload type: {type(payload).__name__}')

    def create_installation_access_token(self, trace_id: str, installation_id: int) -> dict:
        span = self._observability.start_span(
            name='github_app_create_installation_token',
            trace_id=trace_id,
            input_payload={'installation_id': installation_id},
        )

        app_jwt = self._create_app_jwt()
        token_response = self._http_json_dict(
            'POST',
            f'{self._github_api_base_url}/app/installations/{installation_id}/access_tokens',
            {},
            {
                'Accept': 'application/vnd.github+json',
                'Authorization': f'Bearer {app_jwt}',
                'X-GitHub-Api-Version': '2022-11-28',
            },
        )

        if 'token' not in token_response:
            raise ValueError('GitHub installation token response missing token field')

        self._observability.end_span(
            span,
            output_payload={
                'installation_id': installation_id,
                'has_token': True,
            },
        )
        return token_response

    def resolve_installation_id_for_repo(self, trace_id: str, owner: str, repo: str) -> int:
        span = self._observability.start_span(
            name='github_app_resolve_installation',
            trace_id=trace_id,
            input_payload={'owner': owner, 'repo': repo},
        )

        app_jwt = self._create_app_jwt()
        payload = self._http_json_dict(
            'GET',
            f'{self._github_api_base_url}/repos/{owner}/{repo}/installation',
            None,
            {
                'Accept': 'application/vnd.github+json',
                'Authorization': f'Bearer {app_jwt}',
                'X-GitHub-Api-Version': '2022-11-28',
            },
        )

        installation_id = payload.get('id')
        if not isinstance(installation_id, int):
            raise ValueError('Unable to resolve installation id for repository')

        self._observability.end_span(
            span,
            output_payload={'installation_id': installation_id},
        )
        return installation_id

    def ensure_repository_webhook(
        self,
        trace_id: str,
        owner: str,
        repo: str,
        webhook_url: str,
        events: tuple[str, ...] = ('push',),
    ) -> dict:
        installation_id = self.resolve_installation_id_for_repo(trace_id=trace_id, owner=owner, repo=repo)
        token_payload = self.create_installation_access_token(trace_id=trace_id, installation_id=installation_id)
        token = token_payload.get('token')
        if not isinstance(token, str) or not token:
            raise ValueError('Unable to register webhook: installation token missing')

        headers = {
            'Accept': 'application/vnd.github+json',
            'Authorization': f'Bearer {token}',
            'X-GitHub-Api-Version': '2022-11-28',
            'Content-Type': 'application/json',
        }
        hooks_response = self._http_json(
            'GET',
            f'{self._github_api_base_url}/repos/{owner}/{repo}/hooks',
            None,
            headers,
        )
        hooks = hooks_response if isinstance(hooks_response, list) else []
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            raw_config = hook.get('config')
            if not isinstance(raw_config, dict):
                continue
            if raw_config.get('url') == webhook_url:
                return {
                    'hook_id': hook.get('id'),
                    'created': False,
                    'installation_id': installation_id,
                    'url': webhook_url,
                }

        payload = {
            'name': 'web',
            'active': True,
            'events': list(events),
            'config': {
                'url': webhook_url,
                'content_type': 'json',
                'secret': self._config.webhook_secret,
                'insecure_ssl': '0',
            },
        }
        created = self._http_json(
            'POST',
            f'{self._github_api_base_url}/repos/{owner}/{repo}/hooks',
            payload,
            headers,
        )
        return {
            'hook_id': created.get('id') if isinstance(created, dict) else None,
            'created': True,
            'installation_id': installation_id,
            'url': webhook_url,
        }

    def _create_app_jwt(self) -> str:
        try:
            import jwt
        except ImportError as exc:
            raise RuntimeError('GitHub App auth requires PyJWT. Install with: pip install "PyJWT[crypto]"') from exc

        private_key = self._config.private_key_path.read_text(encoding='utf-8')
        now = int(time.time())
        payload = {
            'iat': now - 60,
            'exp': now + 540,
            'iss': self._config.app_id,
        }
        token = jwt.encode(payload, private_key, algorithm='RS256')
        return token if isinstance(token, str) else token.decode('utf-8')


def _http_json_request(method: str, url: str, body: dict | None, headers: dict[str, str]) -> dict | list | str:
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = Request(url=url, data=data, method=method)
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode('utf-8')
            if not raw:
                return {}
            content_type = response.headers.get('Content-Type', '')
            if 'application/json' in content_type:
                return json.loads(raw)
            return raw
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace') if hasattr(exc, 'read') else ''
        raise RuntimeError(f'GitHub API HTTP {exc.code}: {detail}') from exc
    except URLError as exc:
        raise RuntimeError(f'GitHub API connection failed: {exc.reason}') from exc
