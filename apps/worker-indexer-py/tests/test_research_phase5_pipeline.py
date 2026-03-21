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


class LlmScoredReasoningAgent(DeterministicReasoningAgent):
    def score_relevancy_batch(self, *, objective: dict, candidates: list[dict]) -> dict:  # noqa: ANN001
        _ = objective
        scores = []
        for row in candidates:
            symbol = str(row.get('symbol', ''))
            confidence = 0.95 if 'alpha_feature_11' in symbol else 0.1
            scores.append(
                {
                    'repo': row.get('repo'),
                    'path': row.get('path'),
                    'symbol': row.get('symbol'),
                    'confidence': confidence,
                    'matched_terms': ['alpha'],
                }
            )
        return {'scores': scores}


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


def test_phase5_emits_all_research_spans_and_reducer_metrics(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    repo_root = workspace_root / 'checkout-service' / 'src'
    repo_root.mkdir(parents=True)
    (repo_root / 'orders.py').write_text(
        'def process(order_id):\n'
        '    payload = "x" * 1200\n'
        '    return f"{payload}-{order_id}"\n',
        encoding='utf-8',
    )

    state_root = tmp_path / 'state'
    index_service = build_persistent_index_service(
        workspace_root=workspace_root,
        state_root=state_root,
        embedding_backend='hash',
        observability_backend='jsonl',
    )
    index_service.index_repository(repo='checkout-service', trace_id='index-phase5-c')

    observability = InMemoryObservabilityAdapter()
    pipeline = ResearchPipeline(
        reasoning_agent=DeterministicReasoningAgent(),
        query_prodder=DeterministicQueryProdder(),
        embedding_generator=SimpleHashEmbeddingGeneratorAdapter(),
        vector_store=JsonFileVectorStoreAdapter(state_root / 'index' / 'vectors.json'),
        index_store=JsonFileSymbolIndexStoreAdapter(state_root / 'index' / 'symbols.json'),
        repository_reader=LocalFsRepositoryReaderAdapter(workspace_root),
        extractor=PythonAstSymbolExtractor(),
        observability=observability,
    )

    _ = pipeline.run(
        trace_id='research-phase5-c',
        prompt='process order payload',
        repos_in_scope=('checkout-service',),
        top_k=3,
        candidate_pool_multiplier=4,
        relevancy_threshold=0.0,
        relevancy_workers=2,
        reducer_token_budget=64,
        reducer_max_contexts=2,
    )

    spans = [row for row in observability.list_spans() if row.trace_id == 'research-phase5-c']
    span_names = {row.name for row in spans}
    assert {
        'research_pipeline_run',
        'reasoning_agent',
        'semantic_prodder',
        'vector_search',
        'relevancy_engine',
        'live_repo_reader',
        'reducer_engine',
    }.issubset(span_names)

    reducer_span = next(row for row in spans if row.name == 'reducer_engine')
    assert reducer_span.metadata is not None
    assert reducer_span.metadata['planner'] == 'relation_line_planner'
    assert reducer_span.metadata['token_budget'] == 64
    assert reducer_span.metadata['overrun'] is False
    assert reducer_span.metadata['consumed_tokens'] <= 64


def test_phase5_uses_llm_batch_relevancy_when_available(tmp_path: Path) -> None:
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
    index_service.index_repository(repo='catalog-service', trace_id='index-phase5-llm-rel')

    pipeline = ResearchPipeline(
        reasoning_agent=LlmScoredReasoningAgent(),
        query_prodder=DeterministicQueryProdder(),
        embedding_generator=SimpleHashEmbeddingGeneratorAdapter(),
        vector_store=JsonFileVectorStoreAdapter(state_root / 'index' / 'vectors.json'),
        index_store=JsonFileSymbolIndexStoreAdapter(state_root / 'index' / 'symbols.json'),
        repository_reader=LocalFsRepositoryReaderAdapter(workspace_root),
        extractor=PythonAstSymbolExtractor(),
        observability=InMemoryObservabilityAdapter(),
        reducer_use_inference=False,
        relevancy_use_inference=True,
    )

    result = pipeline.run(
        trace_id='research-phase5-llm-rel',
        prompt='alpha feature ranking',
        repos_in_scope=('catalog-service',),
        top_k=1,
        candidate_pool_multiplier=6,
        relevancy_threshold=0.9,
        reducer_token_budget=300,
    )

    assert len(result.relevant_candidates) == 1
    assert result.relevant_candidates[0].symbol == 'alpha_feature_11'
