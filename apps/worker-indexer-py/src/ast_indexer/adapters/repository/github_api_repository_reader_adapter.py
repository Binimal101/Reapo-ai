from __future__ import annotations

from urllib.parse import quote

from ast_indexer.application import runtime_config
from ast_indexer.ports.repository_reader import RepositoryFile, RepositoryReaderPort


class GithubApiRepositoryReaderAdapter(RepositoryReaderPort):
    def __init__(self, token: str, http_json) -> None:  # noqa: ANN001
        self._token = token
        self._http_json = http_json
        self._github_api_base_url = runtime_config.github_api_base_url()

    def list_python_files(self, repo: str) -> list[str]:
        owner, name = self._split_repo(repo)
        tree = self._http_json(
            'GET',
            f'{self._github_api_base_url}/repos/{owner}/{name}/git/trees/HEAD?recursive=1',
            None,
            self._headers(),
        )
        nodes = tree.get('tree', []) if isinstance(tree, dict) else []
        paths: list[str] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get('type') != 'blob':
                continue
            path = node.get('path')
            if isinstance(path, str) and path.endswith('.py'):
                paths.append(path)
        return sorted(paths)

    def read_python_file(self, repo: str, path: str) -> RepositoryFile:
        owner, name = self._split_repo(repo)
        payload = self._http_json(
            'GET',
            f'{self._github_api_base_url}/repos/{owner}/{name}/contents/{quote(path)}',
            None,
            self._headers(),
        )
        download_url = payload.get('download_url') if isinstance(payload, dict) else None
        if not isinstance(download_url, str) or not download_url:
            raise ValueError('GitHub API did not return download_url for file')

        text_payload = self._http_json('GET', download_url, None, {'Accept': 'text/plain'})
        if isinstance(text_payload, dict):
            raise ValueError('Expected plain text payload from download_url')
        if not isinstance(text_payload, str):
            raise ValueError('Invalid file payload from GitHub API')
        return RepositoryFile(repo=repo, path=path, content=text_payload)

    def _headers(self) -> dict[str, str]:
        return {
            'Accept': 'application/vnd.github+json',
            'Authorization': f'Bearer {self._token}',
            'X-GitHub-Api-Version': '2022-11-28',
        }

    def _split_repo(self, repo: str) -> tuple[str, str]:
        if '/' not in repo:
            raise ValueError('GitHubApiRepositoryReaderAdapter expects repo in owner/name format')
        owner, name = repo.split('/', 1)
        return owner, name
