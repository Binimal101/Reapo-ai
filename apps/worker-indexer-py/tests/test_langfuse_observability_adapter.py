from __future__ import annotations

from ast_indexer.adapters.observability.langfuse_observability_adapter import (
    LangfuseObservabilityAdapter,
    _normalize_trace_id,
)


class _FakeLiveSpan:
    def __init__(self, observation_id: str) -> None:
        self.observation_id = observation_id
        self.ended = False
        self.output: dict | None = None
        self.metadata: dict | None = None

    def update(self, output: dict | None = None, metadata: dict | None = None) -> None:
        self.output = output
        self.metadata = metadata

    def end(self) -> None:
        self.ended = True


class _FakeClient:
    def __init__(self) -> None:
        self.last_span: _FakeLiveSpan | None = None
        self.start_calls = 0
        self.flush_calls = 0
        self.last_kwargs: dict[str, object] | None = None
        self.start_kwargs: list[dict[str, object]] = []
        self.raise_on_start = False

    def start_observation(self, **kwargs: object) -> _FakeLiveSpan:  # noqa: ARG002
        if self.raise_on_start:
            raise RuntimeError('start_observation failed')
        self.start_calls += 1
        self.last_kwargs = kwargs
        self.start_kwargs.append(dict(kwargs))
        self.last_span = _FakeLiveSpan(observation_id=f'obs-{self.start_calls}')
        return self.last_span

    def flush(self) -> None:
        self.flush_calls += 1


def test_langfuse_adapter_creates_and_finishes_remote_span() -> None:
    client = _FakeClient()
    adapter = LangfuseObservabilityAdapter(
        host='http://localhost:3000',
        public_key='pk',
        secret_key='sk',
        client=client,
    )

    span = adapter.start_span(
        'index_repository',
        'trace-1',
        {'repo': 'checkout-service'},
        session_id='corr-1',
        user_id='octocat',
    )
    adapter.end_span(span, output_payload={'files_scanned': 2}, metadata={'duration_ms': 5})

    assert client.start_calls == 1
    assert client.flush_calls >= 1
    assert client.last_span is not None
    assert client.last_span.ended is True
    assert client.last_span.output == {'files_scanned': 2}
    assert client.last_kwargs is not None
    trace_context = client.last_kwargs['trace_context']
    assert trace_context['session_id'] == 'corr-1'
    assert trace_context['user_id'] == 'octocat'
    assert isinstance(trace_context['trace_id'], str)
    assert len(trace_context['trace_id']) == 32
    assert client.last_kwargs['metadata']['original_trace_id'] == 'trace-1'


def test_langfuse_adapter_health_check_uses_flush() -> None:
    client = _FakeClient()
    adapter = LangfuseObservabilityAdapter(
        host='http://localhost:3000',
        public_key='pk',
        secret_key='sk',
        client=client,
    )

    assert adapter.check_health() is True
    assert client.flush_calls == 1


def test_langfuse_adapter_links_child_spans_to_parent_observation() -> None:
    client = _FakeClient()
    adapter = LangfuseObservabilityAdapter(
        host='http://localhost:3000',
        public_key='pk',
        secret_key='sk',
        client=client,
    )

    parent = adapter.start_span('research_pipeline_run', 'trace-depth-1', {'prompt': 'x'})
    child = adapter.start_span('reasoning_agent', 'trace-depth-1', {'prompt': 'x'})
    adapter.end_span(child, output_payload={'ok': True})
    adapter.end_span(parent, output_payload={'ok': True})

    assert len(client.start_kwargs) == 2
    assert 'parent_span_id' not in client.start_kwargs[0]['trace_context']
    assert client.start_kwargs[1]['trace_context']['parent_span_id'] == 'obs-1'


def test_langfuse_adapter_strict_mode_raises_on_start_failure() -> None:
    client = _FakeClient()
    client.raise_on_start = True
    adapter = LangfuseObservabilityAdapter(
        host='http://localhost:3000',
        public_key='pk',
        secret_key='sk',
        strict=True,
        client=client,
    )

    try:
        adapter.start_span('index_repository', 'trace-2', {'repo': 'checkout-service'})
        assert False, 'Expected RuntimeError'
    except RuntimeError as exc:
        assert 'langfuse_observability_error[start_span]' in str(exc)


def test_normalize_trace_id_is_deterministic_for_non_hex_inputs() -> None:
    left, left_was_normalized = _normalize_trace_id('lf-full-research-20aa4455a1')
    right, right_was_normalized = _normalize_trace_id('lf-full-research-20aa4455a1')

    assert left_was_normalized is True
    assert right_was_normalized is True
    assert left == right
    assert len(left) == 32