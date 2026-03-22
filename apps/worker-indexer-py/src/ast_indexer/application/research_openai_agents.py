from __future__ import annotations

import json
import os
from typing import Callable

from ast_indexer.application import openai_prompt_catalog
from ast_indexer.application import runtime_config
from ast_indexer.application.research_pipeline import QueryProdderPort, ResearchObjective, ReasoningAgentPort


class OpenAIReasoningAgent(ReasoningAgentPort):
    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                'openai package is not installed. Install with: pip install "ast-indexer[embeddings]"'
            ) from exc

        resolved_api_key = api_key or os.getenv('OPENAI_API_KEY')
        if not resolved_api_key:
            raise RuntimeError('OPENAI_API_KEY is required for OpenAI reasoning agent')

        resolved_base_url = base_url.strip() if isinstance(base_url, str) else None
        self._client = OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url or None,
            max_retries=0,
            timeout=6.0,
        )
        self._model = model or runtime_config.default_openai_model()

    def build_objective(self, prompt: str, repos_in_scope: tuple[str, ...]) -> ResearchObjective:
        system_prompt = openai_prompt_catalog.planner_system_prompt()
        user_prompt = (
            'Prompt:\n'
            f'{prompt}\n\n'
            'Known repos in scope:\n'
            f'{json.dumps(list(repos_in_scope))}\n'
            'Respond with JSON only.'
        )

        response = self._client.chat.completions.create(
            model=self._model,
            response_format={'type': 'json_object'},
            temperature=0,
            max_tokens=120,
            timeout=3.0,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        payload = json.loads(response.choices[0].message.content or '{}')

        intent = str(payload.get('intent') or prompt)
        entities = tuple(str(item) for item in payload.get('entities', []) if str(item).strip())
        suggested_repos = tuple(
            str(item) for item in payload.get('repos_in_scope', []) if str(item).strip()
        )

        if suggested_repos:
            return ResearchObjective(intent=intent, entities=entities, repos_in_scope=suggested_repos)
        return ResearchObjective(intent=intent, entities=entities, repos_in_scope=repos_in_scope)

    def summarize_reducer_context(
        self,
        *,
        symbol: str,
        signature: str,
        path: str,
        repo: str,
        kind: str,
        docstring: str | None,
        body: str,
        resolved_callees: tuple[str, ...],
        token_budget: int,
    ) -> dict:
        system_prompt = openai_prompt_catalog.reducer_single_system_prompt(token_budget)
        user_prompt = json.dumps(
            {
                'symbol': symbol,
                'signature': signature,
                'path': path,
                'repo': repo,
                'kind': kind,
                'docstring': docstring,
                'resolved_callees': list(resolved_callees),
                'body': body,
            }
        )

        response = self._client.chat.completions.create(
            model=self._model,
            response_format={'type': 'json_object'},
            temperature=0,
            max_tokens=max(90, min(260, token_budget)),
            timeout=3.0,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        payload = json.loads(response.choices[0].message.content or '{}')
        return payload if isinstance(payload, dict) else {}

    def summarize_reducer_context_batch(
        self,
        *,
        contexts: list[dict],
        token_budget: int,
    ) -> dict:
        if not contexts:
            return {'summaries': []}

        system_prompt = openai_prompt_catalog.reducer_batch_system_prompt(token_budget)
        user_prompt = json.dumps({'contexts': contexts})

        response = self._client.chat.completions.create(
            model=self._model,
            response_format={'type': 'json_object'},
            temperature=0,
            max_tokens=max(140, min(320, token_budget)),
            timeout=3.0,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        payload = json.loads(response.choices[0].message.content or '{}')
        return payload if isinstance(payload, dict) else {'summaries': []}

    def score_relevancy_batch(
        self,
        *,
        objective: dict,
        candidates: list[dict],
    ) -> dict:
        if not candidates:
            return {'scores': []}

        system_prompt = openai_prompt_catalog.relevancy_system_prompt()
        user_prompt = json.dumps({'objective': objective, 'candidates': candidates})

        response = self._client.chat.completions.create(
            model=self._model,
            response_format={'type': 'json_object'},
            temperature=0,
            max_tokens=max(140, min(360, len(candidates) * 12)),
            timeout=3.0,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        payload = json.loads(response.choices[0].message.content or '{}')
        return payload if isinstance(payload, dict) else {'scores': []}

    def cleanup_reducer_corpus(
        self,
        *,
        objective: dict,
        relation_corpus: str,
        token_budget: int,
    ) -> dict:
        if not relation_corpus.strip():
            return {'cleaned_corpus': ''}

        system_prompt = openai_prompt_catalog.relation_cleanup_system_prompt()
        user_prompt = json.dumps(
            {
                'objective': objective,
                'token_budget': max(64, token_budget),
                'relation_corpus': relation_corpus,
            }
        )

        response = self._client.chat.completions.create(
            model=self._model,
            response_format={'type': 'json_object'},
            temperature=0,
            max_tokens=max(160, min(420, token_budget)),
            timeout=3.0,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        payload = json.loads(response.choices[0].message.content or '{}')
        return payload if isinstance(payload, dict) else {'cleaned_corpus': relation_corpus}


class OpenAIQueryProdder(QueryProdderPort):
    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                'openai package is not installed. Install with: pip install "ast-indexer[embeddings]"'
            ) from exc

        resolved_api_key = api_key or os.getenv('OPENAI_API_KEY')
        if not resolved_api_key:
            raise RuntimeError('OPENAI_API_KEY is required for OpenAI query prodder')

        resolved_base_url = base_url.strip() if isinstance(base_url, str) else None
        self._client = OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url or None,
            max_retries=0,
            timeout=6.0,
        )
        self._model = model or runtime_config.default_openai_model()

    def build_queries(self, objective: ResearchObjective) -> tuple[str, ...]:
        system_prompt = openai_prompt_catalog.query_prodder_system_prompt()
        response = self._client.chat.completions.create(
            model=self._model,
            response_format={'type': 'json_object'},
            temperature=0.1,
            max_tokens=120,
            timeout=3.0,
            messages=[
                {
                    'role': 'system',
                    'content': system_prompt,
                },
                {
                    'role': 'user',
                    'content': json.dumps(
                        {
                            'intent': objective.intent,
                            'entities': list(objective.entities),
                            'repos_in_scope': list(objective.repos_in_scope),
                        }
                    ),
                },
            ],
        )
        payload = json.loads(response.choices[0].message.content or '{}')
        queries = tuple(str(item) for item in payload.get('queries', []) if str(item).strip())
        if queries:
            return queries

        fallback = [objective.intent]
        fallback.extend(objective.entities[:3])
        return tuple(dict.fromkeys(item for item in fallback if item.strip()))


class OpenAIConversationalAgent:
    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        tool_definitions: list[dict] | None = None,
        tool_handlers: dict[str, Callable[..., dict]] | None = None,
        max_tool_calls: int = 8,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                'openai package is not installed. Install with: pip install "ast-indexer[embeddings]"'
            ) from exc

        resolved_api_key = api_key or os.getenv('OPENAI_API_KEY')
        if not resolved_api_key:
            raise RuntimeError('OPENAI_API_KEY is required for OpenAI conversational agent')

        resolved_base_url = base_url.strip() if isinstance(base_url, str) else None
        self._client = OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url or None,
            max_retries=0,
            timeout=8.0,
        )
        self._model = model or runtime_config.default_openai_model()
        self._tool_definitions = [item for item in (tool_definitions or []) if isinstance(item, dict)]
        self._tool_handlers = dict(tool_handlers or {})
        self._max_tool_calls = max(1, min(20, int(max_tool_calls)))
        self._last_tool_events: list[dict] = []

    def __call__(
        self,
        *,
        message: str,
        context: str | None = None,
    ) -> str:
        self._last_tool_events = []
        system_prompt = openai_prompt_catalog.conversational_system_prompt()
        if self._tool_handlers:
            system_prompt += (
                ' You can call tools when needed. Enforce this contract: '
                '1) broad exploration -> get_folder_structure first; '
                '2) file evidence -> get_file_contents with narrow line ranges and explicit max_tokens; '
                '3) on max_tokens_exceeded, retry with smaller ranges; '
                '4) if evidence remains weak, say so explicitly and ask for a narrower target. '
                'Keep tool args valid and minimal.'
            )
        if context and context.strip():
            user_content = f'Context (may be empty sections):\n{context.strip()}\n\nUser message:\n{message.strip()}'
        else:
            user_content = message.strip()

        messages: list[dict] = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_content},
        ]

        for _ in range(self._max_tool_calls + 1):
            request: dict = {
                'model': self._model,
                'temperature': 0.7,
                'max_tokens': 300,
                'timeout': 6.0,
                'messages': messages,
            }
            if self._tool_definitions and self._tool_handlers:
                request['tools'] = self._tool_definitions
                request['tool_choice'] = 'auto'

            response = self._client.chat.completions.create(**request)
            assistant_message = response.choices[0].message
            text = str(getattr(assistant_message, 'content', '') or '').strip()
            tool_calls = list(getattr(assistant_message, 'tool_calls', None) or [])

            if not tool_calls:
                if text:
                    return text
                break

            formatted_tool_calls: list[dict] = []
            for call in tool_calls:
                function = getattr(call, 'function', None)
                formatted_tool_calls.append(
                    {
                        'id': getattr(call, 'id', ''),
                        'type': 'function',
                        'function': {
                            'name': str(getattr(function, 'name', '')),
                            'arguments': str(getattr(function, 'arguments', '') or '{}'),
                        },
                    }
                )

            messages.append(
                {
                    'role': 'assistant',
                    'content': text,
                    'tool_calls': formatted_tool_calls,
                }
            )

            for call in tool_calls:
                function = getattr(call, 'function', None)
                tool_name = str(getattr(function, 'name', ''))
                raw_arguments = str(getattr(function, 'arguments', '') or '{}')
                result = self._invoke_tool(tool_name=tool_name, raw_arguments=raw_arguments)
                self._last_tool_events.append(
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

        return (
            'I could not complete a reliable response from available context and tool results. '
            'Please narrow the request or specify a repository and file path.'
        )

    def pop_last_tool_events(self) -> list[dict]:
        events = list(self._last_tool_events)
        self._last_tool_events = []
        return events

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


class OpenAIRoutingAgent:
    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                'openai package is not installed. Install with: pip install "ast-indexer[embeddings]"'
            ) from exc

        resolved_api_key = api_key or os.getenv('OPENAI_API_KEY')
        if not resolved_api_key:
            raise RuntimeError('OPENAI_API_KEY is required for OpenAI routing agent')

        resolved_base_url = base_url.strip() if isinstance(base_url, str) else None
        self._client = OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url or None,
            max_retries=0,
            timeout=6.0,
        )
        self._model = model or runtime_config.default_openai_model()

    def __call__(
        self,
        *,
        message: str,
        repos_in_scope: tuple[str, ...],
        memory_summary: str,
        tool_memory_summary: str,
        has_conversational_agent: bool,
    ) -> dict:
        system_prompt = openai_prompt_catalog.routing_system_prompt()
        user_payload = {
            'message': message,
            'repos_in_scope': list(repos_in_scope),
            'memory_summary': memory_summary,
            'tool_memory_summary': tool_memory_summary,
            'has_conversational_agent': has_conversational_agent,
        }

        response = self._client.chat.completions.create(
            model=self._model,
            response_format={'type': 'json_object'},
            temperature=0,
            max_tokens=90,
            timeout=3.0,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': json.dumps(user_payload)},
            ],
        )
        payload = json.loads(response.choices[0].message.content or '{}')
        return payload if isinstance(payload, dict) else {}
