from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ast_indexer.domain.models import TraceSpan
from ast_indexer.ports.observability import ObservabilityPort


class JsonlFileObservabilityAdapter(ObservabilityPort):
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._spans: list[TraceSpan] = []

    def start_span(
        self,
        name: str,
        trace_id: str,
        input_payload: dict | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> TraceSpan:
        span = TraceSpan(
            name=name,
            trace_id=trace_id,
            span_id=uuid4().hex,
            session_id=session_id,
            user_id=user_id,
            started_at=datetime.now(timezone.utc),
            input_payload=input_payload,
        )
        self._spans.append(span)
        return span

    def end_span(self, span: TraceSpan, output_payload: dict | None = None, metadata: dict | None = None) -> None:
        span.finish(output_payload=output_payload, metadata=metadata)
        event = {
            'trace_id': span.trace_id,
            'span_id': span.span_id,
            'session_id': span.session_id,
            'user_id': span.user_id,
            'name': span.name,
            'started_at': span.started_at.isoformat(),
            'finished_at': span.finished_at.isoformat() if span.finished_at else None,
            'input_payload': span.input_payload,
            'output_payload': span.output_payload,
            'metadata': span.metadata,
        }
        with self._file_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(event) + '\n')

    def list_spans(self) -> list[TraceSpan]:
        return list(self._spans)