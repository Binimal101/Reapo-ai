from __future__ import annotations

import os

from ast_indexer.ports.embedding_generator import EmbeddingGeneratorPort


class OpenAIEmbeddingGeneratorAdapter(EmbeddingGeneratorPort):
    """Embedding adapter backed by the OpenAI embeddings API."""

    def __init__(
        self,
        model_name: str = 'text-embedding-3-small',
        api_key: str | None = None,
        base_url: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                'openai package is not installed. '
                'Install with: pip install "ast-indexer[embeddings]"'
            ) from exc

        resolved_api_key = api_key or os.getenv('OPENAI_API_KEY')
        if not resolved_api_key:
            raise RuntimeError('OPENAI_API_KEY is required for embedding-backend=openai')

        self._client = OpenAI(api_key=resolved_api_key, base_url=base_url)
        self._model_name = model_name
        self._dimensions = dimensions

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []

        request: dict[str, object] = {
            'model': self._model_name,
            'input': texts,
        }
        if self._dimensions is not None:
            request['dimensions'] = self._dimensions

        response = self._client.embeddings.create(**request)
        return [tuple(float(value) for value in row.embedding) for row in response.data]