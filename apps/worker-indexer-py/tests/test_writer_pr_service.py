from __future__ import annotations

from ast_indexer.application.writer_pr_service import WriterFileChange, WriterPrService


class _FakeGithubAuth:
    def resolve_installation_id_for_repo(self, trace_id: str, owner: str, repo: str) -> int:  # noqa: ARG002
        return 123

    def create_installation_access_token(self, trace_id: str, installation_id: int) -> dict:  # noqa: ARG002
        return {
            'token': 'installation-token',
            'permissions': {'contents': 'write'},
        }


def test_writer_service_dry_run_returns_plan() -> None:
    calls: list[tuple[str, str]] = []

    def _http_json(method: str, url: str, body: dict | None, headers: dict[str, str]) -> dict | list | str:  # noqa: ARG001
        calls.append((method, url))
        return {}

    service = WriterPrService(github_auth=_FakeGithubAuth(), http_json=_http_json)
    result = service.open_pull_request(
        trace_id='trace-1',
        owner='acme',
        repo='checkout',
        base_branch='main',
        title='Fix checkout bug',
        body='Applies fix.',
        files=[WriterFileChange(path='src/checkout.py', content='print(1)')],
        dry_run=True,
    )

    assert result['status'] == 'ok'
    assert result['mode'] == 'dry_run'
    assert result['files_changed'] == 1
    assert result['upserted_paths'] == ['src/checkout.py']
    assert result['deleted_paths'] == []
    assert calls == []


def test_writer_service_reuses_existing_open_pr() -> None:
    def _http_json(method: str, url: str, body: dict | None, headers: dict[str, str]) -> dict | list | str:  # noqa: ARG001
        if method == 'GET' and '/git/ref/heads/main' in url:
            return {'object': {'sha': 'sha-main'}}
        if method == 'GET' and '/git/ref/heads/reapo-ai%2Ffix-branch' in url:
            return {'object': {'sha': 'sha-branch'}}
        if method == 'GET' and '/contents/src/checkout.py' in url:
            return {'sha': 'file-sha'}
        if method == 'PUT' and '/contents/src/checkout.py' in url:
            return {'content': {'path': 'src/checkout.py'}}
        if method == 'GET' and '/pulls?state=open' in url:
            return [{'number': 44, 'html_url': 'https://github.com/acme/checkout/pull/44'}]
        raise AssertionError(f'unexpected call: {method} {url} body={body}')

    service = WriterPrService(github_auth=_FakeGithubAuth(), http_json=_http_json)
    result = service.open_pull_request(
        trace_id='trace-2',
        owner='acme',
        repo='checkout',
        base_branch='main',
        title='Fix checkout bug',
        body='Applies fix.',
        branch_name='reapo-ai/fix-branch',
        files=[WriterFileChange(path='src/checkout.py', content='print(2)')],
        dry_run=False,
    )

    assert result['status'] == 'ok'
    assert result['mode'] == 'applied'
    assert result['pull_request']['reused'] is True
    assert result['pull_request']['number'] == 44
    assert result['files_changed'] == 1


def test_writer_service_supports_delete_operation() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def _http_json(method: str, url: str, body: dict | None, headers: dict[str, str]) -> dict | list | str:  # noqa: ARG001
        calls.append((method, url, body))
        if method == 'GET' and '/git/ref/heads/main' in url:
            return {'object': {'sha': 'sha-main'}}
        if method == 'GET' and '/git/ref/heads/reapo-ai%2Fdelete-branch' in url:
            return {'object': {'sha': 'sha-branch'}}
        if method == 'GET' and '/contents/src/legacy.py' in url:
            return {'sha': 'legacy-sha'}
        if method == 'DELETE' and '/contents/src/legacy.py' in url:
            return {'content': None}
        if method == 'GET' and '/pulls?state=open' in url:
            return []
        if method == 'POST' and '/pulls' in url:
            return {'number': 45, 'html_url': 'https://github.com/acme/checkout/pull/45'}
        raise AssertionError(f'unexpected call: {method} {url} body={body}')

    service = WriterPrService(github_auth=_FakeGithubAuth(), http_json=_http_json)
    result = service.open_pull_request(
        trace_id='trace-3',
        owner='acme',
        repo='checkout',
        base_branch='main',
        title='Remove legacy file',
        body='Deletes stale code path.',
        branch_name='reapo-ai/delete-branch',
        files=[WriterFileChange(path='src/legacy.py', content='', operation='delete')],
        dry_run=False,
    )

    assert result['status'] == 'ok'
    assert result['mode'] == 'applied'
    assert result['files_changed'] == 1
    assert result['operations']['delete'] == 1
    assert result['deleted_paths'] == ['src/legacy.py']
    assert result['upserted_paths'] == []
    assert any(method == 'DELETE' and '/contents/src/legacy.py' in url for method, url, _ in calls)
