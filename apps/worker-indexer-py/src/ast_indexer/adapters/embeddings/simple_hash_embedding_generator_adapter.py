from __future__ import annotations

import hashlib

from ast_indexer.ports.embedding_generator import EmbeddingGeneratorPort


class SimpleHashEmbeddingGeneratorAdapter(EmbeddingGeneratorPort):
    """Deterministic local embedding adapter for offline/dev indexing."""

    def __init__(self, dimensions: int = 16) -> None:
        self._dimensions = dimensions

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        vectors: list[tuple[float, ...]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode('utf-8')).digest()
            values = []
            for idx in range(self._dimensions):
                byte_value = digest[idx % len(digest)]
                values.append((byte_value / 255.0) * 2.0 - 1.0)
            vectors.append(tuple(values))
        return vectors