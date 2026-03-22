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
        queries.extend(objective.entities[:3])
        return tuple(dict.fromkeys(item for item in queries if item))


def test_research_pipeline_runs_end_to_end_on_real_index_data(tmp_path: Path) -> None:
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
    index_service = build_persistent_index_service(
        workspace_root=workspace_root,
        state_root=state_root,
        embedding_backend='hash',
        observability_backend='jsonl',
    )
    index_service.index_repository(repo='checkout-service', trace_id='index-1')

    pipeline = ResearchPipeline(
        reasoning_agent=DeterministicReasoningAgent(),
        query_prodder=DeterministicQueryProdder(),
        embedding_generator=SimpleHashEmbeddingGeneratorAdapter(),
        vector_store=JsonFileVectorStoreAdapter(state_root / 'index' / 'vectors.json'),
        index_store=JsonFileSymbolIndexStoreAdapter(state_root / 'index' / 'symbols.json'),
        repository_reader=LocalFsRepositoryReaderAdapter(workspace_root),
        extractor=PythonAstSymbolExtractor(),
        observability=InMemoryObservabilityAdapter(),
    )

    result = pipeline.run(
        trace_id='research-1',
        prompt='process order helper',
        repos_in_scope=('checkout-service',),
        top_k=4,
    )

    assert result.trace_id == 'research-1'
    assert result.objective.intent == 'process order helper'
    assert len(result.queries) >= 1
    assert len(result.candidates) >= 1
    assert len(result.enriched_context) >= 1

    found = {f'{row.path}:{row.symbol}' for row in result.enriched_context}
    assert 'src/orders.py:process' in found or 'src/util.py:helper' in found


def test_research_pipeline_respects_repo_scope_filter(tmp_path: Path) -> None:
    workspace_root = tmp_path / 'workspace'
    repo_a = workspace_root / 'repo-a' / 'src'
    repo_b = workspace_root / 'repo-b' / 'src'
    repo_a.mkdir(parents=True)
    repo_b.mkdir(parents=True)

    (repo_a / 'a.py').write_text('def alpha():\n    return 1\n', encoding='utf-8')
    (repo_b / 'b.py').write_text('def beta():\n    return 2\n', encoding='utf-8')

    state_root = tmp_path / 'state'
    index_service = build_persistent_index_service(
        workspace_root=workspace_root,
        state_root=state_root,
        embedding_backend='hash',
        observability_backend='jsonl',
    )
    index_service.index_repository(repo='repo-a', trace_id='index-a')
    index_service.index_repository(repo='repo-b', trace_id='index-b')

    pipeline = ResearchPipeline(
        reasoning_agent=DeterministicReasoningAgent(),
        query_prodder=DeterministicQueryProdder(),
        embedding_generator=SimpleHashEmbeddingGeneratorAdapter(),
        vector_store=JsonFileVectorStoreAdapter(state_root / 'index' / 'vectors.json'),
        index_store=JsonFileSymbolIndexStoreAdapter(state_root / 'index' / 'symbols.json'),
        repository_reader=LocalFsRepositoryReaderAdapter(workspace_root),
        extractor=PythonAstSymbolExtractor(),
        observability=InMemoryObservabilityAdapter(),
    )

    result = pipeline.run(
        trace_id='research-scope',
        prompt='beta function',
        repos_in_scope=('repo-a',),
        top_k=5,
    )

    assert all(row.repo == 'repo-a' for row in result.candidates)
    assert all(row.repo == 'repo-a' for row in result.enriched_context)


def test_research_pipeline_logs_langgraph_transitions(tmp_path: Path) -> None:
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
    index_service = build_persistent_index_service(
        workspace_root=workspace_root,
        state_root=state_root,
        embedding_backend='hash',
        observability_backend='jsonl',
    )
    index_service.index_repository(repo='checkout-service', trace_id='index-transition')

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

    pipeline.run(
        trace_id='research-transition',
        prompt='process order helper',
        repos_in_scope=('checkout-service',),
        top_k=4,
    )

    transitions = [
        span for span in observability.list_spans() if span.name == 'langgraph.transition' and span.metadata
    ]
    transition_pairs = {(str(span.metadata['from_node']), str(span.metadata['to_node'])) for span in transitions}
    assert ('START', 'reasoning_node') in transition_pairs
    assert ('reasoning_node', 'prodder_node') in transition_pairs
    assert ('prodder_node', 'vector_search_node') in transition_pairs
    assert ('vector_search_node', 'relevancy_node') in transition_pairs
    assert ('relevancy_node', 'retrieval_node') in transition_pairs
    assert ('retrieval_node', 'reducer_node') in transition_pairs
    assert ('reducer_node', 'END') in transition_pairs
