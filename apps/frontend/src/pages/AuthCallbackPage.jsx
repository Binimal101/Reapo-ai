import { useEffect, useRef, useState } from "react";
import { finishOAuthCallback, storeSessionToken } from "../lib/authApi.js";

export default function AuthCallbackPage({ onSuccess }) {
  const [status, setStatus] = useState("Processing GitHub callback...");
  const [error, setError] = useState("");
  const hasProcessedRef = useRef(false);

  useEffect(() => {
    if (hasProcessedRef.current) {
      return;
    }
    hasProcessedRef.current = true;

    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    const flow = state?.includes("signup") ? "signup" : "signin";
    const redirectUri = `${window.location.origin}/oauth/callback`;

    if (!code) {
      setError("Missing OAuth code in callback URL.");
      return;
    }

    finishOAuthCallback({ flow, code, state, redirectUri })
      .then((payload) => {
        storeSessionToken(payload.session_token);
        setStatus("Connected. Entering app...");
        setTimeout(() => {
          onSuccess(payload.user?.user_id || "");
        }, 600);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "OAuth callback failed");
      });
  }, [onSuccess]);

  return (
    <section className="landing" aria-label="OAuth callback status">
      <div className="landing-card compact">
        <h1>GitHub Callback</h1>
        {!error ? <p>{status}</p> : <p className="error-text">{error}</p>}
      </div>
    </section>
  );
}
