from __future__ import annotations

import hashlib
import math
import struct


class StubEmbeddingAdapter:
    """
    Deterministic stub embedder for local development and testing.

    Produces unit-normalised 8-dimensional vectors derived from SHA-256 of
    the input text.  Same text always yields the same vector; different texts
    almost always yield different vectors.  No external dependencies required.

    Replace with a real provider adapter (OpenAI, Cohere, local model, etc.)
    by implementing the same `model_name`, `dimensions`, `embed`, and
    `embed_batch` interface.
    """

    model_name: str = 'stub-v0'
    dimensions: int = 8

    def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode('utf-8')).digest()
        # Unpack 8 IEEE 754 floats from the first 32 bytes of the digest
        raw: tuple[float, ...] = struct.unpack('8f', digest[:32])
        magnitude = math.sqrt(sum(x * x for x in raw))
        if magnitude == 0.0:
            return [0.0] * self.dimensions
        return [x / magnitude for x in raw]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]
