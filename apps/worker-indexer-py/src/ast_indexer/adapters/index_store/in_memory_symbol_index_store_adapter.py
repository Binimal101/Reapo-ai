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

    def list_symbols(self) -> list[SymbolRecord]:
        return list(self._rows.values())
