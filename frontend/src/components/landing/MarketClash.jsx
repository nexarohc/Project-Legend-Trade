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

export default function MarketClash({ className = "" }) {
  const RED = "#EF4444";
  const GREEN = "#10B981";

  return (
    <svg
      className={className}
      viewBox="0 0 1440 620"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        {/* The glow that makes a flat vector read as a projection. One blur
            per side, reused by every element on that side. */}
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

        {/* Horizontal scanlines — the single cheapest cue that says hologram. */}
        <pattern id="scan" width="4" height="4" patternUnits="userSpaceOnUse">
          <rect width="4" height="1" fill="#fff" opacity="0.05" />
        </pattern>

        {/* Fade each half out toward the centre so the two colours meet in a
            soft seam instead of a hard vertical edge behind the headline. */}
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
        {/* Feathered so gradually that no boundary is visible — a scrim you can
            see the edge of is worse than no scrim. */}
        <radialGradient id="text-scrim">
          <stop offset="0%" stopColor="#05070D" stopOpacity="0.82" />
          <stop offset="45%" stopColor="#05070D" stopOpacity="0.66" />
          <stop offset="75%" stopColor="#05070D" stopOpacity="0.28" />
          <stop offset="100%" stopColor="#05070D" stopOpacity="0" />
        </radialGradient>

        <mask id="mask-left">
          <rect x="0" y="0" width="760" height="620" fill="url(#fade-left)" />
        </mask>
        <mask id="mask-right">
          <rect x="680" y="0" width="760" height="620" fill="url(#fade-right)" />
        </mask>
      </defs>

      {/* ---------------- bear side (left, red, falling) ---------------- */}
      <g mask="url(#mask-left)">
        <g transform="translate(20, 60)" opacity="0.5">
          <Candles rising={false} colour={RED} />
        </g>

        {/* Placement is constrained by `slice` cropping, which is easy to get
            wrong by eye. At the common desktop ratio the renderer scales to
            cover and trims the sides, leaving roughly x=100..1340 of the 1440
            viewBox actually visible. An earlier pass sat the bear at scale 1.32
            with its rear at x=58: a third of the animal was cropped away and its
            muzzle landed on top of the body copy. Kept inside 150..410 it reads
            whole, and the centre column stays clear for text. */}
        <g filter="url(#glow-red)" opacity="0.85">
          <g transform="translate(118, 304) scale(0.85)">
            <BearEars colour={RED} />
            <polygon points={BEAR_BODY} fill="url(#bear-fill)" stroke={RED} strokeWidth="1.8" />
            <polygon points={BEAR_BODY} fill="url(#scan)" />
            <Facets points={BEAR_FACETS} stroke={RED} />
            {/* Eye and nose — two dots, and the silhouette becomes a face. */}
            <circle cx="298" cy="122" r="3.4" fill="#fff" opacity="0.95" />
            <circle cx="338" cy="142" r="3" fill={RED} opacity="0.95" />
          </g>
        </g>

        <g fill={RED} fontFamily="'JetBrains Mono Variable', monospace" opacity="0.8">
          <text x="120" y="130" fontSize="22">−4.75%</text>
          <text x="300" y="196" fontSize="18">−2.24%</text>
          <text x="185" y="262" fontSize="16">−1.88%</text>
        </g>

        <g filter="url(#glow-red)">
          <Sign x={262} y={92} r={22} plus={false} colour={RED} delay={0} />
          <Sign x={445} y={140} r={17} plus={false} colour={RED} delay={1.4} />
          <Sign x={136} y={404} r={19} plus={false} colour={RED} delay={2.6} />
        </g>

        {/* Down arrows */}
        <g stroke={RED} strokeWidth="2.4" strokeLinecap="round" opacity="0.7">
          <path d="M74 78 L74 128 M62 114 L74 128 L86 114" fill="none" />
          <path d="M196 210 L196 254 M185 242 L196 254 L207 242" fill="none" />
        </g>
      </g>

      {/* ---------------- bull side (right, green, rising) ---------------- */}
      <g mask="url(#mask-right)">
        <g transform="translate(760, 60)" opacity="0.5">
          <Candles rising colour={GREEN} />
        </g>

        {/* Mirror of the bear's placement: inside 1040..1300, so the horns clear
            the text column on the left and the rump clears the crop on the
            right. Both animals share a baseline at y≈540 so they read as
            standing on the same ground plane rather than floating at
            independent heights. */}
        <g filter="url(#glow-green)" opacity="0.85">
          <g transform="translate(936, 302) scale(0.85)">
            <BullHorns colour={GREEN} />
            <polygon points={BULL_BODY} fill="url(#bull-fill)" stroke={GREEN} strokeWidth="1.8" />
            <polygon points={BULL_BODY} fill="url(#scan)" />
            <Facets points={BULL_FACETS} stroke={GREEN} />
            <circle cx="118" cy="126" r="3.4" fill="#fff" opacity="0.95" />
            <circle cx="70" cy="152" r="3" fill={GREEN} opacity="0.95" />
          </g>
        </g>

        <g fill={GREEN} fontFamily="'JetBrains Mono Variable', monospace" opacity="0.85">
          {/* Kept inside x≈1300. Anything further right is trimmed by the same
              `slice` crop that governs the animals, and a percentage sliced in
              half mid-digit looks like a rendering bug rather than a backdrop. */}
          <text x="952" y="126" fontSize="22">+4.75%</text>
          <text x="1078" y="200" fontSize="18">+3.42%</text>
          <text x="1186" y="286" fontSize="16">+2.18%</text>
        </g>

        <g filter="url(#glow-green)">
          <Sign x={1132} y={100} r={24} plus colour={GREEN} delay={0.6} />
          <Sign x={1372} y={310} r={20} plus colour={GREEN} delay={2} />
          <Sign x={1004} y={430} r={16} plus colour={GREEN} delay={3.1} />
        </g>

        {/* Up arrows */}
        <g stroke={GREEN} strokeWidth="2.4" strokeLinecap="round" opacity="0.75">
          <path d="M1352 150 L1352 100 M1341 114 L1352 100 L1363 114" fill="none" />
          <path d="M1240 250 L1240 206 M1229 218 L1240 206 L1251 218" fill="none" />
        </g>
      </g>

      {/* Reflective floor line where the two halves meet the ground plane. */}
      <line x1="0" y1="560" x2="1440" y2="560" stroke="#fff" strokeWidth="1" opacity="0.07" />

      {/* A scrim under the headline, painted last so it sits over everything.
          Keeping the artwork out of the centre column is the first line of
          defence, but it cannot be the only one: the crop shifts with viewport
          ratio, so at some window sizes a candle or a drifting percentage will
          slide under the text no matter where the animals are parked. This
          guarantees contrast at every size instead of at the sizes checked by
          hand. Elliptical and heavily feathered, so it darkens the middle
          without drawing an edge anyone can see. */}
      <ellipse cx="720" cy="290" rx="560" ry="240" fill="url(#text-scrim)" />
    </svg>
  );
}
