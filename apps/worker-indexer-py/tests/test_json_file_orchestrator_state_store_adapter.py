from pathlib import Path

from ast_indexer.adapters.orchestrator.json_file_orchestrator_state_store_adapter import (
    JsonFileOrchestratorStateStoreAdapter,
)


def test_state_store_persists_session_messages_and_runs(tmp_path: Path) -> None:
    store = JsonFileOrchestratorStateStoreAdapter(tmp_path / 'orchestrator' / 'chat_state.json')

    session = store.create_session(user_id='user-1')
    assert session['user_id'] == 'user-1'

    fetched = store.get_session(str(session['session_id']))
    assert fetched is not None
    assert fetched['session_id'] == session['session_id']

    first_message = store.append_message(
        session_id=str(session['session_id']),
        role='user',
        content='Hello orchestrator',
    )
    assert first_message['role'] == 'user'

    run = store.create_run(
        session_id=str(session['session_id']),
        user_id='user-1',
        trace_id='trace-1',
        prompt='Find endpoint code',
        repos_in_scope=('repo-a',),
    )
    assert run['status'] == 'running'

    updated = store.update_run(
        str(run['run_id']),
        status='completed',
        finished_at='2026-01-01T00:00:00+00:00',
        steps=[{'name': 'plan', 'status': 'completed'}],
        final_response='Done',
        error=None,
    )
    assert updated['status'] == 'completed'
    assert updated['final_response'] == 'Done'

    fetched_run = store.get_run(str(run['run_id']))
    assert fetched_run is not None
    assert fetched_run['status'] == 'completed'

    updated_session = store.get_session(str(session['session_id']))
    assert updated_session is not None
    assert len(updated_session['messages']) == 1


def test_state_store_raises_for_missing_session_or_run(tmp_path: Path) -> None:
    store = JsonFileOrchestratorStateStoreAdapter(tmp_path / 'orchestrator' / 'chat_state.json')

    try:
        store.append_message(session_id='missing', role='user', content='x')
        assert False, 'expected KeyError'
    except KeyError:
        pass

    try:
        store.update_run('missing', status='failed')
        assert False, 'expected KeyError'
    except KeyError:
        pass


def test_state_store_lists_recent_runs_for_session(tmp_path: Path) -> None:
    store = JsonFileOrchestratorStateStoreAdapter(tmp_path / 'orchestrator' / 'chat_state.json')

    session_a = store.create_session(user_id='user-a')
    session_b = store.create_session(user_id='user-b')

    run_a1 = store.create_run(
        session_id=str(session_a['session_id']),
        user_id='user-a',
        trace_id='trace-a1',
        prompt='first',
        repos_in_scope=('repo-a',),
    )
    run_a2 = store.create_run(
        session_id=str(session_a['session_id']),
        user_id='user-a',
        trace_id='trace-a2',
        prompt='second',
        repos_in_scope=('repo-a',),
    )
    store.create_run(
        session_id=str(session_b['session_id']),
        user_id='user-b',
        trace_id='trace-b1',
        prompt='other-session',
        repos_in_scope=('repo-b',),
    )

    listed = store.list_runs_for_session(session_id=str(session_a['session_id']), limit=1)
    assert len(listed) == 1
    assert listed[0]['run_id'] == run_a2['run_id']

    listed_all = store.list_runs_for_session(session_id=str(session_a['session_id']), limit=10)
    run_ids = [row['run_id'] for row in listed_all]
    assert run_a1['run_id'] in run_ids
    assert run_a2['run_id'] in run_ids
