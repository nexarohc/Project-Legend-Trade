/**
 * The hero backdrop: a bear and a bull facing off, drawn as low-poly holograms.
 *
 * Built as inline SVG rather than a generated raster, for four reasons that all
 * matter more than they sound:
 *
 * 1. **It is sharp at any size.** A 3D render would need @1x/@2x/@3x variants
 *    and would still be soft on a 5K display. This is resolution-independent.
 * 2. **It weighs a few kilobytes**, not the megabyte a detailed render costs —
 *    on a page whose whole argument is competence, a hero that takes three
 *    seconds to appear undermines it.
 * 3. **It is themeable.** The red and green come from the palette, so the two
 *    halves stay in sync with the rest of the design instead of being baked
 *    into pixels.
 * 4. **There is no licensing question.** Nothing here is someone else's asset.
 *
 * Composition follows the market's own convention, which is not decoration: the
 * bear sits on the left with a falling series, minus signs and losses in red;
 * the bull on the right with a rising series, plus signs and gains in green.
 * Reversing those would read as a mistake to anyone who trades, which on this
 * product is the worst possible first impression.
 *
 * Every number in the drifting labels is decorative and none is presented as a
 * quote — they carry no symbol and no timestamp, and the panel that shows real
 * data is clearly a separate, labelled surface.
 */

/** Low-poly wireframe overlay: the triangulation that sells the hologram. */
function Facets({ points, stroke }) {
  return (
    <g stroke={stroke} strokeWidth="0.7" fill="none" opacity="0.5">
      {points.map((line, index) => (
        <polyline key={index} points={line} />
      ))}
    </g>
  );
}

/*
 * The animals are drawn as a body polygon PLUS separate feature shapes rather
 * than as one outline. The first attempt used a single polygon each and the
 * result read as two generic quadrupeds — a bear is only a bear because of its
 * shoulder hump and small round ears, and a bull is only a bull because of its
 * horns. Those are exactly the parts a single closed path handles worst: horns
 * need to curve away from the body and ears need to sit on top of the skull.
 * Splitting them out costs a few more elements and makes both silhouettes
 * legible at a glance, which is the entire job.
 */

// --- bear: heavy, low-slung, facing right, big shoulder hump ---------------
const BEAR_BODY =
  "38,150 62,116 118,92 178,82 236,90 286,104 322,124 344,142 338,156 " +
  "312,162 288,176 252,170 222,184 232,256 250,276 200,272 198,192 " +
  "150,198 116,204 114,258 130,278 82,274 78,204 46,182";

const BEAR_FACETS = [
  "62,116 178,82 150,198",
  "150,198 62,116 46,182",
  "178,82 236,90 222,184",
  "222,184 178,82 150,198",
  "236,90 286,104 252,170",
  "252,170 236,90 222,184",
  "286,104 322,124 288,176",
  "288,176 286,104 252,170",
  "322,124 344,142 312,162",
  "78,204 114,258 150,198",
  "198,192 232,256 222,184",
  "38,150 62,116 46,182",
  "116,204 150,198 198,192",
];

// --- bull: deep chest, huge neck hump, head lowered, facing left -----------
const BULL_BODY =
  "372,120 340,98 276,84 208,74 168,92 132,102 104,116 82,132 66,150 " +
  "78,166 108,170 134,180 166,186 158,262 140,280 180,272 182,190 " +
  "240,200 266,262 248,280 288,272 288,202 336,182 368,152";

const BULL_FACETS = [
  "340,98 208,74 240,200",
  "240,200 340,98 336,182",
  "208,74 168,92 182,190",
  "182,190 208,74 240,200",
  "168,92 132,102 166,186",
  "166,186 168,92 182,190",
  "132,102 104,116 134,180",
  "134,180 132,102 166,186",
  "104,116 78,166 108,170",
  "108,170 104,116 134,180",
  "288,202 266,262 240,200",
  "182,190 158,262 166,186",
  "372,120 340,98 368,152",
];

/** The two round ears that make a bear a bear. */
function BearEars({ colour }) {
  return (
    <g fill="none" stroke={colour} strokeWidth="1.8">
      <polygon points="272,114 266,92 284,84 296,102 292,116" fill="url(#bear-fill)" />
      <polygon points="306,110 302,90 320,84 330,102 324,112" fill="url(#bear-fill)" />
    </g>
  );
}

/** Two horns sweeping up and forward — the bull's whole identity. */
function BullHorns({ colour }) {
  return (
    <g stroke={colour} strokeWidth="1.8" fill="url(#bull-fill)">
      {/* Near horn: anchored on the skull, sweeping up and forward, thick at
          the base and tapering to a point. */}
      <path d="M112,116 C 96,96 74,80 46,72 C 70,84 90,102 102,124 Z" />
      {/* Far horn, shorter and dimmer so the pair reads as depth rather than as
          two identical flat shapes. */}
      <path d="M142,104 C 130,84 110,68 84,58 C 106,72 124,90 132,112 Z" opacity="0.65" />
    </g>
  );
}

/** A falling or rising candle series, used behind each animal. */
function Candles({ rising, colour }) {
  // Deterministic, so the backdrop is identical on every load and cannot
  // occasionally generate an ugly frame nobody can reproduce.
  let seed = rising ? 991 : 337;
  const random = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };

  const bars = Array.from({ length: 26 }, (_, i) => {
    const t = i / 25;
    // Drift the series in the direction this side represents.
    const base = rising ? 210 - t * 130 : 80 + t * 130;
    const wobble = (random() - 0.5) * 34;
    const mid = base + wobble;
    const height = 12 + random() * 34;
    return { x: i * 26 + 8, top: mid - height / 2, height, up: random() > 0.42 };
  });

  return (
    <g opacity="0.5">
      {bars.map((bar, index) => (
        <g key={index}>
          <line
            x1={bar.x + 5}
            y1={bar.top - 9}
            x2={bar.x + 5}
            y2={bar.top + bar.height + 9}
            stroke={colour}
            strokeWidth="1"
            opacity="0.7"
          />
          <rect
            x={bar.x}
            y={bar.top}
            width="10"
            height={bar.height}
            fill={bar.up === rising ? colour : "transparent"}
            stroke={colour}
            strokeWidth="1"
          />
        </g>
      ))}
    </g>
  );
}

/** Circled + or − sign, the reference's most recognisable motif. */
function Sign({ x, y, r, plus, colour, delay = 0 }) {
  return (
    <g
      className="animate-float"
      style={{ animationDelay: `${delay}s`, transformOrigin: `${x}px ${y}px` }}
      opacity="0.75"
    >
      <circle cx={x} cy={y} r={r} fill="none" stroke={colour} strokeWidth="2" />
      <line
        x1={x - r * 0.45}
        y1={y}
        x2={x + r * 0.45}
        y2={y}
        stroke={colour}
        strokeWidth="2.4"
        strokeLinecap="round"
      />
      {plus && (
        <line
          x1={x}
          y1={y - r * 0.45}
          x2={x}
          y2={y + r * 0.45}
          stroke={colour}
          strokeWidth="2.4"
          strokeLinecap="round"
        />
      )}
    </g>
  );
}

/**
 * Depth layers.
 *
 * Parallax only reads as depth if the layers move by *different* amounts —
 * shifting everything together just slides the picture. These multipliers are
 * the composition's z-order made literal: distant market data barely moves, the
 * animals move a little, foreground particles move most.
 *
 * Driven by CSS variables the pointer hook writes, so a pointer move costs a
 * compositor transform rather than a React render.
 */
function Layer({ depth, children, className = "", style }) {
  return (
    <g
      className={className}
      style={{
        transform: `translate(calc(var(--px, 0) * ${depth}px), calc(var(--py, 0) * ${depth * 0.42}px))`,
        transition: "transform 60ms linear",
        ...style,
      }}
    >
      {children}
    </g>
  );
}

/** Drifting motes. Deterministic placement, so no load produces a bad frame. */
function Particles({ colour, x0, x1, count = 18, seedStart }) {
  let seed = seedStart;
  const random = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };
  return (
    <g fill={colour}>
      {Array.from({ length: count }, (_, i) => {
        const cx = x0 + random() * (x1 - x0);
        const cy = 140 + random() * 720;
        const r = 0.9 + random() * 1.8;
        return (
          <circle
            key={i}
            cx={cx}
            cy={cy}
            r={r}
            opacity={0.25 + random() * 0.45}
            className="animate-float"
            style={{ animationDelay: `${(random() * 6).toFixed(2)}s`, transformOrigin: `${cx}px ${cy}px` }}
          />
        );
      })}
    </g>
  );
}

export default function MarketClash({ className = "" }) {
  const RED = "#EF4444";
  const GREEN = "#10B981";

  // One definition of each animal, rendered twice: upright, and mirrored below
  // the floor line as a reflection. Duplicating the markup instead would mean
  // every future change to the geometry has to be made in two places, and the
  // second one is the one that gets forgotten.
  const bear = (
    <>
      <BearEars colour={RED} />
      <polygon points={BEAR_BODY} fill="url(#bear-fill)" stroke={RED} strokeWidth="1.8" />
      <polygon points={BEAR_BODY} fill="url(#scan)" />
      <Facets points={BEAR_FACETS} stroke={RED} />
      <circle cx="298" cy="122" r="3.4" fill="#fff" opacity="0.95" />
      <circle cx="338" cy="142" r="3" fill={RED} opacity="0.95" />
    </>
  );

  const bull = (
    <>
      <BullHorns colour={GREEN} />
      <polygon points={BULL_BODY} fill="url(#bull-fill)" stroke={GREEN} strokeWidth="1.8" />
      <polygon points={BULL_BODY} fill="url(#scan)" />
      <Facets points={BULL_FACETS} stroke={GREEN} />
      <circle cx="118" cy="126" r="3.4" fill="#fff" opacity="0.95" />
      <circle cx="70" cy="152" r="3" fill={GREEN} opacity="0.95" />
    </>
  );

  return (
    <svg
      className={className}
      viewBox="0 0 1440 1000"
      preserveAspectRatio="xMidYMax slice"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <filter id="glow-red" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="7" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id="glow-green" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="7" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>

        <linearGradient id="bear-fill" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={RED} stopOpacity="0.5" />
          <stop offset="100%" stopColor={RED} stopOpacity="0.10" />
        </linearGradient>
        <linearGradient id="bull-fill" x1="1" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={GREEN} stopOpacity="0.5" />
          <stop offset="100%" stopColor={GREEN} stopOpacity="0.10" />
        </linearGradient>

        <pattern id="scan" width="4" height="4" patternUnits="userSpaceOnUse">
          <rect width="4" height="1" fill="#fff" opacity="0.05" />
        </pattern>

        {/* Rim light. Volumetric lighting is what separates a cinematic render
            from a flat vector, and the cheapest convincing version is a wedge
            of colour behind each animal, brightest at the silhouette edge. */}
        <radialGradient id="rim-red" cx="0.34" cy="0.62" r="0.5">
          <stop offset="0%" stopColor="#F87171" stopOpacity="0.30" />
          <stop offset="55%" stopColor="#DC2626" stopOpacity="0.11" />
          <stop offset="100%" stopColor="#7F1D1D" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="rim-green" cx="0.66" cy="0.62" r="0.5">
          <stop offset="0%" stopColor="#34D399" stopOpacity="0.30" />
          <stop offset="55%" stopColor="#059669" stopOpacity="0.11" />
          <stop offset="100%" stopColor="#064E3B" stopOpacity="0" />
        </radialGradient>

        {/* Reflections fade with distance from the floor line rather than
            cutting off — a mirror image with a hard bottom edge reads as a
            copy-paste, not as a reflection. */}
        <linearGradient id="reflect-fade" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#fff" stopOpacity="0.22" />
          <stop offset="55%" stopColor="#fff" stopOpacity="0.05" />
          <stop offset="100%" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        <mask id="reflect-mask">
          <rect x="0" y="880" width="1440" height="200" fill="url(#reflect-fade)" />
        </mask>

        <radialGradient id="text-scrim">
          <stop offset="0%" stopColor="#030505" stopOpacity="0.86" />
          <stop offset="45%" stopColor="#030505" stopOpacity="0.70" />
          <stop offset="75%" stopColor="#030505" stopOpacity="0.30" />
          <stop offset="100%" stopColor="#030505" stopOpacity="0" />
        </radialGradient>

        {/* The clash. Sits at the midline and is invisible until the cursor is
            actually between the two animals. */}
        <radialGradient id="collision">
          <stop offset="0%" stopColor="#E2E8F0" stopOpacity="0.5" />
          <stop offset="35%" stopColor="#34D399" stopOpacity="0.22" />
          <stop offset="70%" stopColor="#F87171" stopOpacity="0.14" />
          <stop offset="100%" stopColor="#F87171" stopOpacity="0" />
        </radialGradient>

        <linearGradient id="fade-left" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#fff" stopOpacity="1" />
          <stop offset="70%" stopColor="#fff" stopOpacity="0.75" />
          <stop offset="100%" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="fade-right" x1="1" y1="0" x2="0" y2="0">
          <stop offset="0%" stopColor="#fff" stopOpacity="1" />
          <stop offset="70%" stopColor="#fff" stopOpacity="0.75" />
          <stop offset="100%" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        <mask id="mask-left">
          <rect x="0" y="0" width="760" height="1000" fill="url(#fade-left)" />
        </mask>
        <mask id="mask-right">
          <rect x="680" y="0" width="760" height="1000" fill="url(#fade-right)" />
        </mask>
      </defs>

      {/* Volumetric wash, furthest back and barely moving. */}
      <Layer depth={5}>
        <rect x="0" y="0" width="1440" height="1000" fill="url(#rim-red)" />
        <rect x="0" y="0" width="1440" height="1000" fill="url(#rim-green)" />
      </Layer>

      {/* ---------------- bear side (left, red, falling) ---------------- */}
      <g mask="url(#mask-left)">
        <Layer depth={9}>
          <g transform="translate(20, 430)" opacity="0.5">
            <Candles rising={false} colour={RED} />
          </g>
          <g fill={RED} fontFamily="'JetBrains Mono Variable', monospace" opacity="0.8">
            <text x="96" y="330" fontSize="24">−4.75%</text>
            <text x="246" y="486" fontSize="19">−2.24%</text>
            <text x="132" y="614" fontSize="17">−1.88%</text>
          </g>
        </Layer>

        {/* Brightening is opacity on a duplicate glow pass rather than a filter
            swap: changing a filter forces the browser to re-rasterise the whole
            subtree every frame, which is what makes hover effects on SVG
            stutter. Opacity is a compositor property. */}
        <Layer depth={17}>
          <g
            style={{
              opacity: "calc(0.32 + var(--bear-energy, 0) * 0.55)",
              transition: "opacity 220ms ease-out",
            }}
          >
            <ellipse cx="330" cy="800" rx="330" ry="200" fill="url(#rim-red)" />
          </g>

          <g filter="url(#glow-red)" opacity="0.85">
            <g transform="translate(132, 602)">{bear}</g>
          </g>

          {/* Reflection: mirrored about the floor line, faded by the mask. */}
          <g mask="url(#reflect-mask)" opacity="0.5" filter="url(#glow-red)">
            <g transform="translate(132, 1158) scale(1, -1)">{bear}</g>
          </g>
        </Layer>

        <Layer depth={26}>
          <g filter="url(#glow-red)">
            <Sign x={214} y={232} r={26} plus={false} colour={RED} delay={0} />
            <Sign x={392} y={392} r={18} plus={false} colour={RED} delay={1.4} />
            <Sign x={118} y={716} r={21} plus={false} colour={RED} delay={2.6} />
          </g>
          <g stroke={RED} strokeWidth="2.4" strokeLinecap="round" opacity="0.7">
            <path d="M96 400 L96 470 M80 452 L96 470 L112 452" fill="none" />
            <path d="M300 250 L300 312 M285 296 L300 312 L315 296" fill="none" />
          </g>
          <Particles colour={RED} x0={80} x1={540} count={22} seedStart={4177} />
        </Layer>
      </g>

      {/* ---------------- bull side (right, green, rising) ---------------- */}
      <g mask="url(#mask-right)">
        <Layer depth={9}>
          <g transform="translate(760, 430)" opacity="0.5">
            <Candles rising colour={GREEN} />
          </g>
          <g fill={GREEN} fontFamily="'JetBrains Mono Variable', monospace" opacity="0.85">
            <text x="1084" y="318" fontSize="24">+4.75%</text>
            <text x="1176" y="470" fontSize="19">+3.42%</text>
            <text x="1246" y="610" fontSize="17">+2.18%</text>
          </g>
        </Layer>

        <Layer depth={17}>
          <g
            style={{
              opacity: "calc(0.32 + var(--bull-energy, 0) * 0.55)",
              transition: "opacity 220ms ease-out",
            }}
          >
            <ellipse cx="1110" cy="800" rx="330" ry="200" fill="url(#rim-green)" />
          </g>

          <g filter="url(#glow-green)" opacity="0.85">
            <g transform="translate(898, 600)">{bull}</g>
          </g>

          <g mask="url(#reflect-mask)" opacity="0.5" filter="url(#glow-green)">
            <g transform="translate(898, 1160) scale(1, -1)">{bull}</g>
          </g>
        </Layer>

        <Layer depth={26}>
          <g filter="url(#glow-green)">
            <Sign x={1222} y={232} r={27} plus colour={GREEN} delay={0.6} />
            <Sign x={1306} y={498} r={20} plus colour={GREEN} delay={2} />
            <Sign x={1046} y={706} r={17} plus colour={GREEN} delay={3.1} />
          </g>
          <g stroke={GREEN} strokeWidth="2.4" strokeLinecap="round" opacity="0.75">
            <path d="M1310 400 L1310 330 M1294 348 L1310 330 L1326 348" fill="none" />
            <path d="M1150 300 L1150 236 M1134 254 L1150 236 L1166 254" fill="none" />
          </g>
          <Particles colour={GREEN} x0={920} x1={1360} count={22} seedStart={9311} />
        </Layer>
      </g>

      {/* Floor line where both halves meet the ground plane. */}
      <line x1="0" y1="880" x2="1440" y2="880" stroke="#fff" strokeWidth="1" opacity="0.07" />

      {/* The clash itself, only present while the cursor is between them. */}
      <g
        style={{
          opacity: "var(--centre-energy, 0)",
          transition: "opacity 260ms ease-out",
        }}
      >
        <ellipse cx="720" cy="420" rx="260" ry="230" fill="url(#collision)" />
      </g>

      {/* Painted last so nothing above can compromise the headline's contrast.
          Also the one layer that must not parallax: the text it protects does
          not move, so a scrim that did would slide out from under it. */}
      <ellipse cx="720" cy="330" rx="620" ry="300" fill="url(#text-scrim)" />
    </svg>
  );
}
