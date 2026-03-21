from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ast_indexer.adapters.oauth.encrypted_file_oauth_token_store_adapter import EncryptedFileOAuthTokenStoreAdapter
from ast_indexer.ports.oauth import OAuthTokenRecord


def test_encrypted_store_round_trip(tmp_path: Path) -> None:
    pytest.importorskip('cryptography')
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode('utf-8')
    store = EncryptedFileOAuthTokenStoreAdapter(tmp_path / 'oauth_tokens.enc', key)

    token = OAuthTokenRecord(
        user_id='octocat',
        access_token='token-123',
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        scopes=('repo', 'contents:read'),
    )
    store.save(token)

    loaded = store.get('octocat')
    assert loaded is not None
    assert loaded.user_id == 'octocat'
    assert loaded.access_token == 'token-123'
    assert loaded.scopes == ('repo', 'contents:read')

    persisted = (tmp_path / 'oauth_tokens.enc').read_text(encoding='utf-8', errors='ignore')
    assert 'token-123' not in persisted


def test_encrypted_store_rejects_wrong_key(tmp_path: Path) -> None:
    pytest.importorskip('cryptography')
    from cryptography.fernet import Fernet

    key_a = Fernet.generate_key().decode('utf-8')
    key_b = Fernet.generate_key().decode('utf-8')

    store_a = EncryptedFileOAuthTokenStoreAdapter(tmp_path / 'oauth_tokens.enc', key_a)
    store_a.save(
        OAuthTokenRecord(
            user_id='octocat',
            access_token='secret-token',
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            scopes=('repo',),
        )
    )

    store_b = EncryptedFileOAuthTokenStoreAdapter(tmp_path / 'oauth_tokens.enc', key_b)
    with pytest.raises(RuntimeError, match='Unable to decrypt OAuth token store payload'):
        _ = store_b.get('octocat')
