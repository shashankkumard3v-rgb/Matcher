import { useEffect, useState } from "react";
import { api, pending } from "./api.js";
import Upload from "./Upload.jsx";
import RunView from "./RunView.jsx";
import RunsList from "./RunsList.jsx";
import MyInfo from "./MyInfo.jsx";
import Login from "./Login.jsx";
import { useTasks } from "./store.js";

// Tiny hash router: #/ (upload), #/runs, #/run/<id>
function parseHash() {
  const h = window.location.hash.replace(/^#\/?/, "");
  if (h.startsWith("run/")) return { page: "run", id: decodeURIComponent(h.slice(4)) };
  if (h === "runs") return { page: "runs" };
  if (h.startsWith("me")) return { page: "me", tab: h.split("/")[1] || "resumes" };
  return { page: "upload" };
}


function ProviderBadge() {
  const [info, setInfo] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    api.skills().then(setInfo).catch((e) => setErr(e.message));
  }, []);
  if (err) return <span className="badge badge-bad" title={err}>Server offline</span>;
  if (!info) return <span className="badge">…</span>;
  const none = info.provider.startsWith("none");
  return (
    <span className={`badge ${none ? "badge-bad" : "badge-ok"}`} title={none ? info.provider : info.how}>
      {none ? "No AI key" : <><span className="hide-xs">AI: </span>{info.provider}</>}
    </span>
  );
}

// Auto-refresh: when the app is rebuilt (or updated files are copied in), reload the page.
// If you're typing in a box, show a banner instead so nothing you typed is lost.
function useAutoRefresh() {
  const [stale, setStale] = useState(false);
  useEffect(() => {
    let known = null;
    let stop = false;
    let quietUntil = 0; // an older server without /api/app-version: ask only once a minute
    async function check() {
      if (stop || document.hidden || Date.now() < quietUntil) return;
      try {
        const res = await fetch("/api/app-version", { cache: "no-store" });
        if (res.status === 404) {
          quietUntil = Date.now() + 60000;
          return;
        }
        const { version } = await res.json();
        if (!version) return;
        if (known === null) known = version;
        else if (version !== known) {
          const el = document.activeElement;
          const typing = el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT") && el.value;
          const busy = pending.count > 0 || document.querySelector(".spinner, .spinner-sm");
          if (typing || busy) setStale(true);   // never lose what you typed or a running AI step
          else window.location.reload();
        }
      } catch {
        /* server restarting; try again next time */
      }
    }
    check();
    const t = setInterval(check, 3000);
    document.addEventListener("visibilitychange", check);
    return () => {
      stop = true;
      clearInterval(t);
      document.removeEventListener("visibilitychange", check);
    };
  }, []);
  return { stale };
}

// What's still running (it keeps going when you change page); tap to go back to it.
function Working() {
  const tasks = useTasks();
  if (!tasks.length) return null;
  const t = tasks.sort((a, b) => a.started - b.started)[0];
  const more = tasks.length > 1 ? ` +${tasks.length - 1}` : "";
  return (
    <a className="badge working" href={t.href} title={tasks.map((x) => x.label).join("\n")} aria-live="polite">
      <span className="spinner spinner-xs" aria-hidden="true" />
      <span className="working-label">{t.label.replace(/\s*\(.*?\)/g, "").replace(/…+$/, "")}…</span>{more}
    </a>
  );
}

function UserMenu({ user, onSignOut }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (!e.target.closest(".user-menu")) setOpen(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);
  const initial = (user.name || user.email || "?").trim()[0].toUpperCase();
  return (
    <div className="user-menu">
      <button className="avatar" onClick={() => setOpen(!open)} aria-label="Account" title={user.email}>
        {user.picture ? <img src={user.picture} alt="" referrerPolicy="no-referrer" /> : initial}
      </button>
      {open && (
        <div className="user-pop card">
          <div className="strong">{user.name}</div>
          <div className="small muted">{user.email}</div>
          <button className="btn btn-sm" onClick={onSignOut}>Sign out</button>
        </div>
      )}
    </div>
  );
}

const ICONS = {
  new: "M12 5v14M5 12h14",
  runs: "M4 6h16M4 12h16M4 18h10",
  me: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 8a7 7 0 0 1 14 0",
};

function NavLinks({ page }) {
  const items = [
    ["new", "#/", "New", page === "upload"],
    ["runs", "#/runs", "Runs", page === "runs" || page === "run"],
    ["me", "#/me", "My info", page === "me"],
  ];
  return items.map(([k, href, label, on]) => (
    <a key={k} href={href} className={on ? "active" : ""} aria-current={on ? "page" : undefined}>
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="none" stroke="currentColor"
           strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d={ICONS[k]} /></svg>
      <span>{label}</span>
    </a>
  ));
}

export default function App() {
  const { stale } = useAutoRefresh();
  const [user, setUser] = useState(undefined); // undefined = checking, null = signed out
  const [moved, setMoved] = useState([]);

  useEffect(() => {
    api.me().then((r) => setUser(r.user)).catch(() => setUser(null));
    const out = () => setUser(null);
    window.addEventListener("matcher:signed-out", out);
    return () => window.removeEventListener("matcher:signed-out", out);
  }, []);

  const [route, setRoute] = useState(parseHash());
  useEffect(() => {
    const onHash = () => {
      setRoute(parseHash());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  async function signOut() {
    try { await api.logout(); } catch { /* signed out anyway */ }
    window.google?.accounts?.id?.disableAutoSelect?.();
    setUser(null);
  }

  if (user === undefined) return <div className="boot"><div className="spinner" aria-label="Loading" /></div>;
  if (user === null) {
    return (
      <div className="app">
        <Login onSignedIn={(u, m) => { setUser(u); setMoved(m); }} />
      </div>
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/">
          <img src="/icon-192.png" alt="" width="28" height="28" />
          Matcher
        </a>
        <nav className="topnav"><NavLinks page={route.page} /></nav>
        <span className="topbar-gap" />
        <Working />
        <ProviderBadge />
        <UserMenu user={user} onSignOut={signOut} />
      </header>
      {moved.length > 0 && (
        <div className="alert alert-ok row wrap update-banner">
          Your earlier runs and My info notes are now in your account ({moved.length} item(s) moved).
          <button className="btn btn-sm" onClick={() => setMoved([])}>OK</button>
        </div>
      )}
      {stale && (
        <div className="alert alert-info row wrap update-banner">
          Matcher was updated.
          <button className="btn btn-primary btn-sm" onClick={() => window.location.reload()}>Reload now</button>
          <span className="small muted">(save what you're typing first)</span>
        </div>
      )}
      <main key={route.page === "run" ? `run/${route.id}` : route.page} className="page">
        {route.page === "upload" && <Upload />}
        {route.page === "runs" && <RunsList />}
        {route.page === "run" && <RunView key={route.id} runId={route.id} />}
        {route.page === "me" && <MyInfo tab={route.tab} />}
      </main>
      <nav className="bottomnav" aria-label="Main"><NavLinks page={route.page} /></nav>
    </div>
  );
}
