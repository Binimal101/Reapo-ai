from __future__ import annotations

from datetime import datetime, timezone

from ast_indexer.domain.models import IndexRunMetrics
from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor
from ast_indexer.ports.index_store import IndexStorePort
from ast_indexer.ports.observability import ObservabilityPort
from ast_indexer.ports.repository_reader import RepositoryReaderPort


class IndexPythonRepositoryService:
    def __init__(
        self,
        repository_reader: RepositoryReaderPort,
        index_store: IndexStorePort,
        observability: ObservabilityPort,
        extractor: PythonAstSymbolExtractor,
    ) -> None:
        self._repository_reader = repository_reader
        self._index_store = index_store
        self._observability = observability
        self._extractor = extractor

    def index_repository(self, repo: str, trace_id: str) -> IndexRunMetrics:
        started = datetime.now(timezone.utc)
        run_span = self._observability.start_span(
            name='index_repository',
            trace_id=trace_id,
            input_payload={'repo': repo},
        )

        files = self._repository_reader.list_python_files(repo)
        total_symbols = 0

        for file_path in files:
            file_span = self._observability.start_span(
                name='parse_python_file',
                trace_id=trace_id,
                input_payload={'repo': repo, 'path': file_path},
            )
            repo_file = self._repository_reader.read_python_file(repo, file_path)
            extracted = self._extractor.extract(repo=repo_file.repo, path=repo_file.path, source=repo_file.content)
            total_symbols += len(extracted.symbols)
            self._index_store.upsert_symbols(extracted.symbols)
            self._observability.end_span(
                file_span,
                output_payload={
                    'repo': repo_file.repo,
                    'path': repo_file.path,
                    'symbols_indexed': len(extracted.symbols),
                },
            )

        finished = datetime.now(timezone.utc)
        metrics = IndexRunMetrics(
            files_scanned=len(files),
            symbols_indexed=total_symbols,
            started_at=started,
            finished_at=finished,
        )
        self._observability.end_span(
            run_span,
            output_payload={
                'repo': repo,
                'files_scanned': metrics.files_scanned,
                'symbols_indexed': metrics.symbols_indexed,
            },
            metadata={'duration_ms': int((finished - started).total_seconds() * 1000)},
        )
        return metrics
