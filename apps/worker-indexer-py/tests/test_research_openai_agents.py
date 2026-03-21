from __future__ import annotations

import types

from ast_indexer.application.research_pipeline import ResearchObjective
from ast_indexer.application.research_openai_agents import OpenAIQueryProdder, OpenAIReasoningAgent


class _FakeCompletions:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def create(self, **kwargs):
        _ = kwargs
        message = types.SimpleNamespace(content=self._payload)
        choice = types.SimpleNamespace(message=message)
        return types.SimpleNamespace(choices=[choice])


class _FakeChat:
    def __init__(self, payload: str) -> None:
        self.completions = _FakeCompletions(payload)


class _FakeOpenAI:
    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        _ = (api_key, base_url)
        self.chat = _FakeChat('{"intent":"find auth flow","entities":["auth","oauth"],"repos_in_scope":["repo-a"]}')


class _FakeOpenAIQueries:
    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        _ = (api_key, base_url)
        self.chat = _FakeChat('{"queries":["oauth callback handler","installation token"]}')


def test_openai_reasoning_agent_parses_structured_objective(monkeypatch) -> None:
    fake_module = types.SimpleNamespace(OpenAI=_FakeOpenAI)
    monkeypatch.setitem(__import__('sys').modules, 'openai', fake_module)

    agent = OpenAIReasoningAgent(api_key='test-key')
    objective = agent.build_objective('how auth works', ('repo-x',))

    assert objective.intent == 'find auth flow'
    assert objective.entities == ('auth', 'oauth')
    assert objective.repos_in_scope == ('repo-a',)


def test_openai_query_prodder_returns_queries(monkeypatch) -> None:
    fake_module = types.SimpleNamespace(OpenAI=_FakeOpenAIQueries)
    monkeypatch.setitem(__import__('sys').modules, 'openai', fake_module)

    prodder = OpenAIQueryProdder(api_key='test-key')
    queries = prodder.build_queries(
        objective=ResearchObjective(
            intent='find auth flow',
            entities=('auth',),
            repos_in_scope=('repo-a',),
        )
    )

    assert queries == ('oauth callback handler', 'installation token')
