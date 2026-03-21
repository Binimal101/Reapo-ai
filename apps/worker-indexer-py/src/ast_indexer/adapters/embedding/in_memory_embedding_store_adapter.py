from __future__ import annotations

from ast_indexer.domain.models import EmbeddingRecord


class InMemoryEmbeddingStoreAdapter:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str, str], EmbeddingRecord] = {}

    def upsert_embeddings(self, records: list[EmbeddingRecord]) -> None:
        for record in records:
            key = (record.repo, record.path, record.kind, record.symbol)
            self._rows[key] = record

    def list_embeddings(self) -> list[EmbeddingRecord]:
        return list(self._rows.values())
