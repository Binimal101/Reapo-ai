export default function AppNavbar({
  route,
  hasSession,
  onNavigate,
  onLogout,
  subtitle = "GitHub-native coding operations",
  contextChips = [],
}) {
  const onProtectedNav = (path) => {
    if (hasSession) {
      onNavigate(path);
      return;
    }
    onNavigate("/signin");
  };

  return (
    <header className="app-nav" aria-label="Application navigation">
      <div className="brand-block">
        <div className="brand-mark" aria-hidden="true">
          R
        </div>
        <div>
          <p className="brand-title">Reapo.ai</p>
          <p className="brand-subtitle">{subtitle}</p>
        </div>
      </div>

      {contextChips.length > 0 ? (
        <div className="app-nav-meta" aria-label="Workspace context">
          {contextChips.map((label) => (
            <span className="chip" key={label}>{label}</span>
          ))}
        </div>
      ) : null}

      <nav className="app-nav-links" aria-label="Primary">
        <button
          type="button"
          className={`nav-link ${route === "landing" ? "active" : ""}`}
          onClick={() => onNavigate("/")}
        >
          Landing
        </button>
        <button
          type="button"
          className={`nav-link ${(route === "projects" || route === "project") ? "active" : ""}`}
          onClick={() => onProtectedNav("/projects")}
        >
          Projects
        </button>
        <button
          type="button"
          className={`nav-link ${route === "signup" ? "active" : ""}`}
          onClick={() => {
            if (hasSession) {
              onNavigate("/projects");
              return;
            }
            onNavigate("/signup");
          }}
        >
          Signup
        </button>
      </nav>

      <div className="app-nav-actions">
        {route === "project" ? (
          <button type="button" className="cta secondary" onClick={() => onNavigate("/projects")}>
            Back To Projects
          </button>
        ) : null}
        {!hasSession ? (
          <button type="button" className="cta primary" onClick={() => onNavigate("/signup")}>
            Get Started
          </button>
        ) : (
          <button type="button" className="cta secondary" onClick={onLogout}>
            Logout
          </button>
        )}
      </div>
    </header>
  );
}
