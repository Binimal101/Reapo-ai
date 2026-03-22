export default function CenterPanel({
  messages,
  prompt,
  onPromptChange,
  onSend,
  sending,
  error,
  gitDiff,
}) {
  const hasMessages = Array.isArray(messages) && messages.length > 0;
  return (
    <section className="panel center" aria-label="Main workspace">
      <div className="hero">
        <p className="hero-kicker">Project Workspace</p>
        <h1>Ask, iterate, and review changes from one thread.</h1>
        <p>Conversation history stays visible while generated code impact is summarized as a git-style diff.</p>
      </div>

      <section className="chat-history" aria-label="Chat history">
        <div className="panel-head">
          <h2>Chat History</h2>
          <span className="mono">{hasMessages ? `${messages.length} messages` : "new session"}</span>
        </div>
        {!hasMessages ? <p className="mono">Send a message to start this session.</p> : null}
        {hasMessages
          ? messages.map((message, index) => (
              <article key={`${message.timestamp || index}-${message.role}`} className={`chat-msg ${message.role}`}>
                <p className="chat-role mono">{message.role}</p>
                <p>{message.content}</p>
              </article>
            ))
          : null}
      </section>

      <div className="composer">
        <label htmlFor="prompt" className="mono">
          prompt
        </label>
        <textarea
          id="prompt"
          value={prompt}
          onChange={(event) => onPromptChange(event.target.value)}
          aria-label="Project prompt"
          placeholder="Ask the orchestrator to inspect code, propose changes, or draft a PR summary"
        />
        <button type="button" onClick={onSend} disabled={sending || !prompt.trim()}>
          {sending ? "Sending..." : "Send"}
        </button>
        {error ? <p className="error-text">{error}</p> : null}
      </div>

      <section className="git-diff" aria-label="Git diff preview">
        <div className="panel-head">
          <h2>Git Diff</h2>
          <span className="mono">latest run</span>
        </div>
        <pre>{gitDiff || "No diff available yet."}</pre>
      </section>
    </section>
  );
}
