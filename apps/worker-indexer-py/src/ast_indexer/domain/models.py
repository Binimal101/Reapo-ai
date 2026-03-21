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
    docstring: str | None = None
    callees: tuple[str, ...] = ()
    linked_callees: tuple[str, ...] = ()

    @property
    def embedding_input(self) -> str:
        """
        Canonical text fed to the embedding model (Section 3.2 standard).
        Format: signature, then docstring on the next line if present.
        Body is intentionally excluded — the index is a navigation structure only.
        """
        parts = [self.signature]
        if self.docstring:
            parts.append(self.docstring)
        return '\n'.join(parts)


@dataclass(frozen=True)
class EmbeddingRecord:
    repo: str
    path: str
    symbol: str
    kind: str
    embedding_input: str
    vector: tuple[float, ...]
    model: str
    dimensions: int


@dataclass(frozen=True)
class VectorRecord:
    repo: str
    path: str
    symbol: str
    kind: str
    signature: str
    docstring: str | None
    embedding: tuple[float, ...]
    tree_sha: str
    blob_sha: str
    access_level: str


@dataclass(frozen=True)
class CallEdge:
    repo: str
    caller_path: str
    caller_symbol: str
    callee: str
    resolved_path: str
    resolved_symbol: str
    resolved_canonical: str


@dataclass(frozen=True)
class UnresolvedCallEdge:
    repo: str
    caller_path: str
    caller_symbol: str
    callee: str
    reason: str
    actionable: bool


@dataclass(frozen=True)
class IndexRunMetrics:
    files_scanned: int
    symbols_indexed: int
    linked_edges: int
    embeddings_generated: int
    started_at: datetime
    finished_at: datetime
    vectors_upserted: int = 0
    vectors_deleted: int = 0


@dataclass
class TraceSpan:
    name: str
    trace_id: str
    span_id: str
    session_id: str | None = None
    user_id: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    input_payload: dict | None = None
    output_payload: dict | None = None
    metadata: dict | None = None

    def finish(self, output_payload: dict | None = None, metadata: dict | None = None) -> None:
        self.finished_at = datetime.now(timezone.utc)
        self.output_payload = output_payload
        self.metadata = metadata
