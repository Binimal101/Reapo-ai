from __future__ import annotations

from typing import Protocol

from ast_indexer.domain.models import VectorRecord


class VectorStorePort(Protocol):
    def upsert_vectors(self, vectors: list[VectorRecord]) -> None:
        """Insert or update vector records for symbols."""

    def delete_vectors_for_paths(self, repo: str, paths: list[str]) -> int:
        """Delete vectors for repo-scoped file paths and return number removed."""

    def list_vectors(self) -> list[VectorRecord]:
        """Return currently stored vectors."""