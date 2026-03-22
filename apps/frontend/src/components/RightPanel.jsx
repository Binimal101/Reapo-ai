export default function RightPanel({ snapshot }) {
  const spans = Array.isArray(snapshot?.spans) ? snapshot.spans : [];
  const events = Array.isArray(snapshot?.events) ? snapshot.events : [];
  return (
    <aside className="panel rightbar" aria-label="Run diagnostics">
      <div className="panel-head">
        <h2>Run Snapshot</h2>
        <span className="mono">{snapshot?.trace_id || "no-trace"}</span>
      </div>

      <section className="metric-grid" aria-label="Trace metrics">
        <div>
          <p className="mono">Trace Depth</p>
          <strong>{snapshot?.max_depth ?? 0}</strong>
        </div>
        <div>
          <p className="mono">Active Stack</p>
          <strong>{snapshot?.active_depth ?? 0}</strong>
        </div>
        <div>
          <p className="mono">Span Count</p>
          <strong>{snapshot?.span_count ?? spans.length}</strong>
        </div>
        <div>
          <p className="mono">Recent Events</p>
          <strong>{events.length}</strong>
        </div>
      </section>

      <section className="logbox" aria-label="Trace event log">
        {events.length === 0 ? <p><span>-</span> no events yet</p> : null}
        {events.slice(-12).map((event) => (
          <p key={`${event.at}-${event.span_id}-${event.kind}`}>
            <span>{event.kind}</span>
            depth {event.depth} · {event.span_id.slice(0, 8)}
          </p>
        ))}
      </section>

      <section className="trace-spans" aria-label="Recent trace spans">
        <div className="panel-head">
          <h2>Span Stack</h2>
          <span className="mono">{spans.length} spans</span>
        </div>
        {spans.slice(-8).map((span) => (
          <article key={span.span_id} className="stage-card">
            <h3>{span.name}</h3>
            <p className="mono">{span.duration_ms == null ? "running" : `${span.duration_ms}ms`}</p>
          </article>
        ))}
      </section>
    </aside>
  );
}
