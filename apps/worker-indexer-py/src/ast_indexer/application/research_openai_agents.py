from __future__ import annotations

import json
import os

from ast_indexer.application.research_pipeline import QueryProdderPort, ResearchObjective, ReasoningAgentPort


class OpenAIReasoningAgent(ReasoningAgentPort):
    def __init__(
        self,
        *,
        model: str = 'gpt-4o-mini',
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
        self._client = OpenAI(api_key=resolved_api_key, base_url=resolved_base_url or None)
        self._model = model

    def build_objective(self, prompt: str, repos_in_scope: tuple[str, ...]) -> ResearchObjective:
        system_prompt = (
            'You are a code research planner. '
            'Return strict JSON with fields: intent (string), entities (array of strings), repos_in_scope (array of strings).'
        )
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
        system_prompt = (
            'You are a code reducer for orchestration agents. '
            'Produce concise, factual JSON with fields: '
            'abstract (string), evidence_snippets (array of short code snippets), '
            'open_questions (array of strings). '
            'Preserve function names and behavior. '
            'Do not invent facts. '
            f'Target budget is approximately {max(32, token_budget)} tokens.'
        )
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

        system_prompt = (
            'You are a code reducer for orchestration agents. '
            'Return strict JSON with field summaries (array). '
            'Each item must include repo, path, symbol, abstract, evidence_snippets (array), open_questions (array). '
            'Preserve function names and produce factual, concise summaries only. '
            'Do not invent symbols, paths, or behavior. '
            f'Total output should roughly fit in {max(64, token_budget)} tokens.'
        )
        user_prompt = json.dumps({'contexts': contexts})

        response = self._client.chat.completions.create(
            model=self._model,
            response_format={'type': 'json_object'},
            temperature=0,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        payload = json.loads(response.choices[0].message.content or '{}')
        return payload if isinstance(payload, dict) else {'summaries': []}


class OpenAIQueryProdder(QueryProdderPort):
    def __init__(
        self,
        *,
        model: str = 'gpt-4o-mini',
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
        self._client = OpenAI(api_key=resolved_api_key, base_url=resolved_base_url or None)
        self._model = model

    def build_queries(self, objective: ResearchObjective) -> tuple[str, ...]:
        response = self._client.chat.completions.create(
            model=self._model,
            response_format={'type': 'json_object'},
            temperature=0.1,
            messages=[
                {
                    'role': 'system',
                    'content': (
                        'Generate 3 to 6 focused semantic code-search queries. '
                        'Return strict JSON with one field: queries (array of strings).'
                    ),
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
