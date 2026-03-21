from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ast_indexer.domain.models import EmbeddingRecord, IndexRunMetrics, SymbolRecord
from ast_indexer.parsing.cross_file_linker import CrossFileLinker
from ast_indexer.parsing.module_path_resolver import ModulePathResolver
from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor
from ast_indexer.ports.index_store import IndexStorePort
from ast_indexer.ports.observability import ObservabilityPort
from ast_indexer.ports.repository_reader import RepositoryReaderPort

if TYPE_CHECKING:
    from ast_indexer.ports.embedding import EmbeddingPort, EmbeddingStorePort


class IndexPythonRepositoryService:
    def __init__(
        self,
        repository_reader: RepositoryReaderPort,
        index_store: IndexStorePort,
        observability: ObservabilityPort,
        extractor: PythonAstSymbolExtractor,
        linker: CrossFileLinker,
        module_resolver: ModulePathResolver,
        embedding_port: EmbeddingPort | None = None,
        embedding_store: EmbeddingStorePort | None = None,
    ) -> None:
        self._repository_reader = repository_reader
        self._index_store = index_store
        self._observability = observability
        self._extractor = extractor
        self._linker = linker
        self._module_resolver = module_resolver
        self._embedding_port = embedding_port
        self._embedding_store = embedding_store

    def index_repository(self, repo: str, trace_id: str) -> IndexRunMetrics:
        started = datetime.now(timezone.utc)
        run_span = self._observability.start_span(
            name='index_repository',
            trace_id=trace_id,
            input_payload={'repo': repo},
        )

        files = self._repository_reader.list_python_files(repo)

        # Phase 1: parse all files, collect symbols
        all_symbols: list[SymbolRecord] = []
        for file_path in files:
            file_span = self._observability.start_span(
                name='parse_python_file',
                trace_id=trace_id,
                input_payload={'repo': repo, 'path': file_path},
            )
            repo_file = self._repository_reader.read_python_file(repo, file_path)
            extracted = self._extractor.extract(
                repo=repo_file.repo, path=repo_file.path, source=repo_file.content
            )
            all_symbols.extend(extracted.symbols)
            self._observability.end_span(
                file_span,
                output_payload={
                    'repo': repo_file.repo,
                    'path': repo_file.path,
                    'symbols_indexed': len(extracted.symbols),
                },
            )

        # Phase 2: cross-file call linking
        link_span = self._observability.start_span(
            name='link_callees',
            trace_id=trace_id,
            input_payload={'repo': repo, 'symbol_count': len(all_symbols)},
        )
        linked_symbols = self._linker.link(all_symbols, self._module_resolver)
        linked_edges = sum(len(s.linked_callees) for s in linked_symbols)
        self._observability.end_span(
            link_span,
            output_payload={'linked_edges': linked_edges},
        )

        # Phase 3: upsert enriched symbols
        self._index_store.upsert_symbols(linked_symbols)

        # Phase 4: embed (only when an embedding provider and store are configured)
        embeddings_generated = 0
        if self._embedding_port is not None and self._embedding_store is not None:
            embed_span = self._observability.start_span(
                name='embed_symbols',
                trace_id=trace_id,
                input_payload={
                    'repo': repo,
                    'symbol_count': len(linked_symbols),
                    'model': self._embedding_port.model_name,
                },
            )
            texts = [sym.embedding_input for sym in linked_symbols]
            vectors = self._embedding_port.embed_batch(texts)
            embedding_records = [
                EmbeddingRecord(
                    repo=sym.repo,
                    path=sym.path,
                    symbol=sym.symbol,
                    kind=sym.kind,
                    embedding_input=text,
                    vector=tuple(vec),
                    model=self._embedding_port.model_name,
                    dimensions=self._embedding_port.dimensions,
                )
                for sym, text, vec in zip(linked_symbols, texts, vectors)
            ]
            self._embedding_store.upsert_embeddings(embedding_records)
            embeddings_generated = len(embedding_records)
            self._observability.end_span(
                embed_span,
                output_payload={
                    'embeddings_generated': embeddings_generated,
                    'model': self._embedding_port.model_name,
                    'dimensions': self._embedding_port.dimensions,
                },
            )

        finished = datetime.now(timezone.utc)
        metrics = IndexRunMetrics(
            files_scanned=len(files),
            symbols_indexed=len(all_symbols),
            linked_edges=linked_edges,
            embeddings_generated=embeddings_generated,
            started_at=started,
            finished_at=finished,
        )
        self._observability.end_span(
            run_span,
            output_payload={
                'repo': repo,
                'files_scanned': metrics.files_scanned,
                'symbols_indexed': metrics.symbols_indexed,
                'linked_edges': metrics.linked_edges,
                'embeddings_generated': metrics.embeddings_generated,
            },
            metadata={'duration_ms': int((finished - started).total_seconds() * 1000)},
        )
        return metrics
