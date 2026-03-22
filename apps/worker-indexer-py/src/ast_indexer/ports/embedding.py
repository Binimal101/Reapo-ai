from __future__ import annotations

from typing import Protocol


class EmbeddingPort(Protocol):
    """Port for embedding text strings into float vectors."""

    model_name: str
    dimensions: int

    def embed(self, text: str) -> list[float]:
        """Embed a single text string. Returns a float vector of length `dimensions`."""

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns vectors in the same order as input."""


class EmbeddingStorePort(Protocol):
    """Port for persisting and retrieving embedding records."""

    def upsert_embeddings(self, records: list) -> None:
        """Insert or update embedding records."""

    def list_embeddings(self) -> list:
        """Return all stored embedding records."""
