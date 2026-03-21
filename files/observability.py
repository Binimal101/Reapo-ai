"""
observability.py — Langfuse instrumentation helpers (Section 3.8).

Provides a thin wrapper around the Langfuse SDK that every component uses
to emit structured spans at handoff boundaries.  The three-line pattern
from the design doc:

    span = tracer.span("component_name", parent, input={...})
    # ... work ...
    span.end(output={...}, usage={...}, metadata={...})

Also provides the score attachment and alert-condition helpers.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Span + Trace data types
# ---------------------------------------------------------------------------

@dataclass
class Span:
    """Matches the Langfuse Span schema from Section 6."""
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_time: float
    end_time: float | None = None
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    scores: list[dict[str, Any]] = field(default_factory=list)

    @property
    def latency_ms(self) -> int | None:
        if self.end_time is not None:
            return int((self.end_time - self.start_time) * 1000)
        return None

    def end(
        self,
        output: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.end_time = time.time()
        if output:
            self.output = output
        if usage:
            self.usage = usage
        if metadata:
            self.metadata.update(metadata)
        self.metadata["latency_ms"] = self.latency_ms


@dataclass
class Trace:
    """A pipeline run trace — contains all spans."""
    trace_id: str
    user_id: str | None = None
    prompt_preview: str = ""
    spans: list[Span] = field(default_factory=list)
    scores: list[dict[str, Any]] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None

    def end(self, metadata: dict[str, Any] | None = None) -> None:
        self.end_time = time.time()


# ---------------------------------------------------------------------------
# Tracer — the object components interact with
# ---------------------------------------------------------------------------

class Tracer:
    """
    Lightweight Langfuse-compatible tracer.

    In production, this wraps the real `langfuse.Langfuse` client.
    Here it stores everything in memory so tests can inspect spans
    and the system works without a Langfuse deployment.
    """

    def __init__(self) -> None:
        self._traces: dict[str, Trace] = {}
        self._current_trace_id: str | None = None

    # ── Trace lifecycle ──────────────────────────────────────────────

    def start_trace(
        self,
        *,
        user_id: str | None = None,
        prompt_preview: str = "",
        trace_id: str | None = None,
    ) -> Trace:
        tid = trace_id or f"tr_{uuid.uuid4().hex[:12]}"
        trace = Trace(trace_id=tid, user_id=user_id, prompt_preview=prompt_preview)
        self._traces[tid] = trace
        self._current_trace_id = tid
        logger.debug("Trace started: %s", tid)
        return trace

    def end_trace(self, trace_id: str, metadata: dict[str, Any] | None = None) -> None:
        trace = self._traces.get(trace_id)
        if trace:
            trace.end(metadata)

    # ── Span lifecycle ───────────────────────────────────────────────

    def span(
        self,
        name: str,
        *,
        parent_span_id: str | None = None,
        trace_id: str | None = None,
        input: dict[str, Any] | None = None,
    ) -> Span:
        """Open a span.  Returns the Span object — caller must call .end()."""
        tid = trace_id or self._current_trace_id
        if tid is None:
            raise RuntimeError("No active trace — call start_trace() first")

        span = Span(
            trace_id=tid,
            span_id=f"sp_{uuid.uuid4().hex[:12]}",
            parent_span_id=parent_span_id,
            name=name,
            start_time=time.time(),
            input=input or {},
        )

        trace = self._traces.get(tid)
        if trace:
            trace.spans.append(span)

        return span

    # ── Score attachment ─────────────────────────────────────────────

    def score(
        self,
        *,
        span_id: str,
        name: str,
        value: float,
        trace_id: str | None = None,
    ) -> None:
        """Attach a named score to a span (e.g. relevancy_confidence)."""
        tid = trace_id or self._current_trace_id
        score_rec = {"span_id": span_id, "name": name, "value": value}

        # Attach to the span
        trace = self._traces.get(tid) if tid else None
        if trace:
            for sp in trace.spans:
                if sp.span_id == span_id:
                    sp.scores.append(score_rec)
                    break
            trace.scores.append(score_rec)

    # ── Query methods (match Langfuse MCP server tools) ──────────────

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """langfuse_mcp.get_trace(trace_id) → full span tree."""
        trace = self._traces.get(trace_id)
        if not trace:
            return None
        return {
            "trace_id": trace.trace_id,
            "user_id": trace.user_id,
            "prompt_preview": trace.prompt_preview,
            "span_count": len(trace.spans),
            "spans": [_span_to_dict(s) for s in trace.spans],
            "scores": trace.scores,
        }

    def get_spans(
        self,
        *,
        trace_id: str | None = None,
        name: str | None = None,
        min_score: float | None = None,
    ) -> list[dict[str, Any]]:
        """langfuse_mcp.get_spans(filter) → filtered span list."""
        results = []
        traces = [self._traces[trace_id]] if trace_id and trace_id in self._traces else self._traces.values()
        for trace in traces:
            for sp in trace.spans:
                if name and sp.name != name:
                    continue
                if min_score is not None:
                    max_score = max((s["value"] for s in sp.scores), default=0.0)
                    if max_score < min_score:
                        continue
                results.append(_span_to_dict(sp))
        return results

    def get_scores(self, trace_id: str) -> list[dict[str, Any]]:
        """langfuse_mcp.get_scores(run_id) → all scores for a run."""
        trace = self._traces.get(trace_id)
        return trace.scores if trace else []

    # ── Alert checks ─────────────────────────────────────────────────

    def check_alerts(self, trace_id: str) -> list[dict[str, Any]]:
        """
        Evaluate the two default alert rules (Section 3.8):
          - low_relevancy_confidence: mean relevancy_confidence < 0.55
          - reducer_tier_overrun: tiers_completed > 3
        """
        alerts: list[dict[str, Any]] = []
        trace = self._traces.get(trace_id)
        if not trace:
            return alerts

        # Alert 1: low relevancy confidence
        relevancy_scores = [
            s["value"] for s in trace.scores
            if s.get("name") == "relevancy_confidence"
        ]
        if relevancy_scores:
            mean_conf = sum(relevancy_scores) / len(relevancy_scores)
            if mean_conf < 0.55:
                alerts.append({
                    "alert": "low_relevancy_confidence",
                    "mean_confidence": round(mean_conf, 3),
                    "agent_count": len(relevancy_scores),
                    "message": "Semantic prodder generated poor query angles; reformulation needed",
                })

        # Alert 2: reducer tier overrun
        reducer_spans = [
            sp for sp in trace.spans
            if sp.name == "reducer_system"
        ]
        for sp in reducer_spans:
            tiers = sp.output.get("tiers_completed", 0)
            if tiers > 3:
                alerts.append({
                    "alert": "reducer_tier_overrun",
                    "tiers_completed": tiers,
                    "message": "Too many chunks passing relevancy filter; threshold or top-K needs tuning",
                })

        return alerts


def _span_to_dict(sp: Span) -> dict[str, Any]:
    return {
        "trace_id": sp.trace_id,
        "span_id": sp.span_id,
        "parent_span_id": sp.parent_span_id,
        "name": sp.name,
        "input": sp.input,
        "output": sp.output,
        "usage": sp.usage,
        "metadata": sp.metadata,
        "scores": sp.scores,
        "latency_ms": sp.latency_ms,
    }


# ---------------------------------------------------------------------------
# Module-level singleton (convenience — components import this)
# ---------------------------------------------------------------------------

_default_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    global _default_tracer
    if _default_tracer is None:
        _default_tracer = Tracer()
    return _default_tracer


def set_tracer(tracer: Tracer) -> None:
    global _default_tracer
    _default_tracer = tracer
