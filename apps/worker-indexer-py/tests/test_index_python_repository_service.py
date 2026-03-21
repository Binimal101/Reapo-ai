from pathlib import Path

from ast_indexer.adapters.index_store.in_memory_symbol_index_store_adapter import InMemorySymbolIndexStoreAdapter
from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.adapters.repository.local_fs_repository_reader_adapter import LocalFsRepositoryReaderAdapter
from ast_indexer.application.index_python_repository_service import IndexPythonRepositoryService
from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor


def test_index_repository_collects_symbols_and_emits_spans(tmp_path: Path) -> None:
    repo_root = tmp_path / 'checkout-service' / 'src'
    repo_root.mkdir(parents=True)
    (repo_root / 'orders.py').write_text(
        'def process(order_id):\n    return order_id\n',
        encoding='utf-8',
    )
    (repo_root / 'pricing.py').write_text(
        'def apply_discount(total):\n    return total\n',
        encoding='utf-8',
    )

    observability = InMemoryObservabilityAdapter()
    reader = LocalFsRepositoryReaderAdapter(tmp_path)
    index_store = InMemorySymbolIndexStoreAdapter()
    service = IndexPythonRepositoryService(
        repository_reader=reader,
        index_store=index_store,
        observability=observability,
        extractor=PythonAstSymbolExtractor(),
    )

    metrics = service.index_repository(repo='checkout-service', trace_id='trace-1')

    assert metrics.files_scanned == 2
    assert metrics.symbols_indexed == 2
    assert len(index_store.list_symbols()) == 2

    spans = observability.list_spans()
    assert spans[0].name == 'index_repository'
    parse_spans = [span for span in spans if span.name == 'parse_python_file']
    assert len(parse_spans) == 2
    assert spans[0].finished_at is not None
