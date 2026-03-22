from __future__ import annotations

from typing import Any
from uuid import uuid4

from ast_indexer.adapters.orchestrator.json_file_orchestrator_state_store_adapter import (
    JsonFileOrchestratorStateStoreAdapter,
)
from ast_indexer.application.orchestrator_loop_service import OrchestratorLoopService


class ChatOrchestratorService:
    def __init__(
        self,
        *,
        state_store: JsonFileOrchestratorStateStoreAdapter,
        orchestrator: OrchestratorLoopService,
    ) -> None:
        self._state_store = state_store
        self._orchestrator = orchestrator

    def create_session(self, *, user_id: str) -> dict:
        if not user_id.strip():
            raise ValueError('user_id is required')
        return self._state_store.create_session(user_id=user_id.strip())

    def get_session(self, *, session_id: str) -> dict | None:
        return self._state_store.get_session(session_id)

    def get_run(self, *, run_id: str) -> dict | None:
        return self._state_store.get_run(run_id)

    def send_message(
        self,
        *,
        session_id: str,
        user_id: str,
        message: str,
        repos_in_scope: tuple[str, ...],
        top_k: int = 8,
        candidate_pool_multiplier: int = 6,
        relevancy_threshold: float = 0.35,
        relevancy_workers: int = 6,
        reducer_token_budget: int = 2500,
        reducer_max_contexts: int | None = None,
    ) -> dict:
        session = self._state_store.get_session(session_id)
        if session is None:
            raise KeyError(f'session not found: {session_id}')

        owner = str(session.get('user_id', ''))
        if owner != user_id:
            raise PermissionError('session access denied')

        content = message.strip()
        if not content:
            raise ValueError('message is required')

        self._state_store.append_message(
            session_id=session_id,
            role='user',
            content=content,
        )

        trace_id = f'orchestrator-{uuid4().hex[:16]}'
        run = self._state_store.create_run(
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
            prompt=content,
            repos_in_scope=repos_in_scope,
        )

        execution = self._orchestrator.execute(
            run_id=str(run['run_id']),
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
            message=content,
            repos_in_scope=repos_in_scope,
            top_k=top_k,
            candidate_pool_multiplier=candidate_pool_multiplier,
            relevancy_threshold=relevancy_threshold,
            relevancy_workers=relevancy_workers,
            reducer_token_budget=reducer_token_budget,
            reducer_max_contexts=reducer_max_contexts,
            message_history=list(session.get('messages', [])),
            prior_tool_outcomes=self._collect_prior_tool_outcomes(session_id=session_id),
        )

        persisted = self._state_store.update_run(
            str(run['run_id']),
            status=execution['status'],
            finished_at=execution['finished_at'],
            steps=execution['steps'],
            final_response=execution['final_response'],
            error=execution['error'],
        )

        assistant_content = str(execution.get('final_response') or 'I could not generate a response.')
        self._state_store.append_message(
            session_id=session_id,
            role='assistant',
            content=assistant_content,
            run_id=str(run['run_id']),
        )

        refreshed_session = self._state_store.get_session(session_id)
        return {
            'session': refreshed_session,
            'run': persisted,
            'assistant_message': {
                'role': 'assistant',
                'content': assistant_content,
                'run_id': str(run['run_id']),
            },
        }

    def _collect_prior_tool_outcomes(self, *, session_id: str) -> list[dict[str, Any]]:
        if not hasattr(self._state_store, 'list_runs_for_session'):
            return []
        runs = self._state_store.list_runs_for_session(session_id=session_id, limit=12)
        outcomes: list[dict[str, Any]] = []
        for run in runs:
            steps = run.get('steps', [])
            if not isinstance(steps, list):
                continue
            for step in steps:
                if not isinstance(step, dict):
                    continue
                output = step.get('output')
                if not isinstance(output, dict):
                    continue

                tool_events = output.get('tool_events')
                if isinstance(tool_events, list):
                    for event in tool_events:
                        if isinstance(event, dict):
                            outcomes.append(event)
                    continue

                name = str(step.get('name', ''))
                if name.startswith('execute_step.search'):
                    outcomes.append(
                        {
                            'tool': 'search_tool',
                            'ok': True,
                            'result': {
                                'candidate_count': output.get('candidate_count'),
                                'relevant_count': output.get('relevant_count'),
                                'reduced_count': output.get('reduced_count'),
                            },
                        }
                    )
                elif name.startswith('execute_step.grep_repo'):
                    outcomes.append(
                        {
                            'tool': 'grep_repo_tool',
                            'ok': True,
                            'result': {
                                'returned': output.get('returned'),
                                'total_matches': output.get('total_matches'),
                                'has_more': output.get('has_more'),
                            },
                        }
                    )

        return outcomes[-50:]
