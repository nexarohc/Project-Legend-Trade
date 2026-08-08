import MarketClash from "./MarketClash.jsx";

/**
 * The hero backdrop: your rendered image if you have supplied one, otherwise
 * the generated vector scene.
 *
 * Drop a file named `hero-clash.<ext>` into `src/assets/` and it takes over. No
 * import to add, no flag to flip, nothing else to edit — which matters because
 * the alternative is a hardcoded `import` that breaks the build on every clone
 * that does not happen to have the file.
 *
 * `import.meta.glob` is what makes that safe. It is resolved by the bundler at
 * build time and returns an empty object when nothing matches, so an absent
 * image is a normal state rather than an unresolved-module error. The file also
 * goes through the asset pipeline, so it gets hashed, cached and served like
 * any other bundled asset.
 *
 * A raster wins on detail and loses on weight and sharpness. That trade is the
 * right way round for a photoreal render — no amount of hand-authored SVG will
 * look like an Octane frame — and the wrong way round for the vector fallback,
 * which is why both exist rather than one replacing the other.
 */

// Eager, so the URL is available on first paint rather than after a promise —
// the hero backdrop appearing a frame late is the most visible possible flicker.
const supplied = import.meta.glob("../../assets/hero-clash.{png,jpg,jpeg,webp,avif}", {
  eager: true,
  query: "?url",
  import: "default",
});

const HERO_IMAGE = Object.values(supplied)[0] ?? null;

export default function HeroBackdrop({ className = "" }) {
  if (!HERO_IMAGE) return <MarketClash className={className} />;

  return (
    <div className={className} aria-hidden="true">
      <img
        src={HERO_IMAGE}
        alt=""
        // `cover` and a centred focal point: the hero's aspect changes with the
        // viewport, and the interesting part of a bull-versus-bear composition
        // is the middle, so cropping should eat the outer edges.
        className="h-full w-full object-cover object-center"
        // Never lazy. This is the largest contentful paint on the page; deferring
        // it trades a metric everyone measures for bytes nobody saves.
        loading="eager"
        fetchPriority="high"
        decoding="async"
        draggable="false"
      />
      {/* The same scrim the vector scene paints internally. A photoreal render
          has no idea where the headline sits, so the text needs its own
          guaranteed contrast rather than relying on the image being dark in the
          right place. */}
      <div
        className="pointer-events-none absolute inset-0
                   bg-[radial-gradient(ellipse_46%_42%_at_50%_38%,rgba(3,5,5,0.88),rgba(3,5,5,0.55)_55%,transparent_78%)]"
      />
    </div>
  );
}
