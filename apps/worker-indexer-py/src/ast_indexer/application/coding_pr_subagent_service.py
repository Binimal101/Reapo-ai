from __future__ import annotations

import json
import os
from typing import Any, Callable, Protocol

from ast_indexer.application import openai_prompt_catalog
from ast_indexer.application import runtime_config
from ast_indexer.application.writer_pr_service import WriterFileChange


class WriterOpenPrPort(Protocol):
    def __call__(
        self,
        *,
        requesting_user_id: str,
        owner: str,
        repo: str,
        base_branch: str,
        title: str,
        body: str,
        files: list[WriterFileChange],
        branch_name: str | None,
        commit_message: str,
        draft: bool,
        dry_run: bool,
    ) -> tuple[int, dict]:
        ...


class OpenAICodingPrSubagent:
    def __init__(
        self,
        *,
        open_pr_tool: WriterOpenPrPort,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        tool_definitions: list[dict] | None = None,
        tool_handlers: dict[str, Callable[..., dict]] | None = None,
        max_tool_calls: int = 12,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                'openai package is not installed. Install with: pip install "ast-indexer[embeddings]"'
            ) from exc

        resolved_api_key = api_key or os.getenv('OPENAI_API_KEY')
        if not resolved_api_key:
            raise RuntimeError('OPENAI_API_KEY is required for OpenAI coding subagent')

        resolved_base_url = base_url.strip() if isinstance(base_url, str) else None
        self._client = OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url or None,
            max_retries=0,
            timeout=10.0,
        )
        self._model = model or runtime_config.default_openai_model()
        self._open_pr_tool = open_pr_tool
        self._tool_definitions = [item for item in (tool_definitions or []) if isinstance(item, dict)]
        self._tool_handlers = dict(tool_handlers or {})
        self._max_tool_calls = max(1, min(24, int(max_tool_calls)))

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
        owner = str(coding_request.get('owner', '')).strip()
        repo = str(coding_request.get('repo', '')).strip()
        base_branch = str(coding_request.get('base_branch', 'main')).strip() or 'main'
        if not owner or not repo:
            return {
                'status': 'error',
                'reason': 'coding_request.owner and coding_request.repo are required',
                'assistant_response': (
                    'I have the coding objective and research context, but I need owner and repo '
                    'inside coding_request to open a PR.'
                ),
            }

        payload = {
            'objective': objective,
            'coding_request': coding_request,
            'repos_in_scope': list(repos_in_scope),
            'trace_id': trace_id,
            'session_id': session_id,
            'user_id': user_id,
            'tool_strategy': tool_strategy,
            'memory_summary': memory_summary,
            'tool_memory_summary': tool_memory_summary,
            'recent_history': message_history[-8:],
            'research_context': research_context,
        }

        proposal, tool_events = self._draft_proposal(payload)
        if not proposal:
            return {
                'status': 'error',
                'reason': 'coding_subagent_empty_proposal',
                'assistant_response': (
                    'I could not produce a valid code-change proposal for this objective. '
                    'Please tighten the objective and retry.'
                ),
                'tool_events': tool_events,
            }

        files = self._build_file_changes(proposal.get('files'))
        if not files:
            return {
                'status': 'error',
                'reason': 'coding_subagent_no_files',
                'assistant_response': (
                    'I produced a draft plan but no file changes to apply. '
                    'Please provide a narrower implementation target.'
                ),
                'tool_events': tool_events,
                'proposal': proposal,
            }

        pr_title = str(proposal.get('pr_title') or coding_request.get('title') or f'Implement: {objective[:60]}').strip()
        pr_body = str(proposal.get('pr_body') or coding_request.get('body') or '').strip()
        commit_message = str(
            proposal.get('commit_message')
            or coding_request.get('commit_message')
            or 'feat: apply coding-agent changes'
        ).strip()
        branch_name_raw = proposal.get('branch_name')
        branch_name = str(branch_name_raw).strip() if isinstance(branch_name_raw, str) and branch_name_raw.strip() else None
        draft = bool(coding_request.get('draft', False))
        dry_run = bool(coding_request.get('dry_run', False))

        code, pr_payload = self._open_pr_tool(
            requesting_user_id=user_id,
            owner=owner,
            repo=repo,
            base_branch=base_branch,
            title=pr_title,
            body=pr_body,
            files=files,
            branch_name=branch_name,
            commit_message=commit_message,
            draft=draft,
            dry_run=dry_run,
        )
        if code != 200:
            return {
                'status': 'error',
                'reason': f'writer_open_pr_failed:{code}',
                'assistant_response': f'Code changes were generated, but PR creation failed: {pr_payload}',
                'proposal': proposal,
                'tool_events': tool_events,
                'pr_response_code': code,
                'pr_payload': pr_payload,
            }

        feature_summary = str(proposal.get('feature_summary') or '').strip()
        additions = self._normalize_path_list(proposal.get('additions'))
        deletions = self._normalize_path_list(proposal.get('deletions'))
        changed_paths = pr_payload.get('changed_paths') if isinstance(pr_payload, dict) else None
        if isinstance(changed_paths, list):
            changed_list = [str(item) for item in changed_paths if isinstance(item, str)]
        else:
            changed_list = [item.path for item in files]

        pr_info = pr_payload.get('pull_request', {}) if isinstance(pr_payload, dict) else {}
        pr_number = pr_info.get('number') if isinstance(pr_info, dict) else None
        pr_url = pr_info.get('html_url') if isinstance(pr_info, dict) else None

        response_lines = [
            'Coding subagent completed and opened a pull request.',
            f'- Objective: {objective}',
            f'- Repository: {owner}/{repo}',
            f'- Base branch: {base_branch}',
            f'- Files changed: {len(changed_list)}',
        ]
        if pr_number is not None:
            response_lines.append(f'- PR number: {pr_number}')
        if isinstance(pr_url, str) and pr_url:
            response_lines.append(f'- PR URL: {pr_url}')
        if feature_summary:
            response_lines.append('')
            response_lines.append('Feature summary:')
            response_lines.append(feature_summary)

        return {
            'status': 'completed',
            'assistant_response': '\n'.join(response_lines),
            'proposal': proposal,
            'tool_events': tool_events,
            'pr_payload': pr_payload,
            'feature_details': {
                'summary': feature_summary,
                'additions': additions,
                'deletions': deletions,
                'changed_paths': changed_list,
            },
        }

    def _draft_proposal(self, payload: dict[str, Any]) -> tuple[dict, list[dict]]:
        system_prompt = openai_prompt_catalog.coding_pr_system_prompt()
        messages: list[dict[str, Any]] = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': json.dumps(payload)},
        ]
        tool_events: list[dict] = []

        for _ in range(self._max_tool_calls + 1):
            request: dict[str, Any] = {
                'model': self._model,
                'temperature': 0.1,
                'max_tokens': 2200,
                'timeout': 10.0,
                'messages': messages,
            }
            if self._tool_definitions and self._tool_handlers:
                request['tools'] = self._tool_definitions
                request['tool_choice'] = 'auto'

            response = self._client.chat.completions.create(**request)
            assistant_message = response.choices[0].message
            assistant_text = str(getattr(assistant_message, 'content', '') or '')
            tool_calls = list(getattr(assistant_message, 'tool_calls', None) or [])

            if not tool_calls:
                proposal = self._parse_json_payload(assistant_text)
                return proposal, tool_events

            messages.append(
                {
                    'role': 'assistant',
                    'content': assistant_text,
                    'tool_calls': [
                        {
                            'id': str(getattr(call, 'id', '')),
                            'type': 'function',
                            'function': {
                                'name': str(getattr(getattr(call, 'function', None), 'name', '')),
                                'arguments': str(getattr(getattr(call, 'function', None), 'arguments', '') or '{}'),
                            },
                        }
                        for call in tool_calls
                    ],
                }
            )

            for call in tool_calls:
                function = getattr(call, 'function', None)
                tool_name = str(getattr(function, 'name', ''))
                raw_arguments = str(getattr(function, 'arguments', '') or '{}')
                result = self._invoke_tool(tool_name=tool_name, raw_arguments=raw_arguments)
                tool_events.append(
                    {
                        'tool': tool_name,
                        'arguments': raw_arguments[:1200],
                        'ok': bool(isinstance(result, dict) and result.get('ok', True)),
                        'result': self._trim_tool_result(result),
                    }
                )
                messages.append(
                    {
                        'role': 'tool',
                        'tool_call_id': str(getattr(call, 'id', '')),
                        'content': json.dumps(result),
                    }
                )

        return {}, tool_events

    def _invoke_tool(self, *, tool_name: str, raw_arguments: str) -> dict:
        handler = self._tool_handlers.get(tool_name)
        if handler is None:
            return {'ok': False, 'error': 'unknown_tool', 'tool': tool_name}

        try:
            payload = json.loads(raw_arguments) if raw_arguments.strip() else {}
        except Exception:
            return {'ok': False, 'error': 'invalid_tool_arguments_json', 'tool': tool_name}

        if not isinstance(payload, dict):
            return {'ok': False, 'error': 'invalid_tool_arguments_type', 'tool': tool_name}

        try:
            result = handler(**payload)
        except Exception as exc:  # noqa: BLE001
            return {'ok': False, 'error': 'tool_execution_failed', 'tool': tool_name, 'details': str(exc)}

        if isinstance(result, dict):
            return result
        return {'ok': True, 'tool': tool_name, 'result': result}

    def _build_file_changes(self, payload: Any) -> list[WriterFileChange]:
        if not isinstance(payload, list):
            return []

        changes: list[WriterFileChange] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            path = item.get('path')
            if not isinstance(path, str) or not path.strip():
                continue
            operation_raw = item.get('operation')
            operation = str(operation_raw).strip().lower() if isinstance(operation_raw, str) else 'upsert'
            if operation not in {'upsert', 'delete'}:
                operation = 'upsert'
            content_raw = item.get('content')
            if operation == 'upsert' and not isinstance(content_raw, str):
                continue
            content = content_raw if isinstance(content_raw, str) else ''
            changes.append(WriterFileChange(path=path.strip(), content=content, operation=operation))
        return changes

    def _normalize_path_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if isinstance(item, str) and str(item).strip()]

    def _parse_json_payload(self, text: str) -> dict:
        candidate = text.strip()
        if not candidate:
            return {}

        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        first = candidate.find('{')
        last = candidate.rfind('}')
        if first == -1 or last == -1 or last <= first:
            return {}
        try:
            parsed = json.loads(candidate[first : last + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _trim_tool_result(self, result: dict) -> dict:
        trimmed: dict = {}
        for key, value in result.items():
            if key == 'content' and isinstance(value, str):
                trimmed[key] = value[:500]
                continue
            if key == 'entries' and isinstance(value, list):
                trimmed[key] = value[:20]
                continue
            trimmed[key] = value
        return trimmed
