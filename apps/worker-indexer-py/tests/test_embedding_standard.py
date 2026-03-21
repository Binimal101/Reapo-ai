import math
from pathlib import Path

from ast_indexer.adapters.embedding.in_memory_embedding_store_adapter import InMemoryEmbeddingStoreAdapter
from ast_indexer.adapters.embedding.stub_embedding_adapter import StubEmbeddingAdapter
from ast_indexer.adapters.index_store.in_memory_symbol_index_store_adapter import InMemorySymbolIndexStoreAdapter
from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.adapters.repository.local_fs_repository_reader_adapter import LocalFsRepositoryReaderAdapter
from ast_indexer.application.index_python_repository_service import IndexPythonRepositoryService
from ast_indexer.domain.models import SymbolRecord
from ast_indexer.parsing.cross_file_linker import CrossFileLinker
from ast_indexer.parsing.module_path_resolver import ModulePathResolver
from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor


# ---------------------------------------------------------------------------
# embedding_input standard
# ---------------------------------------------------------------------------

def test_embedding_input_is_signature_only_when_no_docstring() -> None:
    sym = SymbolRecord(
        repo='repo', path='src/orders.py', symbol='process', kind='function',
        line=1, signature='def process(order_id)',
        docstring=None,
    )
    assert sym.embedding_input == 'def process(order_id)'


def test_embedding_input_appends_docstring_when_present() -> None:
    sym = SymbolRecord(
        repo='repo', path='src/orders.py', symbol='process', kind='function',
        line=1, signature='def process(order_id)',
        docstring='Process an incoming order and return a result.',
    )
    assert sym.embedding_input == 'def process(order_id)\nProcess an incoming order and return a result.'


def test_embedding_input_class_with_docstring() -> None:
    sym = SymbolRecord(
        repo='repo', path='src/checkout.py', symbol='Checkout', kind='class',
        line=1, signature='class Checkout',
        docstring='Handles the checkout flow.',
    )
    assert sym.embedding_input == 'class Checkout\nHandles the checkout flow.'


# ---------------------------------------------------------------------------
# docstring extraction via AST extractor
# ---------------------------------------------------------------------------

def test_extractor_captures_function_docstring() -> None:
    source = '''\
def process(order_id):
    """Process an incoming order."""
    return order_id
'''
    extractor = PythonAstSymbolExtractor()
    extracted = extractor.extract(repo='repo', path='orders.py', source=source)
    sym = next(s for s in extracted.symbols if s.symbol == 'process')
    assert sym.docstring == 'Process an incoming order.'


def test_extractor_captures_class_docstring() -> None:
    source = '''\
class Checkout:
    """Handles the checkout flow."""
    def validate(self):
        pass
'''
    extractor = PythonAstSymbolExtractor()
    extracted = extractor.extract(repo='repo', path='checkout.py', source=source)
    cls = next(s for s in extracted.symbols if s.symbol == 'Checkout')
    assert cls.docstring == 'Handles the checkout flow.'


def test_extractor_captures_method_docstring() -> None:
    source = '''\
class Checkout:
    def validate(self, cart):
        """Validate the cart contents."""
        return True
'''
    extractor = PythonAstSymbolExtractor()
    extracted = extractor.extract(repo='repo', path='checkout.py', source=source)
    method = next(s for s in extracted.symbols if s.symbol == 'Checkout.validate')
    assert method.docstring == 'Validate the cart contents.'


def test_extractor_docstring_is_none_when_absent() -> None:
    source = 'def process(order_id):\n    return order_id\n'
    extractor = PythonAstSymbolExtractor()
    extracted = extractor.extract(repo='repo', path='orders.py', source=source)
    sym = next(s for s in extracted.symbols if s.symbol == 'process')
    assert sym.docstring is None


# ---------------------------------------------------------------------------
# StubEmbeddingAdapter
# ---------------------------------------------------------------------------

def test_stub_adapter_returns_correct_dimension() -> None:
    adapter = StubEmbeddingAdapter()
    vec = adapter.embed('def process(order_id)')
    assert len(vec) == StubEmbeddingAdapter.dimensions


def test_stub_adapter_is_unit_normalised() -> None:
    adapter = StubEmbeddingAdapter()
    vec = adapter.embed('def process(order_id)')
    magnitude = math.sqrt(sum(x * x for x in vec))
    assert abs(magnitude - 1.0) < 1e-6


def test_stub_adapter_is_deterministic() -> None:
    adapter = StubEmbeddingAdapter()
    text = 'def apply_discount(total)\nApply a percentage discount.'
    assert adapter.embed(text) == adapter.embed(text)


def test_stub_adapter_different_texts_produce_different_vectors() -> None:
    adapter = StubEmbeddingAdapter()
    vec_a = adapter.embed('def process(order_id)')
    vec_b = adapter.embed('def apply_discount(total)')
    assert vec_a != vec_b


def test_stub_adapter_embed_batch_matches_individual_embeds() -> None:
    adapter = StubEmbeddingAdapter()
    texts = ['def process(order_id)', 'def apply_discount(total)', 'class Checkout']
    batch = adapter.embed_batch(texts)
    assert batch == [adapter.embed(t) for t in texts]


# ---------------------------------------------------------------------------
# End-to-end: embedding phase in IndexPythonRepositoryService
# ---------------------------------------------------------------------------

def _build_repo(tmp_path: Path) -> Path:
    src = tmp_path / 'myrepo' / 'src'
    src.mkdir(parents=True)
    (src / 'orders.py').write_text(
        'def process(order_id):\n    """Process an order."""\n    return order_id\n',
        encoding='utf-8',
    )
    return tmp_path


def test_service_embeds_symbols_when_port_configured(tmp_path: Path) -> None:
    workspace = _build_repo(tmp_path)
    obs = InMemoryObservabilityAdapter()
    index_store = InMemorySymbolIndexStoreAdapter()
    embedding_store = InMemoryEmbeddingStoreAdapter()
    embedding_port = StubEmbeddingAdapter()

    service = IndexPythonRepositoryService(
        repository_reader=LocalFsRepositoryReaderAdapter(workspace),
        index_store=index_store,
        observability=obs,
        extractor=PythonAstSymbolExtractor(),
        linker=CrossFileLinker(),
        module_resolver=ModulePathResolver(),
        embedding_port=embedding_port,
        embedding_store=embedding_store,
    )

    metrics = service.index_repository(repo='myrepo', trace_id='trace-embed-1')

    assert metrics.embeddings_generated == 1
    records = embedding_store.list_embeddings()
    assert len(records) == 1

    rec = records[0]
    assert rec.symbol == 'process'
    assert rec.model == 'stub-v0'
    assert rec.dimensions == 8
    assert len(rec.vector) == 8
    # embedding_input should include docstring
    assert 'Process an order.' in rec.embedding_input


def test_service_emits_embed_symbols_span(tmp_path: Path) -> None:
    workspace = _build_repo(tmp_path)
    obs = InMemoryObservabilityAdapter()

    service = IndexPythonRepositoryService(
        repository_reader=LocalFsRepositoryReaderAdapter(workspace),
        index_store=InMemorySymbolIndexStoreAdapter(),
        observability=obs,
        extractor=PythonAstSymbolExtractor(),
        linker=CrossFileLinker(),
        module_resolver=ModulePathResolver(),
        embedding_port=StubEmbeddingAdapter(),
        embedding_store=InMemoryEmbeddingStoreAdapter(),
    )

    service.index_repository(repo='myrepo', trace_id='trace-embed-2')

    embed_spans = [s for s in obs.list_spans() if s.name == 'embed_symbols']
    assert len(embed_spans) == 1
    assert embed_spans[0].finished_at is not None
    assert embed_spans[0].output_payload['embeddings_generated'] == 1
    assert embed_spans[0].output_payload['model'] == 'stub-v0'


def test_service_skips_embedding_when_no_port_configured(tmp_path: Path) -> None:
    workspace = _build_repo(tmp_path)
    obs = InMemoryObservabilityAdapter()

    service = IndexPythonRepositoryService(
        repository_reader=LocalFsRepositoryReaderAdapter(workspace),
        index_store=InMemorySymbolIndexStoreAdapter(),
        observability=obs,
        extractor=PythonAstSymbolExtractor(),
        linker=CrossFileLinker(),
        module_resolver=ModulePathResolver(),
    )

    metrics = service.index_repository(repo='myrepo', trace_id='trace-embed-3')

    assert metrics.embeddings_generated == 0
    embed_spans = [s for s in obs.list_spans() if s.name == 'embed_symbols']
    assert len(embed_spans) == 0
