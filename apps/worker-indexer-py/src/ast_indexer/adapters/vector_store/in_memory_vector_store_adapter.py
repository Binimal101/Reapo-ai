from __future__ import annotations

from ast_indexer.domain.models import VectorRecord
from ast_indexer.ports.vector_store import VectorStorePort


class InMemoryVectorStoreAdapter(VectorStorePort):
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str, str], VectorRecord] = {}

    def upsert_vectors(self, vectors: list[VectorRecord]) -> None:
        for vector in vectors:
            key = (vector.repo, vector.path, vector.kind, vector.symbol)
            self._rows[key] = vector

    def delete_vectors_for_paths(self, repo: str, paths: list[str]) -> int:
        if not paths:
            return 0

        path_set = set(paths)
        before = len(self._rows)
        self._rows = {
            key: value
            for key, value in self._rows.items()
            if not (value.repo == repo and value.path in path_set)
        }
        return before - len(self._rows)

    def list_vectors(self) -> list[VectorRecord]:
        return list(self._rows.values())