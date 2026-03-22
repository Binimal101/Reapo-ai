from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from ast_indexer.application import runtime_config

class GithubWriteAuthPort(Protocol):
    def resolve_installation_id_for_repo(self, trace_id: str, owner: str, repo: str) -> int:
        ...

    def create_installation_access_token(self, trace_id: str, installation_id: int) -> dict:
        ...


@dataclass(frozen=True)
class WriterFileChange:
    path: str
    content: str
    operation: str = 'upsert'


class WriterPrService:
    def __init__(
        self,
        *,
        github_auth: GithubWriteAuthPort,
        http_json: Callable[[str, str, dict | None, dict[str, str]], dict | list | str] | None = None,
    ) -> None:
        self._github_auth = github_auth
        self._http_json = http_json or _http_json_request
        self._github_api_base_url = runtime_config.github_api_base_url()

    def open_pull_request(
        self,
        *,
        trace_id: str,
        owner: str,
        repo: str,
        base_branch: str,
        title: str,
        body: str,
        files: list[WriterFileChange],
        branch_name: str | None = None,
        commit_message: str = 'chore: apply automated code changes',
        draft: bool = False,
        dry_run: bool = False,
    ) -> dict:
        if not owner.strip() or not repo.strip():
            raise ValueError('owner and repo are required')
        if not title.strip():
            raise ValueError('title is required')
        if not files:
            raise ValueError('at least one file change is required')

        token_payload = self._github_auth.create_installation_access_token(
            trace_id=trace_id,
            installation_id=self._github_auth.resolve_installation_id_for_repo(
                trace_id=trace_id,
                owner=owner,
                repo=repo,
            ),
        )
        permissions = token_payload.get('permissions', {}) if isinstance(token_payload, dict) else {}
        if isinstance(permissions, dict) and permissions.get('contents') not in ('write', 'admin'):
            raise PermissionError('insufficient_repo_permission: contents:write required')

        token = token_payload.get('token') if isinstance(token_payload, dict) else None
        if not isinstance(token, str) or not token:
            raise ValueError('missing installation token')

        target_branch = branch_name.strip() if isinstance(branch_name, str) and branch_name.strip() else self._default_branch_name(title)
        if dry_run:
            upsert_count = sum(1 for item in files if str(getattr(item, 'operation', 'upsert')).strip().lower() != 'delete')
            delete_count = len(files) - upsert_count
            upsert_paths = [
                item.path for item in files if str(getattr(item, 'operation', 'upsert')).strip().lower() != 'delete'
            ]
            delete_paths = [
                item.path for item in files if str(getattr(item, 'operation', 'upsert')).strip().lower() == 'delete'
            ]
            return {
                'status': 'ok',
                'mode': 'dry_run',
                'owner': owner,
                'repo': repo,
                'base_branch': base_branch,
                'target_branch': target_branch,
                'files_changed': len(files),
                'changed_paths': [item.path for item in files],
                'upserted_paths': upsert_paths,
                'deleted_paths': delete_paths,
                'operations': {
                    'upsert': upsert_count,
                    'delete': delete_count,
                },
                'title': title,
            }

        headers = {
            'Accept': 'application/vnd.github+json',
            'Authorization': f'Bearer {token}',
            'X-GitHub-Api-Version': '2022-11-28',
            'Content-Type': 'application/json',
        }

        base_sha = self._resolve_ref_sha(owner=owner, repo=repo, branch=base_branch, headers=headers)
        branch_exists = self._branch_exists(owner=owner, repo=repo, branch=target_branch, headers=headers)
        branch_created = False
        if not branch_exists:
            self._http_json(
                'POST',
                f'{self._github_api_base_url}/repos/{owner}/{repo}/git/refs',
                {'ref': f'refs/heads/{target_branch}', 'sha': base_sha},
                headers,
            )
            branch_created = True

        changed_paths: list[str] = []
        deleted_paths: list[str] = []
        upserted_paths: list[str] = []
        for change in files:
            operation = str(change.operation).strip().lower() if isinstance(change.operation, str) else 'upsert'
            if operation not in {'upsert', 'delete'}:
                raise ValueError(f'unsupported file operation: {change.operation}')

            encoded_path = quote(change.path, safe='/')
            existing_sha = self._resolve_content_sha(
                owner=owner,
                repo=repo,
                path=encoded_path,
                branch=target_branch,
                headers=headers,
            )

            if operation == 'delete':
                if not existing_sha:
                    continue
                self._http_json(
                    'DELETE',
                    f'{self._github_api_base_url}/repos/{owner}/{repo}/contents/{encoded_path}',
                    {
                        'message': commit_message,
                        'sha': existing_sha,
                        'branch': target_branch,
                    },
                    headers,
                )
                changed_paths.append(change.path)
                deleted_paths.append(change.path)
                continue

            payload = {
                'message': commit_message,
                'content': base64.b64encode(change.content.encode('utf-8')).decode('ascii'),
                'branch': target_branch,
            }
            if existing_sha:
                payload['sha'] = existing_sha
            self._http_json(
                'PUT',
                f'{self._github_api_base_url}/repos/{owner}/{repo}/contents/{encoded_path}',
                payload,
                headers,
            )
            changed_paths.append(change.path)
            upserted_paths.append(change.path)

        existing_pr = self._find_open_pr(owner=owner, repo=repo, base=base_branch, branch=target_branch, headers=headers)
        if existing_pr is not None:
            return {
                'status': 'ok',
                'mode': 'applied',
                'owner': owner,
                'repo': repo,
                'base_branch': base_branch,
                'target_branch': target_branch,
                'branch_created': branch_created,
                'files_changed': len(changed_paths),
                'changed_paths': changed_paths,
                'upserted_paths': upserted_paths,
                'deleted_paths': deleted_paths,
                'operations': {
                    'upsert': len(upserted_paths),
                    'delete': len(deleted_paths),
                },
                'pull_request': {
                    'number': existing_pr.get('number'),
                    'html_url': existing_pr.get('html_url'),
                    'reused': True,
                },
            }

        created_pr = self._http_json(
            'POST',
            f'{self._github_api_base_url}/repos/{owner}/{repo}/pulls',
            {
                'title': title,
                'head': target_branch,
                'base': base_branch,
                'body': body,
                'draft': draft,
            },
            headers,
        )
        pr_payload = created_pr if isinstance(created_pr, dict) else {}
        return {
            'status': 'ok',
            'mode': 'applied',
            'owner': owner,
            'repo': repo,
            'base_branch': base_branch,
            'target_branch': target_branch,
            'branch_created': branch_created,
            'files_changed': len(changed_paths),
            'changed_paths': changed_paths,
            'upserted_paths': upserted_paths,
            'deleted_paths': deleted_paths,
            'operations': {
                'upsert': len(upserted_paths),
                'delete': len(deleted_paths),
            },
            'pull_request': {
                'number': pr_payload.get('number'),
                'html_url': pr_payload.get('html_url'),
                'reused': False,
            },
        }

    def _resolve_ref_sha(self, *, owner: str, repo: str, branch: str, headers: dict[str, str]) -> str:
        payload = self._http_json(
            'GET',
            f'{self._github_api_base_url}/repos/{owner}/{repo}/git/ref/heads/{quote(branch, safe="")}',
            None,
            headers,
        )
        if isinstance(payload, dict):
            obj = payload.get('object')
            if isinstance(obj, dict) and isinstance(obj.get('sha'), str):
                return str(obj['sha'])
        raise ValueError(f'unable to resolve base branch sha for {owner}/{repo}:{branch}')

    def _branch_exists(self, *, owner: str, repo: str, branch: str, headers: dict[str, str]) -> bool:
        try:
            payload = self._http_json(
                'GET',
                f'{self._github_api_base_url}/repos/{owner}/{repo}/git/ref/heads/{quote(branch, safe="")}',
                None,
                headers,
            )
            return isinstance(payload, dict)
        except RuntimeError as exc:
            return 'HTTP 404' not in str(exc)

    def _resolve_content_sha(
        self,
        *,
        owner: str,
        repo: str,
        path: str,
        branch: str,
        headers: dict[str, str],
    ) -> str | None:
        try:
            payload = self._http_json(
                'GET',
                f'{self._github_api_base_url}/repos/{owner}/{repo}/contents/{path}?ref={quote(branch, safe="")}',
                None,
                headers,
            )
        except RuntimeError as exc:
            if 'HTTP 404' in str(exc):
                return None
            raise

        if isinstance(payload, dict) and isinstance(payload.get('sha'), str):
            return str(payload.get('sha'))
        return None

    def _find_open_pr(
        self,
        *,
        owner: str,
        repo: str,
        base: str,
        branch: str,
        headers: dict[str, str],
    ) -> dict | None:
        payload = self._http_json(
            'GET',
            f'{self._github_api_base_url}/repos/{owner}/{repo}/pulls?state=open&head={quote(owner, safe="")}:{quote(branch, safe="")}&base={quote(base, safe="")}',
            None,
            headers,
        )
        if isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                return first
        return None

    def _default_branch_name(self, title: str) -> str:
        slug = ''.join(char.lower() if char.isalnum() else '-' for char in title).strip('-')
        if not slug:
            slug = 'writer-change'
        slug = '-'.join(part for part in slug.split('-') if part)[:40]
        return f'reapo-ai/{slug}-{uuid4().hex[:8]}'


def _http_json_request(method: str, url: str, body: dict | None, headers: dict[str, str]) -> dict | list | str:
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = Request(url=url, data=data, method=method)
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode('utf-8')
            if not raw:
                return {}
            content_type = response.headers.get('Content-Type', '')
            if 'application/json' in content_type:
                return json.loads(raw)
            return raw
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace') if hasattr(exc, 'read') else ''
        raise RuntimeError(f'GitHub API HTTP {exc.code}: {detail}') from exc
    except URLError as exc:
        raise RuntimeError(f'GitHub API connection failed: {exc.reason}') from exc
