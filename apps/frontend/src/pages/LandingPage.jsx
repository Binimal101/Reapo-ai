export default function LandingPage({ hasSession, onNavigate }) {

  return (
    <section className="landing" aria-label="Reapo landing page">
      <div className="landing-card landing-showcase">
        <div className="landing-copy">
          <p className="hero-kicker">Agent Orchestration Platform</p>
          <h1>One command center for your entire engineering surface.</h1>
          <p>
            Reapo orchestrates coding agents across your entire microservices system, reducing the need to hop
            from one IDE or tab to another while your work keeps moving.
          </p>

          <div className="landing-actions">
            <button
              type="button"
              className="cta primary"
              onClick={() => onNavigate(hasSession ? "/projects" : "/signup")}
            >
              {hasSession ? "Open Projects" : "Start For Free"}
            </button>
            <button
              type="button"
              className="cta secondary"
              onClick={() => onNavigate(hasSession ? "/projects" : "/signin")}
            >
              {hasSession ? "Go To Workspace" : "Sign In"}
            </button>
          </div>

          <ul className="landing-points">
            <li>Coordinate repo-aware agents from one place.</li>
            <li>Move from idea to implementation without context switching overload.</li>
            <li>Stay focused while Reapo handles multi-service execution flow.</li>
          </ul>
        </div>

        <div className="landing-visual" aria-hidden="true">
          <div className="orbit-ring ring-a" />
          <div className="orbit-ring ring-b" />
          <div className="orbit-ring ring-c" />
          <div className="cube-3d">
            <span className="face front" />
            <span className="face back" />
            <span className="face right" />
            <span className="face left" />
            <span className="face top" />
            <span className="face bottom" />
          </div>
        </div>
      </div>
    </section>
  );
}
