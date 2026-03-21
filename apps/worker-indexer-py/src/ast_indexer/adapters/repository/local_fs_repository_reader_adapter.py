from __future__ import annotations

from pathlib import Path

from ast_indexer.ports.repository_reader import RepositoryFile, RepositoryReaderPort


class LocalFsRepositoryReaderAdapter(RepositoryReaderPort):
    def __init__(self, base_path: Path) -> None:
        self._base_path = base_path

    def list_python_files(self, repo: str) -> list[str]:
        root = (self._base_path / repo).resolve()
        if not root.exists():
            return []

        return [
            str(path.relative_to(root)).replace('\\', '/')
            for path in root.rglob('*.py')
            if path.is_file()
        ]

    def read_python_file(self, repo: str, path: str) -> RepositoryFile:
        root = (self._base_path / repo).resolve()
        target = (root / path).resolve()
        content = target.read_text(encoding='utf-8')
        return RepositoryFile(repo=repo, path=path, content=content)
