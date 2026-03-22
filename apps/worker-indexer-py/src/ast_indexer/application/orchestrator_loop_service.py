from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Protocol, TypedDict

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
    memory_summary: str
    grep_samples: list[GrepRepoMatch]
    search_result: ResearchPipelineResult | None
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
        memory_threshold_messages: int = 20,
        max_tool_iterations: int = 5,
        observability: ObservabilityPort | None = None,
    ) -> None:
        self._search_tool = search_tool
        self._grep_repo_tool = grep_repo_tool
        self._conversational_agent_tool = conversational_agent_tool
        self._memory_threshold_messages = max(4, memory_threshold_messages)
        self._max_tool_iterations = max(1, min(20, max_tool_iterations))
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
                    'steps': steps,
                    'memory_summary': '',
                    'grep_samples': [],
                    'search_result': None,
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
            plan: OrchestratorPlan = {
                'intent': 'conversational' if route == 'conversational_mode' else 'search_and_answer',
                'route': route,
                'use_memory': len(history) >= self._memory_threshold_messages,
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
        plan = state.get('plan', {'use_memory': False, 'intent': 'search_and_answer', 'route': 'coding_mode'})
        history = state.get('message_history', [])
        steps = state['steps']

        if not plan['use_memory']:
            return {'memory_summary': ''}

        span = self._start_span(
            name='orchestrator.memory_check',
            trace_id=trace_id,
            input_payload={'history_size': len(history)},
        )
        self._record_step_start(steps, 'memory_check', {'history_size': len(history)})
        # CAG mode: memory is derived directly from in-session history instead of an external tool call.
        memory_summary = self._build_cag_memory_context(history)
        self._record_step_success(steps, 'memory_check', {'summary': memory_summary})
        self._end_span(
            span,
            output_payload={'memory_summary': memory_summary},
            metadata={'summary_length': len(memory_summary)},
        )
        return {'memory_summary': memory_summary}

    def _route_next_node(self, state: OrchestratorState) -> str:
        plan = state.get('plan', {'route': 'coding_mode', 'intent': 'search_and_answer', 'use_memory': False})
        route = plan['route']
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
        history = state.get('message_history', [])
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
                message_history=history,
            )
            self._record_step_success(
                steps,
                'execute_step.conversation',
                {'response_length': len(assistant_response)},
            )
            self._end_span(
                span,
                output_payload={'response_length': len(assistant_response)},
                metadata={'mode': 'conversational'},
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
        tool_iteration = 0

        try:
            while tool_iteration < self._max_tool_iterations:
                tool_iteration += 1

                should_grep = needs_research and grep_has_more and len(grep_samples) < 6
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
                    if grep_has_more and len(grep_samples) < 6 and tool_iteration < self._max_tool_iterations:
                        grep_page += 1
                        continue

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
                    break

            if search_result is None:
                raise RuntimeError('search tool did not execute before iteration limit')

            self._end_span(
                span,
                output_payload={
                    'grep_samples': len(grep_samples),
                    'candidate_count': len(search_result.candidates),
                    'relevant_count': len(search_result.relevant_candidates),
                    'reduced_count': len(search_result.reduced_context),
                },
                metadata={'mode': 'coding'},
            )
            self._record_transition(
                trace_id=trace_id,
                source='coding_mode',
                target='compose_response',
            )
            return {
                'grep_samples': grep_samples,
                'search_result': search_result,
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
                    state.get('grep_samples', []),
                    state.get('message_history', []),
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
        grep_samples: list[GrepRepoMatch],
        message_history: list[dict],
    ) -> str:
        if not result.reduced_context:
            baseline = self._compose_conversational_response(
                message=message,
                memory_summary=memory_summary,
                message_history=message_history,
                extra_context=(
                    'No code excerpts were retrieved for this query. '
                    'Reply helpfully; you may suggest how to narrow or rephrase if needed.'
                ),
            )
            if grep_samples:
                grep_lines = ['Possible signature hits from grep_repo:']
                for item in grep_samples[:5]:
                    grep_lines.append(f"- {item['repo']}:{item['path']}:{item['line']} -> {item['signature']}")
                baseline = baseline + '\n\n' + '\n'.join(grep_lines)
            return baseline

        lines: list[str] = []
        for item in result.reduced_context[:5]:
            lines.append(f'- {item.path}:{item.symbol}')
            lines.append(f'  {item.reduced_body}')

        response = 'Here is what I found:\n' + '\n'.join(lines)
        if memory_summary:
            response += f'\n\nConversation memory summary:\n{memory_summary}'
        if grep_samples:
            response += '\n\nAdditional grep_repo signatures:\n'
            response += '\n'.join(
                f"- {item['repo']}:{item['path']}:{item['line']} -> {item['signature']}" for item in grep_samples[:5]
            )
        return response

    def _needs_research(self, message: str) -> bool:
        """Heuristic: user message likely needs repository / code retrieval."""
        text = message.strip().lower()
        if not text:
            return False
        code_tokens = (
            'grep',
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
        if self._is_conversational_message(message):
            return 'conversational_mode'
        return 'coding_mode'

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
        if extra_context and extra_context.strip():
            parts.append(extra_context.strip())
        if not parts:
            return None
        return '\n\n'.join(parts)

    def _compose_conversational_response(
        self,
        *,
        message: str,
        memory_summary: str,
        message_history: list[dict],
        extra_context: str | None = None,
    ) -> str:
        if self._conversational_agent_tool is None:
            raise RuntimeError('conversational agent tool is not configured')

        context = self._build_router_context_for_conversational(
            memory_summary=memory_summary,
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
