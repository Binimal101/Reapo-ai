import os
from pathlib import Path
from uuid import uuid4
from ast_indexer.cli import _load_environment
from ast_indexer.main import build_persistent_index_service, build_persistent_research_pipeline

os.environ['LANGFUSE_DEBUG'] = 'false'
_load_environment()
workspace_root = Path('c:/Users/matth/OneDrive/Desktop/programs/personal_brand/Reapo-ai/apps')
state_root = Path('c:/Users/matth/OneDrive/Desktop/programs/personal_brand/Reapo-ai/apps/worker-indexer-py/.mvp-state-quality')
index_trace = f'lf-group-index-{uuid4().hex[:8]}'
research_trace = f'lf-group-research-{uuid4().hex[:8]}'

index_service = build_persistent_index_service(
    workspace_root=workspace_root,
    state_root=state_root,
    embedding_backend='hash',
    observability_backend='langfuse',
    observability_strict=True,
)
index_metrics = index_service.index_repository(repo='worker-indexer-py', trace_id=index_trace)

research = build_persistent_research_pipeline(
    workspace_root=workspace_root,
    state_root=state_root,
    embedding_backend='hash',
    observability_backend='langfuse',
    observability_strict=True,
)
research_result = research.run(
    prompt='Explain how webhook index jobs are dispatched and processed',
    repos_in_scope=('worker-indexer-py',),
    trace_id=research_trace,
    top_k=6,
    candidate_pool_multiplier=6,
    relevancy_threshold=0.35,
    relevancy_workers=6,
    reducer_token_budget=2500,
)
print(index_trace)
print(research_trace)
print(index_metrics.files_scanned, index_metrics.symbols_indexed, index_metrics.vectors_upserted)
print(len(research_result.queries), len(research_result.candidates), len(research_result.relevant_candidates), len(research_result.reduced_context))
