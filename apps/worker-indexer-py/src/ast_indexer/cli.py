from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal
from uuid import uuid4

from ast_indexer.adapters.repository.local_fs_repository_reader_adapter import LocalFsRepositoryReaderAdapter
from ast_indexer.application import runtime_config
from ast_indexer.application.call_graph_linker import CallGraphLinker
from ast_indexer.application.startup_preconditions import (
    validate_index_or_research,
    validate_serve_webhook,
)
from ast_indexer.domain.models import SymbolRecord
from ast_indexer.main import build_persistent_index_service, build_persistent_research_pipeline
from ast_indexer.server import run_webhook_server


def _env_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f'Invalid integer value for {name}: {raw}') from exc


def _walk_directories_up(start: Path) -> Iterable[Path]:
    if start.is_file():
        start = start.parent
    yield from [start, *start.parents]


def _repo_root_for_dotenv() -> Path | None:
    """Directory that holds the canonical `.env.example` (repository root only, never a parent folder)."""
    for anchor in (Path(__file__).resolve(), Path.cwd()):
        for directory in _walk_directories_up(anchor):
            if (directory / '.env.example').is_file():
                return directory
    return None


def _load_environment() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    root = _repo_root_for_dotenv()
    if root is None:
        return
    dotenv_path = root / '.env'
    if dotenv_path.is_file():
        load_dotenv(dotenv_path, override=False)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='ast-indexer', description='Python repository AST indexer')
    subparsers = parser.add_subparsers(dest='command', required=True)

    index = subparsers.add_parser('index', help='Index a Python repository')
    index.add_argument('--workspace-root', type=Path, required=True, help='Base path that contains repositories')
    index.add_argument('--repo', type=str, required=True, help='Repository directory name under workspace-root')
    index.add_argument('--state-root', type=Path, required=True, help='Path for persistent index and span files')
    index.add_argument('--trace-id', type=str, required=False, help='Optional explicit trace id')
    index.add_argument(
        '--embedding-backend',
        type=str,
        choices=['hash', 'sentence-transformers', 'openai'],
        default=os.getenv('AST_INDEXER_EMBEDDING_BACKEND', 'hash'),
        help='Embedding backend for vectors',
    )
    index.add_argument(
        '--embedding-model',
        type=str,
        default=os.getenv('AST_INDEXER_EMBEDDING_MODEL', 'sentence-transformers/all-MiniLM-L6-v2'),
        help='Model name when embedding-backend=sentence-transformers',
    )
    index.add_argument(
        '--embedding-device',
        type=str,
        required=False,
        help='Optional model device (e.g. cpu, cuda)',
    )
    index.add_argument(
        '--openai-api-key',
        type=str,
        required=False,
        default=os.getenv('OPENAI_API_KEY') or os.getenv('AST_INDEXER_OPENAI_API_KEY'),
        help='OpenAI API key (falls back to OPENAI_API_KEY)',
    )
    index.add_argument(
        '--openai-base-url',
        type=str,
        required=False,
        default=os.getenv('AST_INDEXER_OPENAI_BASE_URL'),
        help='Optional OpenAI-compatible base URL',
    )
    index.add_argument(
        '--openai-dimensions',
        type=int,
        required=False,
        default=_env_int('AST_INDEXER_OPENAI_DIMENSIONS'),
        help='Optional embedding dimensions for text-embedding-3 models',
    )
    index.add_argument(
        '--no-normalize-embeddings',
        action='store_true',
        help='Disable L2 normalization for sentence-transformers embeddings',
    )
    index.add_argument(
        '--observability-backend',
        type=str,
        choices=['jsonl', 'langfuse'],
        default=os.getenv('AST_INDEXER_OBSERVABILITY_BACKEND', 'jsonl'),
        help='Observability backend for span export',
    )
    index.add_argument('--langfuse-host', type=str, required=False, default=os.getenv('LANGFUSE_HOST'))
    index.add_argument('--langfuse-public-key', type=str, required=False, default=os.getenv('LANGFUSE_PUBLIC_KEY'))
    index.add_argument('--langfuse-secret-key', type=str, required=False, default=os.getenv('LANGFUSE_SECRET_KEY'))
    index.add_argument(
        '--observability-strict',
        action='store_true',
        default=os.getenv('AST_INDEXER_OBSERVABILITY_STRICT', 'false').lower() in ('1', 'true', 'yes'),
        help='Fail runtime operations when observability transport fails',
    )

    research = subparsers.add_parser('research', help='Run LangGraph research pipeline against indexed data')
    research.add_argument('--workspace-root', type=Path, required=True, help='Base path that contains repositories')
    research.add_argument('--state-root', type=Path, required=True, help='Path for persistent index and span files')
    research.add_argument('--prompt', type=str, required=True, help='Natural language research prompt')
    research.add_argument(
        '--repo',
        action='append',
        default=[],
        help='Repository name in scope. Repeat for multiple repos.',
    )
    research.add_argument('--trace-id', type=str, required=False, help='Optional explicit trace id')
    research.add_argument('--top-k', type=int, default=8, help='Top K candidates to enrich')
    research.add_argument(
        '--candidate-pool-multiplier',
        type=int,
        default=_env_int('AST_INDEXER_CANDIDATE_POOL_MULTIPLIER') or 6,
        help='Multiplier for vector-search frontier size before relevancy filtering',
    )
    research.add_argument(
        '--relevancy-threshold',
        type=float,
        default=float(os.getenv('AST_INDEXER_RELEVANCY_THRESHOLD', '0.35')),
        help='Minimum confidence threshold for Phase 5 relevancy filtering',
    )
    research.add_argument(
        '--relevancy-workers',
        type=int,
        default=_env_int('AST_INDEXER_RELEVANCY_WORKERS') or 6,
        help='Parallel worker count for Phase 5 relevancy scoring',
    )
    research.add_argument(
        '--reducer-token-budget',
        type=int,
        default=_env_int('AST_INDEXER_REDUCER_TOKEN_BUDGET') or 2500,
        help='Token budget for reducer-style compacted context output',
    )
    research.add_argument(
        '--reducer-max-contexts',
        type=int,
        required=False,
        default=_env_int('AST_INDEXER_REDUCER_MAX_CONTEXTS'),
        help='Optional maximum number of contexts retained by reducer output',
    )
    research.add_argument(
        '--research-model',
        type=str,
        default=os.getenv('AST_INDEXER_RESEARCH_MODEL') or runtime_config.default_openai_model(),
        help='OpenAI model for reasoning + query generation',
    )
    research.add_argument(
        '--embedding-backend',
        type=str,
        choices=['hash', 'sentence-transformers', 'openai'],
        default=os.getenv('AST_INDEXER_EMBEDDING_BACKEND', 'hash'),
        help='Embedding backend for retrieval query vectors',
    )
    research.add_argument(
        '--embedding-model',
        type=str,
        default=os.getenv('AST_INDEXER_EMBEDDING_MODEL', 'sentence-transformers/all-MiniLM-L6-v2'),
        help='Model name when embedding-backend=sentence-transformers or openai',
    )
    research.add_argument(
        '--embedding-device',
        type=str,
        required=False,
        help='Optional model device (e.g. cpu, cuda)',
    )
    research.add_argument(
        '--openai-api-key',
        type=str,
        required=False,
        default=os.getenv('OPENAI_API_KEY') or os.getenv('AST_INDEXER_OPENAI_API_KEY'),
        help='OpenAI API key (falls back to OPENAI_API_KEY)',
    )
    research.add_argument(
        '--openai-base-url',
        type=str,
        required=False,
        default=os.getenv('AST_INDEXER_OPENAI_BASE_URL'),
        help='Optional OpenAI-compatible base URL',
    )
    research.add_argument(
        '--openai-dimensions',
        type=int,
        required=False,
        default=_env_int('AST_INDEXER_OPENAI_DIMENSIONS'),
        help='Optional embedding dimensions for text-embedding-3 models',
    )
    research.add_argument(
        '--no-normalize-embeddings',
        action='store_true',
        help='Disable L2 normalization for sentence-transformers embeddings',
    )
    research.add_argument(
        '--observability-backend',
        type=str,
        choices=['jsonl', 'langfuse'],
        default=os.getenv('AST_INDEXER_OBSERVABILITY_BACKEND', 'jsonl'),
        help='Observability backend for span export',
    )
    research.add_argument('--langfuse-host', type=str, required=False, default=os.getenv('LANGFUSE_HOST'))
    research.add_argument('--langfuse-public-key', type=str, required=False, default=os.getenv('LANGFUSE_PUBLIC_KEY'))
    research.add_argument('--langfuse-secret-key', type=str, required=False, default=os.getenv('LANGFUSE_SECRET_KEY'))
    research.add_argument(
        '--observability-strict',
        action='store_true',
        default=os.getenv('AST_INDEXER_OBSERVABILITY_STRICT', 'false').lower() in ('1', 'true', 'yes'),
        help='Fail runtime operations when observability transport fails',
    )

    serve = subparsers.add_parser('serve-webhook', help='Run local GitHub webhook server')
    serve.add_argument('--workspace-root', type=Path, required=True, help='Base path that contains repositories')
    serve.add_argument('--state-root', type=Path, required=True, help='Path for persistent index and span files')
    serve.add_argument(
        '--webhook-secret',
        type=str,
        required=False,
        default=os.getenv('AST_INDEXER_WEBHOOK_SECRET'),
        help='GitHub webhook shared secret',
    )
    serve.add_argument('--host', type=str, default=runtime_config.default_bind_host(), help='Bind host')
    serve.add_argument('--port', type=int, default=_env_int('AST_INDEXER_PORT') or 8080, help='Bind port')
    serve.add_argument(
        '--queue-backend',
        type=str,
        choices=['memory', 'redis'],
        default=os.getenv('AST_INDEXER_QUEUE_BACKEND', 'memory'),
        help='Queue backend',
    )
    serve.add_argument('--redis-url', type=str, required=False, default=os.getenv('AST_INDEXER_REDIS_URL'), help='Redis URL when queue backend is redis')
    serve.add_argument('--redis-key', type=str, default=os.getenv('AST_INDEXER_REDIS_KEY', 'ast_indexer:index_jobs'), help='Redis list key')
    serve.add_argument(
        '--redis-dead-letter-key',
        type=str,
        default=os.getenv('AST_INDEXER_REDIS_DEAD_LETTER_KEY', 'ast_indexer:index_jobs:dead_letter'),
        help='Redis dead-letter list key',
    )
    serve.add_argument(
        '--max-attempts',
        type=int,
        default=_env_int('AST_INDEXER_MAX_ATTEMPTS') or 3,
        help='Max retries before moving job to dead-letter queue',
    )
    serve.add_argument(
        '--embedding-backend',
        type=str,
        choices=['hash', 'sentence-transformers', 'openai'],
        default=os.getenv('AST_INDEXER_EMBEDDING_BACKEND', 'hash'),
        help='Embedding backend for vectors',
    )
    serve.add_argument(
        '--embedding-model',
        type=str,
        default=os.getenv('AST_INDEXER_EMBEDDING_MODEL', 'sentence-transformers/all-MiniLM-L6-v2'),
        help='Model name when embedding-backend=sentence-transformers',
    )
    serve.add_argument(
        '--embedding-device',
        type=str,
        required=False,
        help='Optional model device (e.g. cpu, cuda)',
    )
    serve.add_argument(
        '--openai-api-key',
        type=str,
        required=False,
        default=os.getenv('OPENAI_API_KEY') or os.getenv('AST_INDEXER_OPENAI_API_KEY'),
        help='OpenAI API key (falls back to OPENAI_API_KEY)',
    )
    serve.add_argument(
        '--openai-base-url',
        type=str,
        required=False,
        default=os.getenv('AST_INDEXER_OPENAI_BASE_URL'),
        help='Optional OpenAI-compatible base URL',
    )
    serve.add_argument(
        '--openai-dimensions',
        type=int,
        required=False,
        default=_env_int('AST_INDEXER_OPENAI_DIMENSIONS'),
        help='Optional embedding dimensions for text-embedding-3 models',
    )
    serve.add_argument(
        '--no-normalize-embeddings',
        action='store_true',
        help='Disable L2 normalization for sentence-transformers embeddings',
    )
    serve.add_argument(
        '--observability-backend',
        type=str,
        choices=['jsonl', 'langfuse'],
        default=os.getenv('AST_INDEXER_OBSERVABILITY_BACKEND', 'jsonl'),
        help='Observability backend for span export',
    )
    serve.add_argument('--langfuse-host', type=str, required=False, default=os.getenv('LANGFUSE_HOST'))
    serve.add_argument('--langfuse-public-key', type=str, required=False, default=os.getenv('LANGFUSE_PUBLIC_KEY'))
    serve.add_argument('--langfuse-secret-key', type=str, required=False, default=os.getenv('LANGFUSE_SECRET_KEY'))
    serve.add_argument(
        '--observability-strict',
        action='store_true',
        default=os.getenv('AST_INDEXER_OBSERVABILITY_STRICT', 'false').lower() in ('1', 'true', 'yes'),
        help='Fail runtime operations when observability transport fails',
    )

    return parser


def run_index_once(
    workspace_root: Path,
    repo: str,
    state_root: Path,
    trace_id: str | None = None,
    embedding_backend: Literal['hash', 'sentence-transformers', 'openai'] = 'hash',
    embedding_model: str = 'sentence-transformers/all-MiniLM-L6-v2',
    embedding_device: str | None = None,
    normalize_embeddings: bool = True,
    openai_api_key: str | None = None,
    openai_base_url: str | None = None,
    openai_dimensions: int | None = None,
    observability_backend: Literal['jsonl', 'langfuse'] = 'jsonl',
    langfuse_host: str | None = None,
    langfuse_public_key: str | None = None,
    langfuse_secret_key: str | None = None,
    observability_strict: bool = False,
) -> dict:
    service = build_persistent_index_service(
        workspace_root=workspace_root,
        state_root=state_root,
        embedding_backend=embedding_backend,
        embedding_model=embedding_model,
        embedding_device=embedding_device,
        normalize_embeddings=normalize_embeddings,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        openai_dimensions=openai_dimensions,
        observability_backend=observability_backend,
        langfuse_host=langfuse_host,
        langfuse_public_key=langfuse_public_key,
        langfuse_secret_key=langfuse_secret_key,
        observability_strict=observability_strict,
    )
    run_trace_id = trace_id or f'run-{datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")}-{uuid4().hex[:8]}'

    index_file = state_root / 'index' / 'symbols.json'
    manifest_file = state_root / 'index' / 'file_manifest.json'
    call_edges_file = state_root / 'index' / 'call_edges.json'
    unresolved_edges_file = state_root / 'index' / 'unresolved_call_edges.json'
    vectors_file = state_root / 'index' / 'vectors.json'
    spans_file = state_root / 'observability' / 'spans.jsonl'

    reader = LocalFsRepositoryReaderAdapter(workspace_root)
    current_paths = reader.list_python_files(repo)
    current_hashes: dict[str, str] = {}
    for file_path in current_paths:
        file_content = reader.read_python_file(repo, file_path).content
        current_hashes[file_path] = hashlib.sha256(file_content.encode('utf-8')).hexdigest()

    previous_hashes = _read_manifest(manifest_file)
    previous_paths = set(previous_hashes)
    current_path_set = set(current_hashes)

    changed_paths = sorted([path for path in current_paths if previous_hashes.get(path) != current_hashes[path]])
    deleted_paths = sorted(previous_paths - current_path_set)
    skipped_files = len(current_paths) - len(changed_paths)

    metrics = service.index_repository_subset(
        repo=repo,
        trace_id=run_trace_id,
        file_paths=changed_paths,
        deleted_paths=deleted_paths,
    )
    _write_manifest(manifest_file, current_hashes)

    symbols = _read_symbols(index_file)
    link_report = CallGraphLinker().link_report(symbols)
    call_edges_file.parent.mkdir(parents=True, exist_ok=True)
    call_edges_file.write_text(
        json.dumps(
            [
                {
                    'repo': edge.repo,
                    'caller_path': edge.caller_path,
                    'caller_symbol': edge.caller_symbol,
                    'callee': edge.callee,
                    'resolved_path': edge.resolved_path,
                    'resolved_symbol': edge.resolved_symbol,
                    'resolved_canonical': edge.resolved_canonical,
                }
                for edge in link_report.linked_edges
            ],
            indent=2,
        ),
        encoding='utf-8',
    )
    unresolved_edges_file.write_text(
        json.dumps(
            [
                {
                    'repo': edge.repo,
                    'caller_path': edge.caller_path,
                    'caller_symbol': edge.caller_symbol,
                    'callee': edge.callee,
                    'reason': edge.reason,
                    'actionable': edge.actionable,
                }
                for edge in link_report.unresolved_edges
            ],
            indent=2,
        ),
        encoding='utf-8',
    )

    if link_report.actionable_resolution_rate >= 0.85:
        linkage_quality = 'high'
    elif link_report.actionable_resolution_rate >= 0.5:
        linkage_quality = 'medium'
    else:
        linkage_quality = 'low'

    return {
        'status': 'ok',
        'repo': repo,
        'trace_id': run_trace_id,
        'files_scanned': metrics.files_scanned,
        'changed_files': len(changed_paths),
        'deleted_files': len(deleted_paths),
        'skipped_files': skipped_files,
        'symbols_indexed': metrics.symbols_indexed,
        'vectors_upserted': metrics.vectors_upserted,
        'vectors_deleted': metrics.vectors_deleted,
        'linked_edges': len(link_report.linked_edges),
        'unresolved_edges': len(link_report.unresolved_edges),
        'actionable_unresolved_edges': len(link_report.actionable_unresolved_edges),
        'resolution_rate': round(link_report.resolution_rate, 4),
        'actionable_resolution_rate': round(link_report.actionable_resolution_rate, 4),
        'linkage_quality': linkage_quality,
        'duration_ms': int((metrics.finished_at - metrics.started_at).total_seconds() * 1000),
        'index_file': str(index_file),
        'manifest_file': str(manifest_file),
        'call_edges_file': str(call_edges_file),
        'unresolved_edges_file': str(unresolved_edges_file),
        'vectors_file': str(vectors_file),
        'spans_file': str(spans_file),
    }


def _read_symbols(index_file: Path) -> list[SymbolRecord]:
    if not index_file.exists():
        return []

    rows = json.loads(index_file.read_text(encoding='utf-8'))
    return [
        SymbolRecord(
            repo=row['repo'],
            path=row['path'],
            symbol=row['symbol'],
            kind=row['kind'],
            line=row['line'],
            signature=row['signature'],
            docstring=row.get('docstring'),
            callees=tuple(row.get('callees', [])),
        )
        for row in rows
    ]


def _read_manifest(manifest_file: Path) -> dict[str, str]:
    if not manifest_file.exists():
        return {}
    return json.loads(manifest_file.read_text(encoding='utf-8'))


def _write_manifest(manifest_file: Path, manifest: dict[str, str]) -> None:
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding='utf-8')


def main(argv: list[str] | None = None) -> int:
    _load_environment()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == 'index':
        try:
            validate_index_or_research(
                args.embedding_backend,
                args.openai_api_key,
                args.observability_backend,
                args.langfuse_host,
                args.langfuse_public_key,
                args.langfuse_secret_key,
            )
        except ValueError as exc:
            print(f'ERROR: {exc}', file=sys.stderr)
            return 2
        summary = run_index_once(
            workspace_root=args.workspace_root,
            repo=args.repo,
            state_root=args.state_root,
            trace_id=args.trace_id,
            embedding_backend=args.embedding_backend,
            embedding_model=args.embedding_model,
            embedding_device=args.embedding_device,
            normalize_embeddings=not args.no_normalize_embeddings,
            openai_api_key=args.openai_api_key,
            openai_base_url=args.openai_base_url,
            openai_dimensions=args.openai_dimensions,
            observability_backend=args.observability_backend,
            langfuse_host=args.langfuse_host,
            langfuse_public_key=args.langfuse_public_key,
            langfuse_secret_key=args.langfuse_secret_key,
            observability_strict=args.observability_strict,
        )
        print(json.dumps(summary, indent=2))
        return 0

    if args.command == 'serve-webhook':
        if not args.webhook_secret:
            parser.error('webhook secret is required: set --webhook-secret or AST_INDEXER_WEBHOOK_SECRET')
        try:
            validate_serve_webhook(
                args.embedding_backend,
                args.openai_api_key,
                args.observability_backend,
                args.langfuse_host,
                args.langfuse_public_key,
                args.langfuse_secret_key,
                args.queue_backend,
                args.redis_url,
            )
        except ValueError as exc:
            print(f'ERROR: {exc}', file=sys.stderr)
            return 2
        run_webhook_server(
            workspace_root=args.workspace_root,
            state_root=args.state_root,
            webhook_secret=args.webhook_secret,
            host=args.host,
            port=args.port,
            queue_backend=args.queue_backend,
            redis_url=args.redis_url,
            redis_key=args.redis_key,
            redis_dead_letter_key=args.redis_dead_letter_key,
            max_attempts=args.max_attempts,
            embedding_backend=args.embedding_backend,
            embedding_model=args.embedding_model,
            embedding_device=args.embedding_device,
            normalize_embeddings=not args.no_normalize_embeddings,
            openai_api_key=args.openai_api_key,
            openai_base_url=args.openai_base_url,
            openai_dimensions=args.openai_dimensions,
            observability_backend=args.observability_backend,
            langfuse_host=args.langfuse_host,
            langfuse_public_key=args.langfuse_public_key,
            langfuse_secret_key=args.langfuse_secret_key,
            observability_strict=args.observability_strict,
        )
        return 0

    if args.command == 'research':
        try:
            validate_index_or_research(
                args.embedding_backend,
                args.openai_api_key,
                args.observability_backend,
                args.langfuse_host,
                args.langfuse_public_key,
                args.langfuse_secret_key,
            )
        except ValueError as exc:
            print(f'ERROR: {exc}', file=sys.stderr)
            return 2
        run_trace_id = args.trace_id or f'research-{datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")}-{uuid4().hex[:8]}'
        pipeline = build_persistent_research_pipeline(
            workspace_root=args.workspace_root,
            state_root=args.state_root,
            embedding_backend=args.embedding_backend,
            embedding_model=args.embedding_model,
            embedding_device=args.embedding_device,
            normalize_embeddings=not args.no_normalize_embeddings,
            openai_api_key=args.openai_api_key,
            openai_base_url=args.openai_base_url,
            openai_dimensions=args.openai_dimensions,
            observability_backend=args.observability_backend,
            langfuse_host=args.langfuse_host,
            langfuse_public_key=args.langfuse_public_key,
            langfuse_secret_key=args.langfuse_secret_key,
            observability_strict=args.observability_strict,
            research_model=args.research_model,
        )
        result = pipeline.run(
            trace_id=run_trace_id,
            prompt=args.prompt,
            repos_in_scope=tuple(args.repo),
            top_k=args.top_k,
            candidate_pool_multiplier=args.candidate_pool_multiplier,
            relevancy_threshold=args.relevancy_threshold,
            relevancy_workers=args.relevancy_workers,
            reducer_token_budget=args.reducer_token_budget,
            reducer_max_contexts=args.reducer_max_contexts,
        )
        print(
            json.dumps(
                {
                    'status': 'ok',
                    'trace_id': result.trace_id,
                    'objective': {
                        'intent': result.objective.intent,
                        'entities': list(result.objective.entities),
                        'repos_in_scope': list(result.objective.repos_in_scope),
                    },
                    'query_count': len(result.queries),
                    'queries': list(result.queries),
                    'candidate_count': len(result.candidates),
                    'relevant_count': len(result.relevant_candidates),
                    'enriched_count': len(result.enriched_context),
                    'reduced_count': len(result.reduced_context),
                    'relevancy': [
                        {
                            'repo': row.repo,
                            'path': row.path,
                            'symbol': row.symbol,
                            'score': row.score,
                            'confidence': row.confidence,
                            'matched_terms': list(row.matched_terms),
                        }
                        for row in result.relevant_candidates
                    ],
                    'enriched_context': [
                        {
                            'repo': row.repo,
                            'path': row.path,
                            'symbol': row.symbol,
                            'kind': row.kind,
                            'signature': row.signature,
                            'docstring': row.docstring,
                            'callees': list(row.callees),
                            'resolved_callees': list(row.resolved_callees),
                        }
                        for row in result.enriched_context
                    ],
                    'reduced_context': [
                        {
                            'repo': row.repo,
                            'path': row.path,
                            'symbol': row.symbol,
                            'kind': row.kind,
                            'signature': row.signature,
                            'docstring': row.docstring,
                            'estimated_tokens': row.estimated_tokens,
                            'body_was_truncated': row.body_was_truncated,
                            'callees': list(row.callees),
                            'resolved_callees': list(row.resolved_callees),
                        }
                        for row in result.reduced_context
                    ],
                },
                indent=2,
            )
        )
        return 0

    parser.error('Unknown command')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
