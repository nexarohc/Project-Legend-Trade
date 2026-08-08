import { useEffect, useRef, useState } from "react";

/** True when the visitor has asked the OS for less motion. */
export function prefersReducedMotion() {
  return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
}

/**
 * Reveal-on-scroll. One observer per element, unhooked once it has fired.
 *
 * Falls back to "visible" when IntersectionObserver is missing rather than
 * leaving the page permanently blank — an old browser should see a static page,
 * not an empty one.
 */
export function useReveal() {
  const ref = useRef(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    if (typeof IntersectionObserver === "undefined") {
      setShown(true);
      return undefined;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true);
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -10% 0px", threshold: 0.08 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return [ref, shown];
}

export function Reveal({ children, delay = 0, className = "" }) {
  const [ref, shown] = useReveal();
  return (
    <div
      ref={ref}
      className={`${className} transition-all duration-700 ease-out ${
        shown ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"
      }`}
      style={{ transitionDelay: `${delay}ms` }}
    >
      {children}
    </div>
  );
}

/**
 * Headline that decodes from noise into its final text.
 *
 * The effect earns its place on *this* product: the whole pitch is that the
 * platform resolves noise into a legible answer, and the headline literally
 * doing that is the argument in miniature rather than an unrelated flourish.
 *
 * Accessibility: while it scrambles, a visually-hidden copy of the real string
 * carries the meaning and the noisy copy is `aria-hidden`. Once it settles the
 * hidden copy is removed and the real text is exposed directly, so the finished
 * page contains the headline exactly once — a permanent second copy would show
 * up doubled in `innerText`, in copy-paste, and to anything extracting text.
 * Under reduced-motion it renders the final text immediately and never animates.
 */
export function Scramble({ text, className = "", delay = 0, speed = 28 }) {
  const reduced = prefersReducedMotion();
  const [display, setDisplay] = useState(() => (reduced ? text : ""));
  // While scrambling, a visually-hidden copy of the real string carries the
  // meaning for assistive tech. It is *removed* once the animation settles, so
  // the finished page has exactly one copy of the headline — otherwise
  // `innerText`, copy-paste and any text extractor see it twice.
  const [settled, setSettled] = useState(reduced);

  useEffect(() => {
    if (prefersReducedMotion()) {
      setDisplay(text);
      setSettled(true);
      return undefined;
    }

    const GLYPHS = "01<>/\\|_-=+*#%$&ABCDEFGHJKLMNPQRSTUVWXYZ";
    let frame = 0;
    let interval = 0;
    let timeout = 0;

    const run = () => {
      interval = window.setInterval(() => {
        frame += 1;
        // Each character settles after a staggered number of frames, so the
        // string resolves left to right rather than all at once.
        const next = text
          .split("")
          .map((character, index) => {
            if (character === " ") return " ";
            const settleAt = index * 1.6 + 6;
            if (frame >= settleAt) return character;
            return GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
          })
          .join("");
        setDisplay(next);
        if (frame >= text.length * 1.6 + 6) {
          window.clearInterval(interval);
          setDisplay(text);
          setSettled(true);
        }
      }, speed);
    };

    timeout = window.setTimeout(run, delay);
    return () => {
      window.clearTimeout(timeout);
      window.clearInterval(interval);
    };
  }, [text, delay, speed]);

  return (
    <span className={className}>
      {!settled && <span className="sr-only">{text}</span>}
      <span aria-hidden={!settled}>{display || " "}</span>
    </span>
  );
}

/**
 * A radial glow that follows the pointer.
 *
 * Written straight to a CSS custom property rather than through React state:
 * a `setState` per `mousemove` re-renders the subtree dozens of times a second
 * for a purely visual effect. This touches one element's style and nothing
 * else. Skipped entirely on touch devices, where there is no cursor to follow.
 */
export function Spotlight() {
  const ref = useRef(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    if (prefersReducedMotion()) return undefined;
    if (!window.matchMedia?.("(pointer: fine)").matches) return undefined;

    let raf = 0;
    let x = 0;
    let y = 0;
    let queued = false;

    const apply = () => {
      queued = false;
      node.style.setProperty("--x", `${x}px`);
      node.style.setProperty("--y", `${y}px`);
      node.style.opacity = "1";
    };

    const onMove = (event) => {
      x = event.clientX;
      y = event.clientY;
      if (!queued) {
        queued = true;
        raf = requestAnimationFrame(apply);
      }
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      window.removeEventListener("pointermove", onMove);
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <div
      ref={ref}
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 z-10 opacity-0 transition-opacity duration-700"
      style={{
        background:
          "radial-gradient(340px circle at var(--x, 50%) var(--y, 40%), rgba(34,211,199,0.055), transparent 70%)",
      }}
    />
  );
}

/**
 * A figure that counts up when it scrolls into view.
 *
 * Eased rather than linear so it decelerates into the final value, which reads
 * as a measurement settling instead of a number spinning. Under reduced motion
 * the final value is shown immediately.
 */
export function Counter({ value, className = "" }) {
  const [ref, shown] = useReveal();
  const [current, setCurrent] = useState(0);

  useEffect(() => {
    if (!shown) return undefined;
    if (prefersReducedMotion()) {
      setCurrent(value);
      return undefined;
    }

    const DURATION = 1100;
    const start = performance.now();
    let raf = 0;

    const tick = (now) => {
      const t = Math.min(1, (now - start) / DURATION);
      // easeOutCubic
      const eased = 1 - (1 - t) ** 3;
      setCurrent(Math.round(value * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [shown, value]);

  return (
    <span ref={ref} className={className}>
      {current.toLocaleString()}
    </span>
  );
}
