from __future__ import annotations

from ast_indexer.adapters.repository.github_api_repository_reader_adapter import GithubApiRepositoryReaderAdapter


def test_list_python_files_filters_blob_python_paths() -> None:
    def _fake_http_json(method: str, url: str, body: dict | None, headers: dict[str, str]) -> dict:  # noqa: ARG001
        assert method == 'GET'
        assert '/git/trees/HEAD?recursive=1' in url
        assert headers['Authorization'] == 'Bearer token-123'
        return {
            'tree': [
                {'type': 'blob', 'path': 'src/a.py'},
                {'type': 'blob', 'path': 'README.md'},
                {'type': 'tree', 'path': 'src'},
                {'type': 'blob', 'path': 'src/sub/b.py'},
            ]
        }

    adapter = GithubApiRepositoryReaderAdapter(token='token-123', http_json=_fake_http_json)
    files = adapter.list_python_files('owner/repo')

    assert files == ['src/a.py', 'src/sub/b.py']


def test_read_python_file_returns_downloaded_plain_text_content() -> None:
    calls: list[str] = []

    def _fake_http_json(method: str, url: str, body: dict | None, headers: dict[str, str]) -> dict | str:  # noqa: ARG001
        calls.append(url)
        if '/contents/src/a.py' in url:
            return {'download_url': 'https://raw.githubusercontent.com/owner/repo/src/a.py'}
        if 'raw.githubusercontent.com' in url:
            return 'def run() -> str:\n    return "ok"\n'
        raise AssertionError(f'unexpected url: {url}')

    adapter = GithubApiRepositoryReaderAdapter(token='token-123', http_json=_fake_http_json)
    file_record = adapter.read_python_file('owner/repo', 'src/a.py')

    assert file_record.repo == 'owner/repo'
    assert file_record.path == 'src/a.py'
    assert 'def run' in file_record.content
    assert len(calls) == 2


def test_read_python_file_requires_download_url() -> None:
    def _fake_http_json(method: str, url: str, body: dict | None, headers: dict[str, str]) -> dict:  # noqa: ARG001
        if '/contents/src/a.py' in url:
            return {'name': 'a.py'}
        raise AssertionError(f'unexpected url: {url}')

    adapter = GithubApiRepositoryReaderAdapter(token='token-123', http_json=_fake_http_json)

    try:
        adapter.read_python_file('owner/repo', 'src/a.py')
        raise AssertionError('expected ValueError')
    except ValueError as exc:
        assert 'download_url' in str(exc)
