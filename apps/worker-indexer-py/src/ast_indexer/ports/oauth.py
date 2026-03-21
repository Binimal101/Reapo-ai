from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class OAuthTokenRecord:
    user_id: str
    access_token: str
    expires_at: datetime
    scopes: tuple[str, ...]


class OAuthTokenStorePort(Protocol):
    def save(self, token: OAuthTokenRecord) -> None:
        """Persist oauth token record."""

    def get(self, user_id: str) -> OAuthTokenRecord | None:
        """Fetch oauth token for user if available."""
