// Moving between pages (tiny hash router: #/ New, #/runs, #/run/<id>, #/me/<tab>).
// Kept out of App.jsx so Vite can live-update App without reloading the page.
export function go(path) {
  window.location.hash = "/" + path.replace(/^\/+/, "");
}
