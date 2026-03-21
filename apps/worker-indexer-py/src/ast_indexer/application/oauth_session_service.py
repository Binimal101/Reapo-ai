from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from ast_indexer.ports.oauth import OAuthTokenRecord, OAuthTokenStorePort
from ast_indexer.ports.observability import ObservabilityPort


class OAuthSessionService:
    def __init__(self, token_store: OAuthTokenStorePort, observability: ObservabilityPort) -> None:
        self._token_store = token_store
        self._observability = observability

    def save_token(
        self,
        trace_id: str,
        user_id: str,
        access_token: str,
        scopes: tuple[str, ...],
        expires_in_seconds: int,
        refresh_token: str | None = None,
    ) -> OAuthTokenRecord:
        span = self._observability.start_span(
            name='oauth_save_token',
            trace_id=trace_id,
            input_payload={
                'user_id': user_id,
                'scopes': list(scopes),
                'expires_in_seconds': expires_in_seconds,
                'has_refresh_token': bool(refresh_token),
            },
        )

        token = OAuthTokenRecord(
            user_id=user_id,
            access_token=access_token,
            scopes=scopes,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
            refresh_token=refresh_token,
        )
        self._token_store.save(token)
        self._observability.end_span(
            span,
            output_payload={
                'user_id': token.user_id,
                'expires_at': token.expires_at.isoformat(),
            },
        )
        return token

    def get_valid_token(self, trace_id: str, user_id: str) -> OAuthTokenRecord | None:
        span = self._observability.start_span(
            name='oauth_get_valid_token',
            trace_id=trace_id,
            input_payload={'user_id': user_id},
        )

        token = self._token_store.get(user_id)
        now = datetime.now(timezone.utc)
        if token is None or token.expires_at <= now:
            self._observability.end_span(span, output_payload={'user_id': user_id, 'is_valid': False})
            return None

        self._observability.end_span(span, output_payload={'user_id': user_id, 'is_valid': True})
        return token

    def get_valid_token_with_refresh(
        self,
        trace_id: str,
        user_id: str,
        refresh: Callable[[str], dict],
    ) -> OAuthTokenRecord | None:
        token = self._token_store.get(user_id)
        now = datetime.now(timezone.utc)
        if token is None:
            return None
        if token.expires_at > now:
            return token
        if not token.refresh_token:
            return None

        refreshed = refresh(token.refresh_token)
        if not isinstance(refreshed, dict):
            return None

        access_token = refreshed.get('access_token')
        if not isinstance(access_token, str) or not access_token:
            return None

        scope_raw = refreshed.get('scope')
        scopes = tuple(scope.strip() for scope in scope_raw.split(',') if scope.strip()) if isinstance(scope_raw, str) else token.scopes
        expires_in = refreshed.get('expires_in')
        expires_in_seconds = int(expires_in) if isinstance(expires_in, int) else 3600
        refresh_token = refreshed.get('refresh_token') if isinstance(refreshed.get('refresh_token'), str) else token.refresh_token

        return self.save_token(
            trace_id=trace_id,
            user_id=user_id,
            access_token=access_token,
            scopes=scopes,
            expires_in_seconds=expires_in_seconds,
            refresh_token=refresh_token,
        )
