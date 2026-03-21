from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PushDelta:
    repo: str
    repo_full_name: str
    changed_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]


class GithubPushPayloadResolver:
    def resolve(self, payload: dict) -> PushDelta:
        repo_info = payload.get('repository', {})
        repo = repo_info.get('name') or repo_info.get('full_name', '').split('/')[-1]
        if not repo:
            raise ValueError('Missing repository name in push payload')
        full_name = repo_info.get('full_name') if isinstance(repo_info.get('full_name'), str) else ''
        if not full_name:
            owner = ''
            owner_info = repo_info.get('owner') if isinstance(repo_info, dict) else None
            if isinstance(owner_info, dict):
                owner = owner_info.get('login') or owner_info.get('name') or ''
            full_name = f'{owner}/{repo}'.strip('/') if owner else repo

        changed: set[str] = set()
        deleted: set[str] = set()

        commits = payload.get('commits', [])
        for commit in commits:
            for path in commit.get('added', []):
                if path.endswith('.py'):
                    changed.add(path)
            for path in commit.get('modified', []):
                if path.endswith('.py'):
                    changed.add(path)
            for path in commit.get('removed', []):
                if path.endswith('.py'):
                    deleted.add(path)

        # Removed files should not remain in changed set for this run.
        changed -= deleted

        return PushDelta(
            repo=repo,
            repo_full_name=full_name,
            changed_paths=tuple(sorted(changed)),
            deleted_paths=tuple(sorted(deleted)),
        )
