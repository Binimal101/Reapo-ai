from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4


class JsonFileOrchestratorStateStoreAdapter:
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def create_session(self, *, user_id: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        session = {
            'session_id': uuid4().hex,
            'user_id': user_id,
            'created_at': now,
            'updated_at': now,
            'messages': [],
        }
        with self._lock:
            state = self._read_state()
            state['sessions'][session['session_id']] = session
            self._write_state(state)
        return session

    def get_session(self, session_id: str) -> dict | None:
        with self._lock:
            state = self._read_state()
            session = state['sessions'].get(session_id)
            return dict(session) if isinstance(session, dict) else None

    def append_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        run_id: str | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        message = {
            'role': role,
            'content': content,
            'timestamp': now,
            'run_id': run_id,
        }
        with self._lock:
            state = self._read_state()
            session = state['sessions'].get(session_id)
            if not isinstance(session, dict):
                raise KeyError(f'session not found: {session_id}')
            messages = session.setdefault('messages', [])
            if not isinstance(messages, list):
                session['messages'] = []
                messages = session['messages']
            messages.append(message)
            session['updated_at'] = now
            self._write_state(state)
        return message

    def create_run(
        self,
        *,
        session_id: str,
        user_id: str,
        trace_id: str,
        prompt: str,
        repos_in_scope: tuple[str, ...],
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        run = {
            'run_id': uuid4().hex,
            'session_id': session_id,
            'user_id': user_id,
            'trace_id': trace_id,
            'prompt': prompt,
            'repos_in_scope': list(repos_in_scope),
            'status': 'running',
            'started_at': now,
            'finished_at': None,
            'steps': [],
            'final_response': None,
            'error': None,
        }
        with self._lock:
            state = self._read_state()
            state['runs'][run['run_id']] = run
            self._write_state(state)
        return run

    def update_run(self, run_id: str, **fields: object) -> dict:
        with self._lock:
            state = self._read_state()
            run = state['runs'].get(run_id)
            if not isinstance(run, dict):
                raise KeyError(f'run not found: {run_id}')
            for key, value in fields.items():
                run[key] = value
            self._write_state(state)
        return dict(run)

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            state = self._read_state()
            run = state['runs'].get(run_id)
            return dict(run) if isinstance(run, dict) else None

    def _read_state(self) -> dict:
        if not self._file_path.exists():
            return {'sessions': {}, 'runs': {}}

        raw = self._file_path.read_text(encoding='utf-8').strip()
        if not raw:
            return {'sessions': {}, 'runs': {}}

        payload = json.loads(raw)
        sessions = payload.get('sessions', {}) if isinstance(payload, dict) else {}
        runs = payload.get('runs', {}) if isinstance(payload, dict) else {}
        return {
            'sessions': sessions if isinstance(sessions, dict) else {},
            'runs': runs if isinstance(runs, dict) else {},
        }

    def _write_state(self, state: dict) -> None:
        self._file_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
