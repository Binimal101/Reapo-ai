from __future__ import annotations

from typing import Protocol

from ast_indexer.domain.models import TraceSpan


class ObservabilityPort(Protocol):
    def start_span(
        self,
        name: str,
        trace_id: str,
        input_payload: dict | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> TraceSpan:
        """Start and register a span."""

    def end_span(self, span: TraceSpan, output_payload: dict | None = None, metadata: dict | None = None) -> None:
        """Finish and persist span details."""

    def list_spans(self) -> list[TraceSpan]:
        """Return all captured spans in insertion order."""
