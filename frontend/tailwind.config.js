/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          bg: "#0b0d12",
          panel: "#151822",
          accent: "#7c5cff",
          accent2: "#22d3c7",
        },
        // Marketing-surface palette. Light, enterprise, no neon anywhere — a
        // deliberately different register from the terminal below, which is a
        // dense dark instrument. A trading application is dark because a trader
        // stares at it for eight hours; a site that has to earn trust in eight
        // seconds is not the same problem and should not borrow that answer.
        ent: {
          navy: "#0F172A",
          slate: "#1E293B",
          blue: "#2563EB",
          "blue-light": "#3B82F6",
          sky: "#38BDF8",
          surface: "#F8FAFC",
          border: "#E2E8F0",
          ink: "#111827",
          muted: "#64748B",
          success: "#10B981",
          warning: "#F59E0B",
          error: "#EF4444",
        },
        // Terminal palette. Deliberately desaturated so the only saturated
        // colour on screen is price direction — the eye should go straight to
        // the market, not to the chrome.
        term: {
          bg: "#07090e",
          surface: "#0e1118",
          panel: "#141823",
          raised: "#1b2030",
          border: "#232a3a",
          text: "#e6e9f0",
          muted: "#8b93a7",
          dim: "#5a6377",
          up: "#26a69a",
          down: "#ef5350",
          warn: "#f0a500",
          info: "#4a9eff",
        },
      },
      fontFamily: {
        // `sans` was never defined before, so every non-mono surface fell back
        // to the browser default — the single biggest reason the UI looked
        // unfinished. Inter is the workhorse; Space Grotesk carries display
        // type only.
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        display: ["'Space Grotesk'", "Inter", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "'SF Mono'", "Menlo", "Consolas", "monospace"],
      },
      keyframes: {
        // Landing-page motion. Kept to opacity and transform only: those are
        // the two properties the compositor can animate without laying the
        // page out again, so none of this costs a frame on a slow machine.
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(18px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        drift: {
          "0%, 100%": { transform: "translate3d(0, 0, 0) scale(1)" },
          "50%": { transform: "translate3d(0, -22px, 0) scale(1.06)" },
        },
        ticker: {
          "0%": { transform: "translateX(0)" },
          "100%": { transform: "translateX(-50%)" },
        },
        "pulse-dot": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
        "grow-bar": {
          "0%": { transform: "scaleY(0.15)" },
          "100%": { transform: "scaleY(1)" },
        },
        // A light bar sweeping down a panel edge, like a sensor pass. Used
        // sparingly — one or two on screen at a time, or it stops reading as
        // instrumentation and starts reading as a screensaver.
        scan: {
          "0%": { transform: "translateY(-100%)", opacity: "0" },
          "10%, 90%": { opacity: "1" },
          "100%": { transform: "translateY(1000%)", opacity: "0" },
        },
        // Rotates a conic-gradient border. `--angle` is a registered property
        // (see index.css) because plain custom properties cannot be animated.
        "spin-border": {
          to: { "--angle": "360deg" },
        },
        "glow-pulse": {
          "0%, 100%": { opacity: "0.35" },
          "50%": { opacity: "0.75" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-10px)" },
        },
        // The readout blink on a live figure. Deliberately brief: a value that
        // flashes constantly is noise, one that flashes on change is a signal.
        flare: {
          "0%": { opacity: "0", transform: "scaleX(0.2)" },
          "40%": { opacity: "1" },
          "100%": { opacity: "0", transform: "scaleX(1)" },
        },
        // A pulse travelling along an architecture connector. `stroke-dashoffset`
        // is the one non-compositor property used anywhere here; it is confined
        // to a handful of short SVG paths in a single diagram, which is well
        // inside what a browser handles without dropping frames.
        "flow-down": {
          "0%": { strokeDashoffset: "28" },
          "100%": { strokeDashoffset: "0" },
        },
        "sheen": {
          "0%": { transform: "translateX(-120%)" },
          "100%": { transform: "translateX(220%)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.7s cubic-bezier(0.16, 1, 0.3, 1) both",
        "fade-in": "fade-in 0.9s ease-out both",
        drift: "drift 14s ease-in-out infinite",
        "drift-slow": "drift 22s ease-in-out infinite",
        ticker: "ticker 40s linear infinite",
        "ticker-slow": "ticker 70s linear infinite",
        "pulse-dot": "pulse-dot 1.8s ease-in-out infinite",
        "grow-bar": "grow-bar 0.8s cubic-bezier(0.16, 1, 0.3, 1) both",
        scan: "scan 7s linear infinite",
        "spin-border": "spin-border 6s linear infinite",
        "glow-pulse": "glow-pulse 4s ease-in-out infinite",
        float: "float 6s ease-in-out infinite",
        flare: "flare 1.1s ease-out",
        "flow-down": "flow-down 1.6s linear infinite",
        sheen: "sheen 1.1s ease-out",
      },
    },
  },
  plugins: [],
};
