from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class JsonFileRepoCapabilityStoreAdapter:
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._rows: dict[str, dict] = {}
        self._load()

    def upsert(self, owner: str, repo: str, installation_id: int, permissions: dict, repository_selection: str | None) -> None:
        key = f'{owner}/{repo}'.lower()
        self._rows[key] = {
            'owner': owner,
            'repo': repo,
            'installation_id': installation_id,
            'permissions': permissions,
            'repository_selection': repository_selection,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        self._persist()

    def get(self, owner: str, repo: str) -> dict | None:
        return self._rows.get(f'{owner}/{repo}'.lower())

    def _load(self) -> None:
        if not self._file_path.exists():
            return
        raw = json.loads(self._file_path.read_text(encoding='utf-8'))
        if isinstance(raw, dict):
            self._rows = raw

    def _persist(self) -> None:
        self._file_path.write_text(json.dumps(self._rows, indent=2), encoding='utf-8')
