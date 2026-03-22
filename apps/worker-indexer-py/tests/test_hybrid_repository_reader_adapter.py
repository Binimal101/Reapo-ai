from pathlib import Path
from datetime import datetime, timedelta, timezone

from ast_indexer.adapters.repository.hybrid_repository_reader_adapter import (
    HybridRepositoryReaderAdapter,
    build_github_app_token_provider,
)
from ast_indexer.adapters.repository.local_fs_repository_reader_adapter import LocalFsRepositoryReaderAdapter


def test_hybrid_reader_uses_local_reader_for_bare_repo_name(tmp_path: Path) -> None:
    repo_root = tmp_path / 'checkout-service' / 'src'
    repo_root.mkdir(parents=True)
    (repo_root / 'orders.py').write_text('def process(order_id):\n    return order_id\n', encoding='utf-8')

    reader = HybridRepositoryReaderAdapter(
        local_reader=LocalFsRepositoryReaderAdapter(tmp_path),
        github_token_provider=None,
        http_json=lambda *_args, **_kwargs: {},
    )

    files = reader.list_python_files('checkout-service')
    assert files == ['src/orders.py']


def test_hybrid_reader_uses_github_reader_for_owner_repo() -> None:
    calls: list[tuple[str, str]] = []

    def _http_json(method: str, url: str, body, headers):  # noqa: ANN001, ANN202
        calls.append((method, url))
        if url.endswith('/git/trees/HEAD?recursive=1'):
            return {
                'tree': [
                    {'type': 'blob', 'path': 'src/a.py'},
                    {'type': 'blob', 'path': 'README.md'},
                ]
            }
        if '/contents/' in url:
            return {'download_url': 'https://example.test/file'}
        if url == 'https://example.test/file':
            return 'def run():\n    return 1\n'
        raise AssertionError(f'unexpected url: {url}')

    reader = HybridRepositoryReaderAdapter(
        local_reader=LocalFsRepositoryReaderAdapter(Path('.')),
        github_token_provider=lambda _repo: 'ghs_test',
        http_json=_http_json,
    )

    files = reader.list_python_files('octo/sample')
    assert files == ['src/a.py']

    file_row = reader.read_python_file('octo/sample', 'src/a.py')
    assert file_row.repo == 'octo/sample'
    assert file_row.path == 'src/a.py'
    assert 'def run' in file_row.content
    assert calls


def test_hybrid_reader_requires_github_provider_for_owner_repo(tmp_path: Path) -> None:
    reader = HybridRepositoryReaderAdapter(
        local_reader=LocalFsRepositoryReaderAdapter(tmp_path),
        github_token_provider=None,
        http_json=lambda *_args, **_kwargs: {},
    )

    try:
        _ = reader.list_python_files('octo/sample')
    except ValueError as exc:
        assert 'github_repository_reader_not_configured' in str(exc)
        return

    raise AssertionError('expected ValueError when github token provider is missing')


def test_build_github_app_token_provider_caches_installation_and_token() -> None:
    class _FakeGithubAuth:
        def __init__(self) -> None:
            self.resolve_calls = 0
            self.create_calls = 0

        def resolve_installation_id_for_repo(self, *, trace_id: str, owner: str, repo: str) -> int:  # noqa: ARG002
            self.resolve_calls += 1
            return 123

        def create_installation_access_token(self, *, trace_id: str, installation_id: int) -> dict:  # noqa: ARG002
            self.create_calls += 1
            expires = datetime.now(timezone.utc) + timedelta(minutes=30)
            return {
                'token': f'ghs_token_{self.create_calls}',
                'expires_at': expires.isoformat(),
            }

    auth = _FakeGithubAuth()
    provider = build_github_app_token_provider(auth)

    token_first = provider('octo/sample')
    token_second = provider('octo/sample')

    assert token_first == 'ghs_token_1'
    assert token_second == 'ghs_token_1'
    assert auth.resolve_calls == 1
    assert auth.create_calls == 1


def test_build_github_app_token_provider_refreshes_expired_token() -> None:
    class _FakeGithubAuth:
        def __init__(self) -> None:
            self.resolve_calls = 0
            self.create_calls = 0

        def resolve_installation_id_for_repo(self, *, trace_id: str, owner: str, repo: str) -> int:  # noqa: ARG002
            self.resolve_calls += 1
            return 456

        def create_installation_access_token(self, *, trace_id: str, installation_id: int) -> dict:  # noqa: ARG002
            self.create_calls += 1
            if self.create_calls == 1:
                expires = datetime.now(timezone.utc) - timedelta(seconds=10)
            else:
                expires = datetime.now(timezone.utc) + timedelta(minutes=30)
            return {
                'token': f'ghs_token_{self.create_calls}',
                'expires_at': expires.isoformat(),
            }

    auth = _FakeGithubAuth()
    provider = build_github_app_token_provider(auth)

    token_first = provider('octo/sample')
    token_second = provider('octo/sample')

    assert token_first == 'ghs_token_1'
    assert token_second == 'ghs_token_2'
    assert auth.resolve_calls == 1
    assert auth.create_calls == 2