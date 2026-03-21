from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


class JsonFileWebhookReplayGuardAdapter:
    def __init__(self, file_path: Path, retention_hours: int = 24) -> None:
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._retention = timedelta(hours=retention_hours)
        self._rows: dict[str, str] = {}
        self._load()

    def seen_before_then_mark(self, delivery_id: str) -> bool:
        self._prune()
        if delivery_id in self._rows:
            return True
        self._rows[delivery_id] = datetime.now(timezone.utc).isoformat()
        self._persist()
        return False

    def _load(self) -> None:
        if not self._file_path.exists():
            return
        raw = json.loads(self._file_path.read_text(encoding='utf-8'))
        if isinstance(raw, dict):
            self._rows = {k: v for k, v in raw.items() if isinstance(v, str)}

    def _persist(self) -> None:
        self._file_path.write_text(json.dumps(self._rows, indent=2), encoding='utf-8')

    def _prune(self) -> None:
        now = datetime.now(timezone.utc)
        keep: dict[str, str] = {}
        for key, value in self._rows.items():
            try:
                seen_at = datetime.fromisoformat(value)
            except ValueError:
                continue
            if now - seen_at <= self._retention:
                keep[key] = value
        if keep != self._rows:
            self._rows = keep
            self._persist()
