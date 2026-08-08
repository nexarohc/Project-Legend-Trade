import React from "react";
import ReactDOM from "react-dom/client";

/*
 * Typefaces are bundled, not fetched from a CDN.
 *
 * The page previously linked fonts.googleapis.com, and the request simply
 * failed — every visitor got a browser default while the CSS confidently asked
 * for Space Grotesk. That is invisible in code review and obvious on screen,
 * which is exactly the failure mode a third-party font host produces: it works
 * on the developer's machine and silently does not on a restricted network, a
 * corporate proxy, or in a country where Google is blocked.
 *
 * Self-hosting also settles three other things at once:
 *
 * 1. **Privacy.** PRIVACY.md states this site makes no third-party requests and
 *    loads no external scripts. A Google Fonts link contradicted that in the
 *    document's own first section — it discloses the visitor's IP to a third
 *    party on every page load, which German courts have already found to be a
 *    GDPR problem. The claim is now true.
 * 2. **Render blocking.** A stylesheet on another origin blocks first paint
 *    behind a DNS lookup, a TLS handshake and a round trip.
 * 3. **Determinism.** The bundle contains the exact font binaries. It cannot
 *    render differently tomorrow because an upstream changed.
 *
 * Variable faces for Inter and JetBrains Mono — one file covers every weight.
 * Space Grotesk has no variable build on this package, so only the three
 * weights actually used are imported rather than all seven.
 */
import "@fontsource-variable/inter";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/600.css";
import "@fontsource/space-grotesk/700.css";
import "@fontsource-variable/jetbrains-mono";

import App from "./App.jsx";
import { initTheme } from "./lib/theme.js";
import "./index.css";

// Before the first render, so the page never paints light and then snaps to
// dark. A flash of the wrong theme is the one bug in a theme switcher that
// every visitor notices.
initTheme();

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
