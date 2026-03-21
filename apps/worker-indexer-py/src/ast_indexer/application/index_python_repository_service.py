from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from ast_indexer.domain.models import IndexRunMetrics, VectorRecord
from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor
from ast_indexer.ports.embedding_generator import EmbeddingGeneratorPort
from ast_indexer.ports.index_store import IndexStorePort
from ast_indexer.ports.observability import ObservabilityPort
from ast_indexer.ports.repository_reader import RepositoryReaderPort
from ast_indexer.ports.vector_store import VectorStorePort


class IndexPythonRepositoryService:
    def __init__(
        self,
        repository_reader: RepositoryReaderPort,
        index_store: IndexStorePort,
        observability: ObservabilityPort,
        extractor: PythonAstSymbolExtractor,
        embedding_generator: EmbeddingGeneratorPort | None = None,
        vector_store: VectorStorePort | None = None,
        access_level: str = 'read',
    ) -> None:
        self._repository_reader = repository_reader
        self._index_store = index_store
        self._observability = observability
        self._extractor = extractor
        self._embedding_generator = embedding_generator
        self._vector_store = vector_store
        self._access_level = access_level

    def index_repository(self, repo: str, trace_id: str) -> IndexRunMetrics:
        files = self._repository_reader.list_python_files(repo)
        return self.index_repository_subset(
            repo=repo,
            trace_id=trace_id,
            file_paths=files,
            deleted_paths=[],
        )

    def index_repository_subset(
        self,
        repo: str,
        trace_id: str,
        file_paths: list[str],
        deleted_paths: list[str],
    ) -> IndexRunMetrics:
        started = datetime.now(timezone.utc)
        run_span = self._observability.start_span(
            name='index_repository',
            trace_id=trace_id,
            input_payload={
                'repo': repo,
                'file_paths': file_paths,
                'deleted_paths': deleted_paths,
            },
        )

        removed_symbols = self._index_store.delete_symbols_for_paths(repo=repo, paths=deleted_paths)
        removed_vectors = 0
        if self._vector_store is not None:
            removed_vectors = self._vector_store.delete_vectors_for_paths(repo=repo, paths=deleted_paths)

        total_symbols = 0
        vectors_upserted = 0
        vector_rows: list[VectorRecord] = []
        file_blob_shas: dict[str, str] = {}

        for file_path in file_paths:
            file_span = self._observability.start_span(
                name='parse_python_file',
                trace_id=trace_id,
                input_payload={'repo': repo, 'path': file_path},
            )
            repo_file = self._repository_reader.read_python_file(repo, file_path)
            extracted = self._extractor.extract(repo=repo_file.repo, path=repo_file.path, source=repo_file.content)
            total_symbols += len(extracted.symbols)
            self._index_store.upsert_symbols(extracted.symbols)

            if self._vector_store is not None and self._embedding_generator is not None and extracted.symbols:
                blob_sha = hashlib.sha256(repo_file.content.encode('utf-8')).hexdigest()
                file_blob_shas[repo_file.path] = blob_sha

                texts = [
                    f"{symbol.signature}\n\n{symbol.docstring or ''}".strip()
                    for symbol in extracted.symbols
                ]
                embeddings = self._embedding_generator.embed(texts)
                for symbol, embedding in zip(extracted.symbols, embeddings):
                    vector_rows.append(
                        VectorRecord(
                            repo=symbol.repo,
                            path=symbol.path,
                            symbol=symbol.symbol,
                            kind=symbol.kind,
                            signature=symbol.signature,
                            docstring=symbol.docstring,
                            embedding=embedding,
                            tree_sha='',
                            blob_sha=blob_sha,
                            access_level=self._access_level,
                        )
                    )

            self._observability.end_span(
                file_span,
                output_payload={
                    'repo': repo_file.repo,
                    'path': repo_file.path,
                    'symbols_indexed': len(extracted.symbols),
                },
            )

        if self._vector_store is not None and vector_rows:
            tree_sha = self._compute_tree_sha(file_blob_shas)
            finalized = [
                VectorRecord(
                    repo=row.repo,
                    path=row.path,
                    symbol=row.symbol,
                    kind=row.kind,
                    signature=row.signature,
                    docstring=row.docstring,
                    embedding=row.embedding,
                    tree_sha=tree_sha,
                    blob_sha=row.blob_sha,
                    access_level=row.access_level,
                )
                for row in vector_rows
            ]
            self._vector_store.upsert_vectors(finalized)
            vectors_upserted = len(finalized)

        finished = datetime.now(timezone.utc)
        metrics = IndexRunMetrics(
            files_scanned=len(file_paths),
            symbols_indexed=total_symbols,
            vectors_upserted=vectors_upserted,
            vectors_deleted=removed_vectors,
            started_at=started,
            finished_at=finished,
        )
        self._observability.end_span(
            run_span,
            output_payload={
                'repo': repo,
                'files_scanned': metrics.files_scanned,
                'symbols_indexed': metrics.symbols_indexed,
                'deleted_files': len(deleted_paths),
                'deleted_symbols': removed_symbols,
                'vectors_upserted': metrics.vectors_upserted,
                'vectors_deleted': metrics.vectors_deleted,
            },
            metadata={'duration_ms': int((finished - started).total_seconds() * 1000)},
        )
        return metrics

    def _compute_tree_sha(self, file_blob_shas: dict[str, str]) -> str:
        if not file_blob_shas:
            return hashlib.sha256(b'').hexdigest()

        digest_input = '|'.join(
            f'{path}:{blob_sha}'
            for path, blob_sha in sorted(file_blob_shas.items(), key=lambda row: row[0])
        )
        return hashlib.sha256(digest_input.encode('utf-8')).hexdigest()
