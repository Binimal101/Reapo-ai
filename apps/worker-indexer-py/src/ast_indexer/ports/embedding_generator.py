from __future__ import annotations

from typing import Protocol


class EmbeddingGeneratorPort(Protocol):
    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        """Generate embedding vectors for a list of texts."""