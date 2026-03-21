from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from ast_indexer.domain.models import TraceSpan
from ast_indexer.ports.observability import ObservabilityPort


class InMemoryObservabilityAdapter(ObservabilityPort):
    def __init__(self) -> None:
        self._spans: list[TraceSpan] = []

    def start_span(self, name: str, trace_id: str, input_payload: dict | None = None) -> TraceSpan:
        span = TraceSpan(
            name=name,
            trace_id=trace_id,
            span_id=uuid4().hex,
            started_at=datetime.now(timezone.utc),
            input_payload=input_payload,
        )
        self._spans.append(span)
        return span

    def end_span(self, span: TraceSpan, output_payload: dict | None = None, metadata: dict | None = None) -> None:
        span.finish(output_payload=output_payload, metadata=metadata)

    def list_spans(self) -> list[TraceSpan]:
        return list(self._spans)
