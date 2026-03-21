from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Protocol, TypedDict

from ast_indexer.application.research_pipeline import ResearchPipelineResult
from ast_indexer.ports.observability import ObservabilityPort


SearchTool = Callable[..., ResearchPipelineResult]
MemoryTool = Callable[[list[dict]], str]


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


class OrchestratorLoopService:
    def __init__(
        self,
        *,
        observability: ObservabilityPort,
        search_tool: SearchTool,
        grep_repo_tool: GrepRepoTool,
        memory_tool: MemoryTool | None = None,
        memory_threshold_messages: int = 20,
        max_tool_iterations: int = 5,
    ) -> None:
        self._observability = observability
        self._search_tool = search_tool
        self._grep_repo_tool = grep_repo_tool
        self._memory_tool = memory_tool or self._default_memory_tool
        self._memory_threshold_messages = max(4, memory_threshold_messages)
        self._max_tool_iterations = max(1, min(20, max_tool_iterations))

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
        loop_span = self._observability.start_span(
            'orchestrator_loop',
            trace_id,
            input_payload={
                'run_id': run_id,
                'session_id': session_id,
                'message': message,
                'repos_in_scope': list(repos_in_scope),
            },
            session_id=session_id,
            user_id=user_id,
        )

        steps: list[dict] = []
        try:
            self._record_step_start(steps, 'plan', {'message': message})
            plan = {
                'intent': 'search_and_answer',
                'use_memory': len(message_history) >= self._memory_threshold_messages,
            }
            self._record_step_success(steps, 'plan', {'plan': plan})

            if plan['use_memory']:
                self._record_step_start(steps, 'memory_check', {'history_size': len(message_history)})
                memory_summary = self._memory_tool(message_history)
                self._record_step_success(steps, 'memory_check', {'summary': memory_summary})
            else:
                memory_summary = ''

            grep_samples: list[GrepRepoMatch] = []
            grep_page = 1
            grep_has_more = True
            search_result: ResearchPipelineResult | None = None
            needs_research = self._needs_research(message)
            tool_iteration = 0

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

            self._record_step_start(steps, 'execute_step.compose_response', {})
            assistant_response = self._compose_response(search_result, memory_summary, grep_samples)
            self._record_step_success(
                steps,
                'execute_step.compose_response',
                {'response_length': len(assistant_response)},
            )

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
            self._observability.end_span(
                loop_span,
                output_payload={
                    'run_id': run_id,
                    'status': 'completed',
                    'step_count': len(steps),
                },
                metadata={
                    'session_id': session_id,
                    'user_id': user_id,
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
            self._observability.end_span(
                loop_span,
                output_payload={
                    'run_id': run_id,
                    'status': 'failed',
                    'error': str(exc),
                    'step_count': len(steps),
                },
                metadata={
                    'session_id': session_id,
                    'user_id': user_id,
                },
            )
            return failed

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
        memory_summary: str,
        grep_samples: list[GrepRepoMatch],
    ) -> str:
        if not result.reduced_context:
            baseline = 'I did not find strong matching symbols yet. Try adding repo scope or a more specific function name.'
            if grep_samples:
                grep_lines = ['Possible signature hits from grep_repo:']
                for item in grep_samples[:5]:
                    grep_lines.append(f"- {item['repo']}:{item['path']}:{item['line']} -> {item['signature']}")
                baseline = baseline + '\n\n' + '\n'.join(grep_lines)
            if memory_summary:
                return f'{baseline}\n\nMemory context: {memory_summary}'
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
        text = message.strip().lower()
        if not text:
            return False
        keywords = (
            'where',
            'find',
            'search',
            'trace',
            'locate',
            'grep',
            'repo',
            'function',
            'class',
            'symbol',
            'implementation',
        )
        return any(token in text for token in keywords)

    def _default_memory_tool(self, history: list[dict]) -> str:
        if not history:
            return ''
        tail = history[-6:]
        return ' | '.join(f"{item.get('role', 'unknown')}: {str(item.get('content', ''))[:80]}" for item in tail)
