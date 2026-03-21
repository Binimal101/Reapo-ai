from __future__ import annotations

from ast_indexer.ports.oauth import OAuthTokenRecord, OAuthTokenStorePort


class InMemoryOAuthTokenStoreAdapter(OAuthTokenStorePort):
    def __init__(self) -> None:
        self._records: dict[str, OAuthTokenRecord] = {}

    def save(self, token: OAuthTokenRecord) -> None:
        self._records[token.user_id] = token

    def get(self, user_id: str) -> OAuthTokenRecord | None:
        return self._records.get(user_id)

    def list_user_ids(self) -> list[str]:
        return sorted(self._records.keys())
