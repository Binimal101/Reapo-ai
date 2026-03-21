from pathlib import Path

from ast_indexer.adapters.embeddings.simple_hash_embedding_generator_adapter import SimpleHashEmbeddingGeneratorAdapter
from ast_indexer.adapters.index_store.in_memory_symbol_index_store_adapter import InMemorySymbolIndexStoreAdapter
from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.adapters.repository.local_fs_repository_reader_adapter import LocalFsRepositoryReaderAdapter
from ast_indexer.adapters.vector_store.in_memory_vector_store_adapter import InMemoryVectorStoreAdapter
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
    vector_store = InMemoryVectorStoreAdapter()
    service = IndexPythonRepositoryService(
        repository_reader=reader,
        index_store=index_store,
        observability=observability,
        extractor=PythonAstSymbolExtractor(),
        embedding_generator=SimpleHashEmbeddingGeneratorAdapter(),
        vector_store=vector_store,
    )

    metrics = service.index_repository(repo='checkout-service', trace_id='trace-1')

    assert metrics.files_scanned == 2
    assert metrics.symbols_indexed == 2
    assert metrics.vectors_upserted == 2
    assert metrics.vectors_deleted == 0
    assert len(index_store.list_symbols()) == 2
    vectors = vector_store.list_vectors()
    assert len(vectors) == 2
    assert vectors[0].tree_sha != ''
    assert vectors[0].blob_sha != ''
    assert vectors[0].access_level == 'read'

    spans = observability.list_spans()
    assert spans[0].name == 'index_repository'
    assert spans[0].input_payload is not None
    assert spans[0].output_payload is not None
    assert spans[0].metadata is not None
    assert spans[0].input_payload['file_count'] == 2
    assert spans[0].output_payload['files_scanned'] == 2
    assert len(spans[0].output_payload['blob_shas_by_file']) == 2
    assert spans[0].metadata['avg_symbols_per_file'] == 1.0
    parse_spans = [span for span in spans if span.name == 'parse_python_file']
    assert len(parse_spans) == 2
    for span in parse_spans:
        assert span.output_payload is not None
        assert span.output_payload['blob_sha'] != ''
        assert span.output_payload['content_bytes'] > 0
        assert isinstance(span.output_payload['symbols'], list)
    assert spans[0].finished_at is not None


def test_index_repository_subset_prunes_deleted_paths(tmp_path: Path) -> None:
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
    vector_store = InMemoryVectorStoreAdapter()
    service = IndexPythonRepositoryService(
        repository_reader=reader,
        index_store=index_store,
        observability=observability,
        extractor=PythonAstSymbolExtractor(),
        embedding_generator=SimpleHashEmbeddingGeneratorAdapter(),
        vector_store=vector_store,
    )

    service.index_repository(repo='checkout-service', trace_id='trace-subset-1')
    metrics = service.index_repository_subset(
        repo='checkout-service',
        trace_id='trace-subset-2',
        file_paths=[],
        deleted_paths=['src/pricing.py'],
    )

    assert metrics.files_scanned == 0
    assert metrics.symbols_indexed == 0
    assert metrics.vectors_deleted == 1
    symbols = index_store.list_symbols()
    assert len(symbols) == 1
    assert symbols[0].path == 'src/orders.py'

    vectors = vector_store.list_vectors()
    assert len(vectors) == 1
    assert vectors[0].path == 'src/orders.py'
