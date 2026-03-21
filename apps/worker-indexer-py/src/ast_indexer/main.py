from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from ast_indexer.adapters.embeddings.openai_embedding_generator_adapter import OpenAIEmbeddingGeneratorAdapter
from ast_indexer.adapters.embeddings.sentence_transformers_embedding_generator_adapter import (
    SentenceTransformersEmbeddingGeneratorAdapter,
)
from ast_indexer.adapters.embeddings.simple_hash_embedding_generator_adapter import SimpleHashEmbeddingGeneratorAdapter
from ast_indexer.adapters.index_store.in_memory_symbol_index_store_adapter import InMemorySymbolIndexStoreAdapter
from ast_indexer.adapters.index_store.json_file_symbol_index_store_adapter import JsonFileSymbolIndexStoreAdapter
from ast_indexer.adapters.observability.langfuse_observability_adapter import LangfuseObservabilityAdapter
from ast_indexer.adapters.observability.jsonl_file_observability_adapter import JsonlFileObservabilityAdapter
from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.adapters.repository.local_fs_repository_reader_adapter import LocalFsRepositoryReaderAdapter
from ast_indexer.adapters.vector_store.in_memory_vector_store_adapter import InMemoryVectorStoreAdapter
from ast_indexer.adapters.vector_store.json_file_vector_store_adapter import JsonFileVectorStoreAdapter
from ast_indexer.application.index_python_repository_service import IndexPythonRepositoryService
from ast_indexer.application.research_openai_agents import OpenAIQueryProdder, OpenAIReasoningAgent
from ast_indexer.application.research_pipeline import QueryProdderPort, ResearchObjective, ReasoningAgentPort, ResearchPipeline
from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor
from ast_indexer.ports.embedding_generator import EmbeddingGeneratorPort
from ast_indexer.ports.observability import ObservabilityPort


class DeterministicReasoningAgent(ReasoningAgentPort):
    def build_objective(self, prompt: str, repos_in_scope: tuple[str, ...]) -> ResearchObjective:
        entities = tuple(token for token in prompt.split() if token.isidentifier())
        return ResearchObjective(intent=prompt, entities=entities, repos_in_scope=repos_in_scope)


class DeterministicQueryProdder(QueryProdderPort):
    def build_queries(self, objective: ResearchObjective) -> tuple[str, ...]:
        queries: list[str] = [objective.intent]
        queries.extend(objective.entities[:4])
        return tuple(dict.fromkeys(item for item in queries if item.strip()))


def _build_embedding_generator(
    backend: Literal['hash', 'sentence-transformers', 'openai'],
    model: str,
    device: str | None,
    normalize_embeddings: bool,
    openai_api_key: str | None,
    openai_base_url: str | None,
    openai_dimensions: int | None,
) -> EmbeddingGeneratorPort:
    if backend == 'sentence-transformers':
        return SentenceTransformersEmbeddingGeneratorAdapter(
            model_name=model,
            device=device,
            normalize_embeddings=normalize_embeddings,
        )
    if backend == 'openai':
        model_name = model
        if model_name == 'sentence-transformers/all-MiniLM-L6-v2':
            model_name = 'text-embedding-3-small'
        return OpenAIEmbeddingGeneratorAdapter(
            model_name=model_name,
            api_key=openai_api_key,
            base_url=openai_base_url,
            dimensions=openai_dimensions,
        )
    return SimpleHashEmbeddingGeneratorAdapter()


def build_persistent_observability_adapter(
    state_root: Path,
    backend: Literal['jsonl', 'langfuse'] = 'jsonl',
    langfuse_host: str | None = None,
    langfuse_public_key: str | None = None,
    langfuse_secret_key: str | None = None,
    strict: bool = False,
) -> ObservabilityPort:
    if backend == 'langfuse':
        host = langfuse_host or os.getenv('LANGFUSE_HOST')
        public_key = langfuse_public_key or os.getenv('LANGFUSE_PUBLIC_KEY')
        secret_key = langfuse_secret_key or os.getenv('LANGFUSE_SECRET_KEY')
        if not host or not public_key or not secret_key:
            raise ValueError(
                'Langfuse backend requires LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, and LANGFUSE_SECRET_KEY'
            )
        return LangfuseObservabilityAdapter(
            host=host,
            public_key=public_key,
            secret_key=secret_key,
            strict=strict,
        )

    return JsonlFileObservabilityAdapter(state_root / 'observability' / 'spans.jsonl')


def build_index_service(
    workspace_root: Path,
    embedding_backend: Literal['hash', 'sentence-transformers', 'openai'] = 'hash',
    embedding_model: str = 'sentence-transformers/all-MiniLM-L6-v2',
    embedding_device: str | None = None,
    normalize_embeddings: bool = True,
    openai_api_key: str | None = None,
    openai_base_url: str | None = None,
    openai_dimensions: int | None = None,
) -> IndexPythonRepositoryService:
    observability = InMemoryObservabilityAdapter()
    reader = LocalFsRepositoryReaderAdapter(workspace_root)
    index_store = InMemorySymbolIndexStoreAdapter()
    vector_store = InMemoryVectorStoreAdapter()
    embedding_generator = _build_embedding_generator(
        backend=embedding_backend,
        model=embedding_model,
        device=embedding_device,
        normalize_embeddings=normalize_embeddings,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        openai_dimensions=openai_dimensions,
    )
    extractor = PythonAstSymbolExtractor()
    return IndexPythonRepositoryService(
        repository_reader=reader,
        index_store=index_store,
        observability=observability,
        extractor=extractor,
        embedding_generator=embedding_generator,
        vector_store=vector_store,
    )


def build_persistent_index_service(
    workspace_root: Path,
    state_root: Path,
    embedding_backend: Literal['hash', 'sentence-transformers', 'openai'] = 'hash',
    embedding_model: str = 'sentence-transformers/all-MiniLM-L6-v2',
    embedding_device: str | None = None,
    normalize_embeddings: bool = True,
    openai_api_key: str | None = None,
    openai_base_url: str | None = None,
    openai_dimensions: int | None = None,
    observability_backend: Literal['jsonl', 'langfuse'] = 'jsonl',
    langfuse_host: str | None = None,
    langfuse_public_key: str | None = None,
    langfuse_secret_key: str | None = None,
    observability_strict: bool = False,
) -> IndexPythonRepositoryService:
    observability = build_persistent_observability_adapter(
        state_root=state_root,
        backend=observability_backend,
        langfuse_host=langfuse_host,
        langfuse_public_key=langfuse_public_key,
        langfuse_secret_key=langfuse_secret_key,
        strict=observability_strict,
    )
    reader = LocalFsRepositoryReaderAdapter(workspace_root)
    index_store = JsonFileSymbolIndexStoreAdapter(state_root / 'index' / 'symbols.json')
    vector_store = JsonFileVectorStoreAdapter(state_root / 'index' / 'vectors.json')
    embedding_generator = _build_embedding_generator(
        backend=embedding_backend,
        model=embedding_model,
        device=embedding_device,
        normalize_embeddings=normalize_embeddings,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        openai_dimensions=openai_dimensions,
    )
    extractor = PythonAstSymbolExtractor()
    return IndexPythonRepositoryService(
        repository_reader=reader,
        index_store=index_store,
        observability=observability,
        extractor=extractor,
        embedding_generator=embedding_generator,
        vector_store=vector_store,
    )


def build_persistent_research_pipeline(
    workspace_root: Path,
    state_root: Path,
    embedding_backend: Literal['hash', 'sentence-transformers', 'openai'] = 'hash',
    embedding_model: str = 'sentence-transformers/all-MiniLM-L6-v2',
    embedding_device: str | None = None,
    normalize_embeddings: bool = True,
    openai_api_key: str | None = None,
    openai_base_url: str | None = None,
    openai_dimensions: int | None = None,
    observability_backend: Literal['jsonl', 'langfuse'] = 'jsonl',
    langfuse_host: str | None = None,
    langfuse_public_key: str | None = None,
    langfuse_secret_key: str | None = None,
    observability_strict: bool = False,
    research_model: str = 'gpt-4o-mini',
    research_latency_mode: Literal['quality', 'fast'] = 'fast',
) -> ResearchPipeline:
    observability = build_persistent_observability_adapter(
        state_root=state_root,
        backend=observability_backend,
        langfuse_host=langfuse_host,
        langfuse_public_key=langfuse_public_key,
        langfuse_secret_key=langfuse_secret_key,
        strict=observability_strict,
    )
    reader = LocalFsRepositoryReaderAdapter(workspace_root)
    index_store = JsonFileSymbolIndexStoreAdapter(state_root / 'index' / 'symbols.json')
    vector_store = JsonFileVectorStoreAdapter(state_root / 'index' / 'vectors.json')
    query_embedding_generator = _build_embedding_generator(
        backend=embedding_backend,
        model=embedding_model,
        device=embedding_device,
        normalize_embeddings=normalize_embeddings,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        openai_dimensions=openai_dimensions,
    )
    extractor = PythonAstSymbolExtractor()
    if research_latency_mode == 'fast':
        reasoning_agent = DeterministicReasoningAgent()
        query_prodder = DeterministicQueryProdder()
        reducer_use_inference = False
    else:
        reasoning_agent = OpenAIReasoningAgent(
            model=research_model,
            api_key=openai_api_key,
            base_url=openai_base_url,
        )
        query_prodder = OpenAIQueryProdder(
            model=research_model,
            api_key=openai_api_key,
            base_url=openai_base_url,
        )
        reducer_use_inference = True

    return ResearchPipeline(
        reasoning_agent=reasoning_agent,
        query_prodder=query_prodder,
        embedding_generator=query_embedding_generator,
        vector_store=vector_store,
        index_store=index_store,
        repository_reader=reader,
        extractor=extractor,
        observability=observability,
        reducer_use_inference=reducer_use_inference,
        reducer_batch_inference=True,
    )
