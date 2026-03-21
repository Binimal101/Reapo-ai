from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from ast_indexer.ports.repository_reader import RepositoryFile, RepositoryReaderPort


_DEFAULT_INDEXIGNORE_PATTERNS: tuple[str, ...] = (
    '__pycache__',
    '__pycache__/**',
    '**/__pycache__',
    '**/__pycache__/**',
    '.venv',
    '.venv/**',
    '**/.venv',
    '**/.venv/**',
    'venv',
    'venv/**',
    '**/venv',
    '**/venv/**',
    '**/*venv*',
    '**/*venv*/**',
    '**/.git',
    '**/.git/**',
    '**/node_modules',
    '**/node_modules/**',
)


class LocalFsRepositoryReaderAdapter(RepositoryReaderPort):
    def __init__(self, base_path: Path, indexignore_file: str = '.gitignore') -> None:
        self._base_path = base_path
        self._indexignore_file = indexignore_file

    def list_python_files(self, repo: str) -> list[str]:
        root = (self._base_path / repo).resolve()
        if not root.exists():
            return []

        patterns = self._load_indexignore_patterns(root)
        ignored = self._build_ignore_matcher(patterns)
        python_files: list[str] = []

        for current_root, dir_names, file_names in os.walk(root):
            current_path = Path(current_root)
            rel_dir = current_path.relative_to(root).as_posix()
            dir_prefix = '' if rel_dir == '.' else rel_dir

            kept_dirs: list[str] = []
            for dir_name in dir_names:
                rel_path = f'{dir_prefix}/{dir_name}' if dir_prefix else dir_name
                if ignored(rel_path):
                    continue
                kept_dirs.append(dir_name)
            dir_names[:] = kept_dirs

            for file_name in file_names:
                if not file_name.endswith('.py'):
                    continue

                rel_path = f'{dir_prefix}/{file_name}' if dir_prefix else file_name
                if ignored(rel_path):
                    continue
                python_files.append(rel_path)

        return sorted(python_files)

    def read_python_file(self, repo: str, path: str) -> RepositoryFile:
        root = (self._base_path / repo).resolve()
        target = (root / path).resolve()
        content = target.read_text(encoding='utf-8')
        return RepositoryFile(repo=repo, path=path, content=content)

    def _load_indexignore_patterns(self, root: Path) -> list[str]:
        patterns: list[str] = []
        indexignore_path = root / self._indexignore_file
        if not indexignore_path.exists():
            return patterns

        for raw_line in indexignore_path.read_text(encoding='utf-8').splitlines():
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue
            patterns.append(line.replace('\\', '/'))

        return patterns

    def _build_ignore_matcher(self, patterns: list[str]):
        def _ignored(relative_path: str) -> bool:
            normalized = relative_path.replace('\\', '/').strip('/')
            if not normalized:
                return False

            # Always honor hard default exclusions to prevent accidental large scans.
            for pattern in _DEFAULT_INDEXIGNORE_PATTERNS:
                candidate = pattern.strip().replace('\\', '/')
                if candidate.endswith('/'):
                    candidate = candidate.rstrip('/')
                if fnmatch.fnmatch(normalized, candidate):
                    return True
                if '/' not in candidate and fnmatch.fnmatch(Path(normalized).name, candidate):
                    return True

            ignored = False
            for pattern in patterns:
                candidate = pattern.strip().replace('\\', '/')
                if not candidate:
                    continue

                is_negated = candidate.startswith('!')
                if is_negated:
                    candidate = candidate[1:]
                    if not candidate:
                        continue

                is_anchored = candidate.startswith('/')
                if is_anchored:
                    candidate = candidate[1:]

                if candidate.endswith('/'):
                    candidate = candidate.rstrip('/')

                matched = False
                if is_anchored:
                    matched = fnmatch.fnmatch(normalized, candidate)
                elif '/' not in candidate:
                    matched = fnmatch.fnmatch(Path(normalized).name, candidate)
                else:
                    matched = fnmatch.fnmatch(normalized, candidate)

                if matched:
                    ignored = not is_negated

            return ignored

        return _ignored
