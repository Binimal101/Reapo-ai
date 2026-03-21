from __future__ import annotations

from typing import Protocol

from ast_indexer.domain.models import SymbolRecord


class IndexStorePort(Protocol):
    def upsert_symbols(self, symbols: list[SymbolRecord]) -> None:
        """Insert or update symbol records."""

    def list_symbols(self) -> list[SymbolRecord]:
        """Return currently stored symbols."""
