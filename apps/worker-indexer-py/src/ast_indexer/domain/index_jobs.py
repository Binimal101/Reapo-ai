from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndexJob:
    repo: str
    changed_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    trace_id: str
    repo_full_name: str | None = None
    attempt: int = 0
    max_attempts: int = 3
    source: str = 'github_push'


@dataclass(frozen=True)
class DeadLetterIndexJob:
    job: IndexJob
    reason: str
