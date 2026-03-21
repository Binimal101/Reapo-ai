from __future__ import annotations

import types

from ast_indexer.adapters.embeddings.openai_embedding_generator_adapter import OpenAIEmbeddingGeneratorAdapter


class _FakeEmbeddingsApi:
    def create(self, **kwargs):
        _ = kwargs
        row = types.SimpleNamespace(embedding=[0.1, -0.2, 0.3])
        return types.SimpleNamespace(data=[row])


class _FakeOpenAI:
    last_init: dict[str, object] = {}

    def __init__(self, api_key: str, base_url: str | None = None):
        self.__class__.last_init = {'api_key': api_key, 'base_url': base_url}
        self.embeddings = _FakeEmbeddingsApi()


def test_blank_base_url_is_normalized(monkeypatch):
    fake_module = types.SimpleNamespace(OpenAI=_FakeOpenAI)
    monkeypatch.setitem(__import__('sys').modules, 'openai', fake_module)

    adapter = OpenAIEmbeddingGeneratorAdapter(api_key='test-key', base_url='   ')
    vectors = adapter.embed(['hello'])

    assert _FakeOpenAI.last_init['api_key'] == 'test-key'
    assert _FakeOpenAI.last_init['base_url'] is None
    assert vectors == [(0.1, -0.2, 0.3)]
