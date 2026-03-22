from pathlib import Path

from ast_indexer.adapters.repository.hybrid_repository_reader_adapter import HybridRepositoryReaderAdapter
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