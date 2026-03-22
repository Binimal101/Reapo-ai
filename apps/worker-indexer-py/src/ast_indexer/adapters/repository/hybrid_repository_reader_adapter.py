from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable
from uuid import uuid4

from ast_indexer.adapters.repository.github_api_repository_reader_adapter import GithubApiRepositoryReaderAdapter
from ast_indexer.ports.repository_reader import RepositoryFile, RepositoryReaderPort


@dataclass
class _CachedInstallationToken:
    token: str
    expires_at: datetime


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
    installation_ids_by_repo: dict[str, int] = {}
    tokens_by_installation: dict[int, _CachedInstallationToken] = {}
    cache_lock = RLock()
    refresh_skew = timedelta(seconds=120)

    def _parse_expires_at(raw: object) -> datetime:
        if isinstance(raw, str):
            parsed = raw.strip()
            if parsed.endswith('Z'):
                parsed = parsed[:-1] + '+00:00'
            try:
                value = datetime.fromisoformat(parsed)
                if value.tzinfo is None:
                    return value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc)
            except ValueError:
                pass
        # GitHub installation tokens are short-lived (typically 1 hour).
        return datetime.now(timezone.utc) + timedelta(minutes=55)

    def _provide(repo: str) -> str:
        repo_key = repo.strip()
        owner, name = repo_key.split('/', 1)

        with cache_lock:
            installation_id = installation_ids_by_repo.get(repo_key)

        if installation_id is None:
            trace_id = uuid4().hex
            installation_id = github_app_auth_service.resolve_installation_id_for_repo(
                trace_id=trace_id,
                owner=owner,
                repo=name,
            )
            with cache_lock:
                installation_ids_by_repo[repo_key] = installation_id

        now = datetime.now(timezone.utc)
        with cache_lock:
            cached_token = tokens_by_installation.get(installation_id)
            if cached_token is not None and (cached_token.expires_at - refresh_skew) > now:
                return cached_token.token

        trace_id = uuid4().hex
        payload = github_app_auth_service.create_installation_access_token(
            trace_id=trace_id,
            installation_id=installation_id,
        )
        token = payload.get('token') if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise ValueError('github_installation_token_missing')

        expires_at = _parse_expires_at(payload.get('expires_at') if isinstance(payload, dict) else None)
        with cache_lock:
            tokens_by_installation[installation_id] = _CachedInstallationToken(token=token, expires_at=expires_at)
        return token

    return _provide