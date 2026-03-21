from pathlib import Path

from ast_indexer.adapters.index_store.json_file_symbol_index_store_adapter import JsonFileSymbolIndexStoreAdapter
from ast_indexer.adapters.observability.jsonl_file_observability_adapter import JsonlFileObservabilityAdapter
from ast_indexer.domain.models import SymbolRecord


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