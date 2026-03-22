from __future__ import annotations

import re
from pathlib import Path
from typing import Callable


REPO_AGENT_TOOL_SCHEMAS: list[dict] = [
    {
        'type': 'function',
        'function': {
            'name': 'get_folder_structure',
            'description': (
                'List files and folders for a repository path with pagination. '
                'Use this for broad exploration before reading specific files.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'repo': {
                        'type': 'string',
                        'description': 'Repository name in scope. Prefer owner/repo when available.',
                    },
                    'path': {
                        'type': 'string',
                        'description': 'Optional repository-relative directory path. Defaults to root.',
                    },
                    'page': {
                        'type': 'integer',
                        'description': '1-based page number.',
                        'minimum': 1,
                    },
                    'page_size': {
                        'type': 'integer',
                        'description': 'Entries per page. Max 200.',
                        'minimum': 1,
                        'maximum': 200,
                    },
                },
                'required': ['repo'],
                'additionalProperties': False,
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_file_contents',
            'description': (
                'Read file content by line range. Must include max_tokens; returns an error '
                'if estimated token count for requested range exceeds max_tokens.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'repo': {
                        'type': 'string',
                        'description': 'Repository name in scope. Prefer owner/repo when available.',
                    },
                    'path': {
                        'type': 'string',
                        'description': 'Repository-relative file path.',
                    },
                    'line_beginning': {
                        'type': 'integer',
                        'description': '1-based first line to include.',
                        'minimum': 1,
                    },
                    'line_ending': {
                        'type': 'integer',
                        'description': '1-based last line to include (inclusive).',
                        'minimum': 1,
                    },
                    'max_tokens': {
                        'type': 'integer',
                        'description': 'Maximum estimated output token budget. Hard limit enforced.',
                        'minimum': 1,
                        'maximum': 12000,
                    },
                },
                'required': ['repo', 'path', 'line_beginning', 'line_ending', 'max_tokens'],
                'additionalProperties': False,
            },
        },
    },
]


def build_repo_agent_tool_handlers(workspace_root: Path) -> dict[str, Callable[..., dict]]:
    root = workspace_root.resolve()

    def _get_folder_structure(
        *,
        repo: str,
        path: str = '',
        page: int = 1,
        page_size: int = 100,
    ) -> dict:
        repo_root = _resolve_repo_root(root, repo)
        directory = _safe_repo_path(repo_root, path or '.')
        if not directory.exists() or not directory.is_dir():
            return {
                'ok': False,
                'error': 'directory_not_found',
                'repo': repo,
                'path': path or '.',
            }

        page_clean = max(1, int(page))
        size_clean = max(1, min(200, int(page_size)))

        children = sorted(
            directory.iterdir(),
            key=lambda item: (0 if item.is_dir() else 1, item.name.lower()),
        )
        total_entries = len(children)
        start = (page_clean - 1) * size_clean
        end = start + size_clean

        entries: list[dict] = []
        for child in children[start:end]:
            entries.append(
                {
                    'name': child.name,
                    'type': 'directory' if child.is_dir() else 'file',
                    'path': str(child.relative_to(repo_root)).replace('\\', '/'),
                }
            )

        return {
            'ok': True,
            'repo': repo,
            'path': str(directory.relative_to(repo_root)).replace('\\', '/') or '.',
            'page': page_clean,
            'page_size': size_clean,
            'total_entries': total_entries,
            'has_more': end < total_entries,
            'entries': entries,
        }

    def _get_file_contents(
        *,
        repo: str,
        path: str,
        line_beginning: int,
        line_ending: int,
        max_tokens: int,
    ) -> dict:
        repo_root = _resolve_repo_root(root, repo)
        file_path = _safe_repo_path(repo_root, path)
        if not file_path.exists() or not file_path.is_file():
            return {
                'ok': False,
                'error': 'file_not_found',
                'repo': repo,
                'path': path,
            }

        start = max(1, int(line_beginning))
        end = max(1, int(line_ending))
        if end < start:
            return {
                'ok': False,
                'error': 'invalid_line_range',
                'line_beginning': start,
                'line_ending': end,
            }

        max_tokens_clean = max(1, min(12000, int(max_tokens)))

        lines = file_path.read_text(encoding='utf-8', errors='replace').splitlines()
        total_lines = len(lines)
        start_idx = min(start - 1, total_lines)
        end_idx = min(end, total_lines)
        slice_lines = lines[start_idx:end_idx]
        content = '\n'.join(slice_lines)
        estimated_tokens = _estimate_tokens(content)

        if estimated_tokens > max_tokens_clean:
            return {
                'ok': False,
                'error': 'max_tokens_exceeded',
                'repo': repo,
                'path': path,
                'line_beginning': start,
                'line_ending': end,
                'estimated_tokens': estimated_tokens,
                'max_tokens': max_tokens_clean,
            }

        return {
            'ok': True,
            'repo': repo,
            'path': str(file_path.relative_to(repo_root)).replace('\\', '/'),
            'line_beginning': start,
            'line_ending': min(end, total_lines),
            'total_lines': total_lines,
            'estimated_tokens': estimated_tokens,
            'max_tokens': max_tokens_clean,
            'content': content,
        }

    return {
        'get_folder_structure': _get_folder_structure,
        'get_file_contents': _get_file_contents,
    }


def _resolve_repo_root(workspace_root: Path, repo: str) -> Path:
    repo_clean = str(repo or '').strip().replace('\\', '/').strip('/')
    if not repo_clean:
        raise ValueError('repo is required')

    candidate = (workspace_root / repo_clean).resolve()
    if candidate.exists() and candidate.is_dir() and _is_within(candidate, workspace_root):
        return candidate

    short_name = repo_clean.split('/')[-1]
    short_candidate = (workspace_root / short_name).resolve()
    if short_candidate.exists() and short_candidate.is_dir() and _is_within(short_candidate, workspace_root):
        return short_candidate

    raise ValueError(f'repository not found in workspace: {repo_clean}')


def _safe_repo_path(repo_root: Path, relative_path: str) -> Path:
    rel = _normalize_relative_path(relative_path)
    target = (repo_root / rel).resolve()
    if not _is_within(target, repo_root):
        raise ValueError('path traversal is not allowed')
    return target


def _normalize_relative_path(raw: str) -> Path:
    text = str(raw or '').strip()
    if not text:
        return Path('.')
    normalized = text.replace('\\', '/').strip('/')
    candidate = Path(normalized)
    if candidate.is_absolute() or '..' in candidate.parts:
        raise ValueError('path must be repository-relative')
    return candidate


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # Approximation for guardrails: split by words/punctuation to avoid undercounting dense code.
    return len(re.findall(r"\w+|[^\w\s]", text))
