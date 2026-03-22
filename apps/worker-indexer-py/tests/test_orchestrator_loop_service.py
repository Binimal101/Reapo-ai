from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.application.orchestrator_loop_service import GrepRepoResult, OrchestratorLoopService
from ast_indexer.application.research_pipeline import (
    ReducedResearchContext,
    ResearchCandidate,
    ResearchObjective,
    ResearchPipelineResult,
    RelevancyCandidate,
)


def _conversational_agent_tool(*, message: str, context: str | None = None) -> str:
    ctx = f' | ctx={len(context)}' if context else ''
    return f'conversation:{message.strip()}{ctx}'


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
    assert 'conversation:' in str(execution['final_response'])
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
    assert 'execute_step.conversation' in step_names
    assert 'execute_step.search' not in step_names


def test_orchestrator_loop_emits_observability_hierarchy() -> None:
    observability = InMemoryObservabilityAdapter()

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
            'matches': [],
        }

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
        observability=observability,
    )
    execution = service.execute(
        run_id='run-observability',
        session_id='session-observability',
        user_id='user-observability',
        trace_id='trace-observability',
        message='find auth function flow in the repository',
        repos_in_scope=('repo-b',),
        top_k=8,
        candidate_pool_multiplier=6,
        relevancy_threshold=0.35,
        relevancy_workers=4,
        reducer_token_budget=1200,
        reducer_max_contexts=None,
        message_history=[],
    )

    assert execution['status'] == 'completed'
    spans = observability.list_spans()
    names = [span.name for span in spans]
    assert 'orchestrator_loop_run' in names
    assert 'orchestrator.plan' in names
    assert 'orchestrator.coding_mode' in names
    assert 'orchestrator.compose_response' in names
    assert 'langgraph.transition' in names
    assert all(span.finished_at is not None for span in spans)

    transitions = [span for span in spans if span.name == 'langgraph.transition']
    transition_pairs = {(str(span.metadata.get('from_node')), str(span.metadata.get('to_node'))) for span in transitions if span.metadata}
    assert ('START', 'plan') in transition_pairs
    assert ('plan', 'memory_check') in transition_pairs
    assert ('memory_check', 'coding_mode') in transition_pairs
    assert ('coding_mode', 'compose_response') in transition_pairs
    assert ('compose_response', 'END') in transition_pairs


def test_orchestrator_loop_logs_conversational_transitions() -> None:
    observability = InMemoryObservabilityAdapter()

    def _search_tool(**kwargs: object) -> ResearchPipelineResult:  # noqa: ARG001
        raise AssertionError('search should not run for conversational flow')

    def _grep_repo_tool(
        *,
        query: str,
        repos_in_scope: tuple[str, ...],
        page: int = 1,
        page_size: int = 10,
        signature_max_chars: int = 120,
    ) -> GrepRepoResult:  # noqa: ARG001
        raise AssertionError('grep should not run for conversational flow')

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
        observability=observability,
    )
    execution = service.execute(
        run_id='run-conv-observability',
        session_id='session-conv-observability',
        user_id='user-conv-observability',
        trace_id='trace-conv-observability',
        message='Hey can you help me think through this?',
        repos_in_scope=('repo-b',),
        top_k=8,
        candidate_pool_multiplier=6,
        relevancy_threshold=0.35,
        relevancy_workers=4,
        reducer_token_budget=1200,
        reducer_max_contexts=None,
        message_history=[],
    )

    assert execution['status'] == 'completed'
    transitions = [
        span for span in observability.list_spans() if span.name == 'langgraph.transition' and span.metadata
    ]
    transition_pairs = {
        (str(metadata.get('from_node')), str(metadata.get('to_node')))
        for span in transitions
        for metadata in [span.metadata or {}]
    }
    assert ('START', 'plan') in transition_pairs
    assert ('plan', 'memory_check') in transition_pairs
    assert ('memory_check', 'conversational_mode') in transition_pairs
    assert ('conversational_mode', 'compose_response') in transition_pairs
    assert ('compose_response', 'END') in transition_pairs


def test_orchestrator_routes_broad_repo_exploration_to_conversational_mode() -> None:
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
        raise AssertionError('grep should not run for broad conversational exploration route')

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
    )

    execution = service.execute(
        run_id='run-broad-explore',
        session_id='session-broad-explore',
        user_id='user-broad-explore',
        trace_id='trace-broad-explore',
        message='Can you map the repository structure and help me understand this codebase?',
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
    assert 'conversation:' in str(execution['final_response'])
    assert search_calls['count'] == 0


def test_orchestrator_routes_explicit_search_request_to_coding_mode() -> None:
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
        return {
            'query': query,
            'page': page,
            'page_size': page_size,
            'total_matches': 0,
            'has_more': False,
            'matches': [],
        }

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
    )

    execution = service.execute(
        run_id='run-explicit-search',
        session_id='session-explicit-search',
        user_id='user-explicit-search',
        trace_id='trace-explicit-search',
        message='use search tool to look for stuff about sms',
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
    assert search_calls['count'] == 1
    step_names = [step['name'] for step in execution['steps']]
    assert 'execute_step.search' in step_names


def test_orchestrator_routes_follow_up_to_coding_when_tool_memory_exists() -> None:
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
        return {
            'query': query,
            'page': page,
            'page_size': page_size,
            'total_matches': 0,
            'has_more': False,
            'matches': [],
        }

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
    )

    execution = service.execute(
        run_id='run-memory-aware-route',
        session_id='session-memory-aware-route',
        user_id='user-memory-aware-route',
        trace_id='trace-memory-aware-route',
        message='im ready lets do this, tell me more',
        repos_in_scope=('repo-a',),
        top_k=8,
        candidate_pool_multiplier=6,
        relevancy_threshold=0.35,
        relevancy_workers=4,
        reducer_token_budget=1200,
        reducer_max_contexts=3,
        message_history=[],
        prior_tool_outcomes=[
            {
                'tool': 'search_tool',
                'ok': True,
                'result': {'candidate_count': 0, 'reduced_count': 0},
            }
        ],
    )

    assert execution['status'] == 'completed'
    assert search_calls['count'] == 1
    step_names = [step['name'] for step in execution['steps']]
    assert 'execute_step.search' in step_names


def test_orchestrator_prefers_routing_agent_decision() -> None:
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
        return {
            'query': query,
            'page': page,
            'page_size': page_size,
            'total_matches': 0,
            'has_more': False,
            'matches': [],
        }

    def _routing_agent_tool(**kwargs: object) -> dict:  # noqa: ARG001
        return {'route': 'coding_mode', 'reason': 'explicit_agent_choice'}

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
        routing_agent_tool=_routing_agent_tool,
    )

    execution = service.execute(
        run_id='run-routing-agent-priority',
        session_id='session-routing-agent-priority',
        user_id='user-routing-agent-priority',
        trace_id='trace-routing-agent-priority',
        message='Hey can we talk through a plan first?',
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
    assert search_calls['count'] == 1
    step_names = [step['name'] for step in execution['steps']]
    assert 'execute_step.search' in step_names


def test_orchestrator_defaults_to_coding_when_routing_agent_returns_invalid_route() -> None:
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
        return {
            'query': query,
            'page': page,
            'page_size': page_size,
            'total_matches': 0,
            'has_more': False,
            'matches': [],
        }

    def _routing_agent_tool(**kwargs: object) -> dict:  # noqa: ARG001
        return {'route': 'something_else'}

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
        routing_agent_tool=_routing_agent_tool,
    )

    execution = service.execute(
        run_id='run-routing-agent-fallback',
        session_id='session-routing-agent-fallback',
        user_id='user-routing-agent-fallback',
        trace_id='trace-routing-agent-fallback',
        message='Hey can we talk through a plan first?',
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
    assert search_calls['count'] == 1
    step_names = [step['name'] for step in execution['steps']]
    assert 'execute_step.search' in step_names


def test_orchestrator_selects_semantic_strategy_for_system_mapping_prompt() -> None:
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
            'total_matches': 0,
            'has_more': False,
            'matches': [],
        }

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
    )

    strategy = service._select_tool_strategy('Look for general pieces of the system and map architecture')
    assert strategy == 'semantic_then_grep_then_file'


def test_orchestrator_retrieval_compose_context_contains_fuller_tool_outputs() -> None:
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
            'total_matches': 0,
            'has_more': False,
            'matches': [],
        }

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
    )

    context = service._build_retrieval_compose_context(
        result=_result_with_context(),
        grep_samples=[],
        research_story='1) Started iterative research run.',
        research_next_steps='- No additional research steps required.',
        satisfaction_clause='Return when evidence is grounded.',
        satisfaction_met=True,
    )

    assert 'Research objective:' in context
    assert 'Semantic queries used:' in context
    assert 'Vector candidate matches (top 12):' in context
    assert 'Relevancy-filtered candidates (top 12):' in context
    assert 'Retrieved source contexts (top 8):' in context
    assert 'Repository access note:' in context
    assert 'Research story so far:' in context
    assert 'Research next steps:' in context
    assert 'Satisfaction clause:' in context


def test_orchestrator_selects_grep_first_strategy_for_variable_import_prompt() -> None:
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
            'total_matches': 0,
            'has_more': False,
            'matches': [],
        }

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
    )

    strategy = service._select_tool_strategy('Find variable imports for auth environment constants')
    assert strategy == 'grep_then_semantic_then_file'


def test_orchestrator_loop_uses_human_readable_fallback_without_conversational_tool() -> None:
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
            'total_matches': 0,
            'has_more': False,
            'matches': [],
        }

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=None,
    )

    execution = service.execute(
        run_id='run-fallback',
        session_id='session-fallback',
        user_id='user-fallback',
        trace_id='trace-fallback',
        message='where is billing function logic?',
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
    final_response = str(execution['final_response'])
    assert 'I found relevant code in the indexed repositories' in final_response
    assert 'repo-a:src/billing.py:charge_customer' in final_response


def test_orchestrator_compacts_context_when_window_is_small() -> None:
    observed_context = {'value': ''}

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
        raise AssertionError('grep should not run for conversational flow')

    def _capturing_conversation_tool(*, message: str, context: str | None = None) -> str:
        observed_context['value'] = context or ''
        return f'conversation:{message}'

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_capturing_conversation_tool,
        context_window_chars=420,
    )

    long_history = [
        {'role': 'user', 'content': 'A' * 380},
        {'role': 'assistant', 'content': 'B' * 380},
        {'role': 'user', 'content': 'C' * 380},
        {'role': 'assistant', 'content': 'D' * 380},
        {'role': 'user', 'content': 'E' * 380},
    ]

    execution = service.execute(
        run_id='run-compact',
        session_id='session-compact',
        user_id='user-compact',
        trace_id='trace-compact',
        message='help me understand this',
        repos_in_scope=('repo-a',),
        top_k=8,
        candidate_pool_multiplier=6,
        relevancy_threshold=0.35,
        relevancy_workers=4,
        reducer_token_budget=1200,
        reducer_max_contexts=3,
        message_history=long_history,
        prior_tool_outcomes=[
            {'tool': 'get_folder_structure', 'ok': True, 'result': {'path': 'src', 'total_entries': 240}},
            {'tool': 'get_file_contents', 'ok': False, 'result': {'error': 'max_tokens_exceeded'}},
        ],
    )

    assert execution['status'] == 'completed'
    assert 'Recent messages (compacted):' in observed_context['value']
    assert 'Prior tool outcomes (compacted):' in observed_context['value']


def test_orchestrator_records_conversational_tool_events_in_steps() -> None:
    class _ToolAwareConversationAgent:
        def __call__(self, *, message: str, context: str | None = None) -> str:  # noqa: ARG002
            return f'conversation:{message}'

        def pop_last_tool_events(self) -> list[dict]:
            return [
                {
                    'tool': 'get_folder_structure',
                    'ok': True,
                    'result': {'path': 'src', 'total_entries': 12},
                }
            ]

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
        raise AssertionError('grep should not run for conversational flow')

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_ToolAwareConversationAgent(),
    )

    execution = service.execute(
        run_id='run-tool-events',
        session_id='session-tool-events',
        user_id='user-tool-events',
        trace_id='trace-tool-events',
        message='hello there',
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
    conversation_steps = [step for step in execution['steps'] if step.get('name') == 'execute_step.conversation']
    assert conversation_steps
    output = conversation_steps[-1].get('output', {})
    assert isinstance(output, dict)
    assert isinstance(output.get('tool_events'), list)
    assert output['tool_events'][0]['tool'] == 'get_folder_structure'


def test_orchestrator_delegates_code_changes_to_coding_subagent() -> None:
    captured = {'called': False, 'context': ''}

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

    def _coding_subagent(**kwargs: object) -> dict:
        captured['called'] = True
        captured['context'] = str(kwargs.get('research_context', ''))
        return {
            'status': 'completed',
            'assistant_response': 'Coding subagent completed and opened a pull request.',
            'pr_payload': {'pull_request': {'number': 101, 'html_url': 'https://example/pull/101'}},
            'feature_details': {'summary': 'Implemented requested behavior.', 'changed_paths': ['src/billing.py']},
        }

    service = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
        coding_subagent_tool=_coding_subagent,
    )

    execution = service.execute(
        run_id='run-coding-subagent',
        session_id='session-coding-subagent',
        user_id='user-coding-subagent',
        trace_id='trace-coding-subagent',
        message='Implement idempotent billing retry behavior',
        repos_in_scope=('repo-a',),
        top_k=8,
        candidate_pool_multiplier=6,
        relevancy_threshold=0.35,
        relevancy_workers=4,
        reducer_token_budget=1200,
        reducer_max_contexts=3,
        message_history=[],
        coding_request={
            'owner': 'acme',
            'repo': 'checkout',
            'base_branch': 'main',
            'dry_run': True,
        },
    )

    assert execution['status'] == 'completed'
    assert captured['called'] is True
    assert 'Development prerequisites from orchestration' in captured['context']
    assert 'Coding subagent completed and opened a pull request.' in str(execution['final_response'])
    assert isinstance(execution.get('coding_result'), dict)
    step_names = [step['name'] for step in execution['steps']]
    assert 'execute_step.coding_subagent' in step_names
