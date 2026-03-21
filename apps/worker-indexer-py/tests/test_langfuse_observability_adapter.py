from __future__ import annotations

from ast_indexer.adapters.observability.langfuse_observability_adapter import LangfuseObservabilityAdapter


class _FakeLiveSpan:
    def __init__(self) -> None:
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
        self.raise_on_start = False

    def start_observation(self, **kwargs: object) -> _FakeLiveSpan:  # noqa: ARG002
        if self.raise_on_start:
            raise RuntimeError('start_observation failed')
        self.start_calls += 1
        self.last_kwargs = kwargs
        self.last_span = _FakeLiveSpan()
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