from __future__ import annotations

from ast_indexer.ports.embedding_generator import EmbeddingGeneratorPort


class SentenceTransformersEmbeddingGeneratorAdapter(EmbeddingGeneratorPort):
    """Semantic embedding adapter powered by sentence-transformers."""

    def __init__(
        self,
        model_name: str = 'sentence-transformers/all-MiniLM-L6-v2',
        device: str | None = None,
        normalize_embeddings: bool = True,
        batch_size: int = 32,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                'sentence-transformers is not installed. '
                'Install with: pip install "ast-indexer[embeddings]"'
            ) from exc

        self._model = SentenceTransformer(model_name, device=device)
        self._normalize_embeddings = normalize_embeddings
        self._batch_size = batch_size

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []

        vectors = self._model.encode(
            texts,
            batch_size=self._batch_size,
            convert_to_numpy=True,
            normalize_embeddings=self._normalize_embeddings,
            show_progress_bar=False,
        )
        return [tuple(float(value) for value in row.tolist()) for row in vectors]