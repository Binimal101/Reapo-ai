from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ast_indexer.ports.oauth import OAuthTokenRecord, OAuthTokenStorePort


class EncryptedFileOAuthTokenStoreAdapter(OAuthTokenStorePort):
    def __init__(self, file_path: Path, encryption_key: str) -> None:
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise RuntimeError(
                'Encrypted OAuth token storage requires cryptography. Install with: pip install cryptography'
            ) from exc

        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(encryption_key.encode('utf-8'))

    def save(self, token: OAuthTokenRecord) -> None:
        records = self._read_records()
        records[token.user_id] = {
            'user_id': token.user_id,
            'access_token': token.access_token,
            'expires_at': token.expires_at.isoformat(),
            'scopes': list(token.scopes),
        }
        self._write_records(records)

    def get(self, user_id: str) -> OAuthTokenRecord | None:
        records = self._read_records()
        row = records.get(user_id)
        if row is None:
            return None

        expires_at_raw = row.get('expires_at')
        if not isinstance(expires_at_raw, str):
            raise ValueError('Invalid encrypted OAuth token payload: expires_at missing')

        scopes_raw = row.get('scopes', [])
        if not isinstance(scopes_raw, list):
            raise ValueError('Invalid encrypted OAuth token payload: scopes must be a list')

        return OAuthTokenRecord(
            user_id=row.get('user_id', user_id),
            access_token=row.get('access_token', ''),
            expires_at=datetime.fromisoformat(expires_at_raw),
            scopes=tuple(scope for scope in scopes_raw if isinstance(scope, str)),
        )

    def _read_records(self) -> dict[str, dict]:
        if not self._file_path.exists():
            return {}

        encrypted = self._file_path.read_bytes()
        if not encrypted:
            return {}

        try:
            raw = self._fernet.decrypt(encrypted)
        except Exception as exc:
            raise RuntimeError('Unable to decrypt OAuth token store payload') from exc

        data = json.loads(raw.decode('utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Invalid encrypted OAuth token store payload')
        return data

    def _write_records(self, records: dict[str, dict]) -> None:
        raw = json.dumps(records, separators=(',', ':')).encode('utf-8')
        encrypted = self._fernet.encrypt(raw)
        self._file_path.write_bytes(encrypted)
