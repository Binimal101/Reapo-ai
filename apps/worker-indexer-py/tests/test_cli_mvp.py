import json
from pathlib import Path

from ast_indexer.cli import main


def test_cli_index_command_creates_persistent_artifacts(tmp_path: Path, capsys) -> None:
    workspace_root = tmp_path / 'workspace'
    repo_root = workspace_root / 'checkout-service' / 'src'
    repo_root.mkdir(parents=True)
    (repo_root / 'util.py').write_text(
        'def helper(order_id):\n    return order_id\n',
        encoding='utf-8',
    )
    (repo_root / 'orders.py').write_text(
        'from src.util import helper\n\ndef process(order_id):\n    return helper(order_id)\n',
        encoding='utf-8',
    )

    state_root = tmp_path / 'state'
    exit_code = main(
        [
            'index',
            '--workspace-root',
            str(workspace_root),
            '--repo',
            'checkout-service',
            '--state-root',
            str(state_root),
            '--trace-id',
            'trace-mvp-1',
            '--embedding-backend',
            'hash',
            '--observability-backend',
            'jsonl',
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert summary['status'] == 'ok'
    assert summary['repo'] == 'checkout-service'
    assert summary['trace_id'] == 'trace-mvp-1'
    assert summary['files_scanned'] == 2
    assert summary['changed_files'] == 2
    assert summary['deleted_files'] == 0
    assert summary['skipped_files'] == 0
    assert summary['symbols_indexed'] == 2
    assert summary['vectors_upserted'] == 2
    assert summary['vectors_deleted'] == 0
    assert summary['linked_edges'] == 1
    assert summary['unresolved_edges'] == 0
    assert summary['actionable_unresolved_edges'] == 0
    assert summary['resolution_rate'] == 1.0
    assert summary['actionable_resolution_rate'] == 1.0
    assert summary['linkage_quality'] == 'high'

    index_file = state_root / 'index' / 'symbols.json'
    manifest_file = state_root / 'index' / 'file_manifest.json'
    call_edges_file = state_root / 'index' / 'call_edges.json'
    unresolved_edges_file = state_root / 'index' / 'unresolved_call_edges.json'
    vectors_file = state_root / 'index' / 'vectors.json'
    spans_file = state_root / 'observability' / 'spans.jsonl'

    assert index_file.exists()
    assert manifest_file.exists()
    assert call_edges_file.exists()
    assert unresolved_edges_file.exists()
    assert vectors_file.exists()
    assert spans_file.exists()

    index_rows = json.loads(index_file.read_text(encoding='utf-8'))
    assert len(index_rows) == 2

    call_edges = json.loads(call_edges_file.read_text(encoding='utf-8'))
    assert len(call_edges) == 1
    assert call_edges[0]['caller_symbol'] == 'process'
    assert call_edges[0]['resolved_symbol'] == 'helper'

    unresolved = json.loads(unresolved_edges_file.read_text(encoding='utf-8'))
    assert unresolved == []

    span_rows = spans_file.read_text(encoding='utf-8').splitlines()
    assert len(span_rows) == 3


def test_cli_index_command_is_incremental_across_runs(tmp_path: Path, capsys) -> None:
    workspace_root = tmp_path / 'workspace'
    repo_root = workspace_root / 'checkout-service' / 'src'
    repo_root.mkdir(parents=True)
    (repo_root / 'orders.py').write_text(
        'def process(order_id):\n    return order_id\n',
        encoding='utf-8',
    )
    (repo_root / 'pricing.py').write_text(
        'def apply_discount(total):\n    return total\n',
        encoding='utf-8',
    )

    state_root = tmp_path / 'state'
    first_exit = main(
        [
            'index',
            '--workspace-root',
            str(workspace_root),
            '--repo',
            'checkout-service',
            '--state-root',
            str(state_root),
            '--trace-id',
            'trace-mvp-incremental-1',
            '--embedding-backend',
            'hash',
            '--observability-backend',
            'jsonl',
        ]
    )
    assert first_exit == 0
    _ = capsys.readouterr()

    # Modify one file and delete one file before rerun.
    (repo_root / 'orders.py').write_text(
        'def process(order_id):\n    return str(order_id)\n',
        encoding='utf-8',
    )
    (repo_root / 'pricing.py').unlink()

    second_exit = main(
        [
            'index',
            '--workspace-root',
            str(workspace_root),
            '--repo',
            'checkout-service',
            '--state-root',
            str(state_root),
            '--trace-id',
            'trace-mvp-incremental-2',
            '--embedding-backend',
            'hash',
            '--observability-backend',
            'jsonl',
        ]
    )
    assert second_exit == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert summary['files_scanned'] == 1
    assert summary['changed_files'] == 1
    assert summary['deleted_files'] == 1
    assert summary['skipped_files'] == 0
    assert summary['unresolved_edges'] == 1
    assert summary['actionable_unresolved_edges'] == 0
    assert summary['actionable_resolution_rate'] == 1.0
    assert summary['vectors_upserted'] == 1
    assert summary['vectors_deleted'] == 1

    index_file = state_root / 'index' / 'symbols.json'
    index_rows = json.loads(index_file.read_text(encoding='utf-8'))
    assert len(index_rows) == 1
    assert index_rows[0]['path'] == 'src/orders.py'
