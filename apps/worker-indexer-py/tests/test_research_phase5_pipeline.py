from __future__ import annotations

from pathlib import Path

from ast_indexer.adapters.embeddings.simple_hash_embedding_generator_adapter import SimpleHashEmbeddingGeneratorAdapter
from ast_indexer.adapters.index_store.json_file_symbol_index_store_adapter import JsonFileSymbolIndexStoreAdapter
from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.adapters.repository.local_fs_repository_reader_adapter import LocalFsRepositoryReaderAdapter
from ast_indexer.adapters.vector_store.json_file_vector_store_adapter import JsonFileVectorStoreAdapter
from ast_indexer.application.research_pipeline import (
    QueryProdderPort,
    ReasoningAgentPort,
    ResearchObjective,
    ResearchPipeline,
)
from ast_indexer.main import build_persistent_index_service
from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor


class DeterministicReasoningAgent(ReasoningAgentPort):
    def build_objective(self, prompt: str, repos_in_scope: tuple[str, ...]) -> ResearchObjective:
        entities = tuple(word for word in prompt.split() if word.isidentifier())
        return ResearchObjective(intent=prompt, entities=entities, repos_in_scope=repos_in_scope)


class DeterministicQueryProdder(QueryProdderPort):
    def build_queries(self, objective: ResearchObjective) -> tuple[str, ...]:
        queries = [objective.intent]
        queries.extend(objective.entities[:4])
        return tuple(dict.fromkeys(item for item in queries if item))


def _build_pipeline(workspace_root: Path, state_root: Path) -> ResearchPipeline:
    return ResearchPipeline(
        reasoning_agent=DeterministicReasoningAgent(),
        query_prodder=DeterministicQueryProdder(),
        embedding_generator=SimpleHashEmbeddingGeneratorAdapter(),
        vector_store=JsonFileVectorStoreAdapter(state_root / 'index' / 'vectors.json'),
        index_store=JsonFileSymbolIndexStoreAdapter(state_root / 'index' / 'symbols.json'),
        repository_reader=LocalFsRepositoryReaderAdapter(workspace_root),
        extractor=PythonAstSymbolExtractor(),
        observability=InMemoryObservabilityAdapter(),
    )


def test_phase5_expands_frontier_then_filters_with_relevancy(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    repo_root = workspace_root / 'catalog-service' / 'src'
    repo_root.mkdir(parents=True)

    body = []
    for idx in range(12):
        body.append(f'def alpha_feature_{idx}():\n    return {idx}\n')
    (repo_root / 'catalog.py').write_text('\n'.join(body), encoding='utf-8')

    state_root = tmp_path / 'state'
    index_service = build_persistent_index_service(
        workspace_root=workspace_root,
        state_root=state_root,
        embedding_backend='hash',
        observability_backend='jsonl',
    )
    index_service.index_repository(repo='catalog-service', trace_id='index-phase5-a')

    pipeline = _build_pipeline(workspace_root, state_root)
    result = pipeline.run(
        trace_id='research-phase5-a',
        prompt='alpha feature ranking',
        repos_in_scope=('catalog-service',),
        top_k=3,
        candidate_pool_multiplier=5,
        relevancy_threshold=0.0,
        relevancy_workers=4,
        reducer_token_budget=320,
        reducer_max_contexts=3,
    )

    assert len(result.candidates) > 3
    assert len(result.relevant_candidates) == 3
    assert len(result.enriched_context) <= 3
    assert len(result.reduced_context) <= 3
    assert sum(row.estimated_tokens for row in result.reduced_context) <= 320


def test_phase5_reducer_truncates_bodies_when_budget_is_tight(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    repo_root = workspace_root / 'billing-service' / 'src'
    repo_root.mkdir(parents=True)

    huge_block = 'x' * 1500
    (repo_root / 'billing.py').write_text(
        'def expensive_billing_path(invoice_id):\n'
        f'    payload = "{huge_block}"\n'
        '    return payload + str(invoice_id)\n',
        encoding='utf-8',
    )

    state_root = tmp_path / 'state'
    index_service = build_persistent_index_service(
        workspace_root=workspace_root,
        state_root=state_root,
        embedding_backend='hash',
        observability_backend='jsonl',
    )
    index_service.index_repository(repo='billing-service', trace_id='index-phase5-b')

    pipeline = _build_pipeline(workspace_root, state_root)
    result = pipeline.run(
        trace_id='research-phase5-b',
        prompt='expensive billing path',
        repos_in_scope=('billing-service',),
        top_k=1,
        candidate_pool_multiplier=4,
        relevancy_threshold=0.0,
        relevancy_workers=2,
        reducer_token_budget=32,
        reducer_max_contexts=1,
    )

    assert len(result.reduced_context) == 1
    reduced = result.reduced_context[0]
    assert reduced.body_was_truncated is True
    assert reduced.estimated_tokens <= 40
