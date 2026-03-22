from pathlib import Path

import pytest

from ast_indexer.adapters.orchestrator.json_file_orchestrator_state_store_adapter import (
    JsonFileOrchestratorStateStoreAdapter,
)
from ast_indexer.application.chat_orchestrator_service import ChatOrchestratorService
from ast_indexer.application.orchestrator_loop_service import GrepRepoResult, OrchestratorLoopService
from ast_indexer.application.research_pipeline import (
    ReducedResearchContext,
    ResearchCandidate,
    ResearchObjective,
    ResearchPipelineResult,
    RelevancyCandidate,
)


def _conversational_agent_tool(*, message: str, context: str | None = None) -> str:
    tail = f' | ctx={len(context)}' if context else ''
    return f'conversation:{message.strip()}{tail}'


def _build_service(tmp_path: Path) -> ChatOrchestratorService:
    store = JsonFileOrchestratorStateStoreAdapter(tmp_path / 'orchestrator' / 'chat_state.json')

    def _search_tool(**kwargs: object) -> ResearchPipelineResult:  # noqa: ARG001
        return ResearchPipelineResult(
            trace_id='trace-1',
            objective=ResearchObjective(intent='lookup', entities=('checkout',), repos_in_scope=('checkout',)),
            queries=('checkout flow',),
            candidates=(
                ResearchCandidate(
                    repo='checkout',
                    path='src/checkout.py',
                    symbol='process_order',
                    kind='function',
                    signature='process_order(order)',
                    score=0.9,
                ),
            ),
            relevant_candidates=(
                RelevancyCandidate(
                    repo='checkout',
                    path='src/checkout.py',
                    symbol='process_order',
                    kind='function',
                    signature='process_order(order)',
                    score=0.9,
                    confidence=0.8,
                    matched_terms=('checkout',),
                ),
            ),
            enriched_context=(),
            reduced_context=(
                ReducedResearchContext(
                    repo='checkout',
                    path='src/checkout.py',
                    symbol='process_order',
                    kind='function',
                    signature='process_order(order)',
                    docstring=None,
                    reduced_body='def process_order(order): return True',
                    estimated_tokens=24,
                    body_was_truncated=False,
                    callees=(),
                    resolved_callees=(),
                ),
            ),
        )

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
                    'repo': 'checkout',
                    'path': 'src/checkout.py',
                    'symbol': 'process_order',
                    'kind': 'function',
                    'line': 1,
                    'signature': 'process_order(order)'[:signature_max_chars],
                }
            ],
        }

    orchestrator = OrchestratorLoopService(
        search_tool=_search_tool,
        grep_repo_tool=_grep_repo_tool,
        conversational_agent_tool=_conversational_agent_tool,
    )
    return ChatOrchestratorService(state_store=store, orchestrator=orchestrator)


def test_chat_service_creates_session_and_sends_message(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    session = service.create_session(user_id='user-1')
    response = service.send_message(
        session_id=str(session['session_id']),
        user_id='user-1',
        message='Find checkout function flow',
        repos_in_scope=('checkout',),
    )

    assert response['run']['status'] == 'completed'
    assert 'conversation:' in response['assistant_message']['content']

    updated_session = service.get_session(session_id=str(session['session_id']))
    assert updated_session is not None
    assert len(updated_session['messages']) == 2
    assert updated_session['messages'][0]['role'] == 'user'
    assert updated_session['messages'][1]['role'] == 'assistant'


def test_chat_service_enforces_session_access_and_input_validation(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    session = service.create_session(user_id='owner')

    with pytest.raises(PermissionError):
        service.send_message(
            session_id=str(session['session_id']),
            user_id='other-user',
            message='Any update?',
            repos_in_scope=(),
        )

    with pytest.raises(ValueError):
        service.send_message(
            session_id=str(session['session_id']),
            user_id='owner',
            message='   ',
            repos_in_scope=(),
        )

    with pytest.raises(KeyError):
        service.send_message(
            session_id='missing',
            user_id='owner',
            message='Find code',
            repos_in_scope=(),
        )


def test_chat_service_passes_prior_tool_outcomes_into_orchestrator(tmp_path: Path) -> None:
    store = JsonFileOrchestratorStateStoreAdapter(tmp_path / 'orchestrator' / 'chat_state.json')

    class _CapturingOrchestrator:
        def __init__(self) -> None:
            self.last_execute_kwargs: dict = {}

        def execute(self, **kwargs: object) -> dict:
            self.last_execute_kwargs = dict(kwargs)
            return {
                'run_id': str(kwargs['run_id']),
                'status': 'completed',
                'started_at': '2026-01-01T00:00:00+00:00',
                'finished_at': '2026-01-01T00:00:01+00:00',
                'steps': [],
                'final_response': 'ok',
                'coding_result': {'status': 'completed', 'feature_details': {'summary': 'done'}},
                'error': None,
            }

    orchestrator = _CapturingOrchestrator()
    service = ChatOrchestratorService(state_store=store, orchestrator=orchestrator)  # type: ignore[arg-type]

    session = service.create_session(user_id='user-1')
    session_id = str(session['session_id'])

    historical = store.create_run(
        session_id=session_id,
        user_id='user-1',
        trace_id='trace-history',
        prompt='older',
        repos_in_scope=('repo-a',),
    )
    store.update_run(
        str(historical['run_id']),
        status='completed',
        steps=[
            {
                'name': 'execute_step.conversation',
                'status': 'completed',
                'output': {
                    'tool_events': [
                        {
                            'tool': 'get_folder_structure',
                            'ok': True,
                            'result': {'path': 'src', 'total_entries': 24},
                        }
                    ]
                },
            }
        ],
        final_response='done',
        finished_at='2026-01-01T00:00:00+00:00',
        error=None,
    )

    response = service.send_message(
        session_id=session_id,
        user_id='user-1',
        message='next prompt',
        repos_in_scope=('repo-a',),
        coding_request={
            'owner': 'acme',
            'repo': 'checkout',
            'base_branch': 'main',
            'dry_run': True,
        },
    )

    prior = orchestrator.last_execute_kwargs.get('prior_tool_outcomes')
    assert isinstance(prior, list)
    assert prior
    assert prior[0]['tool'] == 'get_folder_structure'
    assert isinstance(orchestrator.last_execute_kwargs.get('coding_request'), dict)
    assert isinstance(response['run'].get('coding_result'), dict)
