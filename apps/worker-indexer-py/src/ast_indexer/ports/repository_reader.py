from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RepositoryFile:
    repo: str
    path: str
    content: str


class RepositoryReaderPort(Protocol):
    def list_python_files(self, repo: str) -> list[str]:
        """List python file paths for the given repository root."""

    def read_python_file(self, repo: str, path: str) -> RepositoryFile:
        """Read a python file from repository root and return content."""
