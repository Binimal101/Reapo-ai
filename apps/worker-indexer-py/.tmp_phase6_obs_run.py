import os
from pathlib import Path
from uuid import uuid4
from ast_indexer.cli import _load_environment
from ast_indexer.main import build_persistent_research_pipeline

os.environ['LANGFUSE_DEBUG'] = 'false'
_load_environment()
workspace_root = Path('c:/Users/matth/OneDrive/Desktop/programs/personal_brand/Reapo-ai/apps')
state_root = Path('c:/Users/matth/OneDrive/Desktop/programs/personal_brand/Reapo-ai/apps/worker-indexer-py/.mvp-state-quality')
trace_id = f'lf-phase6-obs-{uuid4().hex[:8]}'

pipeline = build_persistent_research_pipeline(
    workspace_root=workspace_root,
    state_root=state_root,
    embedding_backend='hash',
    observability_backend='langfuse',
    observability_strict=True,
)
_ = pipeline.run(
    trace_id=trace_id,
    prompt='Explain how webhook index jobs are dispatched and processed',
    repos_in_scope=('worker-indexer-py',),
    top_k=6,
    candidate_pool_multiplier=6,
    relevancy_threshold=0.35,
    relevancy_workers=6,
    reducer_token_budget=2500,
)
print(trace_id)
