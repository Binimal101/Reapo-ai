from __future__ import annotations

import ast
import concurrent.futures
import math
import re
import textwrap
from dataclasses import asdict, dataclass
from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from ast_indexer.domain.models import SymbolRecord, TraceSpan, VectorRecord
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
    _active_retrieval_span: TraceSpan | None


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
        query_use_inference: bool = False,
        reducer_use_inference: bool = True,
        reducer_batch_inference: bool = True,
        relevancy_use_inference: bool = True,
    ) -> None:
        self._reasoning_agent = reasoning_agent
        self._query_prodder = query_prodder
        self._embedding_generator = embedding_generator
        self._vector_store = vector_store
        self._index_store = index_store
        self._repository_reader = repository_reader
        self._extractor = extractor
        self._observability = observability
        self._query_use_inference = query_use_inference
        self._reducer_use_inference = reducer_use_inference
        self._reducer_batch_inference = reducer_batch_inference
        self._relevancy_use_inference = relevancy_use_inference
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
        self._record_transition(
            trace_id=trace_id,
            source='START',
            target='reasoning_node',
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
        objective = _deterministic_objective(prompt, repos)
        self._observability.end_span(
            span,
            output_payload={'objective': _serialize_objective(objective)},
            metadata={
                'entity_count': len(objective.entities),
                'repo_scope_count': len(objective.repos_in_scope),
                'planner': 'deterministic',
            },
        )
        self._record_transition(
            trace_id=trace_id,
            source='reasoning_node',
            target='prodder_node',
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
        queries, escalated = self._build_queries_with_escalation(objective)
        self._observability.end_span(
            span,
            output_payload={
                'queries': list(queries),
                'query_token_estimates': [_token_estimate(row) for row in queries],
            },
            metadata={
                'query_count': len(queries),
                'objective_intent': objective.intent,
                'llm_escalated': escalated,
            },
        )
        self._record_transition(
            trace_id=trace_id,
            source='prodder_node',
            target='vector_search_node',
        )
        return {'queries': queries}

    def _build_queries_with_escalation(self, objective: ResearchObjective) -> tuple[tuple[str, ...], bool]:
        baseline = _deterministic_queries(objective)
        if not self._query_use_inference:
            return baseline, False

        if not self._should_escalate_query_generation(objective, baseline):
            return baseline, False

        try:
            llm_queries = self._query_prodder.build_queries(objective)
        except Exception:  # noqa: BLE001
            return baseline, False

        merged = tuple(dict.fromkeys(item for item in (*llm_queries, *baseline) if item.strip()))
        if not merged:
            return baseline, False
        return merged, True

    def _should_escalate_query_generation(
        self,
        objective: ResearchObjective,
        baseline_queries: tuple[str, ...],
    ) -> bool:
        # Escalate only on weak deterministic signals to protect throughput.
        if len(objective.entities) >= 2:
            return False
        if len(baseline_queries) >= 3:
            return False
        return _token_estimate(objective.intent) > 24

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
        self._record_transition(
            trace_id=trace_id,
            source='vector_search_node',
            target='relevancy_node',
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

        llm_shortlist_size = min(len(candidates), max(top_k * 4, 24))
        llm_candidates = candidates[:llm_shortlist_size]
        scored = self._score_candidates_with_llm_batch(objective=objective, candidates=llm_candidates)
        if not scored:
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
                'llm_shortlist_size': llm_shortlist_size,
                'llm_shortlist_used': bool(scored),
            },
        )
        self._record_transition(
            trace_id=trace_id,
            source='relevancy_node',
            target='retrieval_node',
        )
        return {'relevant_candidates': relevant}

    def _score_candidates_with_llm_batch(
        self,
        *,
        objective: ResearchObjective,
        candidates: tuple[ResearchCandidate, ...],
    ) -> list[RelevancyCandidate]:
        if not self._relevancy_use_inference or not candidates:
            return []

        judge_batch = getattr(self._reasoning_agent, 'score_relevancy_batch', None)
        if not callable(judge_batch):
            return []

        payload_candidates = [
            {
                'repo': row.repo,
                'path': row.path,
                'symbol': row.symbol,
                'kind': row.kind,
                'signature': row.signature,
                'score': row.score,
            }
            for row in candidates
        ]

        try:
            payload = judge_batch(
                objective={
                    'intent': objective.intent,
                    'entities': list(objective.entities),
                    'repos_in_scope': list(objective.repos_in_scope),
                },
                candidates=payload_candidates,
            )
        except Exception:  # noqa: BLE001
            return []

        if not isinstance(payload, dict):
            return []
        scored_raw = payload.get('scores', [])
        if not isinstance(scored_raw, list):
            return []

        lookup = {(row.repo, row.path, row.symbol): row for row in candidates}
        scored: list[RelevancyCandidate] = []
        for item in scored_raw:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get('repo') or ''),
                str(item.get('path') or ''),
                str(item.get('symbol') or ''),
            )
            candidate = lookup.get(key)
            if candidate is None:
                continue

            confidence_raw = item.get('confidence', 0.0)
            try:
                confidence = max(0.0, min(1.0, float(confidence_raw)))
            except (TypeError, ValueError):
                confidence = 0.0

            matched_terms = tuple(
                str(entry).strip()
                for entry in item.get('matched_terms', [])
                if str(entry).strip()
            )

            scored.append(
                RelevancyCandidate(
                    repo=candidate.repo,
                    path=candidate.path,
                    symbol=candidate.symbol,
                    kind=candidate.kind,
                    signature=candidate.signature,
                    score=candidate.score,
                    confidence=confidence,
                    matched_terms=matched_terms,
                )
            )

        scored.sort(key=lambda row: row.confidence, reverse=True)
        return scored

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
        self._record_transition(
            trace_id=trace_id,
            source='retrieval_node',
            target='reducer_node',
        )
        return {
            'enriched_context': enriched_context,
            '_active_retrieval_span': span,
        }

    def _reducer_node(self, state: ResearchState) -> ResearchState:
        trace_id = state['trace_id']
        objective = state['objective']
        enriched = state.get('enriched_context', ())
        retrieval_span = state.get('_active_retrieval_span')
        token_budget = max(16, int(state.get('reducer_token_budget', 2500)))
        max_contexts = max(1, int(state.get('reducer_max_contexts', state.get('top_k', 8))))
        selected_count = min(len(enriched), max_contexts)
        dropped_contexts = max(0, len(enriched) - selected_count)
        pre_reduce_estimated_tokens = sum(_token_estimate(row.body) for row in enriched[:selected_count])

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
            objective=objective,
            enriched_context=enriched,
            token_budget=token_budget,
            max_contexts=max_contexts,
        )
        consumed_tokens = sum(row.estimated_tokens for row in reduced)
        reduced_truncations = sum(1 for row in reduced if row.body_was_truncated)
        self._observability.end_span(
            span,
            output_payload={
                'reduced_count': len(reduced),
                'reduced_context': [_serialize_reduced_context(row) for row in reduced],
            },
            metadata={
                'planner': 'relation_line_planner',
                'token_budget': token_budget,
                'consumed_tokens': consumed_tokens,
                'token_utilization': _safe_ratio(consumed_tokens, token_budget),
                'pre_reduce_estimated_tokens': pre_reduce_estimated_tokens,
                'dropped_contexts': dropped_contexts,
                'selected_contexts': selected_count,
                'reduced_body_truncations': reduced_truncations,
                'truncation_ratio': _safe_ratio(reduced_truncations, len(reduced)),
                'overrun': consumed_tokens > token_budget,
                'global_cleanup_inference': self._reducer_use_inference,
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
                    'selected_contexts': selected_count,
                    'dropped_contexts': dropped_contexts,
                    'reducer_token_budget': token_budget,
                },
            )

        self._record_transition(
            trace_id=trace_id,
            source='reducer_node',
            target='END',
        )
        return {
            'reduced_context': reduced,
            '_active_retrieval_span': None,
        }

    def _record_transition(
        self,
        *,
        trace_id: str,
        source: str,
        target: str,
        reason: str | None = None,
    ) -> None:
        span = self._observability.start_span(
            'langgraph.transition',
            trace_id,
            input_payload={
                'graph': 'research_pipeline',
                'from': source,
                'to': target,
            },
        )
        self._observability.end_span(
            span,
            output_payload={
                'from': source,
                'to': target,
            },
            metadata={
                'graph': 'research_pipeline',
                'from_node': source,
                'to_node': target,
                'reason': reason,
            },
        )

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
        objective: ResearchObjective,
        enriched_context: tuple[EnrichedResearchContext, ...],
        token_budget: int,
        max_contexts: int,
    ) -> tuple[ReducedResearchContext, ...]:
        selected = list(enriched_context[:max_contexts])
        if not selected:
            return ()
        relation_lines = self._build_relation_lines(selected)
        raw_corpus = '\n'.join(line for _, line in relation_lines if line)

        cleaned_corpus = raw_corpus
        cleanup = getattr(self._reasoning_agent, 'cleanup_reducer_corpus', None)
        if self._reducer_use_inference and callable(cleanup) and raw_corpus.strip():
            try:
                payload = cleanup(
                    objective={
                        'intent': objective.intent,
                        'entities': list(objective.entities),
                        'repos_in_scope': list(objective.repos_in_scope),
                    },
                    relation_corpus=raw_corpus,
                    token_budget=max(64, token_budget),
                )
                if isinstance(payload, dict):
                    candidate = str(payload.get('cleaned_corpus') or '').strip()
                    if candidate:
                        cleaned_corpus = candidate
            except Exception:  # noqa: BLE001
                cleaned_corpus = raw_corpus

        bounded_corpus, _, _ = _truncate_to_token_budget(cleaned_corpus, max(16, token_budget))
        parsed = _parse_relation_corpus(bounded_corpus)

        reduced: list[ReducedResearchContext] = []
        remaining_tokens = max(0, token_budget)
        for row, fallback_line in relation_lines:
            if remaining_tokens <= 0:
                break
            line = parsed.get(row.symbol, fallback_line)
            body, tokens, truncated = _truncate_to_token_budget(line, remaining_tokens)
            reduced.append(
                ReducedResearchContext(
                    repo=row.repo,
                    path=row.path,
                    symbol=row.symbol,
                    kind=row.kind,
                    signature=row.signature,
                    docstring=row.docstring,
                    reduced_body=body,
                    estimated_tokens=tokens,
                    body_was_truncated=truncated,
                    callees=row.callees,
                    resolved_callees=row.resolved_callees,
                )
            )
            remaining_tokens = max(0, remaining_tokens - tokens)

        return tuple(reduced)

    def _build_relation_lines(
        self,
        rows: list[EnrichedResearchContext],
    ) -> list[tuple[EnrichedResearchContext, str]]:
        key_to_symbol = {_context_key(row): row.symbol for row in rows}
        used_in_by_symbol: dict[str, list[str]] = {row.symbol: [] for row in rows}
        for row in rows:
            source_ref = _symbol_signature_ref(row.symbol, row.signature)
            for callee in row.resolved_callees:
                target_symbol = key_to_symbol.get(callee)
                if target_symbol is None:
                    continue
                used_in_by_symbol.setdefault(target_symbol, []).append(source_ref)

        lines: list[tuple[EnrichedResearchContext, str]] = []
        for row in rows:
            does = _build_relation_does_clause(row)
            used_in_values = tuple(dict.fromkeys(used_in_by_symbol.get(row.symbol, [])))
            uses_values = tuple(
                dict.fromkeys(
                    _normalize_resolved_reference(callee)
                    for callee in row.resolved_callees
                    if _normalize_resolved_reference(callee)
                )
            )
            used_in_text = ', '.join(used_in_values[:6]) if used_in_values else 'NONE'
            uses_text = ', '.join(uses_values[:6]) if uses_values else 'NONE'
            line = (
                f'FUNCTION {_symbol_signature_ref(row.symbol, row.signature)} '
                f'DOES {does}, IS USED IN {used_in_text}, USES {uses_text}'
            )
            lines.append((row, line))
        return lines

    def _summarize_context_for_agent(
        self,
        *,
        row: EnrichedResearchContext,
        token_budget: int,
        precomputed_summary: str | None = None,
    ) -> tuple[str, int, bool]:
        original_tokens = _token_estimate(row.body)
        evidence = _extract_evidence_snippets(row.body)
        fallback = _format_reducer_brief(
            symbol=row.symbol,
            signature=row.signature,
            path=row.path,
            abstract=f'Implements {row.kind} {row.symbol} for {row.repo}.',
            evidence_snippets=evidence,
            open_questions=_derive_open_questions(row),
            resolved_callees=row.resolved_callees,
        )

        candidate = precomputed_summary or fallback
        if candidate is fallback:
            inference_result = self._invoke_reducer_inference(
                row=row,
                token_budget=token_budget,
                evidence_snippets=evidence,
            )
            candidate = inference_result or fallback
        reduced_body, body_tokens, truncated_by_budget = _truncate_to_token_budget(candidate, token_budget)
        truncated = truncated_by_budget or body_tokens < original_tokens
        return reduced_body, body_tokens, truncated

    def _invoke_reducer_batch_inference(
        self,
        rows: list[EnrichedResearchContext],
        *,
        token_budget: int,
    ) -> dict[str, str]:
        if not self._reducer_use_inference or not self._reducer_batch_inference or not rows:
            return {}

        summarize_batch = getattr(self._reasoning_agent, 'summarize_reducer_context_batch', None)
        if not callable(summarize_batch):
            return {}

        payload_rows = [
            {
                'symbol': row.symbol,
                'signature': row.signature,
                'path': row.path,
                'repo': row.repo,
                'kind': row.kind,
                'docstring': row.docstring,
                'body': row.body,
                'resolved_callees': row.resolved_callees,
            }
            for row in rows
        ]

        try:
            payload = summarize_batch(
                contexts=payload_rows,
                token_budget=token_budget,
            )
        except Exception:  # noqa: BLE001
            return {}

        if not isinstance(payload, dict):
            return {}

        summaries_raw = payload.get('summaries', [])
        if not isinstance(summaries_raw, list):
            return {}

        by_key: dict[str, str] = {}
        rows_by_key = {_context_key(row): row for row in rows}
        for item in summaries_raw:
            if not isinstance(item, dict):
                continue
            key = _context_key_parts(
                repo=str(item.get('repo') or ''),
                path=str(item.get('path') or ''),
                symbol=str(item.get('symbol') or ''),
            )
            row = rows_by_key.get(key)
            if row is None:
                continue
            abstract = str(item.get('abstract') or '').strip()
            if not abstract:
                continue

            evidence = tuple(
                str(entry).strip()
                for entry in item.get('evidence_snippets', [])
                if str(entry).strip()
            ) or _extract_evidence_snippets(row.body)
            open_questions = tuple(
                str(entry).strip()
                for entry in item.get('open_questions', [])
                if str(entry).strip()
            )
            by_key[key] = _format_reducer_brief(
                symbol=row.symbol,
                signature=row.signature,
                path=row.path,
                abstract=abstract,
                evidence_snippets=evidence,
                open_questions=open_questions,
                resolved_callees=row.resolved_callees,
            )

        return by_key

    def _invoke_reducer_inference(
        self,
        *,
        row: EnrichedResearchContext,
        token_budget: int,
        evidence_snippets: tuple[str, ...],
    ) -> str | None:
        if not self._reducer_use_inference:
            return None

        summarize = getattr(self._reasoning_agent, 'summarize_reducer_context', None)
        if not callable(summarize):
            return None

        try:
            payload = summarize(
                symbol=row.symbol,
                signature=row.signature,
                path=row.path,
                repo=row.repo,
                kind=row.kind,
                docstring=row.docstring,
                body=row.body,
                resolved_callees=row.resolved_callees,
                token_budget=token_budget,
            )
        except Exception:  # noqa: BLE001
            return None

        if not isinstance(payload, dict):
            return None

        abstract = str(payload.get('abstract') or '').strip()
        if not abstract:
            return None

        evidence = tuple(
            str(item).strip()
            for item in payload.get('evidence_snippets', [])
            if str(item).strip()
        )
        if not evidence:
            evidence = evidence_snippets

        open_questions = tuple(
            str(item).strip()
            for item in payload.get('open_questions', [])
            if str(item).strip()
        )

        return _format_reducer_brief(
            symbol=row.symbol,
            signature=row.signature,
            path=row.path,
            abstract=abstract,
            evidence_snippets=evidence,
            open_questions=open_questions,
            resolved_callees=row.resolved_callees,
        )

    def _recursive_reduce_groups(
        self,
        contexts: list[ReducedResearchContext],
        *,
        token_budget: int,
        max_contexts: int,
    ) -> tuple[ReducedResearchContext, ...]:
        if not contexts:
            return ()

        current = list(contexts)
        total_tokens = sum(row.estimated_tokens for row in current)

        while (len(current) > max_contexts or total_tokens > token_budget) and len(current) > 1:
            merged: list[ReducedResearchContext] = []
            pair_count = math.ceil(len(current) / 2)
            per_pair_budget = max(48, token_budget // max(1, pair_count))
            for idx in range(0, len(current), 2):
                pair = current[idx : idx + 2]
                if len(pair) == 1:
                    merged.append(pair[0])
                    continue
                merged.append(self._merge_reducer_pair(pair, per_pair_budget))
            current = merged
            total_tokens = sum(row.estimated_tokens for row in current)

        if current and sum(row.estimated_tokens for row in current) > token_budget:
            first = current[0]
            reduced_body, tokens, truncated = _truncate_to_token_budget(first.reduced_body, token_budget)
            current[0] = ReducedResearchContext(
                repo=first.repo,
                path=first.path,
                symbol=first.symbol,
                kind=first.kind,
                signature=first.signature,
                docstring=first.docstring,
                reduced_body=reduced_body,
                estimated_tokens=tokens,
                body_was_truncated=first.body_was_truncated or truncated,
                callees=first.callees,
                resolved_callees=first.resolved_callees,
            )

        return tuple(current[:max_contexts])

    def _merge_reducer_pair(
        self,
        pair: list[ReducedResearchContext],
        token_budget: int,
    ) -> ReducedResearchContext:
        symbols = [row.symbol for row in pair]
        sources = [f'{row.repo}:{row.path}:{row.symbol}' for row in pair]
        combined = '\n\n'.join(row.reduced_body for row in pair)
        evidence = _extract_evidence_snippets(combined)

        abstract = (
            f'Combined reducer brief for {", ".join(symbols)}. '
            'Use evidence snippets for direct code verification.'
        )
        merged_text = _format_reducer_brief(
            symbol=' + '.join(symbols[:3]) + (' + ...' if len(symbols) > 3 else ''),
            signature='aggregate cluster',
            path='multiple files',
            abstract=abstract,
            evidence_snippets=evidence,
            open_questions=(),
            resolved_callees=tuple(dict.fromkeys(callee for row in pair for callee in row.resolved_callees)),
            sources=tuple(sources),
        )

        reduced_body, tokens, truncated = _truncate_to_token_budget(merged_text, token_budget)
        return ReducedResearchContext(
            repo='multi',
            path='multiple files',
            symbol='cluster(' + ', '.join(symbols[:3]) + ('...' if len(symbols) > 3 else '') + ')',
            kind='cluster',
            signature='aggregate cluster',
            docstring=None,
            reduced_body=reduced_body,
            estimated_tokens=tokens,
            body_was_truncated=truncated or any(row.body_was_truncated for row in pair),
            callees=tuple(dict.fromkeys(callee for row in pair for callee in row.callees)),
            resolved_callees=tuple(dict.fromkeys(callee for row in pair for callee in row.resolved_callees)),
        )

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


def _deterministic_objective(prompt: str, repos_in_scope: tuple[str, ...]) -> ResearchObjective:
    entities = tuple(token for token in _tokenize_terms(prompt) if token.isidentifier())
    return ResearchObjective(intent=prompt, entities=entities, repos_in_scope=repos_in_scope)


def _deterministic_queries(objective: ResearchObjective) -> tuple[str, ...]:
    queries: list[str] = [objective.intent]
    queries.extend(objective.entities[:4])
    return tuple(dict.fromkeys(item for item in queries if item.strip()))


def _build_relation_does_clause(row: EnrichedResearchContext) -> str:
    if row.docstring and row.docstring.strip():
        doc = re.sub(r'\s+', ' ', row.docstring.strip())
        return doc.rstrip('.')
    return f'implements {row.kind} {row.symbol} with signature {row.signature}'.rstrip('.')


def _normalize_resolved_reference(reference: str) -> str:
    parts = reference.split(':', 2)
    if len(parts) == 3 and parts[2].strip():
        return parts[2].strip()
    return reference.strip()


def _symbol_signature_ref(symbol: str, signature: str) -> str:
    signature_value = signature.strip()
    if signature_value:
        return f'{symbol}{signature_value}'
    return symbol


def _parse_relation_corpus(corpus: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in corpus.splitlines():
        line = raw_line.strip()
        if not line.startswith('FUNCTION '):
            continue
        prefix = line[len('FUNCTION ') :]
        head = prefix.split(' DOES ', 1)[0].strip()
        symbol = head.split('(', 1)[0].strip()
        if not symbol:
            continue
        parsed[symbol] = line
    return parsed


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


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


def _context_key(row: EnrichedResearchContext) -> str:
    return _context_key_parts(repo=row.repo, path=row.path, symbol=row.symbol)


def _context_key_parts(*, repo: str, path: str, symbol: str) -> str:
    return f'{repo}:{path}:{symbol}'


def _extract_evidence_snippets(text: str, *, max_snippets: int = 2, max_lines: int = 5) -> tuple[str, ...]:
    if not text.strip():
        return ()

    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ()

    snippets: list[str] = []
    window = max(1, min(max_lines, len(lines)))
    for idx in range(0, len(lines), max(1, len(lines) // max(1, max_snippets))):
        segment = '\n'.join(lines[idx : idx + window]).strip()
        if not segment:
            continue
        if segment not in snippets:
            snippets.append(segment)
        if len(snippets) >= max_snippets:
            break

    return tuple(snippets)


def _derive_open_questions(row: EnrichedResearchContext) -> tuple[str, ...]:
    if not row.resolved_callees:
        return ('No resolved callees found; verify external dependencies manually.',)
    return ('Confirm behavior of resolved callees used by this symbol.',)


def _format_reducer_brief(
    *,
    symbol: str,
    signature: str,
    path: str,
    abstract: str,
    evidence_snippets: tuple[str, ...],
    open_questions: tuple[str, ...],
    resolved_callees: tuple[str, ...],
    sources: tuple[str, ...] = (),
) -> str:
    parts = [
        f'SYMBOL: {symbol}',
        f'PATH: {path}',
        f'SIGNATURE: {signature}',
        'ABSTRACT:',
        textwrap.fill(abstract, width=100),
    ]

    if sources:
        parts.append('SOURCES:')
        parts.extend(f'- {item}' for item in sources)

    if resolved_callees:
        parts.append('RESOLVED_CALLEES:')
        parts.extend(f'- {item}' for item in resolved_callees[:8])

    if open_questions:
        parts.append('OPEN_QUESTIONS:')
        parts.extend(f'- {item}' for item in open_questions[:3])

    if evidence_snippets:
        parts.append('EVIDENCE_SNIPPETS:')
        for snippet in evidence_snippets[:2]:
            parts.append('```python')
            parts.append(snippet)
            parts.append('```')

    return '\n'.join(parts).strip()


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
