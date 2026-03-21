from __future__ import annotations

from pathlib import Path

from ast_indexer.adapters.index_store.in_memory_symbol_index_store_adapter import InMemorySymbolIndexStoreAdapter
from ast_indexer.adapters.index_store.json_file_symbol_index_store_adapter import JsonFileSymbolIndexStoreAdapter
from ast_indexer.adapters.observability.jsonl_file_observability_adapter import JsonlFileObservabilityAdapter
from ast_indexer.adapters.observability.in_memory_observability_adapter import InMemoryObservabilityAdapter
from ast_indexer.adapters.repository.local_fs_repository_reader_adapter import LocalFsRepositoryReaderAdapter
from ast_indexer.application.index_python_repository_service import IndexPythonRepositoryService
from ast_indexer.parsing.cross_file_linker import CrossFileLinker
from ast_indexer.parsing.module_path_resolver import ModulePathResolver
from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor


def build_index_service(workspace_root: Path) -> IndexPythonRepositoryService:
    return IndexPythonRepositoryService(
        repository_reader=LocalFsRepositoryReaderAdapter(workspace_root),
        index_store=InMemorySymbolIndexStoreAdapter(),
        observability=InMemoryObservabilityAdapter(),
        extractor=PythonAstSymbolExtractor(),
        linker=CrossFileLinker(),
        module_resolver=ModulePathResolver(),
    )


def build_persistent_index_service(workspace_root: Path, state_root: Path) -> IndexPythonRepositoryService:
    return IndexPythonRepositoryService(
        repository_reader=LocalFsRepositoryReaderAdapter(workspace_root),
        index_store=JsonFileSymbolIndexStoreAdapter(state_root / 'index' / 'symbols.json'),
        observability=JsonlFileObservabilityAdapter(state_root / 'observability' / 'spans.jsonl'),
        extractor=PythonAstSymbolExtractor(),
        linker=CrossFileLinker(),
        module_resolver=ModulePathResolver(),
    )
