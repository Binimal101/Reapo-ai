export default function TopBar({ activeProjectName, onLogout }) {
  return (
    <header className="topbar">
      <div className="brand-block">
        <div className="brand-mark" aria-hidden="true">
          R
        </div>
        <div>
          <p className="brand-title">Reapo.ai</p>
          <p className="brand-subtitle">Project Workspace</p>
        </div>
      </div>

      <nav className="topbar-nav" aria-label="Primary navigation">
        <span className="chip">Project: {activeProjectName || "none selected"}</span>
        <span className="chip">Live Session</span>
        <span className="chip">Trace Aware</span>
        <button type="button" className="cta secondary" onClick={onLogout}>
          Logout
        </button>
      </nav>
    </header>
  );
}
