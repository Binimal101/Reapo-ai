from __future__ import annotations

import sys
from hashlib import md5
from datetime import datetime, timezone
from uuid import uuid4

from ast_indexer.domain.models import TraceSpan
from ast_indexer.ports.observability import ObservabilityPort


class LangfuseObservabilityAdapter(ObservabilityPort):
    """Observability adapter that mirrors spans to Langfuse and in-memory state."""

    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        strict: bool = False,
        client: object | None = None,
    ) -> None:
        if client is None:
            try:
                from langfuse import Langfuse  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    'Langfuse backend requires the "langfuse" package. '
                    'Install with: pip install "ast-indexer[observability]"'
                ) from exc

            self._client = Langfuse(host=host, public_key=public_key, secret_key=secret_key)
        else:
            self._client = client

        self._strict = strict
        self._spans: list[TraceSpan] = []
        self._live_spans: dict[str, object] = {}
        self._span_trace_ids: dict[str, str] = {}
        self._active_span_stack_by_trace: dict[str, list[str]] = {}
        self._last_error: str | None = None

    def _record_error(self, stage: str, exc: Exception) -> None:
        message = f'langfuse_observability_error[{stage}]: {exc}'
        self._last_error = message
        print(message, file=sys.stderr)
        if self._strict:
            raise RuntimeError(message) from exc

    def start_span(
        self,
        name: str,
        trace_id: str,
        input_payload: dict | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> TraceSpan:
        normalized_trace_id, trace_id_was_normalized = _normalize_trace_id(trace_id)
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
        self._span_trace_ids[span.span_id] = trace_id

        parent_span_id: str | None = None
        trace_stack = self._active_span_stack_by_trace.setdefault(trace_id, [])
        if trace_stack:
            parent_span_id = trace_stack[-1]
            parent_live_span = self._live_spans.get(parent_span_id)
            parent_span_id = _extract_live_observation_id(parent_live_span)

        try:
            trace_context: dict[str, object] = {
                    'trace_id': normalized_trace_id,
                    'session_id': session_id,
                    'user_id': user_id,
            }
            if parent_span_id is not None:
                trace_context['parent_span_id'] = parent_span_id

            observation_kwargs: dict[str, object] = {
                'trace_context': trace_context,
                'name': name,
                'as_type': 'span',
                'input': input_payload,
                'metadata': {
                    'component': 'worker-indexer-py',
                    'original_trace_id': trace_id if trace_id_was_normalized else None,
                },
            }

            live_span = self._client.start_observation(**observation_kwargs)
            self._live_spans[span.span_id] = live_span
            trace_stack.append(span.span_id)
            self._last_error = None
        except Exception as exc:
            self._record_error('start_span', exc)

        return span

    def end_span(self, span: TraceSpan, output_payload: dict | None = None, metadata: dict | None = None) -> None:
        span.finish(output_payload=output_payload, metadata=metadata)

        trace_id = self._span_trace_ids.pop(span.span_id, None)
        if trace_id is not None:
            trace_stack = self._active_span_stack_by_trace.get(trace_id)
            if trace_stack:
                if trace_stack and trace_stack[-1] == span.span_id:
                    trace_stack.pop()
                elif span.span_id in trace_stack:
                    trace_stack.remove(span.span_id)
                if not trace_stack:
                    self._active_span_stack_by_trace.pop(trace_id, None)

        live_span = self._live_spans.pop(span.span_id, None)
        if live_span is None:
            return

        try:
            live_span.update(output=output_payload, metadata=metadata)
            live_span.end()
            if hasattr(self._client, 'flush'):
                self._client.flush()
            self._last_error = None
        except Exception as exc:
            self._record_error('end_span', exc)

    def list_spans(self) -> list[TraceSpan]:
        return list(self._spans)

    def check_health(self) -> bool:
        if self._last_error is not None:
            return False
        try:
            if hasattr(self._client, 'flush'):
                self._client.flush()
            return True
        except Exception as exc:
            self._record_error('check_health', exc)
            return False


def _extract_live_observation_id(live_span: object | None) -> str | None:
    if live_span is None:
        return None
    for attribute in ('id', 'observation_id', 'span_id'):
        candidate = getattr(live_span, attribute, None)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _normalize_trace_id(trace_id: str) -> tuple[str, bool]:
    cleaned = trace_id.replace('-', '').lower()
    if len(cleaned) == 32 and all(char in '0123456789abcdef' for char in cleaned):
        return cleaned, cleaned != trace_id
    # Keep grouping stable for non-hex trace ids by deriving a deterministic 32-hex surrogate.
    return md5(trace_id.encode('utf-8')).hexdigest(), True