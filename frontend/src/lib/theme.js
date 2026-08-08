/**
 * Light/dark theme, stored per browser.
 *
 * Three states, not two. "system" is the default and follows the OS, which
 * matters because a visitor who has set their machine to dark at night has
 * already expressed a preference and should not have to express it again here.
 * Choosing light or dark explicitly pins it and stops following.
 *
 * The class goes on `<html>` rather than `<body>` so that the element painting
 * the page background is itself themed — otherwise the overscroll area keeps
 * the browser default and flashes white on every bounce scroll in dark mode.
 */

const STORAGE_KEY = "legend.theme";
const VALID = new Set(["light", "dark", "system"]);

const listeners = new Set();

/** The OS-level preference, for when the stored choice is "system". */
function systemPrefersDark() {
  return typeof window !== "undefined"
    && window.matchMedia?.("(prefers-color-scheme: dark)").matches === true;
}

export function getThemePreference() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return VALID.has(stored) ? stored : "system";
  } catch {
    return "system";   // private browsing, or storage disabled
  }
}

/** What is actually on screen: "light" or "dark", never "system". */
export function resolveTheme(preference = getThemePreference()) {
  if (preference === "system") return systemPrefersDark() ? "dark" : "light";
  return preference;
}

export function applyTheme(preference = getThemePreference()) {
  const resolved = resolveTheme(preference);
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  // Tells the browser to theme its own furniture — form controls, scrollbars
  // and the like — which CSS variables cannot reach.
  root.style.colorScheme = resolved;
  return resolved;
}

export function setThemePreference(preference) {
  const next = VALID.has(preference) ? preference : "system";
  try {
    if (next === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* not persisted, but still applied for this page */
  }
  const resolved = applyTheme(next);
  listeners.forEach((fn) => fn(next, resolved));
  return resolved;
}

export function onThemeChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Call once at startup.
 *
 * Also subscribes to OS changes, so a machine that switches to dark at sunset
 * updates an open tab — but only while the preference is still "system", since
 * an explicit choice should not be overridden by the OS.
 */
export function initTheme() {
  applyTheme();

  const query = window.matchMedia?.("(prefers-color-scheme: dark)");
  query?.addEventListener?.("change", () => {
    if (getThemePreference() === "system") {
      const resolved = applyTheme("system");
      listeners.forEach((fn) => fn("system", resolved));
    }
  });
}
