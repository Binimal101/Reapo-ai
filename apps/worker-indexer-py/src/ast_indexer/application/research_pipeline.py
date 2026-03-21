from __future__ import annotations

import ast
import math
from dataclasses import asdict, dataclass
from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from ast_indexer.domain.models import SymbolRecord, VectorRecord
from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor
from ast_indexer.ports.embedding_generator import EmbeddingGeneratorPort
from ast_indexer.ports.index_store import IndexStorePort
from ast_indexer.ports.observability import ObservabilityPort
from ast_indexer.ports.repository_reader import RepositoryReaderPort
from ast_indexer.ports.vector_store import VectorStorePort


@dataclass(frozen=True)
class ResearchObjective:
    intent: str
    entities: tuple[str, ...]
    repos_in_scope: tuple[str, ...]


@dataclass(frozen=True)
class ResearchCandidate:
    repo: str
    path: str
    symbol: str
    kind: str
    signature: str
    score: float


@dataclass(frozen=True)
class EnrichedResearchContext:
    repo: str
    path: str
    symbol: str
    kind: str
    signature: str
    docstring: str | None
    body: str
    callees: tuple[str, ...]
    resolved_callees: tuple[str, ...]


@dataclass(frozen=True)
class ResearchPipelineResult:
    trace_id: str
    objective: ResearchObjective
    queries: tuple[str, ...]
    candidates: tuple[ResearchCandidate, ...]
    enriched_context: tuple[EnrichedResearchContext, ...]


class ReasoningAgentPort(Protocol):
    def build_objective(self, prompt: str, repos_in_scope: tuple[str, ...]) -> ResearchObjective:
        """Build a structured objective from raw user prompt."""


class QueryProdderPort(Protocol):
    def build_queries(self, objective: ResearchObjective) -> tuple[str, ...]:
        """Build semantic retrieval queries for a research objective."""


class ResearchState(TypedDict, total=False):
    trace_id: str
    prompt: str
    repos_in_scope: tuple[str, ...]
    top_k: int
    objective: ResearchObjective
    queries: tuple[str, ...]
    candidates: tuple[ResearchCandidate, ...]
    enriched_context: tuple[EnrichedResearchContext, ...]


class ResearchPipeline:
    def __init__(
        self,
        *,
        reasoning_agent: ReasoningAgentPort,
        query_prodder: QueryProdderPort,
        embedding_generator: EmbeddingGeneratorPort,
        vector_store: VectorStorePort,
        index_store: IndexStorePort,
        repository_reader: RepositoryReaderPort,
        extractor: PythonAstSymbolExtractor,
        observability: ObservabilityPort,
    ) -> None:
        self._reasoning_agent = reasoning_agent
        self._query_prodder = query_prodder
        self._embedding_generator = embedding_generator
        self._vector_store = vector_store
        self._index_store = index_store
        self._repository_reader = repository_reader
        self._extractor = extractor
        self._observability = observability
        self._app = self._build_graph()

    def run(
        self,
        *,
        trace_id: str,
        prompt: str,
        repos_in_scope: tuple[str, ...] = (),
        top_k: int = 8,
    ) -> ResearchPipelineResult:
        final_state: ResearchState = self._app.invoke(
            {
                'trace_id': trace_id,
                'prompt': prompt,
                'repos_in_scope': repos_in_scope,
                'top_k': top_k,
            }
        )

        objective = final_state.get('objective')
        if objective is None:
            objective = ResearchObjective(intent='', entities=(), repos_in_scope=repos_in_scope)

        return ResearchPipelineResult(
            trace_id=trace_id,
            objective=objective,
            queries=final_state.get('queries', ()),
            candidates=final_state.get('candidates', ()),
            enriched_context=final_state.get('enriched_context', ()),
        )

    def _build_graph(self):
        graph = StateGraph(ResearchState)
        graph.add_node('reasoning_node', self._reasoning_node)
        graph.add_node('prodder_node', self._prodder_node)
        graph.add_node('vector_search_node', self._vector_search_node)
        graph.add_node('retrieval_node', self._retrieval_node)
        graph.add_edge(START, 'reasoning_node')
        graph.add_edge('reasoning_node', 'prodder_node')
        graph.add_edge('prodder_node', 'vector_search_node')
        graph.add_edge('vector_search_node', 'retrieval_node')
        graph.add_edge('retrieval_node', END)
        return graph.compile()

    def _reasoning_node(self, state: ResearchState) -> ResearchState:
        trace_id = state['trace_id']
        repos = state.get('repos_in_scope', ())
        prompt = state['prompt']
        span = self._observability.start_span(
            'reasoning_agent',
            trace_id,
            input_payload={'prompt': prompt, 'repos_in_scope': list(repos)},
        )
        objective = self._reasoning_agent.build_objective(prompt, repos)
        self._observability.end_span(span, output_payload=asdict(objective))
        return {'objective': objective}

    def _prodder_node(self, state: ResearchState) -> ResearchState:
        trace_id = state['trace_id']
        objective = state['objective']
        span = self._observability.start_span(
            'semantic_prodder',
            trace_id,
            input_payload=asdict(objective),
        )
        queries = self._query_prodder.build_queries(objective)
        self._observability.end_span(
            span,
            output_payload={'queries': list(queries)},
            metadata={'query_count': len(queries)},
        )
        return {'queries': queries}

    def _vector_search_node(self, state: ResearchState) -> ResearchState:
        trace_id = state['trace_id']
        queries = state.get('queries', ())
        objective = state['objective']
        top_k = max(1, int(state.get('top_k', 8)))

        span = self._observability.start_span(
            'vector_search',
            trace_id,
            input_payload={'query_count': len(queries), 'top_k': top_k},
        )

        candidates = self._rank_candidates(queries=queries, objective=objective, top_k=top_k)
        self._observability.end_span(
            span,
            output_payload={'candidate_count': len(candidates)},
            metadata={'repos_in_scope': list(objective.repos_in_scope)},
        )
        return {'candidates': candidates}

    def _retrieval_node(self, state: ResearchState) -> ResearchState:
        trace_id = state['trace_id']
        candidates = state.get('candidates', ())
        span = self._observability.start_span(
            'live_repo_reader',
            trace_id,
            input_payload={'candidate_count': len(candidates)},
        )

        enriched_context = self._enrich_candidates(candidates)
        self._observability.end_span(
            span,
            output_payload={'enriched_count': len(enriched_context)},
        )
        return {'enriched_context': enriched_context}

    def _rank_candidates(
        self,
        *,
        queries: tuple[str, ...],
        objective: ResearchObjective,
        top_k: int,
    ) -> tuple[ResearchCandidate, ...]:
        vectors = self._vector_store.list_vectors()
        if objective.repos_in_scope:
            repo_set = set(objective.repos_in_scope)
            vectors = [row for row in vectors if row.repo in repo_set]

        if not vectors or not queries:
            return ()

        query_vectors = self._embedding_generator.embed(list(queries))
        symbol_lookup = {
            (row.repo, row.path, row.symbol): row
            for row in self._index_store.list_symbols()
        }

        scored: dict[tuple[str, str, str], ResearchCandidate] = {}
        for query_embedding in query_vectors:
            for vector in vectors:
                score = _cosine_similarity(query_embedding, vector.embedding)
                key = (vector.repo, vector.path, vector.symbol)
                symbol_row = symbol_lookup.get(key)
                if symbol_row is None:
                    continue

                candidate = ResearchCandidate(
                    repo=vector.repo,
                    path=vector.path,
                    symbol=vector.symbol,
                    kind=vector.kind,
                    signature=symbol_row.signature,
                    score=score,
                )
                previous = scored.get(key)
                if previous is None or previous.score < candidate.score:
                    scored[key] = candidate

        ranked = sorted(scored.values(), key=lambda row: row.score, reverse=True)
        return tuple(ranked[:top_k])

    def _enrich_candidates(self, candidates: tuple[ResearchCandidate, ...]) -> tuple[EnrichedResearchContext, ...]:
        canonical_map = _build_canonical_symbol_map(self._index_store.list_symbols())
        enriched: list[EnrichedResearchContext] = []

        for candidate in candidates:
            source = self._repository_reader.read_python_file(candidate.repo, candidate.path).content
            extracted = self._extractor.extract(candidate.repo, candidate.path, source)
            symbol = next((row for row in extracted.symbols if row.symbol == candidate.symbol), None)
            if symbol is None:
                continue

            resolved: list[str] = []
            for callee in symbol.callees:
                target = canonical_map.get(callee)
                if target is None:
                    continue
                resolved.append(f'{target.repo}:{target.path}:{target.symbol}')

            body = _extract_symbol_body(source, symbol)
            enriched.append(
                EnrichedResearchContext(
                    repo=symbol.repo,
                    path=symbol.path,
                    symbol=symbol.symbol,
                    kind=symbol.kind,
                    signature=symbol.signature,
                    docstring=symbol.docstring,
                    body=body,
                    callees=symbol.callees,
                    resolved_callees=tuple(resolved),
                )
            )

        return tuple(enriched)


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right:
        return -1.0

    size = min(len(left), len(right))
    dot = sum(left[idx] * right[idx] for idx in range(size))
    left_norm = math.sqrt(sum(left[idx] * left[idx] for idx in range(size)))
    right_norm = math.sqrt(sum(right[idx] * right[idx] for idx in range(size)))
    if left_norm == 0.0 or right_norm == 0.0:
        return -1.0
    return dot / (left_norm * right_norm)


def _module_name(path: str) -> str:
    normalized = path.replace('\\', '/').strip('/')
    if not normalized.endswith('.py'):
        return ''
    module_path = normalized[:-3]
    if module_path.endswith('/__init__'):
        module_path = module_path[: -len('/__init__')]
    return module_path.replace('/', '.')


def _build_canonical_symbol_map(symbols: list[SymbolRecord]) -> dict[str, SymbolRecord]:
    canonical: dict[str, SymbolRecord] = {}
    for symbol in symbols:
        canonical.setdefault(symbol.symbol, symbol)
        module = _module_name(symbol.path)
        if module:
            canonical.setdefault(f'{module}.{symbol.symbol}', symbol)
    return canonical


def _extract_symbol_body(source: str, symbol: SymbolRecord) -> str:
    tree = ast.parse(source)
    lines = source.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent_class = _find_parent_class_name(tree, node)
                if parent_class:
                    name = f'{parent_class}.{node.name}'

            if name != symbol.symbol:
                continue

            segment = ast.get_source_segment(source, node)
            if segment:
                return segment

            start = max(node.lineno - 1, 0)
            end = getattr(node, 'end_lineno', node.lineno)
            return '\n'.join(lines[start:end])

    return ''


def _find_parent_class_name(tree: ast.Module, target: ast.AST) -> str | None:
    for class_node in [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]:
        for child in class_node.body:
            if child is target:
                return class_node.name
    return None
