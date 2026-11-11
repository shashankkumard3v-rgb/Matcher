import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const GIS_SRC = "https://accounts.google.com/gsi/client";

function loadGoogle() {
  if (window.google?.accounts?.id) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = GIS_SRC;
    s.async = true;
    s.onload = resolve;
    s.onerror = () => reject(new Error("Couldn't load Google sign-in. Check your internet connection."));
    document.head.appendChild(s);
  });
}

export default function Login({ onSignedIn }) {
  const [cfg, setCfg] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const button = useRef(null);

  useEffect(() => {
    api.authConfig().then(setCfg).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!cfg?.ready) return;
    let gone = false;
    loadGoogle()
      .then(() => {
        if (gone || !button.current) return;
        window.google.accounts.id.initialize({
          client_id: cfg.google_client_id,
          callback: async ({ credential }) => {
            setBusy(true);
            setError(null);
            try {
              const res = await api.signInGoogle(credential);
              onSignedIn(res.user, res.moved || []);
            } catch (e) {
              setError(e.message);
            } finally {
              setBusy(false);
            }
          },
        });
        window.google.accounts.id.renderButton(button.current, {
          theme: window.matchMedia("(prefers-color-scheme: dark)").matches ? "filled_black" : "outline",
          size: "large", text: "signin_with", shape: "pill", width: 280,
        });
      })
      .catch((e) => setError(e.message));
    return () => { gone = true; };
  }, [cfg, onSignedIn]);

  const local = ["localhost", "127.0.0.1"].includes(window.location.hostname) || window.location.protocol === "https:";

  return (
    <section className="login">
      <div className="card login-card">
        <img src="/icon-192.png" alt="" width="56" height="56" />
        <h1>Sign in to Matcher</h1>
        <p className="muted">Your runs and My info notes are private to your Google account.</p>
        {!cfg && !error && <div className="muted small">Loading…</div>}
        {cfg && cfg.missing.length > 0 && (
          <div className="alert alert-warn small">
            Sign-in isn't set up yet. Add {cfg.missing.join(" and ")} to <code>backend/.env</code>, then restart
            the server. Steps are in the README ("Sign in with Google").
          </div>
        )}
        {cfg?.db_error && (
          <div className="alert alert-bad small">
            <strong>Database:</strong> {cfg.db_error}
            <div className="muted">Fix it in <code>backend/.env</code> or MongoDB Atlas, restart the server, then reload this page.</div>
          </div>
        )}
        {cfg?.ready && !local && (
          <div className="alert alert-warn small">
            Google sign-in only works on <strong>http://localhost</strong> addresses (or https). You opened{" "}
            <code>{window.location.origin}</code>; use http://localhost:8000 instead.
          </div>
        )}
        <div ref={button} className="gbutton" />
        {cfg?.ready && local && (
          <div className="small muted">
            Google error "origin_mismatch"? In Google Cloud &gt; Credentials &gt; your OAuth client, add{" "}
            <code>{window.location.origin}</code> to <em>Authorized JavaScript origins</em>, then wait a few minutes.
          </div>
        )}
        {busy && <div className="muted small">Signing in…</div>}
        {error && <div className="alert alert-bad small">{error}</div>}
      </div>
    </section>
  );
}
