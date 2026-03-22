import { useState } from "react";

export default function AuthPage({ mode = "signup", busyFlow, onStartFlow }) {
  const [error, setError] = useState("");
  const isSignin = mode === "signin";

  const begin = async (flow) => {
    setError("");
    try {
      await onStartFlow(flow);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to connect to GitHub");
    }
  };

  return (
    <section className="auth-shell" aria-label="Authentication">
      <article className="auth-card">
        <p className="hero-kicker">Secure GitHub Access</p>
        <h1>{isSignin ? "Welcome back." : "Create your Reapo workspace."}</h1>
        <p>
          {isSignin
            ? "Sign in with GitHub to continue inside your projects workspace."
            : "Sign up with GitHub to start orchestrating coding agents across your repositories."}
        </p>

        <div className="auth-actions">
          <button
            type="button"
            className="cta primary"
            onClick={() => begin(isSignin ? "signin" : "signup")}
            disabled={busyFlow !== null}
          >
            {busyFlow === (isSignin ? "signin" : "signup")
              ? "Redirecting..."
              : isSignin
                ? "Sign in with GitHub"
                : "Sign up with GitHub"}
          </button>
          <button
            type="button"
            className="cta secondary"
            onClick={() => begin(isSignin ? "signup" : "signin")}
            disabled={busyFlow !== null}
          >
            {busyFlow === (isSignin ? "signup" : "signin")
              ? "Redirecting..."
              : isSignin
                ? "Need an account? Sign up"
                : "Already have an account? Sign in"}
          </button>
        </div>

        {error ? <p className="error-text">{error}</p> : null}
      </article>
    </section>
  );
}
