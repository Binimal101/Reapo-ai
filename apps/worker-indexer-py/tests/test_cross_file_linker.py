from pathlib import Path

import pytest

from ast_indexer.adapters.index_store.in_memory_symbol_index_store_adapter import InMemorySymbolIndexStoreAdapter
from ast_indexer.adapters.index_store.json_file_symbol_index_store_adapter import JsonFileSymbolIndexStoreAdapter
from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.adapters.repository.local_fs_repository_reader_adapter import LocalFsRepositoryReaderAdapter
from ast_indexer.application.index_python_repository_service import IndexPythonRepositoryService
from ast_indexer.domain.models import SymbolRecord
from ast_indexer.parsing.cross_file_linker import CrossFileLinker
from ast_indexer.parsing.module_path_resolver import ModulePathResolver
from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor


# ---------------------------------------------------------------------------
# Fixture repo layout
#
#   my_repo/
#     src/
#       pricing.py   — defines apply_discount, Pricer class
#       orders.py    — imports apply_discount from pricing; calls it
#
# After indexing, orders.process_order.linked_callees should resolve to
# the apply_discount symbol in pricing.py.
# ---------------------------------------------------------------------------

PRICING_PY = '''\
def apply_discount(total):
    return total * 0.9

class Pricer:
    def compute(self, total):
        return apply_discount(total)
'''

ORDERS_PY = '''\
from pricing import apply_discount

def process_order(cart_total):
    return apply_discount(cart_total)
'''


def _make_fixture_repo(tmp_path: Path) -> Path:
    src = tmp_path / 'my_repo' / 'src'
    src.mkdir(parents=True)
    (src / 'pricing.py').write_text(PRICING_PY, encoding='utf-8')
    (src / 'orders.py').write_text(ORDERS_PY, encoding='utf-8')
    return tmp_path


def _make_service(
    workspace: Path,
    index_store: InMemorySymbolIndexStoreAdapter | None = None,
) -> tuple[IndexPythonRepositoryService, InMemorySymbolIndexStoreAdapter, InMemoryObservabilityAdapter]:
    obs = InMemoryObservabilityAdapter()
    store = index_store or InMemorySymbolIndexStoreAdapter()
    svc = IndexPythonRepositoryService(
        repository_reader=LocalFsRepositoryReaderAdapter(workspace),
        index_store=store,
        observability=obs,
        extractor=PythonAstSymbolExtractor(),
        linker=CrossFileLinker(),
        module_resolver=ModulePathResolver(),
    )
    return svc, store, obs


def test_linked_callees_resolved_across_files(tmp_path: Path) -> None:
    workspace = _make_fixture_repo(tmp_path)
    service, store, _ = _make_service(workspace)

    metrics = service.index_repository(repo='my_repo', trace_id='trace-link-1')

    symbols = store.list_symbols()
    process_order = next(s for s in symbols if s.symbol == 'process_order')

    # apply_discount is called in process_order; it lives in pricing.py
    assert len(process_order.linked_callees) == 1
    assert 'apply_discount' in process_order.linked_callees[0]
    assert 'pricing.py' in process_order.linked_callees[0]

    # metrics reflect the resolved edge
    assert metrics.linked_edges >= 1


def test_intra_file_call_also_resolved(tmp_path: Path) -> None:
    workspace = _make_fixture_repo(tmp_path)
    service, store, _ = _make_service(workspace)
    service.index_repository(repo='my_repo', trace_id='trace-link-2')

    symbols = store.list_symbols()
    pricer_compute = next(s for s in symbols if s.symbol == 'Pricer.compute')

    # Pricer.compute calls apply_discount which is defined in the same file
    assert any('apply_discount' in lc for lc in pricer_compute.linked_callees)


def test_symbols_without_cross_file_calls_have_empty_linked_callees(tmp_path: Path) -> None:
    workspace = _make_fixture_repo(tmp_path)
    service, store, _ = _make_service(workspace)
    service.index_repository(repo='my_repo', trace_id='trace-link-3')

    symbols = store.list_symbols()
    apply_discount = next(s for s in symbols if s.symbol == 'apply_discount')

    # apply_discount calls nothing
    assert apply_discount.linked_callees == ()


def test_linked_callees_persist_and_reload(tmp_path: Path) -> None:
    workspace = _make_fixture_repo(tmp_path)
    state_root = tmp_path / 'state'
    json_store = JsonFileSymbolIndexStoreAdapter(state_root / 'index' / 'symbols.json')

    service, _, _ = _make_service(workspace, index_store=json_store)
    service.index_repository(repo='my_repo', trace_id='trace-link-4')

    # Reload from disk
    reloaded = JsonFileSymbolIndexStoreAdapter(state_root / 'index' / 'symbols.json')
    symbols = reloaded.list_symbols()
    process_order = next(s for s in symbols if s.symbol == 'process_order')

    assert len(process_order.linked_callees) == 1
    assert 'apply_discount' in process_order.linked_callees[0]


def test_link_callees_span_emitted(tmp_path: Path) -> None:
    workspace = _make_fixture_repo(tmp_path)
    service, _, obs = _make_service(workspace)
    service.index_repository(repo='my_repo', trace_id='trace-link-5')

    link_spans = [s for s in obs.list_spans() if s.name == 'link_callees']
    assert len(link_spans) == 1
    assert link_spans[0].finished_at is not None
    assert link_spans[0].output_payload is not None
    assert link_spans[0].output_payload['linked_edges'] >= 1
