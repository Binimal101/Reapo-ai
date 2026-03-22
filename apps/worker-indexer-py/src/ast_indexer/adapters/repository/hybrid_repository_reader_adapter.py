from __future__ import annotations

from typing import Callable
from uuid import uuid4

from ast_indexer.adapters.repository.github_api_repository_reader_adapter import GithubApiRepositoryReaderAdapter
from ast_indexer.ports.repository_reader import RepositoryFile, RepositoryReaderPort


class HybridRepositoryReaderAdapter(RepositoryReaderPort):
    def __init__(
        self,
        *,
        local_reader: RepositoryReaderPort,
        github_token_provider: Callable[[str], str] | None,
        http_json: Callable[[str, str, dict | None, dict[str, str]], dict | list | str],
    ) -> None:
        self._local_reader = local_reader
        self._github_token_provider = github_token_provider
        self._http_json = http_json

    def list_python_files(self, repo: str) -> list[str]:
        reader = self._resolve_reader(repo)
        return reader.list_python_files(repo)

    def read_python_file(self, repo: str, path: str) -> RepositoryFile:
        reader = self._resolve_reader(repo)
        return reader.read_python_file(repo, path)

    def _resolve_reader(self, repo: str) -> RepositoryReaderPort:
        if '/' not in repo:
            return self._local_reader

        if self._github_token_provider is None:
            raise ValueError('github_repository_reader_not_configured')

        token = self._github_token_provider(repo)
        if not token.strip():
            raise ValueError('github_repository_token_unavailable')

        return GithubApiRepositoryReaderAdapter(token=token, http_json=self._http_json)


def build_github_app_token_provider(github_app_auth_service):  # noqa: ANN001, ANN201
    def _provide(repo: str) -> str:
        owner, name = repo.split('/', 1)
        trace_id = uuid4().hex
        installation_id = github_app_auth_service.resolve_installation_id_for_repo(
            trace_id=trace_id,
            owner=owner,
            repo=name,
        )
        payload = github_app_auth_service.create_installation_access_token(
            trace_id=trace_id,
            installation_id=installation_id,
        )
        token = payload.get('token') if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise ValueError('github_installation_token_missing')
        return token

    return _provide