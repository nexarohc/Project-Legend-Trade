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
      },
      animation: {
        "fade-up": "fade-up 0.7s cubic-bezier(0.16, 1, 0.3, 1) both",
        "fade-in": "fade-in 0.9s ease-out both",
        drift: "drift 14s ease-in-out infinite",
        "drift-slow": "drift 22s ease-in-out infinite",
        ticker: "ticker 40s linear infinite",
        "pulse-dot": "pulse-dot 1.8s ease-in-out infinite",
        "grow-bar": "grow-bar 0.8s cubic-bezier(0.16, 1, 0.3, 1) both",
      },
    },
  },
  plugins: [],
};
