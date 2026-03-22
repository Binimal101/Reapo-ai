from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from ast_indexer.domain.models import TraceSpan
from ast_indexer.application.research_pipeline import ResearchPipelineResult
from ast_indexer.ports.observability import ObservabilityPort


SearchTool = Callable[..., ResearchPipelineResult]


class GrepRepoMatch(TypedDict):
    repo: str
    path: str
    symbol: str
    kind: str
    line: int
    signature: str


class GrepRepoResult(TypedDict):
    query: str
    page: int
    page_size: int
    total_matches: int
    has_more: bool
    matches: list[GrepRepoMatch]


class OrchestratorPlan(TypedDict):
    intent: str
    route: str
    use_memory: bool
    tool_strategy: str


class OrchestratorState(TypedDict, total=False):
    run_id: str
    session_id: str
    user_id: str
    trace_id: str
    message: str
    repos_in_scope: tuple[str, ...]
    top_k: int
    candidate_pool_multiplier: int
    relevancy_threshold: float
    relevancy_workers: int
    reducer_token_budget: int
    reducer_max_contexts: int | None
    message_history: list[dict]
    steps: list[dict]
    plan: OrchestratorPlan
    coding_request: dict[str, Any] | None
    memory_summary: str
    tool_memory_summary: str
    prior_tool_outcomes: list[dict[str, Any]]
    grep_samples: list[GrepRepoMatch]
    search_result: ResearchPipelineResult | None
    research_story: str
    research_next_steps: str
    satisfaction_clause: str
    satisfaction_met: bool
    coding_result: dict[str, Any] | None
    assistant_response: str


class GrepRepoTool(Protocol):
    def __call__(
        self,
        *,
        query: str,
        repos_in_scope: tuple[str, ...],
        page: int = 1,
        page_size: int = 10,
        signature_max_chars: int = 120,
    ) -> GrepRepoResult:
        """Search indexed repository symbols and return hard-truncated signatures.

        Pagination semantics:
        - page is 1-based.
        - page_size bounds number of results per page.
        - has_more indicates whether additional pages remain.
        """
        ...


class ConversationalAgentTool(Protocol):
    def __call__(
        self,
        *,
        message: str,
        context: str | None = None,
    ) -> str:
        """Generate a reply for the current user message.

        `context` is optional background assembled by the orchestrator (memory, prior
        turns, retrieval notes). Callers must not pass routing labels or graph names.
        """
        ...


class RoutingAgentTool(Protocol):
    def __call__(
        self,
        *,
        message: str,
        repos_in_scope: tuple[str, ...],
        memory_summary: str,
        tool_memory_summary: str,
        has_conversational_agent: bool,
    ) -> dict:
        """Return strict JSON-like payload containing route and optional reason."""
        ...


class CodingSubagentTool(Protocol):
    def __call__(
        self,
        *,
        objective: str,
        coding_request: dict[str, Any],
        research_context: str,
        repos_in_scope: tuple[str, ...],
        trace_id: str,
        session_id: str,
        user_id: str,
        memory_summary: str,
        tool_memory_summary: str,
        message_history: list[dict],
        tool_strategy: str,
    ) -> dict:
        """Generate code changes from orchestration context and open a pull request."""
        ...


class OrchestratorLoopService:
    """LangGraph orchestrator: one full graph run per user message (no persisted graph state).

    Routing runs on every turn; the conversational model only receives `message` plus optional
    orchestrator-built `context` (memory and recent transcript), never routing metadata.
    """

    def __init__(
        self,
        *,
        search_tool: SearchTool,
        grep_repo_tool: GrepRepoTool,
        conversational_agent_tool: ConversationalAgentTool | None = None,
        routing_agent_tool: RoutingAgentTool | None = None,
        coding_subagent_tool: CodingSubagentTool | None = None,
        memory_threshold_messages: int = 20,
        max_tool_iterations: int = 5,
        context_window_chars: int | None = None,
        observability: ObservabilityPort | None = None,
    ) -> None:
        self._search_tool = search_tool
        self._grep_repo_tool = grep_repo_tool
        self._conversational_agent_tool = conversational_agent_tool
        self._routing_agent_tool = routing_agent_tool
        self._coding_subagent_tool = coding_subagent_tool
        self._memory_threshold_messages = max(4, memory_threshold_messages)
        self._max_tool_iterations = max(1, min(20, max_tool_iterations))
        default_ctx_window = int(os.getenv('AST_INDEXER_CONTEXT_WINDOW_CHARS', '24000'))
        self._context_window_chars = max(800, context_window_chars or default_ctx_window)
        self._observability = observability
        self._app = self._build_graph()

    def execute(
        self,
        *,
        run_id: str,
        session_id: str,
        user_id: str,
        trace_id: str,
        message: str,
        repos_in_scope: tuple[str, ...],
        top_k: int,
        candidate_pool_multiplier: int,
        relevancy_threshold: float,
        relevancy_workers: int,
        reducer_token_budget: int,
        reducer_max_contexts: int | None,
        message_history: list[dict],
        prior_tool_outcomes: list[dict[str, Any]] | None = None,
        coding_request: dict[str, Any] | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        steps: list[dict] = []
        run_span = self._start_span(
            name='orchestrator_loop_run',
            trace_id=trace_id,
            input_payload={
                'message': message,
                'repos_in_scope': list(repos_in_scope),
                'top_k': top_k,
                'candidate_pool_multiplier': candidate_pool_multiplier,
                'relevancy_threshold': relevancy_threshold,
                'relevancy_workers': relevancy_workers,
                'reducer_token_budget': reducer_token_budget,
                'reducer_max_contexts': reducer_max_contexts,
                'history_size': len(message_history),
                'prior_tool_outcomes': len(prior_tool_outcomes or []),
                'has_coding_request': isinstance(coding_request, dict),
            },
            session_id=session_id,
            user_id=user_id,
        )
        self._record_transition(
            trace_id=trace_id,
            source='START',
            target='plan',
        )
        try:
            final_state: OrchestratorState = self._app.invoke(
                {
                    'run_id': run_id,
                    'session_id': session_id,
                    'user_id': user_id,
                    'trace_id': trace_id,
                    'message': message,
                    'repos_in_scope': repos_in_scope,
                    'top_k': top_k,
                    'candidate_pool_multiplier': candidate_pool_multiplier,
                    'relevancy_threshold': relevancy_threshold,
                    'relevancy_workers': relevancy_workers,
                    'reducer_token_budget': reducer_token_budget,
                    'reducer_max_contexts': reducer_max_contexts,
                    'message_history': message_history,
                    'coding_request': coding_request if isinstance(coding_request, dict) else None,
                    'prior_tool_outcomes': list(prior_tool_outcomes or []),
                    'steps': steps,
                    'memory_summary': '',
                    'tool_memory_summary': '',
                    'grep_samples': [],
                    'search_result': None,
                    'research_story': '',
                    'research_next_steps': '',
                    'satisfaction_clause': '',
                    'satisfaction_met': False,
                    'coding_result': None,
                    'assistant_response': '',
                }
            )

            assistant_response = str(final_state.get('assistant_response', '')).strip()
            if not assistant_response:
                raise RuntimeError('orchestrator graph did not produce an assistant response')

            finished_at = datetime.now(timezone.utc).isoformat()
            result = {
                'run_id': run_id,
                'status': 'completed',
                'started_at': now,
                'finished_at': finished_at,
                'steps': steps,
                'final_response': assistant_response,
                'coding_result': final_state.get('coding_result'),
                'error': None,
            }
            self._end_span(
                run_span,
                output_payload={
                    'status': 'completed',
                    'response_length': len(assistant_response),
                    'step_count': len(steps),
                },
                metadata={
                    'route': final_state.get('plan', {}).get('route'),
                },
            )
            return result
        except Exception as exc:  # noqa: BLE001
            finished_at = datetime.now(timezone.utc).isoformat()
            failed = {
                'run_id': run_id,
                'status': 'failed',
                'started_at': now,
                'finished_at': finished_at,
                'steps': steps,
                'final_response': None,
                'error': str(exc),
            }
            self._end_span(
                run_span,
                output_payload={
                    'status': 'failed',
                    'step_count': len(steps),
                    'error': str(exc),
                },
                metadata={'error_type': type(exc).__name__},
            )
            return failed

    def _build_graph(self):
        graph = StateGraph(OrchestratorState)
        graph.add_node('plan', self._plan_node)
        graph.add_node('memory_check', self._memory_node)
        graph.add_node('conversational_mode', self._conversational_node)
        graph.add_node('coding_mode', self._coding_node)
        graph.add_node('compose_response', self._compose_node)

        graph.add_edge(START, 'plan')
        graph.add_edge('plan', 'memory_check')
        graph.add_conditional_edges(
            'memory_check',
            self._route_next_node,
            {
                'conversational_mode': 'conversational_mode',
                'coding_mode': 'coding_mode',
            },
        )
        graph.add_edge('conversational_mode', 'compose_response')
        graph.add_edge('coding_mode', 'compose_response')
        graph.add_edge('compose_response', END)
        return graph.compile()

    def _plan_node(self, state: OrchestratorState) -> OrchestratorState:
        trace_id = state.get('trace_id', '')
        message = state.get('message', '')
        repos = state.get('repos_in_scope', ())
        history = state.get('message_history', [])
        steps = state['steps']
        span = self._start_span(
            name='orchestrator.plan',
            trace_id=trace_id,
            input_payload={
                'message': message,
                'repos_in_scope': list(repos),
                'history_size': len(history),
            },
        )

        try:
            self._record_step_start(steps, 'plan', {'message': message})
            route = self._route_intent(message, repos_in_scope=repos)
            tool_strategy = self._select_tool_strategy(message)
            plan: OrchestratorPlan = {
                'intent': 'conversational' if route == 'conversational_mode' else 'search_and_answer',
                'route': route,
                'use_memory': len(history) >= self._memory_threshold_messages,
                'tool_strategy': tool_strategy,
            }
            self._record_step_success(steps, 'plan', {'plan': plan})
            self._end_span(
                span,
                output_payload={'plan': plan},
                metadata={'route': route},
            )
            self._record_transition(
                trace_id=trace_id,
                source='plan',
                target='memory_check',
            )
            return {'plan': plan}
        except Exception as exc:
            self._end_span(
                span,
                output_payload={'error': str(exc)},
                metadata={'error_type': type(exc).__name__},
            )
            raise

    def _memory_node(self, state: OrchestratorState) -> OrchestratorState:
        trace_id = state.get('trace_id', '')
        message = state.get('message', '')
        plan = state.get(
            'plan',
            {
                'use_memory': False,
                'intent': 'search_and_answer',
                'route': 'coding_mode',
                'tool_strategy': self._select_tool_strategy(message),
            },
        )
        repos_in_scope = state.get('repos_in_scope', ())
        history = state.get('message_history', [])
        prior_tool_outcomes = state.get('prior_tool_outcomes', [])
        steps = state['steps']

        tool_memory_summary = self._summarize_tool_outcomes(prior_tool_outcomes)
        if not plan['use_memory']:
            route = self._route_intent_with_memory(
                message=message,
                repos_in_scope=repos_in_scope,
                memory_summary='',
                tool_memory_summary=tool_memory_summary,
            )
            next_plan: OrchestratorPlan = {
                'intent': 'conversational' if route == 'conversational_mode' else 'search_and_answer',
                'route': route,
                'use_memory': False,
                'tool_strategy': self._select_tool_strategy(message),
            }
            return {
                'memory_summary': '',
                'tool_memory_summary': tool_memory_summary,
                'plan': next_plan,
            }

        span = self._start_span(
            name='orchestrator.memory_check',
            trace_id=trace_id,
            input_payload={
                'history_size': len(history),
                'prior_tool_outcomes': len(prior_tool_outcomes),
            },
        )
        self._record_step_start(steps, 'memory_check', {'history_size': len(history)})
        # CAG mode: memory is derived directly from in-session history instead of an external tool call.
        memory_summary = self._build_cag_memory_context(history)
        route = self._route_intent_with_memory(
            message=message,
            repos_in_scope=repos_in_scope,
            memory_summary=memory_summary,
            tool_memory_summary=tool_memory_summary,
        )
        next_plan: OrchestratorPlan = {
            'intent': 'conversational' if route == 'conversational_mode' else 'search_and_answer',
            'route': route,
            'use_memory': True,
            'tool_strategy': self._select_tool_strategy(message),
        }
        self._record_step_success(
            steps,
            'memory_check',
            {
                'summary': memory_summary,
                'tool_memory_summary': tool_memory_summary,
                'route': route,
            },
        )
        self._end_span(
            span,
            output_payload={
                'memory_summary': memory_summary,
                'tool_memory_summary': tool_memory_summary,
                'route': route,
            },
            metadata={
                'summary_length': len(memory_summary),
                'tool_summary_length': len(tool_memory_summary),
                'route': route,
            },
        )
        return {
            'memory_summary': memory_summary,
            'tool_memory_summary': tool_memory_summary,
            'plan': next_plan,
        }

    def _route_next_node(self, state: OrchestratorState) -> str:
        plan = state.get(
            'plan',
            {
                'route': 'coding_mode',
                'intent': 'search_and_answer',
                'use_memory': False,
                'tool_strategy': 'semantic_then_grep_then_file',
            },
        )
        route = str(plan.get('route', 'coding_mode'))
        self._record_transition(
            trace_id=state.get('trace_id', ''),
            source='memory_check',
            target=route,
            reason='route_decision',
        )
        if route == 'conversational_mode':
            return 'conversational_mode'
        return 'coding_mode'

    def _conversational_node(self, state: OrchestratorState) -> OrchestratorState:
        trace_id = state.get('trace_id', '')
        steps = state['steps']
        message = state.get('message', '')
        memory_summary = state.get('memory_summary', '')
        tool_memory_summary = state.get('tool_memory_summary', '')
        history = state.get('message_history', [])
        repos_in_scope = state.get('repos_in_scope', ())
        span = self._start_span(
            name='orchestrator.conversational_mode',
            trace_id=trace_id,
            input_payload={'message': message, 'history_size': len(history)},
        )

        try:
            self._record_step_start(steps, 'execute_step.conversation', {})
            assistant_response = self._compose_conversational_response(
                message=message,
                memory_summary=memory_summary,
                tool_memory_summary=tool_memory_summary,
                message_history=history,
                extra_context=self._build_tool_usage_context(
                    message=message,
                    repos_in_scope=repos_in_scope,
                ),
            )
            tool_events = self._collect_conversational_tool_events()
            self._record_step_success(
                steps,
                'execute_step.conversation',
                {
                    'response_length': len(assistant_response),
                    'tool_events': tool_events,
                },
            )
            self._end_span(
                span,
                output_payload={
                    'response_length': len(assistant_response),
                    'tool_events': len(tool_events),
                },
                metadata={
                    'mode': 'conversational',
                    'tool_events': len(tool_events),
                },
            )
            self._record_transition(
                trace_id=trace_id,
                source='conversational_mode',
                target='compose_response',
            )
            return {'assistant_response': assistant_response}
        except Exception as exc:
            self._end_span(
                span,
                output_payload={'error': str(exc)},
                metadata={'error_type': type(exc).__name__},
            )
            raise

    def _coding_node(self, state: OrchestratorState) -> OrchestratorState:
        steps = state['steps']
        message = state.get('message', '')
        repos_in_scope = state.get('repos_in_scope', ())
        top_k = int(state.get('top_k', 8))
        candidate_pool_multiplier = int(state.get('candidate_pool_multiplier', 6))
        relevancy_threshold = float(state.get('relevancy_threshold', 0.35))
        relevancy_workers = int(state.get('relevancy_workers', 6))
        reducer_token_budget = int(state.get('reducer_token_budget', 2500))
        reducer_max_contexts = state.get('reducer_max_contexts')
        trace_id = state.get('trace_id', '')
        span = self._start_span(
            name='orchestrator.coding_mode',
            trace_id=trace_id,
            input_payload={
                'message': message,
                'repos_in_scope': list(repos_in_scope),
                'top_k': top_k,
                'candidate_pool_multiplier': candidate_pool_multiplier,
                'relevancy_threshold': relevancy_threshold,
                'reducer_token_budget': reducer_token_budget,
            },
        )

        grep_samples: list[GrepRepoMatch] = []
        grep_page = 1
        grep_has_more = True
        search_result: ResearchPipelineResult | None = None
        needs_research = self._needs_research(message)
        plan = state.get('plan', {})
        strategy = str(plan.get('tool_strategy') or self._select_tool_strategy(message))
        semantic_first = strategy.startswith('semantic_then')
        strategy_uses_grep = 'grep' in strategy
        tool_iteration = 0
        findings: list[str] = []
        next_steps: list[str] = []
        satisfaction_clause = self._build_satisfaction_clause(message)
        satisfaction_met = False

        try:
            while tool_iteration < self._max_tool_iterations:
                tool_iteration += 1

                if semantic_first and search_result is None:
                    self._record_step_start(
                        steps,
                        'execute_step.search',
                        {
                            'top_k': top_k,
                            'candidate_pool_multiplier': candidate_pool_multiplier,
                            'relevancy_threshold': relevancy_threshold,
                            'reducer_token_budget': reducer_token_budget,
                            'tool_iteration': tool_iteration,
                            'strategy': strategy,
                        },
                    )
                    search_result = self._search_tool(
                        trace_id=trace_id,
                        prompt=message,
                        repos_in_scope=repos_in_scope,
                        top_k=top_k,
                        candidate_pool_multiplier=candidate_pool_multiplier,
                        relevancy_threshold=relevancy_threshold,
                        relevancy_workers=relevancy_workers,
                        reducer_token_budget=reducer_token_budget,
                        reducer_max_contexts=reducer_max_contexts,
                    )
                    self._record_step_success(
                        steps,
                        'execute_step.search',
                        {
                            'candidate_count': len(search_result.candidates),
                            'relevant_count': len(search_result.relevant_candidates),
                            'reduced_count': len(search_result.reduced_context),
                        },
                    )
                    findings.append(
                        (
                            'semantic_search completed: '
                            f"candidates={len(search_result.candidates)}, "
                            f"relevant={len(search_result.relevant_candidates)}, "
                            f"reduced={len(search_result.reduced_context)}"
                        )
                    )
                    if self._query_targets_non_symbol_details(message) and not search_result.reduced_context:
                        next_steps.append(
                            'Semantic retrieval is sparse for variable/import-level intent; rely on grep/file reads for evidence.'
                        )
                    satisfaction_met = self._is_research_satisfied(
                        message=message,
                        search_result=search_result,
                        grep_samples=grep_samples,
                    )
                    if satisfaction_met:
                        break
                    if not (needs_research and strategy_uses_grep):
                        break
                    continue

                should_grep = needs_research and strategy_uses_grep and grep_has_more and len(grep_samples) < 6
                if should_grep:
                    self._record_step_start(
                        steps,
                        'execute_step.grep_repo',
                        {
                            'query': message,
                            'page': grep_page,
                            'page_size': 8,
                            'signature_max_chars': 120,
                            'tool_iteration': tool_iteration,
                            'strategy': strategy,
                        },
                    )
                    grep_result = self._grep_repo_tool(
                        query=message,
                        repos_in_scope=repos_in_scope,
                        page=grep_page,
                        page_size=8,
                        signature_max_chars=120,
                    )
                    grep_samples.extend(grep_result.get('matches', []))
                    grep_has_more = bool(grep_result.get('has_more'))
                    self._record_step_success(
                        steps,
                        'execute_step.grep_repo',
                        {
                            'page': grep_page,
                            'returned': len(grep_result.get('matches', [])),
                            'total_matches': int(grep_result.get('total_matches', 0)),
                            'has_more': grep_has_more,
                            'aggregated_matches': len(grep_samples),
                        },
                    )
                    findings.append(
                        (
                            'grep_repo completed: '
                            f"page={grep_page}, returned={len(grep_result.get('matches', []))}, "
                            f"total_matches={int(grep_result.get('total_matches', 0))}"
                        )
                    )
                    if self._query_targets_non_symbol_details(message) and grep_samples:
                        findings.append(
                            'grep evidence can reveal variables/import references that semantic symbol vectors may under-represent.'
                        )
                    if grep_has_more and len(grep_samples) < 6 and tool_iteration < self._max_tool_iterations:
                        grep_page += 1
                        continue

                    if semantic_first and search_result is not None:
                        satisfaction_met = self._is_research_satisfied(
                            message=message,
                            search_result=search_result,
                            grep_samples=grep_samples,
                        )
                        break

                if search_result is None:
                    self._record_step_start(
                        steps,
                        'execute_step.search',
                        {
                            'top_k': top_k,
                            'candidate_pool_multiplier': candidate_pool_multiplier,
                            'relevancy_threshold': relevancy_threshold,
                            'reducer_token_budget': reducer_token_budget,
                            'tool_iteration': tool_iteration,
                            'strategy': strategy,
                        },
                    )
                    search_result = self._search_tool(
                        trace_id=trace_id,
                        prompt=message,
                        repos_in_scope=repos_in_scope,
                        top_k=top_k,
                        candidate_pool_multiplier=candidate_pool_multiplier,
                        relevancy_threshold=relevancy_threshold,
                        relevancy_workers=relevancy_workers,
                        reducer_token_budget=reducer_token_budget,
                        reducer_max_contexts=reducer_max_contexts,
                    )
                    self._record_step_success(
                        steps,
                        'execute_step.search',
                        {
                            'candidate_count': len(search_result.candidates),
                            'relevant_count': len(search_result.relevant_candidates),
                            'reduced_count': len(search_result.reduced_context),
                        },
                    )
                    findings.append(
                        (
                            'semantic_search completed: '
                            f"candidates={len(search_result.candidates)}, "
                            f"relevant={len(search_result.relevant_candidates)}, "
                            f"reduced={len(search_result.reduced_context)}"
                        )
                    )
                    satisfaction_met = self._is_research_satisfied(
                        message=message,
                        search_result=search_result,
                        grep_samples=grep_samples,
                    )
                    break

            if search_result is None:
                raise RuntimeError('search tool did not execute before iteration limit')

            if not satisfaction_met:
                satisfaction_met = self._is_research_satisfied(
                    message=message,
                    search_result=search_result,
                    grep_samples=grep_samples,
                )
            if not satisfaction_met:
                next_steps.append(
                    'Need tighter evidence: run get_file_contents on top candidate paths with narrow line ranges.'
                )

            research_story = self._build_research_story(findings=findings, satisfaction_met=satisfaction_met)
            research_next_steps = self._build_next_steps_summary(next_steps=next_steps)
            coding_result: dict[str, Any] | None = None
            coding_request = state.get('coding_request')
            if isinstance(coding_request, dict) and self._coding_subagent_tool is not None:
                self._record_step_start(
                    steps,
                    'execute_step.coding_subagent',
                    {
                        'owner': coding_request.get('owner'),
                        'repo': coding_request.get('repo'),
                        'base_branch': coding_request.get('base_branch', 'main'),
                    },
                )
                coding_context = self._build_coding_subagent_context(
                    message=message,
                    repos_in_scope=repos_in_scope,
                    result=search_result,
                    grep_samples=grep_samples,
                    research_story=research_story,
                    research_next_steps=research_next_steps,
                    satisfaction_clause=satisfaction_clause,
                    satisfaction_met=satisfaction_met,
                    memory_summary=str(state.get('memory_summary', '')),
                    tool_memory_summary=str(state.get('tool_memory_summary', '')),
                    message_history=state.get('message_history', []),
                    strategy=strategy,
                )
                coding_result = self._coding_subagent_tool(
                    objective=message,
                    coding_request=coding_request,
                    research_context=coding_context,
                    repos_in_scope=repos_in_scope,
                    trace_id=trace_id,
                    session_id=str(state.get('session_id', '')),
                    user_id=str(state.get('user_id', '')),
                    memory_summary=str(state.get('memory_summary', '')),
                    tool_memory_summary=str(state.get('tool_memory_summary', '')),
                    message_history=state.get('message_history', []),
                    tool_strategy=strategy,
                )
                pr_payload = coding_result.get('pr_payload') if isinstance(coding_result, dict) else None
                pr_number = None
                if isinstance(pr_payload, dict):
                    pull_request = pr_payload.get('pull_request')
                    if isinstance(pull_request, dict):
                        pr_number = pull_request.get('number')
                self._record_step_success(
                    steps,
                    'execute_step.coding_subagent',
                    {
                        'status': coding_result.get('status') if isinstance(coding_result, dict) else 'unknown',
                        'pr_number': pr_number,
                        'has_feature_details': bool(
                            isinstance(coding_result, dict) and isinstance(coding_result.get('feature_details'), dict)
                        ),
                    },
                )

            self._end_span(
                span,
                output_payload={
                    'grep_samples': len(grep_samples),
                    'candidate_count': len(search_result.candidates),
                    'relevant_count': len(search_result.relevant_candidates),
                    'reduced_count': len(search_result.reduced_context),
                    'satisfaction_met': satisfaction_met,
                    'coding_subagent_status': (
                        coding_result.get('status') if isinstance(coding_result, dict) else None
                    ),
                },
                metadata={
                    'mode': 'coding',
                    'strategy': strategy,
                    'satisfaction_met': satisfaction_met,
                },
            )
            self._record_transition(
                trace_id=trace_id,
                source='coding_mode',
                target='compose_response',
            )
            return {
                'grep_samples': grep_samples,
                'search_result': search_result,
                'research_story': research_story,
                'research_next_steps': research_next_steps,
                'satisfaction_clause': satisfaction_clause,
                'satisfaction_met': satisfaction_met,
                'coding_result': coding_result,
                'assistant_response': (
                    str(coding_result.get('assistant_response', '')).strip()
                    if isinstance(coding_result, dict)
                    else ''
                ),
            }
        except Exception as exc:
            self._end_span(
                span,
                output_payload={'error': str(exc)},
                metadata={'error_type': type(exc).__name__},
            )
            raise

    def _compose_node(self, state: OrchestratorState) -> OrchestratorState:
        trace_id = state.get('trace_id', '')
        steps = state['steps']
        span = self._start_span(
            name='orchestrator.compose_response',
            trace_id=trace_id,
            input_payload={'has_existing_response': bool(str(state.get('assistant_response', '')).strip())},
        )
        self._record_step_start(steps, 'execute_step.compose_response', {})

        try:
            existing_response = str(state.get('assistant_response', '')).strip()
            if existing_response:
                assistant_response = existing_response
            else:
                search_result = state.get('search_result')
                if search_result is None:
                    raise RuntimeError('compose step missing research result')
                message = state.get('message', '')
                assistant_response = self._compose_response(
                    search_result,
                    message,
                    state.get('memory_summary', ''),
                    state.get('tool_memory_summary', ''),
                    state.get('grep_samples', []),
                    state.get('message_history', []),
                    str(state.get('research_story', '')),
                    str(state.get('research_next_steps', '')),
                    str(state.get('satisfaction_clause', '')),
                    bool(state.get('satisfaction_met', False)),
                )

            self._record_step_success(
                steps,
                'execute_step.compose_response',
                {'response_length': len(assistant_response)},
            )
            self._end_span(
                span,
                output_payload={'response_length': len(assistant_response)},
                metadata={'used_existing_response': bool(existing_response)},
            )
            self._record_transition(
                trace_id=trace_id,
                source='compose_response',
                target='END',
            )
            return {'assistant_response': assistant_response}
        except Exception as exc:
            self._end_span(
                span,
                output_payload={'error': str(exc)},
                metadata={'error_type': type(exc).__name__},
            )
            raise

    def _start_span(
        self,
        *,
        name: str,
        trace_id: str,
        input_payload: dict | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> TraceSpan | None:
        if self._observability is None:
            return None
        return self._observability.start_span(
            name=name,
            trace_id=trace_id,
            input_payload=input_payload,
            session_id=session_id,
            user_id=user_id,
        )

    def _end_span(self, span: TraceSpan | None, output_payload: dict | None = None, metadata: dict | None = None) -> None:
        if span is None or self._observability is None:
            return
        self._observability.end_span(span, output_payload=output_payload, metadata=metadata)

    def _record_transition(
        self,
        *,
        trace_id: str,
        source: str,
        target: str,
        reason: str | None = None,
    ) -> None:
        span = self._start_span(
            name='langgraph.transition',
            trace_id=trace_id,
            input_payload={
                'graph': 'orchestrator',
                'from': source,
                'to': target,
            },
        )
        self._end_span(
            span,
            output_payload={
                'from': source,
                'to': target,
            },
            metadata={
                'graph': 'orchestrator',
                'from_node': source,
                'to_node': target,
                'reason': reason,
            },
        )

    def _record_step_start(self, steps: list[dict], name: str, payload: dict) -> None:
        steps.append(
            {
                'name': name,
                'status': 'running',
                'started_at': datetime.now(timezone.utc).isoformat(),
                'finished_at': None,
                'input': payload,
                'output': None,
                'error': None,
            }
        )

    def _record_step_success(self, steps: list[dict], name: str, output: dict) -> None:
        target = self._find_last_step(steps, name)
        target['status'] = 'completed'
        target['finished_at'] = datetime.now(timezone.utc).isoformat()
        target['output'] = output

    def _find_last_step(self, steps: list[dict], name: str) -> dict:
        for step in reversed(steps):
            if step.get('name') == name:
                return step
        raise RuntimeError(f'missing step: {name}')

    def _compose_response(
        self,
        result: ResearchPipelineResult,
        message: str,
        memory_summary: str,
        tool_memory_summary: str,
        grep_samples: list[GrepRepoMatch],
        message_history: list[dict],
        research_story: str,
        research_next_steps: str,
        satisfaction_clause: str,
        satisfaction_met: bool,
    ) -> str:
        extra_context = self._build_retrieval_compose_context(
            result=result,
            grep_samples=grep_samples,
            research_story=research_story,
            research_next_steps=research_next_steps,
            satisfaction_clause=satisfaction_clause,
            satisfaction_met=satisfaction_met,
        )

        if self._conversational_agent_tool is not None:
            try:
                return self._compose_conversational_response(
                    message=message,
                    memory_summary=memory_summary,
                    tool_memory_summary=tool_memory_summary,
                    message_history=message_history,
                    extra_context=extra_context,
                )
            except Exception:
                # Keep retrieval answers resilient even if the conversational model fails.
                pass

        return self._compose_retrieval_fallback(result=result, grep_samples=grep_samples)

    def _build_retrieval_compose_context(
        self,
        *,
        result: ResearchPipelineResult,
        grep_samples: list[GrepRepoMatch],
        research_story: str,
        research_next_steps: str,
        satisfaction_clause: str,
        satisfaction_met: bool,
    ) -> str:
        guidance = (
            'You are composing the final user-facing answer for a repository assistant. '
            'Return one final answer only. Do not expose internal tool names, routes, or raw pipeline dumps. '
            'Use plain, human-readable language. Be concise but complete. '
            'If evidence is weak or missing, clearly say so and suggest a practical next step.'
        )

        summary = (
            f"Research stats: candidates={len(result.candidates)}, "
            f"relevant={len(result.relevant_candidates)}, reduced={len(result.reduced_context)}"
        )
        objective = result.objective
        objective_lines = [
            'Research objective:',
            f'- intent: {objective.intent}',
            f"- entities: {', '.join(objective.entities) if objective.entities else '(none)'}",
            f"- repos_in_scope: {', '.join(objective.repos_in_scope) if objective.repos_in_scope else '(none)'}",
        ]

        query_lines = ['Semantic queries used:']
        if result.queries:
            query_lines.extend(f'- {query}' for query in result.queries)
        else:
            query_lines.append('- (none)')

        sections: list[str] = [
            guidance,
            'Repository access note: search results are retrieved from indexed repository data in scope; do not claim no access when evidence is present.',
            summary,
            '\n'.join(objective_lines),
            '\n'.join(query_lines),
        ]

        candidate_lines = ['Vector candidate matches (top 12):']
        if result.candidates:
            for item in result.candidates[:12]:
                candidate_lines.append(
                    f'- {item.repo}:{item.path}:{item.symbol} ({item.kind}) score={item.score:.3f} sig={item.signature}'
                )
            hidden = max(0, len(result.candidates) - 12)
            if hidden:
                candidate_lines.append(f'- ... {hidden} more candidates omitted')
        else:
            candidate_lines.append('- (none)')
        sections.append('\n'.join(candidate_lines))

        relevancy_lines = ['Relevancy-filtered candidates (top 12):']
        if result.relevant_candidates:
            for item in result.relevant_candidates[:12]:
                matched = ','.join(item.matched_terms) if item.matched_terms else '(none)'
                relevancy_lines.append(
                    f'- {item.repo}:{item.path}:{item.symbol} conf={item.confidence:.3f} matched_terms={matched}'
                )
            hidden = max(0, len(result.relevant_candidates) - 12)
            if hidden:
                relevancy_lines.append(f'- ... {hidden} more relevant candidates omitted')
        else:
            relevancy_lines.append('- (none)')
        sections.append('\n'.join(relevancy_lines))

        enriched_lines = ['Retrieved source contexts (top 8):']
        if result.enriched_context:
            for item in result.enriched_context[:8]:
                body_preview = item.body[:320].replace('\n', '\\n')
                callees = ', '.join(item.resolved_callees[:5]) if item.resolved_callees else '(none)'
                enriched_lines.append(f'- {item.repo}:{item.path}:{item.symbol}')
                enriched_lines.append(f'  signature: {item.signature}')
                enriched_lines.append(f'  callees: {callees}')
                enriched_lines.append(f'  body_preview: {body_preview}')
        else:
            enriched_lines.append('- (none)')
        sections.append('\n'.join(enriched_lines))

        if result.reduced_context:
            reduced_lines = ['Retrieved code evidence (top items):']
            for item in result.reduced_context[:8]:
                reduced_lines.append(
                    f"- {item.repo}:{item.path}:{item.symbol} ({item.kind})"
                )
                if item.docstring:
                    reduced_lines.append(f'  Doc: {item.docstring}')
                reduced_lines.append(f'  Signature: {item.signature}')
                reduced_lines.append(f'  Tokens: {item.estimated_tokens} truncated={item.body_was_truncated}')
                if item.resolved_callees:
                    reduced_lines.append(f"  Resolved callees: {', '.join(item.resolved_callees[:6])}")
                reduced_lines.append(f'  Snippet: {item.reduced_body}')
            sections.append('\n'.join(reduced_lines))
        else:
            sections.append('No reduced code context was retrieved for this query.')

        if grep_samples:
            grep_lines = ['Additional grep signature matches:']
            for item in grep_samples[:5]:
                grep_lines.append(f"- {item['repo']}:{item['path']}:{item['line']} -> {item['signature']}")
            sections.append('\n'.join(grep_lines))

        if research_story.strip():
            sections.append('Research story so far:\n' + research_story.strip())
        if research_next_steps.strip():
            sections.append('Research next steps:\n' + research_next_steps.strip())
        if satisfaction_clause.strip():
            sections.append(
                'Satisfaction clause:\n'
                f'- target: {satisfaction_clause.strip()}\n'
                f"- status: {'met' if satisfaction_met else 'not_met'}"
            )

        return '\n\n'.join(sections)

    def _build_coding_subagent_context(
        self,
        *,
        message: str,
        repos_in_scope: tuple[str, ...],
        result: ResearchPipelineResult,
        grep_samples: list[GrepRepoMatch],
        research_story: str,
        research_next_steps: str,
        satisfaction_clause: str,
        satisfaction_met: bool,
        memory_summary: str,
        tool_memory_summary: str,
        message_history: list[dict],
        strategy: str,
    ) -> str:
        retrieval_context = self._build_retrieval_compose_context(
            result=result,
            grep_samples=grep_samples,
            research_story=research_story,
            research_next_steps=research_next_steps,
            satisfaction_clause=satisfaction_clause,
            satisfaction_met=satisfaction_met,
        )
        tool_policy = self._build_tool_usage_context(message=message, repos_in_scope=repos_in_scope)
        history_summary = self._summarize_history(message_history) if message_history else '(none)'

        sections = [
            'Coding pipeline objective: produce concrete repository edits and an open pull request.',
            f'Selected strategy: {strategy}',
            'Development prerequisites from orchestration:',
            '- Preserve existing public behavior unless objective requires changes.',
            '- Keep edits minimal and focused to the stated objective.',
            '- Prefer explicit evidence from retrieved snippets when making code decisions.',
            '- If evidence is insufficient, use tool calls to collect missing file context before proposing edits.',
            f'Session memory summary: {memory_summary.strip() or "(none)"}',
            f'Prior tool outcomes summary: {tool_memory_summary.strip() or "(none)"}',
            f'Recent message history summary: {history_summary}',
            'Tool usage policy for coding agent:',
            tool_policy,
            'Research evidence package:',
            retrieval_context,
        ]
        return '\n\n'.join(sections)

    def _compose_retrieval_fallback(self, *, result: ResearchPipelineResult, grep_samples: list[GrepRepoMatch]) -> str:
        if not result.reduced_context:
            response = (
                'I could not find strong code evidence for that request in the currently indexed scope. '
                'Try narrowing to a specific symbol, file path, or repository.'
            )
            if grep_samples:
                response += '\n\nClosest signature matches:\n'
                response += '\n'.join(
                    f"- {item['repo']}:{item['path']}:{item['line']} -> {item['signature']}" for item in grep_samples[:5]
                )
            return response

        response_lines = ['I found relevant code in the indexed repositories:']
        for item in result.reduced_context[:4]:
            response_lines.append(f'- {item.repo}:{item.path}:{item.symbol} ({item.kind})')
            if item.docstring:
                response_lines.append(f'  Purpose: {item.docstring}')
            response_lines.append(f'  Key snippet: {item.reduced_body}')

        if grep_samples:
            response_lines.append('')
            response_lines.append('Related signature matches:')
            response_lines.extend(
                f"- {item['repo']}:{item['path']}:{item['line']} -> {item['signature']}" for item in grep_samples[:5]
            )

        return '\n'.join(response_lines)

    def _needs_research(self, message: str) -> bool:
        """Heuristic: user message likely needs repository / code retrieval."""
        text = message.strip().lower()
        if not text:
            return False
        if self._is_explicit_research_request(text):
            return True
        code_tokens = (
            'grep',
            'search',
            'tool',
            'look for',
            'find',
            'repo',
            'repository',
            'function',
            'class',
            'method',
            'symbol',
            'module',
            'file',
            'code',
            'implementation',
            'implement',
            'traceback',
            'stack trace',
            'call graph',
            'branch',
            'commit',
            'pull request',
            'pr ',
            'merge',
            'bug',
            'regression',
            'endpoint',
            'api',
            'docker',
            'kubernetes',
            'k8s',
            'deploy',
            'build',
            'test',
            'tests',
            'pytest',
            'jest',
            'typescript',
            'python',
            '.py',
            '.ts',
            '.tsx',
            '.js',
            '.jsx',
            '.go',
            '.rs',
        )
        return any(token in text for token in code_tokens)

    def _route_intent(self, message: str, *, repos_in_scope: tuple[str, ...]) -> str:
        """Classify the next graph branch. Runs once per user message (invoke is stateless)."""
        return self._route_intent_with_memory(
            message=message,
            repos_in_scope=repos_in_scope,
            memory_summary='',
            tool_memory_summary='',
        )

    def _route_intent_with_memory(
        self,
        *,
        message: str,
        repos_in_scope: tuple[str, ...],
        memory_summary: str,
        tool_memory_summary: str,
    ) -> str:
        """Classify next graph branch using current message plus loaded memory/tool outcomes."""
        routed = self._route_with_agent(
            message=message,
            repos_in_scope=repos_in_scope,
            memory_summary=memory_summary,
            tool_memory_summary=tool_memory_summary,
        )
        if routed is not None:
            return routed
        if self._routing_agent_tool is not None:
            # With a routing agent configured, avoid deterministic branch heuristics.
            return 'coding_mode'

        lowered = message.strip().lower()
        if self._is_follow_up_message(lowered) and (memory_summary.strip() or tool_memory_summary.strip()):
            return 'coding_mode'

        if self._conversational_agent_tool is not None and self._is_broad_codebase_exploration_message(message):
            return 'conversational_mode'
        if self._is_conversational_message(message):
            return 'conversational_mode'
        return 'coding_mode'

    def _route_with_agent(
        self,
        *,
        message: str,
        repos_in_scope: tuple[str, ...],
        memory_summary: str,
        tool_memory_summary: str,
    ) -> str | None:
        if self._routing_agent_tool is None:
            return None

        try:
            payload = self._routing_agent_tool(
                message=message,
                repos_in_scope=repos_in_scope,
                memory_summary=memory_summary,
                tool_memory_summary=tool_memory_summary,
                has_conversational_agent=self._conversational_agent_tool is not None,
            )
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None
        route = str(payload.get('route', '')).strip()
        if route not in {'coding_mode', 'conversational_mode'}:
            return None
        return route

    def _is_follow_up_message(self, text: str) -> bool:
        if not text:
            return False
        follow_up_markers = (
            'tell me more',
            'continue',
            'go on',
            'next',
            'what else',
            'and then',
            'based on that',
            'compare that',
            'do that now',
            'lets do this',
            "let's do this",
        )
        return any(marker in text for marker in follow_up_markers)

    def _is_explicit_research_request(self, message: str) -> bool:
        text = message.strip().lower()
        if not text:
            return False
        explicit_markers = (
            'use search tool',
            'search tool',
            'run search',
            'look for',
            'find in',
            'find code',
            'grep repo',
            'query codebase',
        )
        if any(marker in text for marker in explicit_markers):
            return True
        # Owner/repo mention usually implies repository-grounded retrieval request.
        if '/' in text and any(ch.isalpha() for ch in text):
            return True
        return False

    def _is_broad_codebase_exploration_message(self, message: str) -> bool:
        text = message.strip().lower()
        if not text:
            return False
        broad_markers = (
            'codebase',
            'folder structure',
            'project structure',
            'repo structure',
            'repository structure',
            'walk me through the repo',
            'walk through the repo',
            'map the repository',
            'map this repo',
            'explore the repository',
            'understand this repo',
            'where should i start',
            'overview of the repo',
            'high level architecture',
        )
        return any(marker in text for marker in broad_markers)

    def _build_tool_usage_context(self, *, message: str, repos_in_scope: tuple[str, ...]) -> str:
        repos_text = ', '.join(repos_in_scope) if repos_in_scope else '(none specified)'
        strategy = self._select_tool_strategy(message)
        return (
            'Tool policy (strict):\n'
            '- Broad exploration: call get_folder_structure first (paged).\n'
            '- Evidence reads: call get_file_contents with repo,path,line_beginning,line_ending,max_tokens.\n'
            '- Keep ranges tight; iterate instead of one broad read.\n'
            '- On max_tokens_exceeded: shrink line range and retry.\n'
            '- Semantic search primarily returns function/class/symbol-level matches; use grep/file reads for variables/imports/constants.\n'
            '- If still ambiguous: state uncertainty and request a narrower symbol/path target.\n'
            '- Example A: get_folder_structure(repo="acme/repo", path="src", page=1, page_size=60).\n'
            '- Example B: get_file_contents(repo="acme/repo", path="src/app.py", line_beginning=1, line_ending=120, max_tokens=1200).\n\n'
            'Planner pattern library:\n'
            '- System mapping / general pieces: semantic_search -> grep_repo -> get_file_contents (targeted).\n'
            '- Symbol location / where is X used: grep_repo -> semantic_search -> get_file_contents.\n'
            '- Implementation details: semantic_search -> get_file_contents around top evidence.\n\n'
            f'Selected strategy for this message: {strategy}\n'
            f'Repositories in scope: {repos_text}\n'
            f'Current user message: {message.strip()}'
        )

    def _select_tool_strategy(self, message: str) -> str:
        text = message.strip().lower()
        if not text:
            return 'semantic_then_grep_then_file'

        if self._query_targets_non_symbol_details(text):
            return 'grep_then_semantic_then_file'

        broad_discovery_markers = (
            'general pieces',
            'major pieces',
            'system overview',
            'map the system',
            'architecture',
            'how this system works',
            'high level',
            'walk me through',
        )
        if any(marker in text for marker in broad_discovery_markers):
            return 'semantic_then_grep_then_file'

        symbol_lookup_markers = (
            'where is',
            'find symbol',
            'find function',
            'used in',
            'grep',
            'look for',
        )
        if any(marker in text for marker in symbol_lookup_markers):
            return 'grep_then_semantic_then_file'
        if 'find' in text and any(token in text for token in ('function', 'symbol', 'class', 'method')):
            return 'grep_then_semantic_then_file'

        implementation_markers = (
            'implementation',
            'implement',
            'how does',
            'logic',
            'code path',
            'line by line',
        )
        if any(marker in text for marker in implementation_markers):
            return 'semantic_then_file'

        return 'semantic_then_grep_then_file'

    def _query_targets_non_symbol_details(self, message: str) -> bool:
        text = message.strip().lower()
        if not text:
            return False
        markers = (
            'variable',
            'variables',
            'import',
            'imports',
            'constant',
            'constants',
            'env var',
            'environment variable',
            'assignment',
        )
        return any(marker in text for marker in markers)

    def _is_research_satisfied(
        self,
        *,
        message: str,
        search_result: ResearchPipelineResult,
        grep_samples: list[GrepRepoMatch],
    ) -> bool:
        if search_result.reduced_context:
            return True
        if search_result.relevant_candidates and grep_samples:
            return True
        if self._query_targets_non_symbol_details(message) and grep_samples:
            return True
        return False

    def _build_satisfaction_clause(self, message: str) -> str:
        if self._query_targets_non_symbol_details(message):
            return (
                'Return when at least one concrete symbol/path evidence item is found and '
                'variable/import-level signals are corroborated by grep or file evidence.'
            )
        return (
            'Return when at least one concrete symbol/path evidence item is found and '
            'the explanation is grounded in retrieved repository context.'
        )

    def _build_research_story(self, *, findings: list[str], satisfaction_met: bool) -> str:
        lines = ['1) Started iterative research run.']
        for idx, item in enumerate(findings[:8], start=2):
            lines.append(f'{idx}) {item}')
        lines.append(f"{len(lines) + 1}) Satisfaction status: {'met' if satisfaction_met else 'not met'}.")
        return '\n'.join(lines)

    def _build_next_steps_summary(self, *, next_steps: list[str]) -> str:
        if not next_steps:
            return 'No additional research steps required.'
        return '\n'.join(f'- {item}' for item in next_steps[:8])

    def _is_conversational_message(self, message: str) -> bool:
        """True when the turn should skip retrieval and use the conversational model only."""
        text = message.strip().lower()
        if not text:
            return True

        words = tuple(token for token in ''.join(ch if ch.isalnum() else ' ' for ch in text).split() if token)
        if any(greeting in words for greeting in ('hello', 'hi', 'hey')):
            return True

        conversational_phrases = (
            'thanks',
            'thank you',
            'how are you',
            'what can you do',
            'who are you',
            'help me understand',
            'explain this',
            'can you help',
        )
        if any(phrase in text for phrase in conversational_phrases):
            return True

        return not self._needs_research(text)

    def _build_router_context_for_conversational(
        self,
        *,
        memory_summary: str,
        tool_memory_summary: str,
        message_history: list[dict],
        extra_context: str | None,
    ) -> str | None:
        """Background for the conversational model; assembled only by the orchestrator."""
        parts: list[str] = []
        if memory_summary.strip():
            parts.append(f'Session memory summary:\n{memory_summary.strip()}')
        if message_history:
            tail = message_history[-6:]
            lines: list[str] = []
            for item in tail:
                role = str(item.get('role', 'unknown'))
                content = str(item.get('content', ''))
                if len(content) > 600:
                    content = content[:600] + '…'
                lines.append(f'{role}: {content}')
            parts.append('Recent messages:\n' + '\n'.join(lines))
        if tool_memory_summary.strip():
            parts.append('Prior tool outcomes summary:\n' + tool_memory_summary.strip())
        if extra_context and extra_context.strip():
            parts.append(extra_context.strip())
        if not parts:
            return None
        context = '\n\n'.join(parts)
        if len(context) <= self._context_window_chars:
            return context

        compact_parts: list[str] = []
        if memory_summary.strip():
            compact_parts.append(f'Session memory summary:\n{memory_summary.strip()}')
        if message_history:
            compact_parts.append('Recent messages (compacted):\n' + self._summarize_history(message_history))
        if tool_memory_summary.strip():
            compact_parts.append('Prior tool outcomes (compacted):\n' + tool_memory_summary[:2200])
        if extra_context and extra_context.strip():
            compact_parts.append('Extra context (compacted):\n' + extra_context.strip()[:2200])
        compact_parts.append('Context compacted to stay within the available context window.')
        compact_context = '\n\n'.join(compact_parts)
        return compact_context[: self._context_window_chars]

    def _compose_conversational_response(
        self,
        *,
        message: str,
        memory_summary: str,
        tool_memory_summary: str,
        message_history: list[dict],
        extra_context: str | None = None,
    ) -> str:
        if self._conversational_agent_tool is None:
            raise RuntimeError('conversational agent tool is not configured')

        context = self._build_router_context_for_conversational(
            memory_summary=memory_summary,
            tool_memory_summary=tool_memory_summary,
            message_history=message_history,
            extra_context=extra_context,
        )
        response = self._conversational_agent_tool(
            message=message,
            context=context,
        )
        text = str(response).strip()
        if not text:
            raise RuntimeError('conversational agent returned empty response')
        return text

    def _build_cag_memory_context(self, history: list[dict]) -> str:
        if not history:
            return ''
        tail = history[-6:]
        return ' | '.join(f"{item.get('role', 'unknown')}: {str(item.get('content', ''))[:80]}" for item in tail)

    def _collect_conversational_tool_events(self) -> list[dict[str, Any]]:
        tool = self._conversational_agent_tool
        pop = getattr(tool, 'pop_last_tool_events', None)
        if pop is None or not callable(pop):
            return []
        try:
            events = pop()
        except Exception:
            return []
        if not isinstance(events, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in events[:24]:
            if isinstance(item, dict):
                normalized.append(item)
        return normalized

    def _summarize_tool_outcomes(self, outcomes: list[dict[str, Any]]) -> str:
        if not outcomes:
            return ''

        lines = []
        for idx, item in enumerate(outcomes[-12:], start=1):
            tool_name = str(item.get('tool') or item.get('name') or 'unknown_tool')
            ok = bool(item.get('ok', True))
            status = 'ok' if ok else 'error'
            result = item.get('result')
            if isinstance(result, dict):
                if 'error' in result:
                    detail = f"error={result.get('error')}"
                elif 'path' in result:
                    detail = f"path={result.get('path')}"
                elif 'total_entries' in result:
                    detail = f"total_entries={result.get('total_entries')}"
                elif 'estimated_tokens' in result:
                    detail = f"estimated_tokens={result.get('estimated_tokens')}"
                else:
                    detail = 'result=structured'
            else:
                detail = str(result)[:120]
            lines.append(f'{idx}. {tool_name} ({status}) {detail}'.strip())
        return '\n'.join(lines)

    def _summarize_history(self, history: list[dict]) -> str:
        if not history:
            return ''
        tail = history[-4:]
        return ' | '.join(
            f"{str(item.get('role', 'unknown'))}: {str(item.get('content', ''))[:140]}" for item in tail
        )
