from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class SymbolRecord:
    repo: str
    path: str
    symbol: str
    kind: str
    line: int
    signature: str
    callees: tuple[str, ...] = ()


@dataclass(frozen=True)
class IndexRunMetrics:
    files_scanned: int
    symbols_indexed: int
    started_at: datetime
    finished_at: datetime


@dataclass
class TraceSpan:
    name: str
    trace_id: str
    span_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    input_payload: dict | None = None
    output_payload: dict | None = None
    metadata: dict | None = None

    def finish(self, output_payload: dict | None = None, metadata: dict | None = None) -> None:
        self.finished_at = datetime.now(timezone.utc)
        self.output_payload = output_payload
        self.metadata = metadata
