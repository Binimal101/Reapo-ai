from pathlib import Path

from ast_indexer.adapters.access.json_file_repo_capability_store_adapter import JsonFileRepoCapabilityStoreAdapter
from ast_indexer.adapters.index_store.json_file_symbol_index_store_adapter import JsonFileSymbolIndexStoreAdapter
from ast_indexer.adapters.observability.jsonl_file_observability_adapter import JsonlFileObservabilityAdapter
from ast_indexer.adapters.vector_store.json_file_vector_store_adapter import JsonFileVectorStoreAdapter
from ast_indexer.adapters.webhooks.json_file_webhook_replay_guard_adapter import JsonFileWebhookReplayGuardAdapter
from ast_indexer.domain.models import SymbolRecord, VectorRecord


def test_json_file_symbol_store_persists_and_reloads(tmp_path: Path) -> None:
    target = tmp_path / 'index' / 'symbols.json'
    store = JsonFileSymbolIndexStoreAdapter(target)

    store.upsert_symbols(
        [
            SymbolRecord(
                repo='checkout-service',
                path='src/orders.py',
                symbol='process',
                kind='function',
                line=1,
                signature='def process(order_id)',
                callees=('fetch_user',),
            )
        ]
    )

    assert target.exists()

    reloaded = JsonFileSymbolIndexStoreAdapter(target)
    symbols = reloaded.list_symbols()
    assert len(symbols) == 1
    assert symbols[0].symbol == 'process'
    assert symbols[0].callees == ('fetch_user',)


def test_jsonl_observability_writes_completed_span(tmp_path: Path) -> None:
    target = tmp_path / 'observability' / 'spans.jsonl'
    adapter = JsonlFileObservabilityAdapter(target)

    span = adapter.start_span(name='index_repository', trace_id='trace-55', input_payload={'repo': 'checkout-service'})
    adapter.end_span(span, output_payload={'symbols_indexed': 2}, metadata={'duration_ms': 12})

    assert target.exists()
    lines = target.read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    assert 'trace-55' in lines[0]
    assert 'index_repository' in lines[0]


def test_json_file_symbol_store_deletes_paths_and_persists(tmp_path: Path) -> None:
    target = tmp_path / 'index' / 'symbols.json'
    store = JsonFileSymbolIndexStoreAdapter(target)

    store.upsert_symbols(
        [
            SymbolRecord(
                repo='checkout-service',
                path='src/orders.py',
                symbol='process',
                kind='function',
                line=1,
                signature='def process(order_id)',
                callees=(),
            ),
            SymbolRecord(
                repo='checkout-service',
                path='src/pricing.py',
                symbol='apply_discount',
                kind='function',
                line=1,
                signature='def apply_discount(total)',
                callees=(),
            ),
        ]
    )

    removed = store.delete_symbols_for_paths('checkout-service', ['src/pricing.py'])
    assert removed == 1

    reloaded = JsonFileSymbolIndexStoreAdapter(target)
    symbols = reloaded.list_symbols()
    assert len(symbols) == 1
    assert symbols[0].symbol == 'process'


def test_json_file_vector_store_persists_and_reloads(tmp_path: Path) -> None:
    target = tmp_path / 'index' / 'vectors.json'
    store = JsonFileVectorStoreAdapter(target)

    store.upsert_vectors(
        [
            VectorRecord(
                repo='checkout-service',
                path='src/orders.py',
                symbol='process',
                kind='function',
                signature='def process(order_id)',
                docstring='Process an order.',
                embedding=(0.1, -0.2, 0.3),
                tree_sha='tree-1',
                blob_sha='blob-1',
                access_level='read',
            )
        ]
    )

    assert target.exists()

    reloaded = JsonFileVectorStoreAdapter(target)
    vectors = reloaded.list_vectors()
    assert len(vectors) == 1
    assert vectors[0].symbol == 'process'
    assert vectors[0].docstring == 'Process an order.'


def test_json_file_vector_store_deletes_paths_and_persists(tmp_path: Path) -> None:
    target = tmp_path / 'index' / 'vectors.json'
    store = JsonFileVectorStoreAdapter(target)

    store.upsert_vectors(
        [
            VectorRecord(
                repo='checkout-service',
                path='src/orders.py',
                symbol='process',
                kind='function',
                signature='def process(order_id)',
                docstring=None,
                embedding=(0.1, -0.2, 0.3),
                tree_sha='tree-1',
                blob_sha='blob-1',
                access_level='read',
            ),
            VectorRecord(
                repo='checkout-service',
                path='src/pricing.py',
                symbol='apply_discount',
                kind='function',
                signature='def apply_discount(total)',
                docstring=None,
                embedding=(0.0, 0.5, -0.1),
                tree_sha='tree-1',
                blob_sha='blob-2',
                access_level='read',
            ),
        ]
    )

    removed = store.delete_vectors_for_paths('checkout-service', ['src/pricing.py'])
    assert removed == 1

    reloaded = JsonFileVectorStoreAdapter(target)
    vectors = reloaded.list_vectors()
    assert len(vectors) == 1
    assert vectors[0].symbol == 'process'


def test_repo_capability_store_persists_and_normalizes_lookup_key(tmp_path: Path) -> None:
    target = tmp_path / 'auth' / 'repo_capabilities.json'
    store = JsonFileRepoCapabilityStoreAdapter(target)

    store.upsert(
        owner='Matth',
        repo='Reapo-ai',
        installation_id=123,
        permissions={'contents': 'write'},
        repository_selection='selected',
    )

    reloaded = JsonFileRepoCapabilityStoreAdapter(target)
    row = reloaded.get(owner='matth', repo='reapo-ai')
    assert row is not None
    assert row['installation_id'] == 123
    assert row['permissions']['contents'] == 'write'


def test_webhook_replay_guard_marks_duplicate_delivery_ids(tmp_path: Path) -> None:
    target = tmp_path / 'webhooks' / 'delivery_ids.json'
    guard = JsonFileWebhookReplayGuardAdapter(target)

    first_seen = guard.seen_before_then_mark('delivery-1')
    second_seen = guard.seen_before_then_mark('delivery-1')

    assert first_seen is False
    assert second_seen is True

    reloaded = JsonFileWebhookReplayGuardAdapter(target)
    assert reloaded.seen_before_then_mark('delivery-1') is True