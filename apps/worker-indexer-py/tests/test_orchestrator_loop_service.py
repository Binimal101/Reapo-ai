from ast_indexer.application.orchestrator_loop_service import GrepRepoResult, OrchestratorLoopService
from ast_indexer.application.research_pipeline import (
    ReducedResearchContext,
    ResearchCandidate,
    ResearchObjective,
    ResearchPipelineResult,
    RelevancyCandidate,
)


def _conversational_agent_tool(*, message: str, memory_summary: str, message_history: list[dict]) -> str:
    tail = f' | memory={memory_summary}' if memory_summary else ''
    return f'conversation:{message.strip()}{tail} | history={len(message_history)}'


def _result_with_context() -> ResearchPipelineResult:
    return ResearchPipelineResult(
        trace_id='trace-1',
        objective=ResearchObjective(intent='lookup', entities=('billing',), repos_in_scope=('repo-a',)),
        queries=('billing service',),
        candidates=(
            ResearchCandidate(
                repo='repo-a',
                path='src/billing.py',
                symbol='charge_customer',
                kind='function',
                signature='charge_customer(user_id, amount)',
                score=0.9,
            ),
        ),
        relevant_candidates=(
            RelevancyCandidate(
                repo='repo-a',
                path='src/billing.py',
                symbol='charge_customer',
                kind='function',
                signature='charge_customer(user_id, amount)',
                score=0.9,
                confidence=0.8,
                matched_terms=('billing',),
            ),
        ),
        enriched_context=(),
        reduced_context=(
            ReducedResearchContext(
                repo='repo-a',
                path='src/billing.py',
                symbol='charge_customer',
                kind='function',
                signature='charge_customer(user_id, amount)',
                docstring='Charge the customer card.',
                reduced_body='def charge_customer(user_id, amount): return True',
                estimated_tokens=30,
                body_was_truncated=False,
                callees=('gateway.charge',),
                resolved_callees=('src/gateway.py:charge',),
            ),
        ),
    )


def test_orchestrator_loop_executes_success_path_and_records_span() -> None:
    def _search_tool(**kwargs: object) -> ResearchPipelineResult:  # noqa: ARG001
        return _result_with_context()

    def _grep_repo_tool(
        *,
        query: str,
        repos_in_scope: tuple[str, ...],
        page: int = 1,
        page_size: int = 10,
        signature_max_chars: int = 120,
    ) -> GrepRepoResult:  # noqa: ARG001
        return {
            'query': query,
            'page': page,
            'page_size': page_size,
            'total_matches': 1,
            'has_more': False,
            'matches': [
                {
                    'repo': 'repo-a',
                    'path': 'src/billing.py',
                    'symbol': 'charge_customer',
                    'kind': 'function',
                    'line': 10,
                    'signature': 'charge_customer(user_id, amount)'[:signature_max_chars],
                }
            ],
        }

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
        memory_threshold_messages=2,
    )

    execution = service.execute(
        run_id='run-1',
        session_id='session-1',
        user_id='user-1',
        trace_id='trace-1',
        message='Where is billing function logic in this repo?',
        repos_in_scope=('repo-a',),
        top_k=8,
        candidate_pool_multiplier=6,
        relevancy_threshold=0.35,
        relevancy_workers=4,
        reducer_token_budget=1200,
        reducer_max_contexts=3,
        message_history=[
            {'role': 'user', 'content': 'Earlier question'},
            {'role': 'assistant', 'content': 'answer'},
            {'role': 'user', 'content': 'Follow-up question'},
            {'role': 'assistant', 'content': 'follow-up answer'},
        ],
    )

    assert execution['status'] == 'completed'
    assert 'Here is what I found' in str(execution['final_response'])
    step_names = [step['name'] for step in execution['steps']]
    assert 'plan' in step_names
    assert 'memory_check' in step_names
    assert 'execute_step.grep_repo' in step_names
    assert 'execute_step.search' in step_names
    assert 'execute_step.compose_response' in step_names


def test_orchestrator_loop_handles_failure_and_marks_span_failed() -> None:
    def _failing_search_tool(**kwargs: object) -> ResearchPipelineResult:  # noqa: ARG001
        raise RuntimeError('synthetic_search_failure')

    def _grep_repo_tool(
        *,
        query: str,
        repos_in_scope: tuple[str, ...],
        page: int = 1,
        page_size: int = 10,
        signature_max_chars: int = 120,
    ) -> GrepRepoResult:  # noqa: ARG001
        return {
            'query': query,
            'page': page,
            'page_size': page_size,
            'total_matches': 0,
            'has_more': False,
            'matches': [],
        }

    service = OrchestratorLoopService(
        search_tool=_failing_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
    )

    execution = service.execute(
        run_id='run-2',
        session_id='session-2',
        user_id='user-2',
        trace_id='trace-2',
        message='Find auth function flow in the repository',
        repos_in_scope=('repo-b',),
        top_k=8,
        candidate_pool_multiplier=6,
        relevancy_threshold=0.35,
        relevancy_workers=4,
        reducer_token_budget=1200,
        reducer_max_contexts=None,
        message_history=[],
    )

    assert execution['status'] == 'failed'
    assert 'synthetic_search_failure' in str(execution['error'])


def test_orchestrator_loop_limits_tool_iterations_to_five() -> None:
    grep_pages: list[int] = []

    def _search_tool(**kwargs: object) -> ResearchPipelineResult:  # noqa: ARG001
        return _result_with_context()

    def _grep_repo_tool(
        *,
        query: str,
        repos_in_scope: tuple[str, ...],
        page: int = 1,
        page_size: int = 10,
        signature_max_chars: int = 120,
    ) -> GrepRepoResult:  # noqa: ARG001
        grep_pages.append(page)
        return {
            'query': query,
            'page': page,
            'page_size': page_size,
            'total_matches': 100,
            'has_more': True,
            'matches': [],
        }

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
        max_tool_iterations=5,
    )

    execution = service.execute(
        run_id='run-limit',
        session_id='session-limit',
        user_id='user-limit',
        trace_id='trace-limit',
        message='find billing function code path',
        repos_in_scope=('repo-a',),
        top_k=8,
        candidate_pool_multiplier=6,
        relevancy_threshold=0.35,
        relevancy_workers=4,
        reducer_token_budget=1200,
        reducer_max_contexts=3,
        message_history=[],
    )

    assert execution['status'] == 'completed'
    assert execution['error'] is None
    assert len(grep_pages) == 5


def test_orchestrator_loop_routes_conversational_messages_before_research() -> None:
    search_calls = {'count': 0}

    def _search_tool(**kwargs: object) -> ResearchPipelineResult:  # noqa: ARG001
        search_calls['count'] += 1
        return _result_with_context()

    def _grep_repo_tool(
        *,
        query: str,
        repos_in_scope: tuple[str, ...],
        page: int = 1,
        page_size: int = 10,
        signature_max_chars: int = 120,
    ) -> GrepRepoResult:  # noqa: ARG001
        raise AssertionError('grep_repo should not run for conversational route')

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
        memory_threshold_messages=2,
    )

    execution = service.execute(
        run_id='run-conversation',
        session_id='session-conversation',
        user_id='user-conversation',
        trace_id='trace-conversation',
        message='Hey can we talk through a plan first?',
        repos_in_scope=('repo-a',),
        top_k=8,
        candidate_pool_multiplier=6,
        relevancy_threshold=0.35,
        relevancy_workers=4,
        reducer_token_budget=1200,
        reducer_max_contexts=3,
        message_history=[
            {'role': 'user', 'content': 'Need help with architecture'},
            {'role': 'assistant', 'content': 'Sure, what constraints matter most?'},
        ],
    )

    assert execution['status'] == 'completed'
    assert 'conversation:' in str(execution['final_response']).lower()
    assert search_calls['count'] == 0

    step_names = [step['name'] for step in execution['steps']]
    assert 'route' in step_names
    assert 'execute_step.conversation' in step_names
    assert 'execute_step.search' not in step_names
