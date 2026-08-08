import { useEffect, useRef } from "react";

/**
 * The animated backdrop: a live candle field drifting behind the page.
 *
 * Why a canvas rather than more CSS gradients — the previous hero used two
 * blurred blobs, which is what every dark SaaS page does and reads as
 * decoration that could belong to any product. This draws the actual thing the
 * product is about: a price series, forming bar by bar, with the newest one
 * lit. It is the same object the terminal draws, at one-tenth the opacity.
 *
 * Three things keep it cheap enough to leave running:
 *
 * 1. **It is frame-gated to ~24fps.** Nothing here benefits from 120, and a
 *    background that eats a whole core is a background that makes the product
 *    feel slow.
 * 2. **It stops when the tab is hidden.** `requestAnimationFrame` already
 *    throttles, but an explicit visibility check means a backgrounded tab does
 *    exactly nothing rather than a little.
 * 3. **It never runs under `prefers-reduced-motion`.** One static frame is
 *    drawn instead, so the composition survives without the movement.
 *
 * The series is generated, not fetched. This has to render identically for a
 * signed-out visitor with no backend reachable, and a hero that sometimes shows
 * a connection error is worse than one that shows a drawing. It is decorative
 * and labelled as such — no number here is presented as a quote.
 */
export default function MarketField({ className = "" }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const context = canvas.getContext("2d");
    if (!context) return undefined;

    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

    // Device-pixel-ratio aware sizing, capped at 2: beyond that the extra
    // pixels are invisible at this opacity and only cost fill rate.
    let width = 0;
    let height = 0;
    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      canvas.width = Math.max(1, Math.floor(width * ratio));
      canvas.height = Math.max(1, Math.floor(height * ratio));
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    };
    resize();

    const BAR_WIDTH = 13;
    const GAP = 5;
    const step = BAR_WIDTH + GAP;

    // A deterministic generator, so the shape is the same on every load and
    // this cannot occasionally produce an ugly frame nobody can reproduce.
    let seed = 20260804;
    const random = () => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed / 2147483648;
    };

    let price = 100;
    const makeBar = () => {
      const drift = (random() - 0.48) * 3.4;
      const open = price;
      const close = Math.max(30, open + drift);
      const spread = random() * 1.5 + 0.2;
      price = close;
      return {
        open,
        close,
        high: Math.max(open, close) + spread,
        low: Math.min(open, close) - spread,
      };
    };

    let bars = [];
    const fill = () => {
      bars = [];
      price = 100;
      const needed = Math.ceil(width / step) + 3;
      for (let i = 0; i < needed; i += 1) bars.push(makeBar());
    };
    fill();

    // Sub-pixel offset so the field slides continuously instead of jumping a
    // whole bar at a time.
    let offset = 0;
    let raf = 0;
    let last = 0;
    const FRAME_MS = 1000 / 24;
    const SPEED = 14; // px per second

    const draw = () => {
      context.clearRect(0, 0, width, height);
      if (!bars.length) return;

      const highs = bars.map((b) => b.high);
      const lows = bars.map((b) => b.low);
      const top = Math.max(...highs);
      const bottom = Math.min(...lows);
      const span = top - bottom || 1;

      // Leave headroom top and bottom so the extremes never touch the edge.
      const pad = height * 0.16;
      const toY = (value) => height - pad - ((value - bottom) / span) * (height - pad * 2);

      bars.forEach((bar, index) => {
        const x = index * step - offset;
        if (x < -step || x > width + step) return;

        const rising = bar.close >= bar.open;
        // The newest few bars are brighter — the eye is drawn to the leading
        // edge, which is where a real chart's attention lives too.
        const recency = Math.min(1, Math.max(0, (index - (bars.length - 9)) / 9));
        const alpha = 0.13 + recency * 0.5;
        const colour = rising ? "34, 211, 199" : "239, 83, 80";

        context.strokeStyle = `rgba(${colour}, ${alpha * 0.85})`;
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(x + BAR_WIDTH / 2, toY(bar.high));
        context.lineTo(x + BAR_WIDTH / 2, toY(bar.low));
        context.stroke();

        const bodyTop = toY(Math.max(bar.open, bar.close));
        const bodyHeight = Math.max(1.5, Math.abs(toY(bar.open) - toY(bar.close)));
        context.fillStyle = `rgba(${colour}, ${alpha})`;
        context.fillRect(x, bodyTop, BAR_WIDTH, bodyHeight);

        // Glow only on the single newest bar, and only via shadowBlur on one
        // rect — a blur per bar would be the expensive mistake here.
        if (index === bars.length - 2) {
          context.save();
          context.shadowColor = `rgba(${colour}, 0.9)`;
          context.shadowBlur = 22;
          context.fillStyle = `rgba(${colour}, 0.85)`;
          context.fillRect(x, bodyTop, BAR_WIDTH, bodyHeight);
          context.restore();
        }
      });

      // The current-price line, dashed, running back from the leading edge.
      const latest = bars[bars.length - 2];
      if (latest) {
        const y = toY(latest.close);
        context.save();
        context.setLineDash([3, 7]);
        context.strokeStyle = "rgba(34, 211, 199, 0.4)";
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(0, y);
        context.lineTo(width, y);
        context.stroke();
        context.restore();
      }
    };

    if (reduced) {
      draw();
      const onResize = () => {
        resize();
        fill();
        draw();
      };
      window.addEventListener("resize", onResize);
      return () => window.removeEventListener("resize", onResize);
    }

    const tick = (now) => {
      raf = requestAnimationFrame(tick);
      if (document.hidden) return;
      if (now - last < FRAME_MS) return;
      const delta = last ? Math.min(now - last, 250) : FRAME_MS;
      last = now;

      offset += (SPEED * delta) / 1000;
      while (offset >= step) {
        offset -= step;
        bars.shift();
        bars.push(makeBar());
      }
      draw();
    };
    raf = requestAnimationFrame(tick);

    const onResize = () => {
      resize();
      fill();
    };
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return <canvas ref={canvasRef} className={className} aria-hidden="true" />;
}
