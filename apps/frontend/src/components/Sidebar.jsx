export default function Sidebar({ repositories }) {
  return (
    <aside className="panel sidebar">
      <div className="panel-head">
        <h2>Linked Repositories</h2>
        <span className="mono">{repositories.length} total</span>
      </div>

      <ul className="repo-list">
        {repositories.map((repo) => (
          <li key={repo.repository_id || repo.full_name} className="repo-item">
            <div>
              <p className="repo-name">{repo.full_name || `${repo.owner}/${repo.name}`}</p>
              <p className="repo-meta">{repo.visibility || "unknown"}</p>
            </div>
            <span className="repo-badge write">linked</span>
          </li>
        ))}
        {!repositories.length ? (
          <li className="repo-item">
            <p className="repo-meta">No repositories linked to this project yet.</p>
          </li>
        ) : null}
      </ul>
    </aside>
  );
}
