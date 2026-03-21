from __future__ import annotations

import json
from pathlib import Path

from ast_indexer.domain.models import SymbolRecord
from ast_indexer.ports.index_store import IndexStorePort


class JsonFileSymbolIndexStoreAdapter(IndexStorePort):
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._rows: dict[tuple[str, str, str, str], SymbolRecord] = {}
        self._load_existing()

    def upsert_symbols(self, symbols: list[SymbolRecord]) -> None:
        for symbol in symbols:
            key = (symbol.repo, symbol.path, symbol.kind, symbol.symbol)
            self._rows[key] = symbol

        self._persist()

    def list_symbols(self) -> list[SymbolRecord]:
        return list(self._rows.values())

    def _load_existing(self) -> None:
        if not self._file_path.exists():
            return

        data = json.loads(self._file_path.read_text(encoding='utf-8'))
        for row in data:
            symbol = SymbolRecord(
                repo=row['repo'],
                path=row['path'],
                symbol=row['symbol'],
                kind=row['kind'],
                line=row['line'],
                signature=row['signature'],
                docstring=row.get('docstring'),
                callees=tuple(row.get('callees', [])),
                linked_callees=tuple(row.get('linked_callees', [])),
            )
            key = (symbol.repo, symbol.path, symbol.kind, symbol.symbol)
            self._rows[key] = symbol

    def _persist(self) -> None:
        rows = [
            {
                'repo': symbol.repo,
                'path': symbol.path,
                'symbol': symbol.symbol,
                'kind': symbol.kind,
                'line': symbol.line,
                'signature': symbol.signature,
                'docstring': symbol.docstring,
                'callees': list(symbol.callees),
                'linked_callees': list(symbol.linked_callees),
            }
            for symbol in self._rows.values()
        ]
        self._file_path.write_text(json.dumps(rows, indent=2), encoding='utf-8')