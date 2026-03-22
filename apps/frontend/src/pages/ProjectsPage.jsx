import { useEffect, useMemo, useState } from "react";

export default function ProjectsPage({
  projects,
  repositories,
  onCreateProject,
  onUpdateProject,
  onDeleteProject,
  onListProjectRepositories,
  onAddRepository,
  onRemoveRepository,
  loadingProjects,
  loadingRepositories,
  onOpenDashboard,
}) {
  const [mode, setMode] = useState("create");
  const [editingProjectId, setEditingProjectId] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [repositorySearch, setRepositorySearch] = useState("");
  const [selectedRepositoryNames, setSelectedRepositoryNames] = useState(() => new Set());
  const [currentLinkedRepositories, setCurrentLinkedRepositories] = useState([]);
  const [saving, setSaving] = useState(false);
  const [deletingProjectId, setDeletingProjectId] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  const ownerProjects = useMemo(
    () => projects.filter((project) => project.role === "owner"),
    [projects]
  );

  const activeProject = useMemo(() => {
    if (!editingProjectId) {
      return null;
    }
    return ownerProjects.find((project) => project.project_id === editingProjectId) || null;
  }, [editingProjectId, ownerProjects]);

  const resetFormForCreate = () => {
    setMode("create");
    setEditingProjectId("");
    setProjectName("");
    setProjectDescription("");
    setSelectedRepositoryNames(new Set());
    setCurrentLinkedRepositories([]);
    setError("");
  };

  const loadProjectForEdit = async (project) => {
    setMode("edit");
    setEditingProjectId(project.project_id);
    setProjectName(project.name || "");
    setProjectDescription(project.description || "");
    setError("");
    try {
      const linked = await onListProjectRepositories(project.project_id);
      setCurrentLinkedRepositories(linked);
      setSelectedRepositoryNames(new Set(linked.map((repo) => repo.full_name)));
    } catch (err) {
      setCurrentLinkedRepositories([]);
      setSelectedRepositoryNames(new Set());
      setError(err instanceof Error ? err.message : "Unable to load linked repositories");
    }
  };

  const handleSave = async () => {
    setError("");
    if (!projectName.trim()) {
      setError("Project name is required.");
      return;
    }
    setSaving(true);
    try {
      const selectedList = repositories.filter((repo) => selectedRepositoryNames.has(repo.full_name));
      if (mode === "create") {
        const created = await onCreateProject(projectName.trim(), projectDescription.trim());
        for (const repo of selectedList) {
          await onAddRepository(created.project_id, repo);
        }
        await loadProjectForEdit(created);
        return;
      }

      if (!editingProjectId) {
        throw new Error("No project selected for edit");
      }
      await onUpdateProject(editingProjectId, projectName.trim(), projectDescription.trim());

      const linkedByFullName = new Map(currentLinkedRepositories.map((repo) => [repo.full_name, repo]));
      const targetSet = new Set(selectedRepositoryNames);

      for (const linked of currentLinkedRepositories) {
        if (!targetSet.has(linked.full_name)) {
          await onRemoveRepository(editingProjectId, linked.repository_id);
        }
      }

      for (const repo of selectedList) {
        if (!linkedByFullName.has(repo.full_name)) {
          await onAddRepository(editingProjectId, repo);
        }
      }

      const refreshedLinked = await onListProjectRepositories(editingProjectId);
      setCurrentLinkedRepositories(refreshedLinked);
      setSelectedRepositoryNames(new Set(refreshedLinked.map((repo) => repo.full_name)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save project");
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteProject = async (projectId) => {
    setError("");
    setDeletingProjectId(projectId);
    try {
      await onDeleteProject(projectId);
      if (editingProjectId === projectId) {
        resetFormForCreate();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to delete project");
    } finally {
      setDeletingProjectId("");
    }
  };

  const filteredRepositoryOptions = useMemo(() => {
    const query = repositorySearch.trim().toLowerCase();
    if (!query) {
      return repositories;
    }
    return repositories.filter((repo) => {
      const fullName = typeof repo.full_name === "string" ? repo.full_name.toLowerCase() : "";
      const fallback = `${repo.owner || ""}/${repo.name || ""}`.toLowerCase();
      return fullName.includes(query) || fallback.includes(query);
    });
  }, [repositories, repositorySearch]);

  const linkedCount = useMemo(
    () => Array.from(selectedRepositoryNames).length,
    [selectedRepositoryNames]
  );

  const toggleRepository = (fullName) => {
    setSelectedRepositoryNames((current) => {
      const next = new Set(current);
      if (next.has(fullName)) {
        next.delete(fullName);
      } else {
        next.add(fullName);
      }
      return next;
    });
  };

  return (
    <section className="projects-shell" aria-label="Tenant project management">
      <header className="projects-header panel">
        <div>
          <p className="hero-kicker">Tenant Projects</p>
          <h1>Create, edit, and open your projects</h1>
        </div>
        <div className="projects-header-actions">
          {activeProject ? (
            <button
              type="button"
              className="cta secondary"
              onClick={() => onOpenDashboard(activeProject.project_id)}
            >
              Open Workspace
            </button>
          ) : null}
          <button type="button" className="cta primary" onClick={resetFormForCreate}>
            New Project
          </button>
        </div>
      </header>

      <div className="projects-grid">
        <article className="panel projects-list-panel">
          <div className="panel-head">
            <h2>Existing Projects</h2>
            <span className="mono">owners only</span>
          </div>

          <div className="project-list">
            {ownerProjects.length === 0 ? (
              <p className="mono">No projects yet.</p>
            ) : (
              ownerProjects.map((project) => (
                <div
                  key={project.project_id}
                  className={`project-row-wrap ${project.project_id === editingProjectId ? "active" : ""}`}
                >
                  <button
                    type="button"
                    className="project-row"
                    onClick={() => loadProjectForEdit(project)}
                  >
                    <strong>{project.name}</strong>
                    <span className="mono">{project.role}</span>
                  </button>
                  <div className="project-row-actions">
                    <button
                      type="button"
                      className="cta secondary"
                      onClick={() => onOpenDashboard(project.project_id)}
                    >
                      Open
                    </button>
                    <button
                      type="button"
                      className="cta secondary"
                      onClick={() => loadProjectForEdit(project)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="cta secondary"
                      disabled={deletingProjectId === project.project_id}
                      onClick={() => handleDeleteProject(project.project_id)}
                    >
                      {deletingProjectId === project.project_id ? "Deleting..." : "Delete"}
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </article>

        <article className="panel">
          <div className="panel-head">
            <h2>{mode === "create" ? "Create Project" : "Edit Project"}</h2>
            <span className="mono">{linkedCount} repos selected</span>
          </div>
          <div className="project-form">
            <input
              value={projectName}
              onChange={(event) => setProjectName(event.target.value)}
              placeholder="Project name"
              aria-label="Project name"
            />
            <textarea
              value={projectDescription}
              onChange={(event) => setProjectDescription(event.target.value)}
              placeholder="Description (optional)"
              aria-label="Project description"
            />
            <button type="button" className="cta primary" onClick={handleSave} disabled={loadingProjects || saving}>
              {saving ? "Saving..." : mode === "create" ? "Create Project" : "Save Changes"}
            </button>
          </div>

          <div className="panel-head repo-selection-head">
            <h2>Repository Selection</h2>
            <span className="mono">OAuth /user/repos</span>
          </div>
          <div className="repo-search-wrap">
            <input
              value={repositorySearch}
              onChange={(event) => setRepositorySearch(event.target.value)}
              placeholder="Search repositories"
              aria-label="Search repositories"
              className="repo-search-input"
            />
          </div>
          {loadingRepositories ? <p className="mono">Loading repositories...</p> : null}
          <div className="repo-catalog">
            {filteredRepositoryOptions.map((repo) => {
              const isSelected = selectedRepositoryNames.has(repo.full_name);
              return (
                <button
                  type="button"
                  key={repo.full_name}
                  className={`repo-catalog-row repo-option ${isSelected ? "selected" : ""}`}
                  onClick={() => toggleRepository(repo.full_name)}
                  aria-pressed={isSelected}
                  aria-label={`Select ${repo.full_name}`}
                >
                  <div>
                    <p className="repo-name">{repo.full_name}</p>
                    <p className="repo-meta">
                      {repo.private ? "private" : "public"}
                      {repo.default_branch ? ` · ${repo.default_branch}` : ""}
                    </p>
                  </div>
                  <span className="repo-select-state">{isSelected ? "Selected" : "Select"}</span>
                </button>
              );
            })}
            {filteredRepositoryOptions.length === 0 && !loadingRepositories ? (
              <p className="mono">No repositories returned for this user.</p>
            ) : null}
          </div>
        </article>
      </div>

      <p className="mono">
        {activeProject ? `Editing project: ${activeProject.name}` : "Use New Project to create one, or Edit on an existing project."}
      </p>
      {error ? <p className="error-text">{error}</p> : null}
    </section>
  );
}
