import { useState, useEffect, useRef, useCallback, useMemo } from "react";

// ═══════════════════════════════════════════════════════════════════════════
//  REAPO.AI — Full App: Sign In → Repo Select → Canvas
// ═══════════════════════════════════════════════════════════════════════════

const themes = {
  light: {
    bg: "#f4f4f8", surface: "#ffffff", surfaceAlt: "#f8f8fb", surfaceHover: "#f0f0f6",
    text: "#1a1a2e", textSec: "#5a5a7a", textDim: "#9999b3", textFaint: "#c4c4d4",
    border: "#e0e0e8", borderHover: "#c8c8d8",
    accent: "#4f46e5", accentLight: "#ede9fe", accentSoft: "#f5f3ff", accentGlow: "rgba(79,70,229,0.12)",
    research: "#2563eb", researchBg: "#eff6ff",
    relevancy: "#d97706", relevancyBg: "#fffbeb",
    reducer: "#059669", reducerBg: "#ecfdf5",
    orchestrator: "#7c3aed", orchestratorBg: "#f5f3ff",
    writer: "#dc2626", writerBg: "#fef2f2",
    success: "#16a34a", successBg: "#f0fdf4", warning: "#ca8a04",
    diffAdd: "#dcfce7", diffDel: "#fee2e2", diffAddText: "#166534", diffDelText: "#991b1b",
    diffAddGutter: "#bbf7d030", diffDelGutter: "#fecaca30",
    shadow: "0 1px 3px rgba(0,0,0,0.06)", shadowLg: "0 4px 16px rgba(0,0,0,0.07)",
    shadowXl: "0 10px 40px rgba(0,0,0,0.10)", ring: "0 0 0 3px rgba(79,70,229,0.15)",
    logBg: "#1a1a2e", logBorder: "#2d2d4a", logText: "#b8b8cc", logDim: "#555577",
    infoBg: "#ffffff", infoBorder: "#e0e0e8",
    heroBg: "linear-gradient(135deg, #f5f3ff 0%, #eff6ff 50%, #f0fdf4 100%)",
    cardBg: "#ffffff",
  },
  dark: {
    bg: "#0b0c12", surface: "#161823", surfaceAlt: "#1c1e2d", surfaceHover: "#22253a",
    text: "#e4e4f0", textSec: "#9a9ab8", textDim: "#5e5e7e", textFaint: "#3a3a54",
    border: "#252738", borderHover: "#33354a",
    accent: "#7c6bf5", accentLight: "#2a2450", accentSoft: "#1a1730", accentGlow: "rgba(124,107,245,0.2)",
    research: "#60a5fa", researchBg: "#1a2640",
    relevancy: "#fbbf24", relevancyBg: "#2a2210",
    reducer: "#34d399", reducerBg: "#0f2a20",
    orchestrator: "#a78bfa", orchestratorBg: "#1f1a35",
    writer: "#f87171", writerBg: "#2a1515",
    success: "#4ade80", successBg: "#0f2a18", warning: "#fbbf24",
    diffAdd: "#0f2a18", diffDel: "#2a1515", diffAddText: "#6ee7b7", diffDelText: "#fca5a5",
    diffAddGutter: "#16a34a18", diffDelGutter: "#dc262618",
    shadow: "0 2px 6px rgba(0,0,0,0.3)", shadowLg: "0 4px 20px rgba(0,0,0,0.4)",
    shadowXl: "0 10px 50px rgba(0,0,0,0.5)", ring: "0 0 0 3px rgba(124,107,245,0.25)",
    logBg: "#0d0e16", logBorder: "#1e2030", logText: "#b8b8cc", logDim: "#444460",
    infoBg: "#1c1e2d", infoBorder: "#33354a",
    heroBg: "linear-gradient(135deg, #0b0c12 0%, #12132a 50%, #0b1a14 100%)",
    cardBg: "#161823",
  },
};

const F = {
  display: "'Space Grotesk', system-ui, sans-serif",
  body: "'DM Sans', system-ui, sans-serif",
  mono: "'JetBrains Mono', 'SF Mono', monospace",
};

// ─── Shared ─────────────────────────────────────────────────────────────────

const Pill = ({ children, color, small }) => (
  <span style={{
    display: "inline-flex", alignItems: "center", gap: 3,
    padding: small ? "1px 7px" : "3px 10px",
    fontSize: small ? 10 : 11, fontFamily: F.mono, fontWeight: 600,
    color, background: `${color}15`, border: `1px solid ${color}25`,
    borderRadius: 20, letterSpacing: 0.3,
  }}>{children}</span>
);

const INFO_MAP = {
  agentPanel: { title: "Agent Panel", desc: "Your AI agent's conversation — shows analysis results, relevant symbols, and actions taken across connected repos." },
  pipeline: { title: "Pipeline Architecture", desc: "Full execution pipeline: Reasoning → Research → Relevancy → Reducer → Orchestrator → Writer. Each stage processes your prompt through semantic search, parallel scoring, context compression, then opens PRs." },
  prPreview: { title: "PR Preview", desc: "Live preview of the pull request the agent created. Shows unified diff with additions/deletions, PR metadata, and the target branch." },
  traceView: { title: "Langfuse Trace", desc: "Observability trace showing every span emitted. Duration bars, token usage, and metrics for diagnosing slow runs or low confidence." },
  promptBar: { title: "Prompt Bar", desc: "Describe your task in natural language. The agent researches across all connected repos, plans changes, and opens PRs." },
  agentLog: { title: "Agent Log", desc: "Raw execution log with timestamps — every API call, cache hit, and decision. Warnings flag issues like read-only repos." },
  repos: { title: "Repositories", desc: "'Write' repos allow PRs. 'Read' repos are for research only. Indexes update via push webhooks." },
};

const InfoBtn = ({ k, t }) => {
  const [open, setOpen] = useState(false);
  const info = INFO_MAP[k];
  if (!info) return null;
  return (
    <div style={{ position: "relative", display: "inline-flex" }}>
      <button onClick={e => { e.stopPropagation(); setOpen(!open); }} style={{
        width: 20, height: 20, borderRadius: "50%", border: `1.5px solid ${open ? t.accent : t.border}`,
        background: open ? t.accentLight : "transparent",
        color: open ? t.accent : t.textDim, fontSize: 11, fontWeight: 700,
        cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: F.body,
        transition: "all 0.2s",
      }}>i</button>
      {open && <>
        <div onClick={() => setOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 99 }} />
        <div style={{
          position: "absolute", top: 26, right: 0, width: 260, padding: "12px 14px",
          background: t.infoBg, border: `1px solid ${t.infoBorder}`, borderRadius: 12, boxShadow: t.shadowXl, zIndex: 100,
        }}>
          <div style={{ fontSize: 12, fontFamily: F.display, fontWeight: 700, color: t.accent, marginBottom: 5 }}>
            {info.title}
          </div>
          <p style={{ fontSize: 11.5, fontFamily: F.body, color: t.textSec, lineHeight: 1.6, margin: 0 }}>{info.desc}</p>
        </div>
      </>}
    </div>
  );
};

// ─── Interactive Canvas BG ──────────────────────────────────────────────────

const ParticleBG = ({ isDark, style = {} }) => {
  const ref = useRef(null);
  const anim = useRef(null);
  const parts = useRef([]);
  const mouse = useRef({ x: -999, y: -999 });

  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    let rW, rH;
    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      rW = c.offsetWidth; rH = c.offsetHeight;
      c.width = rW * dpr; c.height = rH * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize(); window.addEventListener("resize", resize);

    const N = isDark ? 55 : 30;
    parts.current = Array.from({ length: N }, () => ({
      x: Math.random() * (rW || 800), y: Math.random() * (rH || 600),
      vx: (Math.random() - 0.5) * (isDark ? 0.35 : 0.12),
      vy: (Math.random() - 0.5) * (isDark ? 0.35 : 0.12),
      r: isDark ? Math.random() * 2 + 1 : Math.random() * 1.2 + 0.4,
      ph: Math.random() * 6.28,
    }));

    const draw = () => {
      if (!rW) { rW = c.offsetWidth; rH = c.offsetHeight; }
      ctx.clearRect(0, 0, rW, rH);
      const P = parts.current, mx = mouse.current.x, my = mouse.current.y;
      // connections
      for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) {
        const dx = P[i].x - P[j].x, dy = P[i].y - P[j].y, d = Math.sqrt(dx * dx + dy * dy);
        const md = isDark ? 130 : 90;
        if (d < md) {
          ctx.beginPath(); ctx.moveTo(P[i].x, P[i].y); ctx.lineTo(P[j].x, P[j].y);
          ctx.strokeStyle = isDark ? `rgba(124,107,245,${(1 - d / md) * 0.14})` : `rgba(79,70,229,${(1 - d / md) * 0.06})`;
          ctx.lineWidth = 0.5; ctx.stroke();
        }
      }
      P.forEach(p => {
        p.ph += 0.02; p.x += p.vx; p.y += p.vy;
        if (p.x < 0 || p.x > rW) p.vx *= -1;
        if (p.y < 0 || p.y > rH) p.vy *= -1;
        const dmx = p.x - mx, dmy = p.y - my, dM = Math.sqrt(dmx * dmx + dmy * dmy);
        if (dM < 110) { p.x += (dmx / dM) * (110 - dM) / 110 * 0.7; p.y += (dmy / dM) * (110 - dM) / 110 * 0.7; }
        if (isDark) {
          const gr = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r * 4 + Math.sin(p.ph) * 2);
          gr.addColorStop(0, "rgba(124,107,245,0.14)"); gr.addColorStop(1, "rgba(124,107,245,0)");
          ctx.beginPath(); ctx.arc(p.x, p.y, p.r * 4, 0, 6.28); ctx.fillStyle = gr; ctx.fill();
        }
        const a = isDark ? 0.45 + Math.sin(p.ph) * 0.2 : 0.2 + Math.sin(p.ph) * 0.1;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.28);
        ctx.fillStyle = isDark ? `rgba(160,150,255,${a})` : `rgba(79,70,229,${a})`; ctx.fill();
      });
      if (isDark && mx > 0) {
        const g = ctx.createRadialGradient(mx, my, 0, mx, my, 70);
        g.addColorStop(0, "rgba(124,107,245,0.07)"); g.addColorStop(1, "rgba(124,107,245,0)");
        ctx.beginPath(); ctx.arc(mx, my, 70, 0, 6.28); ctx.fillStyle = g; ctx.fill();
      }
      anim.current = requestAnimationFrame(draw);
    };
    draw();
    const onM = e => { const r = c.getBoundingClientRect(); mouse.current = { x: e.clientX - r.left, y: e.clientY - r.top }; };
    c.addEventListener("mousemove", onM);
    return () => { cancelAnimationFrame(anim.current); window.removeEventListener("resize", resize); c.removeEventListener("mousemove", onM); };
  }, [isDark]);

  return <canvas ref={ref} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", ...style }} />;
};

// ═══════════════════════════════════════════════════════════════════════════
//  PAGE 1: SIGN IN
// ═══════════════════════════════════════════════════════════════════════════

const SignInPage = ({ t, isDark, onSignIn, mode, setMode }) => {
  const [hover, setHover] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleClick = () => {
    setLoading(true);
    setTimeout(() => onSignIn(), 1800);
  };

  return (
    <div style={{
      width: "100vw", height: "100vh", display: "flex", flexDirection: "column",
      background: t.bg, position: "relative", overflow: "hidden",
    }}>
      <ParticleBG isDark={isDark} />

      {/* Ambient blobs */}
      <div style={{
        position: "absolute", width: 500, height: 500, borderRadius: "50%",
        background: isDark
          ? "radial-gradient(circle, rgba(124,107,245,0.08) 0%, transparent 70%)"
          : "radial-gradient(circle, rgba(79,70,229,0.06) 0%, transparent 70%)",
        top: "-10%", right: "-5%", filter: "blur(60px)", pointerEvents: "none",
      }} />
      <div style={{
        position: "absolute", width: 400, height: 400, borderRadius: "50%",
        background: isDark
          ? "radial-gradient(circle, rgba(96,165,250,0.06) 0%, transparent 70%)"
          : "radial-gradient(circle, rgba(37,99,235,0.04) 0%, transparent 70%)",
        bottom: "-8%", left: "-3%", filter: "blur(50px)", pointerEvents: "none",
      }} />

      {/* Top bar */}
      <div style={{
        position: "relative", zIndex: 10, display: "flex", alignItems: "center",
        justifyContent: "space-between", padding: "16px 28px",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            width: 34, height: 34, borderRadius: 9, display: "flex",
            alignItems: "center", justifyContent: "center",
            background: `linear-gradient(135deg, ${t.accent}, ${t.research})`,
            boxShadow: `0 3px 12px ${t.accent}40`, fontSize: 16, fontWeight: 800,
            color: "#fff", fontFamily: F.display,
          }}>R</div>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: t.text, fontFamily: F.display, letterSpacing: -0.5 }}>
              Reapo<span style={{ color: t.accent }}>.</span>ai
            </div>
          </div>
        </div>
        <button onClick={() => setMode(m => m === "dark" ? "light" : "dark")} style={{
          display: "flex", alignItems: "center", gap: 6, padding: "6px 14px",
          background: t.surface, border: `1px solid ${t.border}`, borderRadius: 10,
          cursor: "pointer", fontSize: 12, fontFamily: F.mono, color: t.textSec,
          boxShadow: t.shadow, transition: "all 0.2s",
        }}>
          <span style={{ fontSize: 14 }}>{isDark ? "☀️" : "🌙"}</span>
          {isDark ? "Light" : "Dark"}
        </button>
      </div>

      {/* Center content */}
      <div style={{
        flex: 1, display: "flex", flexDirection: "column", alignItems: "center",
        justifyContent: "center", position: "relative", zIndex: 10, gap: 0,
      }}>
        {/* Badge */}
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 14px",
          background: t.surface, border: `1px solid ${t.border}`, borderRadius: 20,
          marginBottom: 24, boxShadow: t.shadow,
        }}>
          <span style={{
            width: 8, height: 8, borderRadius: "50%", background: t.success,
            boxShadow: `0 0 6px ${t.success}60`,
          }} />
          <span style={{ fontSize: 12, fontFamily: F.mono, color: t.textSec, fontWeight: 500 }}>
            Multi-repo coding agent
          </span>
        </div>

        {/* Headline */}
        <h1 style={{
          fontSize: 52, fontFamily: F.display, fontWeight: 800, color: t.text,
          textAlign: "center", lineHeight: 1.1, letterSpacing: -2, margin: 0,
          maxWidth: 650,
        }}>
          Research, Write &<br />
          <span style={{
            background: `linear-gradient(135deg, ${t.accent}, ${t.research}, ${t.reducer})`,
            WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
          }}>Ship Code</span> Across Repos
        </h1>

        <p style={{
          fontSize: 16, fontFamily: F.body, color: t.textSec, textAlign: "center",
          maxWidth: 480, lineHeight: 1.65, margin: "18px 0 36px",
        }}>
          Connect your GitHub repositories. Our agent researches your codebase,
          plans changes across services, and opens pull requests — all from a single prompt.
        </p>

        {/* GitHub sign in */}
        <button
          onClick={handleClick}
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
          disabled={loading}
          style={{
            display: "flex", alignItems: "center", gap: 10, padding: "14px 32px",
            background: loading ? t.textDim : (isDark ? "#ffffff" : "#1a1a2e"),
            color: loading ? t.bg : (isDark ? "#1a1a2e" : "#ffffff"),
            border: "none", borderRadius: 14, fontSize: 16, fontFamily: F.body,
            fontWeight: 700, cursor: loading ? "wait" : "pointer",
            boxShadow: hover && !loading
              ? `0 8px 30px ${isDark ? "rgba(255,255,255,0.15)" : "rgba(0,0,0,0.2)"}`
              : `0 4px 16px ${isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.1)"}`,
            transform: hover && !loading ? "translateY(-2px) scale(1.02)" : "translateY(0)",
            transition: "all 0.25s cubic-bezier(0.4,0,0.2,1)",
          }}
        >
          {loading ? (
            <div style={{
              width: 20, height: 20, border: `2.5px solid ${t.bg}40`,
              borderTopColor: t.bg, borderRadius: "50%",
              animation: "spin 0.7s linear infinite",
            }} />
          ) : (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
            </svg>
          )}
          {loading ? "Connecting to GitHub…" : "Continue with GitHub"}
        </button>

        {/* Scope info */}
        <div style={{
          display: "flex", gap: 16, marginTop: 24, fontSize: 11, fontFamily: F.mono, color: t.textDim,
        }}>
          {["contents:read", "contents:write", "admin:webhooks"].map(s => (
            <div key={s} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <span style={{ width: 4, height: 4, borderRadius: "50%", background: t.textDim }} />
              {s}
            </div>
          ))}
        </div>

        {/* Feature pills */}
        <div style={{
          display: "flex", gap: 10, marginTop: 40, flexWrap: "wrap", justifyContent: "center",
        }}>
          {[
            { icon: "🔬", label: "Hybrid RAG Search", color: t.research },
            { icon: "⚡", label: "Parallel Relevancy", color: t.relevancy },
            { icon: "🔁", label: "Context Reduction", color: t.reducer },
            { icon: "✍️", label: "Auto PR Creation", color: t.writer },
            { icon: "◈", label: "Langfuse Tracing", color: t.orchestrator },
          ].map(f => (
            <div key={f.label} style={{
              display: "flex", alignItems: "center", gap: 5, padding: "6px 14px",
              background: t.surface, border: `1px solid ${t.border}`, borderRadius: 10,
              fontSize: 12, fontFamily: F.body, color: t.textSec, boxShadow: t.shadow,
            }}>
              <span>{f.icon}</span>
              <span style={{ fontWeight: 500 }}>{f.label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Footer */}
      <div style={{
        position: "relative", zIndex: 10, padding: "14px 28px",
        display: "flex", justifyContent: "center", fontSize: 11,
        fontFamily: F.mono, color: t.textDim,
      }}>
        Reapo.ai — OAuth scoped via GitHub Apps · Your code never leaves your repos
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
//  PAGE 2: REPO SELECTION
// ═══════════════════════════════════════════════════════════════════════════

const ALL_REPOS = [
  { id: 1, name: "checkout-service", owner: "acme", desc: "Order processing and checkout flow microservice", lang: "Python", langColor: "#3572A5", stars: 24, updated: "2 days ago", private: false, defaultAccess: "write" },
  { id: 2, name: "pricing-lib", owner: "acme", desc: "Shared pricing and discount calculation library", lang: "Python", langColor: "#3572A5", stars: 12, updated: "5 days ago", private: false, defaultAccess: "read" },
  { id: 3, name: "user-service", owner: "acme", desc: "User authentication and profile management", lang: "TypeScript", langColor: "#2b7489", stars: 31, updated: "1 day ago", private: false, defaultAccess: "write" },
  { id: 4, name: "api-gateway", owner: "acme", desc: "Kong-based API gateway with rate limiting", lang: "Go", langColor: "#00ADD8", stars: 8, updated: "1 week ago", private: true, defaultAccess: "read" },
  { id: 5, name: "notification-hub", owner: "acme", desc: "Push/email/SMS notification dispatcher", lang: "TypeScript", langColor: "#2b7489", stars: 5, updated: "3 days ago", private: false, defaultAccess: "write" },
  { id: 6, name: "inventory-tracker", owner: "acme", desc: "Real-time inventory and warehouse tracking", lang: "Rust", langColor: "#dea584", stars: 18, updated: "4 days ago", private: true, defaultAccess: "read" },
  { id: 7, name: "data-pipeline", owner: "acme", desc: "ETL pipeline for analytics and reporting", lang: "Python", langColor: "#3572A5", stars: 7, updated: "6 days ago", private: false, defaultAccess: "read" },
  { id: 8, name: "mobile-app", owner: "acme", desc: "React Native customer-facing mobile application", lang: "JavaScript", langColor: "#f1e05a", stars: 42, updated: "12 hours ago", private: true, defaultAccess: "write" },
  { id: 9, name: "infra-terraform", owner: "acme", desc: "Infrastructure as code — AWS resources", lang: "HCL", langColor: "#844FBA", stars: 3, updated: "2 weeks ago", private: true, defaultAccess: "read" },
  { id: 10, name: "shared-proto", owner: "acme", desc: "Protobuf definitions shared across services", lang: "Protocol Buffers", langColor: "#5a5a5a", stars: 14, updated: "1 week ago", private: false, defaultAccess: "read" },
];

const RepoSelectPage = ({ t, isDark, onContinue, mode, setMode }) => {
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(new Set());
  const [accessMap, setAccessMap] = useState({});
  const [entering, setEntering] = useState(true);

  useEffect(() => { setTimeout(() => setEntering(false), 50); }, []);

  const filtered = ALL_REPOS.filter(r =>
    r.name.toLowerCase().includes(search.toLowerCase()) ||
    r.desc.toLowerCase().includes(search.toLowerCase())
  );

  const toggle = (id) => {
    setSelected(prev => {
      const s = new Set(prev);
      if (s.has(id)) s.delete(id); else s.add(id);
      return s;
    });
  };

  const cycleAccess = (e, repo) => {
    e.stopPropagation();
    setAccessMap(prev => ({
      ...prev,
      [repo.id]: prev[repo.id] === "write" ? "read" : "write",
    }));
  };

  const getAccess = (repo) => accessMap[repo.id] || repo.defaultAccess;

  const handleContinue = () => {
    const repos = ALL_REPOS.filter(r => selected.has(r.id)).map(r => ({
      name: r.name, owner: r.owner, access: getAccess(r), symbols: Math.floor(Math.random() * 800 + 100),
    }));
    onContinue(repos);
  };

  return (
    <div style={{
      width: "100vw", height: "100vh", display: "flex", flexDirection: "column",
      background: t.bg, position: "relative", overflow: "hidden",
      opacity: entering ? 0 : 1, transform: entering ? "translateY(12px)" : "none",
      transition: "all 0.5s cubic-bezier(0.4,0,0.2,1)",
    }}>
      <ParticleBG isDark={isDark} style={{ opacity: 0.5 }} />

      {/* Top bar */}
      <div style={{
        position: "relative", zIndex: 10, display: "flex", alignItems: "center",
        justifyContent: "space-between", padding: "14px 28px",
        borderBottom: `1px solid ${t.border}`, background: `${t.surface}ee`,
        backdropFilter: "blur(12px)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            width: 30, height: 30, borderRadius: 8, display: "flex",
            alignItems: "center", justifyContent: "center",
            background: `linear-gradient(135deg, ${t.accent}, ${t.research})`,
            fontSize: 14, fontWeight: 800, color: "#fff", fontFamily: F.display,
          }}>R</div>
          <span style={{ fontSize: 15, fontWeight: 700, color: t.text, fontFamily: F.display }}>
            Reapo<span style={{ color: t.accent }}>.</span>ai
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button onClick={() => setMode(m => m === "dark" ? "light" : "dark")} style={{
            display: "flex", alignItems: "center", gap: 5, padding: "5px 12px",
            background: t.bg, border: `1px solid ${t.border}`, borderRadius: 8,
            cursor: "pointer", fontSize: 12, fontFamily: F.mono, color: t.textSec,
          }}>
            <span style={{ fontSize: 13 }}>{isDark ? "☀️" : "🌙"}</span>
            {isDark ? "Light" : "Dark"}
          </button>
          <div style={{
            display: "flex", alignItems: "center", gap: 6, padding: "5px 12px",
            background: t.successBg, border: `1px solid ${t.success}30`, borderRadius: 8,
          }}>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: t.success }} />
            <span style={{ fontSize: 11, fontFamily: F.mono, color: t.success, fontWeight: 600 }}>Connected as acme</span>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div style={{
        flex: 1, overflow: "auto", position: "relative", zIndex: 10,
        display: "flex", flexDirection: "column", alignItems: "center",
        padding: "32px 24px",
      }}>
        {/* Header */}
        <div style={{ textAlign: "center", marginBottom: 28, maxWidth: 500 }}>
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 5, padding: "4px 12px",
            background: t.surface, border: `1px solid ${t.border}`, borderRadius: 16,
            fontSize: 11, fontFamily: F.mono, color: t.textDim, marginBottom: 14,
            boxShadow: t.shadow,
          }}>
            Step 2 of 2
          </div>
          <h2 style={{
            fontSize: 30, fontFamily: F.display, fontWeight: 800, color: t.text,
            letterSpacing: -1, margin: "0 0 10px",
          }}>
            Select Your Repositories
          </h2>
          <p style={{ fontSize: 14, fontFamily: F.body, color: t.textSec, lineHeight: 1.6, margin: 0 }}>
            Choose the repos you want the agent to work with. You can set each to
            <strong> read</strong> (research only) or <strong>write</strong> (can open PRs). Click access badges to toggle.
          </p>
        </div>

        {/* Search */}
        <div style={{
          width: "100%", maxWidth: 640, marginBottom: 16,
          display: "flex", alignItems: "center", gap: 8,
          padding: "10px 14px", background: t.surface, borderRadius: 12,
          border: `1.5px solid ${t.border}`, boxShadow: t.shadow,
        }}>
          <span style={{ fontSize: 16, color: t.textDim }}>🔍</span>
          <input
            value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search repositories…"
            style={{
              flex: 1, background: "transparent", border: "none", outline: "none",
              color: t.text, fontSize: 14, fontFamily: F.body,
            }}
          />
          <span style={{ fontSize: 11, fontFamily: F.mono, color: t.textDim }}>
            {filtered.length} repos
          </span>
        </div>

        {/* Select all / count */}
        <div style={{
          width: "100%", maxWidth: 640, display: "flex", alignItems: "center",
          justifyContent: "space-between", marginBottom: 10, padding: "0 4px",
        }}>
          <button onClick={() => {
            if (selected.size === filtered.length) setSelected(new Set());
            else setSelected(new Set(filtered.map(r => r.id)));
          }} style={{
            background: "transparent", border: "none", cursor: "pointer",
            fontSize: 12, fontFamily: F.mono, color: t.accent, fontWeight: 600,
          }}>
            {selected.size === filtered.length ? "Deselect all" : "Select all"}
          </button>
          <span style={{ fontSize: 12, fontFamily: F.mono, color: t.textDim }}>
            {selected.size} selected
          </span>
        </div>

        {/* Repo list */}
        <div style={{
          width: "100%", maxWidth: 640, display: "flex", flexDirection: "column", gap: 6,
        }}>
          {filtered.map((repo, i) => {
            const isSel = selected.has(repo.id);
            const access = getAccess(repo);
            return (
              <div key={repo.id} onClick={() => toggle(repo.id)} style={{
                display: "flex", alignItems: "center", gap: 12,
                padding: "12px 16px", background: isSel ? t.accentSoft : t.surface,
                border: `1.5px solid ${isSel ? t.accent + "50" : t.border}`,
                borderRadius: 12, cursor: "pointer",
                boxShadow: isSel ? `${t.shadow}, ${t.ring}` : t.shadow,
                transition: "all 0.2s cubic-bezier(0.4,0,0.2,1)",
                opacity: 0, animation: `fadeSlideIn 0.35s ease ${i * 0.04}s forwards`,
              }}>
                {/* Checkbox */}
                <div style={{
                  width: 22, height: 22, borderRadius: 6, flexShrink: 0,
                  border: `2px solid ${isSel ? t.accent : t.border}`,
                  background: isSel ? t.accent : "transparent",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  transition: "all 0.2s",
                }}>
                  {isSel && <span style={{ color: "#fff", fontSize: 12, fontWeight: 700 }}>✓</span>}
                </div>

                {/* Info */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
                    <span style={{ fontSize: 13, fontFamily: F.display, fontWeight: 700, color: t.text }}>
                      {repo.owner}/{repo.name}
                    </span>
                    {repo.private && (
                      <span style={{
                        padding: "0 5px", fontSize: 9, fontFamily: F.mono,
                        background: t.surfaceAlt, border: `1px solid ${t.border}`,
                        borderRadius: 4, color: t.textDim,
                      }}>private</span>
                    )}
                  </div>
                  <p style={{ fontSize: 11.5, fontFamily: F.body, color: t.textSec, margin: 0, lineHeight: 1.4 }}>
                    {repo.desc}
                  </p>
                  <div style={{
                    display: "flex", alignItems: "center", gap: 10, marginTop: 5,
                    fontSize: 10.5, fontFamily: F.mono, color: t.textDim,
                  }}>
                    <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
                      <span style={{ width: 8, height: 8, borderRadius: "50%", background: repo.langColor }} />
                      {repo.lang}
                    </span>
                    <span>⭐ {repo.stars}</span>
                    <span>Updated {repo.updated}</span>
                  </div>
                </div>

                {/* Access toggle */}
                <button onClick={e => cycleAccess(e, repo)} style={{
                  padding: "4px 10px", borderRadius: 8, fontSize: 11, fontFamily: F.mono,
                  fontWeight: 600, cursor: "pointer", transition: "all 0.2s",
                  background: access === "write" ? `${t.success}15` : `${t.warning}15`,
                  color: access === "write" ? t.success : t.warning,
                  border: `1px solid ${access === "write" ? t.success + "35" : t.warning + "35"}`,
                }}>
                  {access === "write" ? "⎇ write" : "👁 read"}
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* Bottom bar */}
      <div style={{
        position: "relative", zIndex: 10, padding: "14px 28px",
        borderTop: `1px solid ${t.border}`, background: `${t.surface}ee`,
        backdropFilter: "blur(12px)",
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <div style={{ fontSize: 12, fontFamily: F.body, color: t.textSec }}>
          {selected.size > 0
            ? `${selected.size} repo${selected.size > 1 ? "s" : ""} selected · ${
                [...selected].filter(id => getAccess(ALL_REPOS.find(r => r.id === id)) === "write").length
              } writable`
            : "Select at least one repository to continue"
          }
        </div>
        <button
          onClick={handleContinue}
          disabled={selected.size === 0}
          style={{
            padding: "10px 28px", borderRadius: 10, border: "none",
            background: selected.size > 0 ? t.accent : t.border,
            color: selected.size > 0 ? "#fff" : t.textDim,
            fontSize: 14, fontFamily: F.body, fontWeight: 700,
            cursor: selected.size > 0 ? "pointer" : "not-allowed",
            boxShadow: selected.size > 0 ? `0 4px 14px ${t.accent}35` : "none",
            transition: "all 0.25s",
          }}
        >
          Launch Workspace →
        </button>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
//  PAGE 3: MAIN CANVAS (condensed from previous version)
// ═══════════════════════════════════════════════════════════════════════════

const STAGES = [
  { id: "reasoning", label: "Reasoning", icon: "🧠", cKey: "research" },
  { id: "research", label: "Research", icon: "🔬", cKey: "research" },
  { id: "relevancy", label: "Relevancy", icon: "⚡", cKey: "relevancy" },
  { id: "reducer", label: "Reducer", icon: "🔁", cKey: "reducer" },
  { id: "orchestrator", label: "Orchestrator", icon: "🤖", cKey: "orchestrator" },
  { id: "writer", label: "Writer", icon: "✍️", cKey: "writer" },
];
const STAGE_INFO = ["prompt → ResearchObjective", "objective → 12 candidates", "12 → 8 passed (≥0.6)", "8 → 4.2K tokens", "3 steps · 2 tool calls", "PR #142 opened"];
const SPANS = [
  { name: "reasoning_agent", dur: 0.8, tokens: 412, color: "research" },
  { name: "semantic_prodder", dur: 1.2, tokens: 1840, color: "research", indent: 1 },
  { name: "live_repo_reader", dur: 3.1, tokens: 4200, color: "research", indent: 1, meta: "12 blobs · 89% cache" },
  { name: "relevancy_system", dur: 2.4, tokens: 6100, color: "relevancy", meta: "8/12 ≥ 0.6" },
  { name: "reducer_tier_1", dur: 1.1, tokens: 3200, color: "reducer", indent: 1 },
  { name: "reducer_tier_2", dur: 0.9, tokens: 2100, color: "reducer", indent: 1 },
  { name: "orchestrator_loop", dur: 2.2, tokens: 5400, color: "orchestrator", meta: "3 steps" },
  { name: "writer_sub_agent", dur: 0.7, tokens: 1579, color: "writer", indent: 1, meta: "PR #142" },
];
const DIFFS = [
  { ty: "c", n: [10, 10], t: "from typing import OrderResult" },
  { ty: "c", n: [11, 11], t: "" },
  { ty: "d", n: [12, null], t: "def processOrder(order_id: str) -> OrderResult:" },
  { ty: "a", n: [null, 12], t: "async def processOrder(order_id: str) -> OrderResult:" },
  { ty: "c", n: [13, 13], t: '    """Validates and persists an incoming order."""' },
  { ty: "d", n: [14, null], t: "    user = fetchUser(order_id)" },
  { ty: "d", n: [15, null], t: "    discount = applyDiscount(user, order_id)" },
  { ty: "a", n: [null, 14], t: "    user, discount = await asyncio.gather(" },
  { ty: "a", n: [null, 15], t: "        fetchUser(order_id)," },
  { ty: "a", n: [null, 16], t: "        applyDiscount(user, order_id)," },
  { ty: "a", n: [null, 17], t: "    )" },
  { ty: "c", n: [16, 18], t: "    return persist_order(user, discount)" },
];
const LOGS = [
  { time: "10:22:31", lv: "info", msg: "OAuth token refreshed" },
  { time: "10:22:32", lv: "info", msg: "Semantic prodder: 4 query angles" },
  { time: "10:22:35", lv: "info", msg: "12 blobs fetched, 89% cache" },
  { time: "10:22:38", lv: "info", msg: "Relevancy: 8/12 passed ≥ 0.6" },
  { time: "10:22:39", lv: "warn", msg: "pricing-lib read-only — write blocked" },
  { time: "10:22:41", lv: "info", msg: "Reducer: 6100 → 2100 tokens" },
  { time: "10:22:42", lv: "info", msg: "Orchestrator: 3 steps" },
  { time: "10:22:44", lv: "ok", msg: "PR #142 opened ✓" },
];

const MainCanvas = ({ t, isDark, repos, mode, setMode, onSignOut }) => {
  const [sel, setSel] = useState("pr");
  const [log, setLog] = useState(false);
  const [input, setInput] = useState("");
  const [focused, setFocused] = useState(false);
  const maxDur = Math.max(...SPANS.map(s => s.dur));
  const totalSymbols = repos.reduce((a, r) => a + (r.symbols || 0), 0);

  return (
    <div style={{
      width: "100vw", height: "100vh", display: "flex", flexDirection: "column",
      background: t.bg, overflow: "hidden",
    }}>
      {/* Banner */}
      <div style={{
        padding: "5px 16px", background: t.accentSoft, borderBottom: `1px solid ${t.accent}15`,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 11, fontFamily: F.body, color: t.accent,
      }}>
        ⚡ Powered by <strong style={{ margin: "0 3px" }}>Reapo.ai</strong> — multi-repo coding agent
      </div>

      {/* Nav */}
      <div style={{
        height: 48, padding: "0 16px", background: t.surface,
        borderBottom: `1px solid ${t.border}`,
        display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{
            width: 28, height: 28, borderRadius: 7, display: "flex",
            alignItems: "center", justifyContent: "center",
            background: `linear-gradient(135deg, ${t.accent}, ${t.research})`,
            fontSize: 13, fontWeight: 800, color: "#fff", fontFamily: F.display,
          }}>R</div>
          <span style={{ fontSize: 14, fontFamily: F.display, fontWeight: 700, color: t.text }}>
            Async Discount Refactor
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button onClick={() => setMode(m => m === "dark" ? "light" : "dark")} style={{
            display: "flex", alignItems: "center", gap: 5, padding: "5px 10px",
            background: t.bg, border: `1px solid ${t.border}`, borderRadius: 8,
            cursor: "pointer", fontSize: 11, fontFamily: F.mono, color: t.textSec,
          }}>
            {isDark ? "☀️" : "🌙"}
          </button>
          <button onClick={onSignOut} style={{
            padding: "5px 12px", background: "transparent",
            border: `1px solid ${t.border}`, borderRadius: 8,
            fontSize: 11, fontFamily: F.mono, color: t.textSec, cursor: "pointer",
          }}>Sign Out</button>
        </div>
      </div>

      <div style={{ flex: 1, display: "flex", overflow: "hidden", position: "relative" }}>
        {/* Left Agent Panel */}
        <div style={{
          width: 270, height: "100%", display: "flex", flexDirection: "column",
          background: t.surface, borderRight: `1px solid ${t.border}`, flexShrink: 0, zIndex: 10,
        }}>
          <div style={{ padding: "12px 14px 4px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{
              width: 28, height: 28, borderRadius: "50%", display: "flex",
              alignItems: "center", justifyContent: "center",
              background: `linear-gradient(135deg, ${t.accent}, ${t.research})`,
              boxShadow: `0 2px 8px ${t.accent}35`, fontSize: 12,
            }}>💬</div>
            <InfoBtn k="agentPanel" t={t} />
          </div>
          <div style={{ flex: 1, overflow: "auto", padding: "10px 14px" }}>
            <p style={{ fontSize: 13, fontFamily: F.body, color: t.text, lineHeight: 1.7, marginBottom: 10 }}>
              I've completed the async refactor across your repositories.
            </p>
            <p style={{ fontSize: 12, fontFamily: F.body, color: t.textSec, lineHeight: 1.65, marginBottom: 14 }}>
              Analyzed <strong>checkout-service</strong> + <strong>pricing-lib</strong>. Found <strong>12 candidates</strong>, <strong>8 passed</strong> scoring.
            </p>
            {[
              { icon: "⚡", label: "Async Conversion", desc: "processOrder → async with asyncio.gather" },
              { icon: "🔗", label: "Cross-Repo Trace", desc: "Call-graph into pricing-lib (read-only)" },
              { icon: "✅", label: "PR Opened", desc: "PR #142 → agent/async-discount" },
            ].map((item, i) => (
              <div key={i} style={{
                padding: "8px 10px", background: t.bg, borderRadius: 8,
                border: `1px solid ${t.border}`, marginBottom: 6,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 2 }}>
                  <span style={{ fontSize: 12 }}>{item.icon}</span>
                  <span style={{ fontSize: 11.5, fontWeight: 700, color: t.text, fontFamily: F.body }}>{item.label}</span>
                </div>
                <p style={{ fontSize: 11, color: t.textSec, lineHeight: 1.4, margin: 0, fontFamily: F.body }}>{item.desc}</p>
              </div>
            ))}
            <div style={{ marginTop: 14 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                <span style={{ fontSize: 9, fontFamily: F.mono, color: t.textDim, letterSpacing: 1, textTransform: "uppercase" }}>Repos</span>
                <InfoBtn k="repos" t={t} />
              </div>
              {repos.map(r => (
                <div key={r.name} style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "4px 8px", background: t.bg, borderRadius: 6,
                  border: `1px solid ${t.border}`, marginBottom: 4,
                  fontSize: 11, fontFamily: F.mono, color: t.text,
                }}>
                  <span>{r.owner}/{r.name}</span>
                  <Pill color={r.access === "write" ? t.success : t.warning} small>{r.access}</Pill>
                </div>
              ))}
            </div>
          </div>
          <button onClick={() => setLog(!log)} style={{
            display: "flex", alignItems: "center", gap: 5, padding: "10px 14px",
            background: log ? t.accentSoft : "transparent", border: "none",
            borderTop: `1px solid ${t.border}`, cursor: "pointer",
            fontSize: 11, fontFamily: F.body, color: log ? t.accent : t.textSec, width: "100%",
          }}>
            ⚙️ <span style={{ fontWeight: 500 }}>Agent log</span>
            <span style={{ marginLeft: "auto", fontSize: 9, color: t.textDim }}>{log ? "▴" : "▾"}</span>
          </button>
        </div>

        {/* Canvas */}
        <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>
          <ParticleBG isDark={isDark} style={{ zIndex: 0 }} />

          <div style={{
            display: "flex", gap: 20, padding: 28,
            height: "100%", overflowX: "auto", overflowY: "hidden",
            alignItems: "flex-start", position: "relative", zIndex: 1,
          }}>
            {/* Pipeline Card */}
            <div style={{ position: "relative", flexShrink: 0 }}>
              <div onClick={() => setSel("pipe")} style={{
                width: 360, background: t.surface, borderRadius: 14,
                border: sel === "pipe" ? `2px solid ${t.accent}` : `1px solid ${t.border}`,
                boxShadow: sel === "pipe" ? `${t.shadowLg}, ${t.ring}` : t.shadow,
                overflow: "hidden", cursor: "pointer", transition: "all 0.25s",
              }}>
                <div style={{
                  padding: "11px 14px", borderBottom: `1px solid ${t.border}`,
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                }}>
                  <span style={{ fontSize: 12.5, fontFamily: F.display, fontWeight: 700, color: t.text }}>Pipeline</span>
                  <div style={{ display: "flex", gap: 5 }}>
                    <Pill color={t.success} small>done</Pill>
                    <InfoBtn k="pipeline" t={t} />
                  </div>
                </div>
                <div style={{ padding: 14 }}>
                  <svg viewBox="0 0 332 420" style={{ width: "100%" }}>
                    {STAGES.map((s, i) => {
                      const y = i * 68 + 6; const c = t[s.cKey]; const bg = t[s.cKey + "Bg"];
                      return (<g key={s.id}>
                        {i > 0 && <line x1={166} y1={y - 18} x2={166} y2={y} stroke={c + "30"} strokeWidth={1.5} />}
                        <rect x={16} y={y} width={300} height={46} rx={10} fill={bg} stroke={c + "30"} strokeWidth={1} />
                        <text x={40} y={y + 28} fontSize={15} textAnchor="middle">{s.icon}</text>
                        <text x={64} y={y + 22} fontSize={11.5} fontFamily={F.display} fontWeight={700} fill={c}>{s.label}</text>
                        <text x={64} y={y + 36} fontSize={9} fontFamily={F.mono} fill={t.textDim}>{STAGE_INFO[i]}</text>
                        <circle cx={300} cy={y + 23} r={7} fill={t.success + "15"} stroke={t.success} strokeWidth={1.5} />
                        <text x={300} y={y + 27} fontSize={9} textAnchor="middle" fill={t.success} fontWeight={700}>✓</text>
                      </g>);
                    })}
                  </svg>
                </div>
              </div>
              {sel === "pipe" && <div style={{ position: "absolute", bottom: -20, left: "50%", transform: "translateX(-50%)", padding: "2px 7px", background: t.accent, borderRadius: 4, fontSize: 9, fontFamily: F.mono, color: "#fff", fontWeight: 600 }}>360 × 500</div>}
            </div>

            {/* PR Card */}
            <div style={{ position: "relative", flexShrink: 0 }}>
              <div onClick={() => setSel("pr")} style={{
                width: 420, background: t.surface, borderRadius: 14,
                border: sel === "pr" ? `2px solid ${t.accent}` : `1px solid ${t.border}`,
                boxShadow: sel === "pr" ? `${t.shadowLg}, ${t.ring}` : t.shadow,
                overflow: "hidden", cursor: "pointer", display: "flex", flexDirection: "column",
                transition: "all 0.25s",
              }}>
                <div style={{
                  padding: "11px 14px", borderBottom: `1px solid ${t.border}`,
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                    <div style={{ width: 22, height: 22, borderRadius: 5, background: t.successBg, border: `1px solid ${t.success}30`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11 }}>⎇</div>
                    <div>
                      <div style={{ fontSize: 12.5, fontFamily: F.display, fontWeight: 700, color: t.text }}>PR #142 — Async Discount</div>
                      <div style={{ fontSize: 9.5, fontFamily: F.mono, color: t.textDim }}>checkout-service → agent/async-discount</div>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 5 }}>
                    <Pill color={t.success} small>open</Pill>
                    <InfoBtn k="prPreview" t={t} />
                  </div>
                </div>
                <div style={{ padding: "8px 14px", borderBottom: `1px solid ${t.border}`, background: t.accentSoft, fontSize: 11, fontFamily: F.body, color: t.text, lineHeight: 1.5 }}>
                  <strong>Summary:</strong> Converts <code style={{ background: t.surface, padding: "0 3px", borderRadius: 3, fontSize: 10, fontFamily: F.mono }}>processOrder</code> to async via <code style={{ background: t.surface, padding: "0 3px", borderRadius: 3, fontSize: 10, fontFamily: F.mono }}>asyncio.gather</code>.
                </div>
                <div style={{ flex: 1, overflow: "auto" }}>
                  <div style={{ padding: "6px 10px", background: t.surfaceAlt, borderBottom: `1px solid ${t.border}`, display: "flex", alignItems: "center" }}>
                    <span style={{ fontSize: 10.5, fontFamily: F.mono, color: t.textSec }}>src/orders.py</span>
                    <div style={{ marginLeft: "auto", display: "flex", gap: 6, fontSize: 10, fontFamily: F.mono }}>
                      <span style={{ color: t.success }}>+4</span><span style={{ color: t.writer }}>−2</span>
                    </div>
                  </div>
                  <div style={{ fontSize: 11, fontFamily: F.mono, lineHeight: 1.75 }}>
                    {DIFFS.map((l, i) => (
                      <div key={i} style={{
                        display: "flex",
                        background: l.ty === "a" ? t.diffAdd : l.ty === "d" ? t.diffDel : "transparent",
                        borderLeft: `3px solid ${l.ty === "a" ? t.success : l.ty === "d" ? t.writer : "transparent"}`,
                      }}>
                        {[0, 1].map(c => (
                          <span key={c} style={{
                            width: 28, textAlign: "right", padding: "0 4px", color: t.textDim, fontSize: 9.5,
                            userSelect: "none", borderRight: `1px solid ${t.border}`,
                            background: l.ty === "a" ? t.diffAddGutter : l.ty === "d" ? t.diffDelGutter : t.surfaceAlt,
                          }}>{l.n[c] || ""}</span>
                        ))}
                        <span style={{ padding: "0 6px", whiteSpace: "pre", color: l.ty === "a" ? t.diffAddText : l.ty === "d" ? t.diffDelText : t.text }}>
                          {l.ty === "a" ? "+" : l.ty === "d" ? "−" : " "} {l.t}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              {sel === "pr" && <div style={{ position: "absolute", bottom: -20, left: "50%", transform: "translateX(-50%)", padding: "2px 7px", background: t.accent, borderRadius: 4, fontSize: 9, fontFamily: F.mono, color: "#fff", fontWeight: 600 }}>420 × 500</div>}
            </div>

            {/* Trace Card */}
            <div style={{ position: "relative", flexShrink: 0 }}>
              <div onClick={() => setSel("trace")} style={{
                width: 360, background: t.surface, borderRadius: 14,
                border: sel === "trace" ? `2px solid ${t.accent}` : `1px solid ${t.border}`,
                boxShadow: sel === "trace" ? `${t.shadowLg}, ${t.ring}` : t.shadow,
                overflow: "hidden", cursor: "pointer", display: "flex", flexDirection: "column",
                transition: "all 0.25s",
              }}>
                <div style={{
                  padding: "11px 14px", borderBottom: `1px solid ${t.border}`,
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                }}>
                  <span style={{ fontSize: 12.5, fontFamily: F.display, fontWeight: 700, color: t.text }}>Langfuse Trace</span>
                  <div style={{ display: "flex", gap: 5 }}>
                    <span style={{ fontSize: 10, fontFamily: F.mono, color: t.textDim }}>run_7f3a</span>
                    <InfoBtn k="traceView" t={t} />
                  </div>
                </div>
                <div style={{
                  padding: "6px 14px", background: t.surfaceAlt, borderBottom: `1px solid ${t.border}`,
                  display: "flex", gap: 14, fontSize: 10.5, fontFamily: F.mono,
                }}>
                  <span><span style={{ color: t.textDim }}>tok </span><span style={{ color: t.text, fontWeight: 600 }}>24,831</span></span>
                  <span><span style={{ color: t.textDim }}>lat </span><span style={{ color: t.text, fontWeight: 600 }}>12.4s</span></span>
                  <span><span style={{ color: t.textDim }}>conf </span><span style={{ color: t.success, fontWeight: 600 }}>0.91</span></span>
                </div>
                <div style={{ flex: 1, overflow: "auto", padding: "4px 0" }}>
                  {SPANS.map((sp, i) => {
                    const c = t[sp.color];
                    return (
                      <div key={i} style={{
                        padding: "5px 14px", paddingLeft: 14 + (sp.indent || 0) * 14,
                        display: "flex", flexDirection: "column", gap: 2,
                        borderBottom: i < SPANS.length - 1 ? `1px solid ${t.border}20` : "none",
                      }}>
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                            {sp.indent > 0 && <span style={{ color: t.textDim, fontSize: 9, fontFamily: F.mono }}>└</span>}
                            <span style={{ width: 5, height: 5, borderRadius: "50%", background: c }} />
                            <span style={{ fontSize: 10.5, fontFamily: F.mono, fontWeight: 600, color: t.text }}>{sp.name}</span>
                          </div>
                          <span style={{ fontSize: 9.5, fontFamily: F.mono, color: t.textDim }}>{sp.dur}s</span>
                        </div>
                        <div style={{ width: "100%", height: 3.5, background: t.bg, borderRadius: 2, marginLeft: sp.indent ? 18 : 0 }}>
                          <div style={{ width: `${(sp.dur / maxDur) * 100}%`, height: "100%", borderRadius: 2, background: `linear-gradient(90deg, ${c}, ${c}80)` }} />
                        </div>
                        <div style={{ display: "flex", gap: 6, fontSize: 9, fontFamily: F.mono, color: t.textDim, marginLeft: sp.indent ? 18 : 0 }}>
                          <span>{sp.tokens} tok</span>
                          {sp.meta && <span style={{ color: c + "99" }}>{sp.meta}</span>}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
              {sel === "trace" && <div style={{ position: "absolute", bottom: -20, left: "50%", transform: "translateX(-50%)", padding: "2px 7px", background: t.accent, borderRadius: 4, fontSize: 9, fontFamily: F.mono, color: "#fff", fontWeight: 600 }}>360 × 500</div>}
            </div>
          </div>

          {/* Log overlay */}
          {log && (
            <div style={{
              position: "absolute", bottom: 0, left: 0, right: 0, height: 180,
              background: t.logBg, borderTop: `1px solid ${t.logBorder}`, zIndex: 30,
              display: "flex", flexDirection: "column", animation: "slideUp 0.25s ease",
            }}>
              <div style={{
                padding: "7px 14px", borderBottom: `1px solid ${t.logBorder}`,
                display: "flex", alignItems: "center", justifyContent: "space-between",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 10.5, fontFamily: F.mono, color: t.logDim }}>AGENT LOG</span>
                  <InfoBtn k="agentLog" t={t} />
                </div>
              </div>
              <div style={{ flex: 1, overflow: "auto", padding: "3px 14px", fontFamily: F.mono, fontSize: 10.5, lineHeight: 1.7 }}>
                {LOGS.map((l, i) => (
                  <div key={i} style={{
                    display: "flex", gap: 6,
                    color: l.lv === "warn" ? t.warning : l.lv === "ok" ? t.success : t.logText,
                    opacity: 0, animation: `fadeSlideIn 0.3s ease ${i * 0.04}s forwards`,
                  }}>
                    <span style={{ color: t.logDim, minWidth: 52 }}>{l.time}</span>
                    <span style={{
                      minWidth: 32, fontSize: 9, padding: "1px 4px", borderRadius: 3, textAlign: "center",
                      background: l.lv === "warn" ? `${t.warning}15` : l.lv === "ok" ? `${t.success}15` : `${t.logText}10`,
                    }}>{l.lv}</span>
                    <span>{l.msg}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Prompt bar */}
      <div style={{
        padding: "9px 20px 10px", borderTop: `1px solid ${t.border}`,
        background: t.surface, flexShrink: 0,
        display: "flex", flexDirection: "column", gap: 5,
      }}>
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: "9px 12px", background: focused ? t.surface : t.bg,
          borderRadius: 12, border: `1.5px solid ${focused ? t.accent : t.border}`,
          boxShadow: focused ? t.ring : "none", transition: "all 0.2s",
        }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 3, padding: "2px 8px",
            background: t.accentLight, borderRadius: 6, fontSize: 10, fontFamily: F.body,
            color: t.accent, fontWeight: 600, flexShrink: 0,
          }}>
            <span style={{
              width: 12, height: 12, borderRadius: 3, display: "inline-flex",
              alignItems: "center", justifyContent: "center",
              background: `linear-gradient(135deg, ${t.accent}, ${t.research})`,
              fontSize: 6, color: "#fff", fontWeight: 700,
            }}>R</span>
            PR #142 <span style={{ cursor: "pointer", opacity: 0.5, fontSize: 9, marginLeft: 1 }}>✕</span>
          </div>
          <input value={input} onChange={e => setInput(e.target.value)}
            onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
            placeholder="What would you like to change or create?"
            style={{ flex: 1, background: "transparent", border: "none", outline: "none", color: t.text, fontSize: 13, fontFamily: F.body }}
          />
          <InfoBtn k="promptBar" t={t} />
          <button style={{
            width: 28, height: 28, borderRadius: 7, background: input.trim() ? t.accent : t.bg,
            border: "none", cursor: "pointer", display: "flex", alignItems: "center",
            justifyContent: "center", color: input.trim() ? "#fff" : t.textDim, fontSize: 13,
            transition: "all 0.2s",
          }}>↑</button>
        </div>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 10, fontFamily: F.mono, color: t.textDim, gap: 5,
        }}>
          <span>{repos.length} repos</span>·<span>{totalSymbols.toLocaleString()} symbols</span>·
          <span style={{ color: t.success }}>● Langfuse</span>
        </div>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
//  ROOT APP — page routing
// ═══════════════════════════════════════════════════════════════════════════

export default function App() {
  const [mode, setMode] = useState("dark");
  const [page, setPage] = useState("signin"); // signin | repos | canvas
  const [repos, setRepos] = useState([]);
  const [fade, setFade] = useState(false);
  const t = themes[mode];
  const isDark = mode === "dark";

  const navigate = (to, data) => {
    setFade(true);
    setTimeout(() => {
      if (data) setRepos(data);
      setPage(to);
      setFade(false);
    }, 350);
  };

  return (
    <div style={{
      opacity: fade ? 0 : 1, transform: fade ? "scale(0.98)" : "scale(1)",
      transition: "all 0.35s cubic-bezier(0.4,0,0.2,1)",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&family=Space+Grotesk:wght@400;500;600;700&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 5px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: ${t.border}; border-radius: 4px; }
        input::placeholder { color: ${t.textDim}; }
        @keyframes slideUp { from { transform: translateY(100%); } to { transform: translateY(0); } }
        @keyframes fadeSlideIn { from { opacity: 0; transform: translateX(-4px); } to { opacity: 1; transform: translateX(0); } }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>

      {page === "signin" && (
        <SignInPage t={t} isDark={isDark} mode={mode} setMode={setMode}
          onSignIn={() => navigate("repos")} />
      )}
      {page === "repos" && (
        <RepoSelectPage t={t} isDark={isDark} mode={mode} setMode={setMode}
          onContinue={(selectedRepos) => navigate("canvas", selectedRepos)} />
      )}
      {page === "canvas" && (
        <MainCanvas t={t} isDark={isDark} repos={repos} mode={mode} setMode={setMode}
          onSignOut={() => navigate("signin")} />
      )}
    </div>
  );
}
