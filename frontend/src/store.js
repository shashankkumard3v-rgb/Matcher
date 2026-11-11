// App-wide state that survives changing pages.
//
// A page's own React state disappears when you leave the page, so a slow AI step that finishes
// while you're elsewhere had nowhere to put its result (it looked like it "stopped"). Values kept
// here live as long as the browser tab, and work started from a page keeps writing into them.
import { useEffect, useReducer } from "react";

const data = new Map();
const subs = new Map(); // key -> Set of re-render callbacks
const ANY = "*";

function notify(key) {
  for (const k of [key, ANY]) for (const f of subs.get(k) || []) f();
}

export function get(key, init) {
  return data.has(key) ? data.get(key) : init;
}

export function set(key, value) {
  const v = typeof value === "function" ? value(data.get(key)) : value;
  data.set(key, v);
  notify(key);
}

function useSubscribe(key) {
  const [, force] = useReducer((x) => x + 1, 0);
  useEffect(() => {
    if (!subs.has(key)) subs.set(key, new Set());
    subs.get(key).add(force);
    return () => subs.get(key).delete(force);
  }, [key]);
}

/** Like useState, but the value outlives the page (keyed, shared by everyone using the key). */
export function useKept(key, init) {
  useSubscribe(key);
  return [get(key, init), (v) => set(key, typeof v === "function" ? (old) => v(old === undefined ? init : old) : v)];
}

/** Bump a counter: pages showing that data reload it (e.g. "resumes" after a save). */
export function bump(key) {
  set(`changed:${key}`, (n) => (n || 0) + 1);
}
export function useChanged(key) {
  return useKept(`changed:${key}`, 0)[0];
}

// ---------- running work, shown in the top bar ----------

const TASKS = "tasks";

/** Runs fn as a tracked task: listed in the top bar (label, link back) until it finishes. */
export async function track(id, label, href, fn) {
  set(TASKS, (t) => ({ ...(t || {}), [id]: { id, label, href, started: Date.now() } }));
  try {
    return await fn();
  } finally {
    set(TASKS, (t) => {
      const n = { ...(t || {}) };
      delete n[id];
      return n;
    });
  }
}

export function useTasks() {
  useSubscribe(TASKS);
  return Object.values(get(TASKS, {}));
}
