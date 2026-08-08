import { useEffect, useState } from "react";
import {
  getThemePreference,
  onThemeChange,
  resolveTheme,
  setThemePreference,
} from "../lib/theme.js";

/**
 * A sliding light/dark switch.
 *
 * Two visible positions rather than three, even though the underlying
 * preference has three states. "System" is a sensible default but a poor
 * control: a switch showing an option most people have never heard of, next to
 * two they understand instantly, costs more than it gives. So the track shows
 * what is currently on screen, and touching it pins that choice. Someone who
 * wants to go back to following the OS can clear site data, which is exactly
 * how often that comes up.
 *
 * Rendered as a real `role="switch"` button so it is reachable by keyboard and
 * announced correctly, rather than a div with an onClick.
 */
export default function ThemeToggle({ onDark = false }) {
  const [resolved, setResolved] = useState(() => resolveTheme());

  useEffect(() => {
    setResolved(resolveTheme());
    return onThemeChange((_preference, next) => setResolved(next));
  }, []);

  const dark = resolved === "dark";

  // On the dark hero band the switch sits on near-black whatever the theme, so
  // it needs its own light-on-dark treatment; elsewhere it follows the tokens.
  const track = onDark
    ? "bg-white/10 border-white/20 hover:bg-white/15"
    : "bg-ent-surface border-ent-border hover:border-ent-muted/50";

  return (
    <button
      type="button"
      role="switch"
      aria-checked={dark}
      aria-label={`Switch to ${dark ? "light" : "dark"} theme`}
      title={`Switch to ${dark ? "light" : "dark"} theme`}
      onClick={() => setThemePreference(dark ? "light" : "dark")}
      className={`relative inline-flex h-8 w-[3.75rem] shrink-0 items-center rounded-full
                  border transition-colors duration-200 ${track}`}
    >
      {/* Both icons are always painted, at fixed positions. Swapping a single
          icon in and out makes the control read as a button that does something
          unknown; showing sun and moon with a knob between them reads as a
          two-position switch on sight. */}
      <SunIcon className={`absolute left-[0.4rem] h-3.5 w-3.5 transition-opacity duration-200
                           ${dark ? "opacity-35" : "opacity-90"}
                           ${onDark ? "text-white" : "text-ent-warning"}`} />
      <MoonIcon className={`absolute right-[0.4rem] h-3.5 w-3.5 transition-opacity duration-200
                            ${dark ? "opacity-95" : "opacity-35"}
                            ${onDark ? "text-white" : "text-ent-muted"}`} />
      <span
        className={`pointer-events-none relative z-10 h-6 w-6 rounded-full shadow-sm
                    transition-transform duration-200 ease-out
                    ${dark ? "translate-x-[1.9rem] bg-slate-200" : "translate-x-[0.25rem] bg-white"}`}
      />
    </button>
  );
}

function SunIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
    </svg>
  );
}

function MoonIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
    </svg>
  );
}
