// Moving between pages (tiny hash router: #/ New, #/runs, #/run/<id>, #/me/<tab>).
// Kept out of App.jsx so Vite can live-update App without reloading the page.
export function go(path) {
  window.location.hash = "/" + path.replace(/^\/+/, "");
}

// How a JD requirement reads on screen:
//   "Java or Go or C++"       -> "Java / Go / C++ (any one)"   or, when you have one, "Java (one of Java / Go / C++)"
//   "2 of Java or Go or C++"  -> "any 2 of Java / Go / C++"
export function skillLabel(skill, matched) {
  if (!skill) return skill;
  const m = /^(\d+) of (.+)$/i.exec(skill);
  const need = m ? Number(m[1]) : 1;
  const rest = m ? m[2] : skill;
  if (!/\sor\s/i.test(rest)) return skill;
  const opts = rest.split(/\s+or\s+/i).join(" / ");
  if (need > 1) return matched ? `${matched} (need ${need} of ${opts})` : `any ${need} of ${opts}`;
  return matched ? `${matched} (one of ${opts})` : `${opts} (any one)`;
}
