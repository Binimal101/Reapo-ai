from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from ast_indexer.main import build_persistent_index_service


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Index a Python repository and emit a run summary.',
    )
    parser.add_argument('--repo', required=True, help='Repository name (subdirectory under --workspace)')
    parser.add_argument('--workspace', required=True, type=Path, help='Workspace root containing repositories')
    parser.add_argument('--state', required=True, type=Path, help='State root for persisted index and span logs')
    args = parser.parse_args()

    service = build_persistent_index_service(args.workspace, args.state)
    trace_id = str(uuid.uuid4())

    metrics = service.index_repository(repo=args.repo, trace_id=trace_id)

    duration_ms = int((metrics.finished_at - metrics.started_at).total_seconds() * 1000)
    print(f'Repo:            {args.repo}')
    print(f'Files scanned:   {metrics.files_scanned}')
    print(f'Symbols indexed: {metrics.symbols_indexed}')
    print(f'Linked edges:    {metrics.linked_edges}')
    print(f'Duration:        {duration_ms}ms')
    print(f'Trace ID:        {trace_id}')


if __name__ == '__main__':
    main()
