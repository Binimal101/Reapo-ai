from __future__ import annotations

import json
from pathlib import Path

from ast_indexer.domain.models import VectorRecord
from ast_indexer.ports.vector_store import VectorStorePort


class JsonFileVectorStoreAdapter(VectorStorePort):
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._rows: dict[tuple[str, str, str, str], VectorRecord] = {}
        self._load_existing()

    def upsert_vectors(self, vectors: list[VectorRecord]) -> None:
        for vector in vectors:
            key = (vector.repo, vector.path, vector.kind, vector.symbol)
            self._rows[key] = vector
        self._persist()

    def delete_vectors_for_paths(self, repo: str, paths: list[str]) -> int:
        if not paths:
            return 0

        path_set = set(paths)
        before = len(self._rows)
        self._rows = {
            key: value
            for key, value in self._rows.items()
            if not (value.repo == repo and value.path in path_set)
        }
        removed = before - len(self._rows)
        if removed:
            self._persist()
        return removed

    def list_vectors(self) -> list[VectorRecord]:
        return list(self._rows.values())

    def _load_existing(self) -> None:
        if not self._file_path.exists():
            return

        data = json.loads(self._file_path.read_text(encoding='utf-8'))
        for row in data:
            vector = VectorRecord(
                repo=row['repo'],
                path=row['path'],
                symbol=row['symbol'],
                kind=row['kind'],
                signature=row['signature'],
                docstring=row.get('docstring'),
                embedding=tuple(row['embedding']),
                tree_sha=row['tree_sha'],
                blob_sha=row['blob_sha'],
                access_level=row['access_level'],
            )
            key = (vector.repo, vector.path, vector.kind, vector.symbol)
            self._rows[key] = vector

    def _persist(self) -> None:
        rows = [
            {
                'repo': vector.repo,
                'path': vector.path,
                'symbol': vector.symbol,
                'kind': vector.kind,
                'signature': vector.signature,
                'docstring': vector.docstring,
                'embedding': list(vector.embedding),
                'tree_sha': vector.tree_sha,
                'blob_sha': vector.blob_sha,
                'access_level': vector.access_level,
            }
            for vector in self._rows.values()
        ]
        self._file_path.write_text(json.dumps(rows, indent=2), encoding='utf-8')