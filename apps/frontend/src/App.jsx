import { useCallback, useEffect, useMemo, useState } from "react";
import AuthCallbackPage from "./pages/AuthCallbackPage.jsx";
import AppNavbar from "./components/AppNavbar.jsx";
import AuthPage from "./pages/AuthPage.jsx";
import DashboardPage from "./pages/DashboardPage.jsx";
import LandingPage from "./pages/LandingPage.jsx";
import ProjectsPage from "./pages/ProjectsPage.jsx";
import {
  addRepositoryToProject,
  clearSessionToken,
  createProject,
  deleteProject,
  getStoredSessionToken,
  listGithubUserRepositories,
  listProjectRepositories,
  listUserProjects,
  removeRepositoryFromProject,
  startOAuthFlow,
  updateProject,
  validateSessionToken,
} from "./lib/authApi.js";

function routeFromPath(pathname) {
  if (pathname.startsWith("/oauth/callback")) {
    return { route: "callback", projectId: null };
  }
  if (pathname === "/signup") {
    return { route: "signup", projectId: null };
  }
  if (pathname === "/signin") {
    return { route: "signin", projectId: null };
  }
  if (pathname.startsWith("/projects/")) {
    const parts = pathname.split("/").filter(Boolean);
    return { route: "project", projectId: parts.length >= 2 ? parts[1] : null };
  }
  if (pathname.startsWith("/projects")) {
    return { route: "projects", projectId: null };
  }
  if (pathname.startsWith("/project/")) {
    const parts = pathname.split("/").filter(Boolean);
    return { route: "project", projectId: parts.length >= 2 ? parts[1] : null };
  }
  if (pathname === "/project") {
    return { route: "project", projectId: null };
  }
  return { route: "landing", projectId: null };
}

function isProtectedRoute(route) {
  return route === "projects" || route === "project";
}

export default function App() {
  const [locationState, setLocationState] = useState(() => routeFromPath(window.location.pathname));
  const [busyFlow, setBusyFlow] = useState(null);
  const [hasSession, setHasSession] = useState(false);
  const [sessionToken, setSessionToken] = useState(() => getStoredSessionToken() || "");
  const [projects, setProjects] = useState([]);
  const [githubRepositories, setGithubRepositories] = useState([]);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [loadingRepositories, setLoadingRepositories] = useState(false);

  const navigate = useCallback((path) => {
    window.history.pushState({}, "", path);
    setLocationState(routeFromPath(path));
  }, []);

  const handleLogout = useCallback(() => {
    clearSessionToken();
    setHasSession(false);
    setSessionToken("");
    setProjects([]);
    setGithubRepositories([]);
    navigate("/");
  }, [navigate]);

  useEffect(() => {
    const onPop = () => setLocationState(routeFromPath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const route = locationState.route;
  const activeProject = useMemo(
    () => projects.find((project) => project.project_id === locationState.projectId) || null,
    [projects, locationState.projectId]
  );
  const navbarSubtitle = route === "project" ? "Project Workspace" : "GitHub-native coding operations";
  const navbarContextChips = useMemo(
    () =>
      route === "project"
        ? [
            `Project: ${activeProject?.name || "Unassigned"}`,
            "Live Session",
            "Trace Aware",
          ]
        : [],
    [route, activeProject]
  );

  useEffect(() => {
    const token = getStoredSessionToken();
    setSessionToken(token || "");
    if (!token) {
      setHasSession(false);
      if (isProtectedRoute(route)) {
        navigate("/signin");
      }
      return;
    }
    validateSessionToken(token)
      .then((userId) => {
        const authenticated = Boolean(userId);
        setHasSession(authenticated);
        if (authenticated && (route === "signup" || route === "signin")) {
          navigate("/projects");
          return;
        }
        if (!authenticated && isProtectedRoute(route)) {
          navigate("/signin");
        }
      })
      .catch(() => {
        setHasSession(false);
        if (isProtectedRoute(route)) {
          navigate("/signin");
        }
      });
  }, [route, navigate]);

  useEffect(() => {
    if (!sessionToken || !hasSession) {
      return;
    }
    setLoadingProjects(true);
    setLoadingRepositories(true);
    listUserProjects(sessionToken)
      .then((rows) => setProjects(rows))
      .finally(() => setLoadingProjects(false));
    listGithubUserRepositories(sessionToken)
      .then((rows) => setGithubRepositories(rows))
      .finally(() => setLoadingRepositories(false));
  }, [sessionToken, hasSession]);

  useEffect(() => {
    if (route !== "project" || !hasSession) {
      return;
    }
    if (loadingProjects) {
      return;
    }
    if (!locationState.projectId) {
      const firstOwned = projects.find((project) => project.role === "owner");
      if (firstOwned) {
        navigate(`/projects/${firstOwned.project_id}`);
      } else {
        navigate("/projects");
      }
      return;
    }

    const selected = projects.find((project) => project.project_id === locationState.projectId);
    if (!selected) {
      navigate("/projects");
      return;
    }
    if (selected.role !== "owner") {
      navigate("/projects");
    }
  }, [route, hasSession, locationState.projectId, projects, navigate, loadingProjects]);

  const handleStartFlow = useCallback(async (flow) => {
    setBusyFlow(flow);
    const redirectUri = `${window.location.origin}/oauth/callback`;
    const payload = await startOAuthFlow({ flow, redirectUri });
    window.location.href = payload.authorizeUrl;
  }, []);

  const content = useMemo(() => {
    if (isProtectedRoute(route) && !hasSession) {
      return <AuthPage mode="signin" busyFlow={busyFlow} onStartFlow={handleStartFlow} />;
    }

    if (route === "callback") {
      return (
        <AuthCallbackPage
          onSuccess={() => {
            setHasSession(true);
            setSessionToken(getStoredSessionToken() || "");
            navigate("/projects");
          }}
        />
      );
    }

    if (route === "projects") {
      return (
        <ProjectsPage
          projects={projects}
          repositories={githubRepositories}
          loadingProjects={loadingProjects}
          loadingRepositories={loadingRepositories}
          onOpenDashboard={(projectId) => navigate(`/projects/${projectId}`)}
          onCreateProject={async (name, description) => {
            if (!sessionToken) throw new Error("Missing session token");
            const project = await createProject(sessionToken, name, description || undefined);
            const hydratedProject = {
              ...project,
              role: project?.role || "owner",
            };
            setProjects((current) => [...current, hydratedProject]);
            return hydratedProject;
          }}
          onUpdateProject={async (projectId, name, description) => {
            if (!sessionToken) throw new Error("Missing session token");
            const updated = await updateProject(sessionToken, projectId, name, description || undefined);
            setProjects((current) =>
              current.map((project) => (project.project_id === projectId ? { ...project, ...updated } : project))
            );
            return updated;
          }}
          onDeleteProject={async (projectId) => {
            if (!sessionToken) throw new Error("Missing session token");
            await deleteProject(sessionToken, projectId);
            setProjects((current) => current.filter((project) => project.project_id !== projectId));
          }}
          onListProjectRepositories={async (projectId) => {
            if (!sessionToken) throw new Error("Missing session token");
            return listProjectRepositories(sessionToken, projectId);
          }}
          onAddRepository={async (projectId, repo) => {
            if (!sessionToken) throw new Error("Missing session token");
            return addRepositoryToProject(sessionToken, projectId, repo);
          }}
          onRemoveRepository={async (projectId, repositoryId) => {
            if (!sessionToken) throw new Error("Missing session token");
            return removeRepositoryFromProject(sessionToken, projectId, repositoryId);
          }}
        />
      );
    }

    if (route === "project" && activeProject?.role === "owner") {
      return (
        <DashboardPage
          project={activeProject}
          projects={projects}
          sessionToken={sessionToken}
          onNavigateProject={(projectId) => navigate(`/projects/${projectId}`)}
        />
      );
    }

    if (route === "project" && loadingProjects) {
      return (
        <section className="landing" aria-label="Loading project workspace">
          <div className="landing-card compact">
            <h1>Loading project...</h1>
            <p>Resolving owner access and restoring workspace context.</p>
          </div>
        </section>
      );
    }

    if (route === "signup") {
      return <AuthPage mode="signup" busyFlow={busyFlow} onStartFlow={handleStartFlow} />;
    }

    if (route === "signin") {
      return <AuthPage mode="signin" busyFlow={busyFlow} onStartFlow={handleStartFlow} />;
    }

    return <LandingPage hasSession={hasSession} onNavigate={navigate} />;
  }, [
    route,
    navigate,
    handleStartFlow,
    busyFlow,
    projects,
    githubRepositories,
    loadingProjects,
    loadingRepositories,
    sessionToken,
    handleLogout,
    hasSession,
    activeProject,
  ]);

  return (
    <div className="shell">
      <div className="backdrop-orb orb-a" />
      <div className="backdrop-orb orb-b" />
      <AppNavbar
        route={route}
        hasSession={hasSession}
        onNavigate={navigate}
        onLogout={handleLogout}
        subtitle={navbarSubtitle}
        contextChips={navbarContextChips}
      />
      <div className="app-content">{content}</div>
    </div>
  );
}
