from __future__ import annotations

from ast_indexer.domain.models import SymbolRecord
from ast_indexer.ports.index_store import IndexStorePort


class InMemorySymbolIndexStoreAdapter(IndexStorePort):
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str, str], SymbolRecord] = {}

    def upsert_symbols(self, symbols: list[SymbolRecord]) -> None:
        for symbol in symbols:
            key = (symbol.repo, symbol.path, symbol.kind, symbol.symbol)
            self._rows[key] = symbol

    def delete_symbols_for_paths(self, repo: str, paths: list[str]) -> int:
        if not paths:
            return 0

        path_set = set(paths)
        before = len(self._rows)
        self._rows = {
            key: value
            for key, value in self._rows.items()
            if not (value.repo == repo and value.path in path_set)
        }
        return before - len(self._rows)

    def list_symbols(self) -> list[SymbolRecord]:
        return list(self._rows.values())
