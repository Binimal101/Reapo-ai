from __future__ import annotations

import ast
import concurrent.futures
import math
import re
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
class RelevancyCandidate:
    repo: str
    path: str
    symbol: str
    kind: str
    signature: str
    score: float
    confidence: float
    matched_terms: tuple[str, ...]


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
class ReducedResearchContext:
    repo: str
    path: str
    symbol: str
    kind: str
    signature: str
    docstring: str | None
    reduced_body: str
    estimated_tokens: int
    body_was_truncated: bool
    callees: tuple[str, ...]
    resolved_callees: tuple[str, ...]


@dataclass(frozen=True)
class ResearchPipelineResult:
    trace_id: str
    objective: ResearchObjective
    queries: tuple[str, ...]
    candidates: tuple[ResearchCandidate, ...]
    relevant_candidates: tuple[RelevancyCandidate, ...]
    enriched_context: tuple[EnrichedResearchContext, ...]
    reduced_context: tuple[ReducedResearchContext, ...]


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
    candidate_pool_multiplier: int
    relevancy_threshold: float
    relevancy_workers: int
    reducer_token_budget: int
    reducer_max_contexts: int
    objective: ResearchObjective
    queries: tuple[str, ...]
    candidates: tuple[ResearchCandidate, ...]
    relevant_candidates: tuple[RelevancyCandidate, ...]
    enriched_context: tuple[EnrichedResearchContext, ...]
    reduced_context: tuple[ReducedResearchContext, ...]
    _active_retrieval_span: object


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
        candidate_pool_multiplier: int = 6,
        relevancy_threshold: float = 0.35,
        relevancy_workers: int = 6,
        reducer_token_budget: int = 2500,
        reducer_max_contexts: int | None = None,
    ) -> ResearchPipelineResult:
        max_contexts = reducer_max_contexts if reducer_max_contexts is not None else top_k
        run_span = self._observability.start_span(
            'research_pipeline_run',
            trace_id,
            input_payload={
                'prompt': prompt,
                'repos_in_scope': list(repos_in_scope),
                'top_k': top_k,
                'candidate_pool_multiplier': candidate_pool_multiplier,
                'relevancy_threshold': relevancy_threshold,
                'relevancy_workers': relevancy_workers,
                'reducer_token_budget': reducer_token_budget,
                'reducer_max_contexts': max_contexts,
            },
        )
        final_state: ResearchState = self._app.invoke(
            {
                'trace_id': trace_id,
                'prompt': prompt,
                'repos_in_scope': repos_in_scope,
                'top_k': top_k,
                'candidate_pool_multiplier': candidate_pool_multiplier,
                'relevancy_threshold': relevancy_threshold,
                'relevancy_workers': relevancy_workers,
                'reducer_token_budget': reducer_token_budget,
                'reducer_max_contexts': max_contexts,
            }
        )

        objective = final_state.get('objective')
        if objective is None:
            objective = ResearchObjective(intent='', entities=(), repos_in_scope=repos_in_scope)

        self._observability.end_span(
            run_span,
            output_payload={
                'objective': _serialize_objective(objective),
                'query_count': len(final_state.get('queries', ())),
                'candidate_count': len(final_state.get('candidates', ())),
                'relevant_count': len(final_state.get('relevant_candidates', ())),
                'enriched_count': len(final_state.get('enriched_context', ())),
                'reduced_count': len(final_state.get('reduced_context', ())),
            },
        )

        return ResearchPipelineResult(
            trace_id=trace_id,
            objective=objective,
            queries=final_state.get('queries', ()),
            candidates=final_state.get('candidates', ()),
            relevant_candidates=final_state.get('relevant_candidates', ()),
            enriched_context=final_state.get('enriched_context', ()),
            reduced_context=final_state.get('reduced_context', ()),
        )

    def _build_graph(self):
        graph = StateGraph(ResearchState)
        graph.add_node('reasoning_node', self._reasoning_node)
        graph.add_node('prodder_node', self._prodder_node)
        graph.add_node('vector_search_node', self._vector_search_node)
        graph.add_node('relevancy_node', self._relevancy_node)
        graph.add_node('retrieval_node', self._retrieval_node)
        graph.add_node('reducer_node', self._reducer_node)
        graph.add_edge(START, 'reasoning_node')
        graph.add_edge('reasoning_node', 'prodder_node')
        graph.add_edge('prodder_node', 'vector_search_node')
        graph.add_edge('vector_search_node', 'relevancy_node')
        graph.add_edge('relevancy_node', 'retrieval_node')
        graph.add_edge('retrieval_node', 'reducer_node')
        graph.add_edge('reducer_node', END)
        return graph.compile()

    def _reasoning_node(self, state: ResearchState) -> ResearchState:
        trace_id = state['trace_id']
        repos = state.get('repos_in_scope', ())
        prompt = state['prompt']
        span = self._observability.start_span(
            'reasoning_agent',
            trace_id,
            input_payload={
                'prompt': prompt,
                'repos_in_scope': list(repos),
                'prompt_token_estimate': _token_estimate(prompt),
            },
        )
        objective = self._reasoning_agent.build_objective(prompt, repos)
        self._observability.end_span(
            span,
            output_payload={'objective': _serialize_objective(objective)},
            metadata={
                'entity_count': len(objective.entities),
                'repo_scope_count': len(objective.repos_in_scope),
            },
        )
        return {'objective': objective}

    def _prodder_node(self, state: ResearchState) -> ResearchState:
        trace_id = state['trace_id']
        objective = state['objective']
        span = self._observability.start_span(
            'semantic_prodder',
            trace_id,
            input_payload={
                'objective': _serialize_objective(objective),
            },
        )
        queries = self._query_prodder.build_queries(objective)
        self._observability.end_span(
            span,
            output_payload={
                'queries': list(queries),
                'query_token_estimates': [_token_estimate(row) for row in queries],
            },
            metadata={
                'query_count': len(queries),
                'objective_intent': objective.intent,
            },
        )
        return {'queries': queries}

    def _vector_search_node(self, state: ResearchState) -> ResearchState:
        trace_id = state['trace_id']
        queries = state.get('queries', ())
        objective = state['objective']
        top_k = max(1, int(state.get('top_k', 8)))
        pool_multiplier = max(1, int(state.get('candidate_pool_multiplier', 6)))
        candidate_pool_k = max(top_k, top_k * pool_multiplier)

        span = self._observability.start_span(
            'vector_search',
            trace_id,
            input_payload={
                'objective': _serialize_objective(objective),
                'queries': list(queries),
                'query_count': len(queries),
                'top_k': top_k,
                'candidate_pool_k': candidate_pool_k,
            },
        )

        candidates = self._rank_candidates(
            queries=queries,
            objective=objective,
            limit=candidate_pool_k,
        )
        self._observability.end_span(
            span,
            output_payload={
                'candidate_count': len(candidates),
                'candidates': [_serialize_candidate(row) for row in candidates],
            },
            metadata={
                'repos_in_scope': list(objective.repos_in_scope),
                'candidate_pool_multiplier': pool_multiplier,
                'top_score': max((row.score for row in candidates), default=-1.0),
                'bottom_score': min((row.score for row in candidates), default=-1.0),
            },
        )
        return {'candidates': candidates}

    def _relevancy_node(self, state: ResearchState) -> ResearchState:
        trace_id = state['trace_id']
        objective = state['objective']
        candidates = state.get('candidates', ())
        top_k = max(1, int(state.get('top_k', 8)))
        threshold = max(0.0, min(1.0, float(state.get('relevancy_threshold', 0.35))))
        workers = max(1, int(state.get('relevancy_workers', 6)))

        span = self._observability.start_span(
            'relevancy_engine',
            trace_id,
            input_payload={
                'objective': _serialize_objective(objective),
                'candidates': [_serialize_candidate(row) for row in candidates],
                'candidate_count': len(candidates),
                'top_k': top_k,
                'threshold': threshold,
                'workers': workers,
            },
        )

        scored = self._score_candidates_parallel(
            objective=objective,
            candidates=candidates,
            workers=workers,
        )
        filtered = [row for row in scored if row.confidence >= threshold]
        if not filtered:
            filtered = scored[:top_k]

        relevant = tuple(filtered[:top_k])
        self._observability.end_span(
            span,
            output_payload={
                'scored_count': len(scored),
                'relevant_count': len(relevant),
                'scored': [_serialize_relevancy_candidate(row) for row in scored],
                'relevant': [_serialize_relevancy_candidate(row) for row in relevant],
            },
            metadata={
                'threshold': threshold,
                'max_confidence': max((row.confidence for row in scored), default=0.0),
                'min_confidence': min((row.confidence for row in scored), default=0.0),
            },
        )
        return {'relevant_candidates': relevant}

    def _retrieval_node(self, state: ResearchState) -> ResearchState:
        trace_id = state['trace_id']
        relevant = state.get('relevant_candidates', ())
        candidates = (
            tuple(
                ResearchCandidate(
                    repo=row.repo,
                    path=row.path,
                    symbol=row.symbol,
                    kind=row.kind,
                    signature=row.signature,
                    score=row.score,
                )
                for row in relevant
            )
            if relevant
            else state.get('candidates', ())
        )
        span = self._observability.start_span(
            'live_repo_reader',
            trace_id,
            input_payload={
                'candidate_count': len(candidates),
                'candidates': [_serialize_candidate(row) for row in candidates],
            },
        )

        enriched_context = self._enrich_candidates(candidates)
        # Keep retrieval span open so reducer_engine can appear as its child in call-depth views.
        return {
            'enriched_context': enriched_context,
            '_active_retrieval_span': span,
        }

    def _reducer_node(self, state: ResearchState) -> ResearchState:
        trace_id = state['trace_id']
        enriched = state.get('enriched_context', ())
        retrieval_span = state.get('_active_retrieval_span')
        token_budget = max(16, int(state.get('reducer_token_budget', 2500)))
        max_contexts = max(1, int(state.get('reducer_max_contexts', state.get('top_k', 8))))

        span = self._observability.start_span(
            'reducer_engine',
            trace_id,
            input_payload={
                'enriched_context': [_serialize_enriched_context(row) for row in enriched],
                'enriched_count': len(enriched),
                'token_budget': token_budget,
                'max_contexts': max_contexts,
            },
        )

        reduced = self._reduce_context(
            enriched_context=enriched,
            token_budget=token_budget,
            max_contexts=max_contexts,
        )
        self._observability.end_span(
            span,
            output_payload={
                'reduced_count': len(reduced),
                'reduced_context': [_serialize_reduced_context(row) for row in reduced],
            },
            metadata={
                'token_budget': token_budget,
                'consumed_tokens': sum(row.estimated_tokens for row in reduced),
                'reduced_body_truncations': sum(1 for row in reduced if row.body_was_truncated),
            },
        )

        if retrieval_span is not None:
            self._observability.end_span(
                retrieval_span,
                output_payload={
                    'enriched_count': len(enriched),
                    'enriched_context': [_serialize_enriched_context(row) for row in enriched],
                },
                metadata={
                    'avg_body_tokens': (
                        sum(_token_estimate(row.body) for row in enriched) / len(enriched)
                        if enriched
                        else 0.0
                    ),
                },
            )

        return {
            'reduced_context': reduced,
            '_active_retrieval_span': None,
        }

    def _rank_candidates(
        self,
        *,
        queries: tuple[str, ...],
        objective: ResearchObjective,
        limit: int,
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
        return tuple(ranked[:limit])

    def _score_candidates_parallel(
        self,
        *,
        objective: ResearchObjective,
        candidates: tuple[ResearchCandidate, ...],
        workers: int,
    ) -> list[RelevancyCandidate]:
        if not candidates:
            return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            scored = list(executor.map(lambda row: self._score_candidate(objective, row), candidates))
        scored.sort(key=lambda row: row.confidence, reverse=True)
        return scored

    def _score_candidate(
        self,
        objective: ResearchObjective,
        candidate: ResearchCandidate,
    ) -> RelevancyCandidate:
        haystack = f'{candidate.path} {candidate.symbol} {candidate.signature}'.lower()
        objective_entities = [item.lower() for item in objective.entities if item.strip()]
        intent_terms = [item for item in _tokenize_terms(objective.intent.lower()) if len(item) > 2]

        matched: list[str] = []
        for term in objective_entities:
            if term in haystack:
                matched.append(term)
        for term in intent_terms:
            if term in haystack and term not in matched:
                matched.append(term)

        entity_signal = 0.0
        if objective_entities:
            hits = sum(1 for term in objective_entities if term in haystack)
            entity_signal = hits / len(objective_entities)

        intent_signal = 0.0
        if intent_terms:
            hits = sum(1 for term in intent_terms if term in haystack)
            intent_signal = min(1.0, hits / 3.0)

        vector_signal = max(0.0, min(1.0, (candidate.score + 1.0) / 2.0))
        confidence = min(1.0, (vector_signal * 0.55) + (entity_signal * 0.3) + (intent_signal * 0.15))

        return RelevancyCandidate(
            repo=candidate.repo,
            path=candidate.path,
            symbol=candidate.symbol,
            kind=candidate.kind,
            signature=candidate.signature,
            score=candidate.score,
            confidence=confidence,
            matched_terms=tuple(matched),
        )

    def _reduce_context(
        self,
        *,
        enriched_context: tuple[EnrichedResearchContext, ...],
        token_budget: int,
        max_contexts: int,
    ) -> tuple[ReducedResearchContext, ...]:
        selected = list(enriched_context[:max_contexts])
        if not selected:
            return ()

        reduced: list[ReducedResearchContext] = []
        remaining_tokens = token_budget
        remaining_items = len(selected)

        for row in selected:
            per_item_budget = max(32, remaining_tokens // remaining_items)
            reduced_body, body_tokens, truncated = _truncate_to_token_budget(row.body, per_item_budget)
            reduced.append(
                ReducedResearchContext(
                    repo=row.repo,
                    path=row.path,
                    symbol=row.symbol,
                    kind=row.kind,
                    signature=row.signature,
                    docstring=row.docstring,
                    reduced_body=reduced_body,
                    estimated_tokens=body_tokens,
                    body_was_truncated=truncated,
                    callees=row.callees,
                    resolved_callees=row.resolved_callees,
                )
            )
            remaining_tokens = max(0, remaining_tokens - body_tokens)
            remaining_items -= 1

        return tuple(reduced)

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


def _tokenize_terms(text: str) -> list[str]:
    return [item for item in re.split(r'[^a-zA-Z0-9_]+', text) if item]


def _token_estimate(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def _truncate_to_token_budget(text: str, token_budget: int) -> tuple[str, int, bool]:
    if token_budget <= 0:
        return '', 0, bool(text)

    estimated_tokens = _token_estimate(text)
    if estimated_tokens <= token_budget:
        return text, estimated_tokens, False

    suffix = '\n# ... truncated for reducer budget'
    max_chars = max(1, token_budget * 4)
    trimmed = text[:max_chars].rstrip()

    if len(trimmed) < len(text):
        while _token_estimate(trimmed + suffix) > token_budget and len(trimmed) > 1:
            trimmed = trimmed[:-1].rstrip()
        candidate = (trimmed + suffix).rstrip()
    else:
        candidate = trimmed

    while _token_estimate(candidate) > token_budget and len(candidate) > 1:
        candidate = candidate[:-1].rstrip()

    return candidate, _token_estimate(candidate), True


def _serialize_objective(objective: ResearchObjective) -> dict:
    return {
        'intent': objective.intent,
        'entities': list(objective.entities),
        'repos_in_scope': list(objective.repos_in_scope),
    }


def _serialize_candidate(candidate: ResearchCandidate) -> dict:
    return {
        'repo': candidate.repo,
        'path': candidate.path,
        'symbol': candidate.symbol,
        'kind': candidate.kind,
        'signature': candidate.signature,
        'score': candidate.score,
    }


def _serialize_relevancy_candidate(candidate: RelevancyCandidate) -> dict:
    return {
        'repo': candidate.repo,
        'path': candidate.path,
        'symbol': candidate.symbol,
        'kind': candidate.kind,
        'signature': candidate.signature,
        'score': candidate.score,
        'confidence': candidate.confidence,
        'matched_terms': list(candidate.matched_terms),
    }


def _serialize_enriched_context(row: EnrichedResearchContext) -> dict:
    return {
        'repo': row.repo,
        'path': row.path,
        'symbol': row.symbol,
        'kind': row.kind,
        'signature': row.signature,
        'docstring': row.docstring,
        'body': row.body,
        'body_token_estimate': _token_estimate(row.body),
        'callees': list(row.callees),
        'resolved_callees': list(row.resolved_callees),
    }


def _serialize_reduced_context(row: ReducedResearchContext) -> dict:
    return {
        'repo': row.repo,
        'path': row.path,
        'symbol': row.symbol,
        'kind': row.kind,
        'signature': row.signature,
        'docstring': row.docstring,
        'reduced_body': row.reduced_body,
        'estimated_tokens': row.estimated_tokens,
        'body_was_truncated': row.body_was_truncated,
        'callees': list(row.callees),
        'resolved_callees': list(row.resolved_callees),
    }
