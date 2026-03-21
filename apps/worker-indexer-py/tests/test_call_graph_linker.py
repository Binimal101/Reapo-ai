from ast_indexer.application.call_graph_linker import CallGraphLinker
from ast_indexer.domain.models import SymbolRecord


def test_links_cross_file_calls_using_module_qualified_callee() -> None:
    symbols = [
        SymbolRecord(
            repo='checkout-service',
            path='src/util.py',
            symbol='helper',
            kind='function',
            line=1,
            signature='def helper(value)',
            callees=(),
        ),
        SymbolRecord(
            repo='checkout-service',
            path='src/orders.py',
            symbol='process',
            kind='function',
            line=1,
            signature='def process(order_id)',
            callees=('src.util.helper',),
        ),
    ]

    edges = CallGraphLinker().link(symbols)
    assert len(edges) == 1
    assert edges[0].caller_symbol == 'process'
    assert edges[0].resolved_symbol == 'helper'
    assert edges[0].resolved_path == 'src/util.py'


def test_reports_unresolved_edges_and_resolution_rate() -> None:
    symbols = [
        SymbolRecord(
            repo='checkout-service',
            path='src/util.py',
            symbol='helper',
            kind='function',
            line=1,
            signature='def helper(value)',
            callees=(),
        ),
        SymbolRecord(
            repo='checkout-service',
            path='src/orders.py',
            symbol='process',
            kind='function',
            line=1,
            signature='def process(order_id)',
            callees=('src.util.helper', 'missing.func'),
        ),
    ]

    report = CallGraphLinker().link_report(symbols)
    assert len(report.linked_edges) == 1
    assert len(report.unresolved_edges) == 1
    assert report.unresolved_edges[0].callee == 'missing.func'
    assert report.unresolved_edges[0].reason == 'external_qualified_call'
    assert report.unresolved_edges[0].actionable is False
    assert report.resolution_rate == 0.5
    assert report.actionable_resolution_rate == 1.0


def test_builtin_and_dynamic_calls_are_non_actionable() -> None:
    symbols = [
        SymbolRecord(
            repo='checkout-service',
            path='src/orders.py',
            symbol='process',
            kind='function',
            line=1,
            signature='def process(order_id)',
            callees=('str', 'append'),
        )
    ]

    report = CallGraphLinker().link_report(symbols)
    assert len(report.linked_edges) == 0
    assert len(report.unresolved_edges) == 2
    assert report.unresolved_edges[0].reason == 'builtin_call'
    assert report.unresolved_edges[1].reason == 'dynamic_attr_call'
    assert report.actionable_unresolved_edges == []
    assert report.actionable_resolution_rate == 1.0
