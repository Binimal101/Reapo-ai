from pathlib import Path

from ast_indexer.adapters.index_store.in_memory_symbol_index_store_adapter import InMemorySymbolIndexStoreAdapter
from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.adapters.repository.local_fs_repository_reader_adapter import LocalFsRepositoryReaderAdapter
from ast_indexer.application.index_python_repository_service import IndexPythonRepositoryService
from ast_indexer.parsing.cross_file_linker import CrossFileLinker
from ast_indexer.parsing.module_path_resolver import ModulePathResolver
from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor


def _make_service(tmp_path: Path) -> tuple[IndexPythonRepositoryService, InMemorySymbolIndexStoreAdapter, InMemoryObservabilityAdapter]:
    observability = InMemoryObservabilityAdapter()
    index_store = InMemorySymbolIndexStoreAdapter()
    service = IndexPythonRepositoryService(
        repository_reader=LocalFsRepositoryReaderAdapter(tmp_path),
        index_store=index_store,
        observability=observability,
        extractor=PythonAstSymbolExtractor(),
        linker=CrossFileLinker(),
        module_resolver=ModulePathResolver(),
    )
    return service, index_store, observability


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

    service, index_store, observability = _make_service(tmp_path)
    metrics = service.index_repository(repo='checkout-service', trace_id='trace-1')

    assert metrics.files_scanned == 2
    assert metrics.symbols_indexed == 2
    assert metrics.linked_edges == 0  # no cross-file calls in these fixtures
    assert metrics.embeddings_generated == 0  # no embedding port configured
    assert len(index_store.list_symbols()) == 2

    spans = observability.list_spans()
    assert spans[0].name == 'index_repository'
    parse_spans = [span for span in spans if span.name == 'parse_python_file']
    assert len(parse_spans) == 2
    link_spans = [span for span in spans if span.name == 'link_callees']
    assert len(link_spans) == 1
    assert spans[0].finished_at is not None
