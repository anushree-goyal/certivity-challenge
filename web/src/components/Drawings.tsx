// Decorative technical drawings (generic hardware, not real product data), drawn as a holographic blueprint:
// glowing teal linework, translucent panels, a frame like a heads-up display and slow, subtle motion.
// Colours come from CSS tokens; all motion stops under prefers-reduced-motion.
import { useId } from "react";
import type { CSSProperties, ReactNode } from "react";

const SKEW = "skewY(-16)";

/** SVG-safe unique prefix for <defs> ids, so several drawings can share a page. */
function useSvgId() {
  return "fx" + useId().replace(/[^a-zA-Z0-9]/g, "");
}

function Defs({ id }: { id: string }) {
  return (
    <defs>
      <filter id={`${id}-glow`} x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="2.4" result="blur" />
        <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
      <linearGradient id={`${id}-panel`} x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" style={{ stopColor: "var(--teal)", stopOpacity: 0.22 }} />
        <stop offset="1" style={{ stopColor: "var(--teal)", stopOpacity: 0.03 }} />
      </linearGradient>
      <linearGradient id={`${id}-scan`} x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" style={{ stopColor: "var(--teal)", stopOpacity: 0 }} />
        <stop offset="0.5" style={{ stopColor: "var(--teal)", stopOpacity: 0.16 }} />
        <stop offset="1" style={{ stopColor: "var(--teal)", stopOpacity: 0 }} />
      </linearGradient>
      <radialGradient id={`${id}-vignette`} cx="0.5" cy="0.5" r="0.7">
        <stop offset="0.55" style={{ stopColor: "#000", stopOpacity: 0 }} />
        <stop offset="1" style={{ stopColor: "#000", stopOpacity: 0.28 }} />
      </radialGradient>
      <pattern id={`${id}-dots`} width="14" height="14" patternUnits="userSpaceOnUse">
        <circle cx="1" cy="1" r="0.9" className="fx-dot" />
      </pattern>
      <marker id={`${id}-arrow`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M0 0L8 4L0 8z" className="fx-fill" />
      </marker>
    </defs>
  );
}

/** Corner brackets, readouts and an edge ruler: the frame like a heads-up display. */
function HudFrame({ w, h, title, meta }: { w: number; h: number; title: string; meta: string }) {
  const c = 14;
  const corners = [
    `M8 ${8 + c} V8 H${8 + c}`, `M${w - 8 - c} 8 H${w - 8} V${8 + c}`,
    `M8 ${h - 8 - c} V${h - 8} H${8 + c}`, `M${w - 8 - c} ${h - 8} H${w - 8} V${h - 8 - c}`,
  ];
  const ticks = [];
  for (let x = 40; x <= w - 40; x += 10) {
    const major = (x - 40) % 50 === 0;
    ticks.push(<path key={x} d={`M${x} ${h - 8} v${major ? -7 : -3.5}`} className="fx-tick" />);
  }
  return (
    <g>
      {corners.map((d) => <path key={d} d={d} className="fx-bracket" />)}
      <text x="24" y="22" className="fx-label fx-strong">{title}</text>
      <text x={w - 24} y="22" textAnchor="end" className="fx-label">{meta}</text>
      {ticks}
    </g>
  );
}

function Plate({ x, y, w, h, depth = 6, r = 10, panel, children, dashed }: {
  x: number; y: number; w: number; h: number; depth?: number; r?: number; panel: string; children?: ReactNode; dashed?: boolean;
}) {
  return (
    <g transform={`translate(${x} ${y}) ${SKEW}`}>
      <rect x={depth} y={-depth} width={w} height={h} rx={r} className="fx-back" />
      <rect x={0} y={0} width={w} height={h} rx={r} fill={`url(#${panel})`} className="fx-ink"
            strokeDasharray={dashed ? "5 4" : undefined} />
      {children}
    </g>
  );
}

/** One exploded part: the outer group is what the step highlights target (lift, glow, dim); the inner group
 *  keeps its own slow drift along the assembly axis, so the two transforms never fight. */
function Part({ i, name, children }: { i: number; name: string; children: ReactNode }) {
  const drift = { "--dx": `${(i - 2) * 3}px`, "--dy": `${(2 - i) * 0.9}px`, animationDelay: `${i * 0.35}s` } as CSSProperties;
  return (
    <g className={`part part-${name}`} style={{ "--i": i } as CSSProperties}>
      <g className="fx-part" style={drift}>{children}</g>
    </g>
  );
}

/** A small regulation tag with leader lines to the parts it applies to (shown for step 2). */
function RegTag({ x, y, label, to }: { x: number; y: number; label: string; to: [number, number][] }) {
  const w = label.length * 6.1 + 14;
  return (
    <g className="reg-tag">
      {to.map(([tx, ty]) => <path key={`${tx}-${ty}`} d={`M${x + w / 2} ${y} L${tx} ${ty}`} className="fx-lead reg-lead" />)}
      <rect x={x} y={y} width={w} height={14} rx={7} className="reg-pill" />
      <text x={x + w / 2} y={y + 10} textAnchor="middle" className="fx-label reg-text">{label}</text>
    </g>
  );
}

/** Exploded view of a lamp-style assembly along an axis, with coded callouts and a dimension line. */
export function ExplodedAssembly({ title = "ASSY-01 // EXPLODED VIEW", step = null }: {
  title?: string; step?: 1 | 2 | 3 | null;
}) {
  const id = useSvgId();
  const panel = `${id}-panel`;
  const callouts: [number, number, number, number, string][] = [
    [70, 46, 92, 98, "HOUSING"],
    [170, 36, 178, 94, "GASKET"],
    [262, 32, 262, 84, "LED BOARD"],
    [356, 36, 344, 72, "REFLECTOR"],
    [452, 46, 430, 68, "OUTER LENS"],
  ];
  return (
    <svg className="drawing-art fx" viewBox="0 0 540 330" role="img" data-step={step ?? undefined}
         aria-label="Technical exploded-view drawing of a generic lamp assembly">
      <Defs id={id} />
      <rect width="540" height="330" fill={`url(#${id}-dots)`} />
      <HudFrame w={540} h={330} title={title} meta="SCALE 1:2 · REV A" />

      {/* assembly axis, flowing */}
      <path d="M40 262 L512 126" className="fx-axis" />

      <g filter={`url(#${id}-glow)`}>
        <Part i={0} name="housing">
          <Plate x={58} y={132} w={86} h={124} depth={16} r={14} panel={panel}>
            {[[18, 18], [68, 18], [18, 106], [68, 106]].map(([cx, cy]) => <circle key={`${cx}${cy}`} cx={cx} cy={cy} r={5} className="fx-ink fx-hollow" />)}
            <rect x={30} y={44} width={26} height={36} rx={4} className="fx-thin" />
          </Plate>
        </Part>
        <Part i={1} name="gasket"><Plate x={160} y={114} w={78} h={112} depth={3} r={12} panel={panel} dashed /></Part>
        <Part i={2} name="led">
          <Plate x={236} y={98} w={70} h={98} depth={4} r={4} panel={panel}>
            {[0, 1, 2].map((c) => [0, 1, 2, 3].map((r) => (
              <rect key={`${c}-${r}`} x={12 + c * 18} y={12 + r * 20} width={9} height={9} rx={1.5}
                    className="fx-led" style={{ animationDelay: `${(c + r) * 0.25}s` }} />
            )))}
            <rect x={24} y={92} width={22} height={10} className="fx-ink fx-hollow" />
          </Plate>
        </Part>
        <Part i={3} name="reflector">
          <Plate x={318} y={80} w={74} h={104} depth={8} r={10} panel={panel}>
            <rect x={9} y={9} width={56} height={86} rx={8} className="fx-thin" />
            <rect x={18} y={18} width={38} height={68} rx={6} className="fx-thin" />
          </Plate>
        </Part>
        <Part i={4} name="lens">
          <Plate x={410} y={62} w={80} h={112} depth={10} r={16} panel={panel}>
            {[0, 1, 2, 3, 4, 5].map((k) => <path key={k} d={`M10 ${16 + k * 15} h60`} className="fx-thin" />)}
          </Plate>
        </Part>
        <g className="part part-fasteners" style={{ "--i": 5 } as CSSProperties}>
          {[[30, 270], [44, 292]].map(([x, y], k) => (
            <g key={k} transform={`translate(${x} ${y}) rotate(-16)`}>
              <rect x={0} y={-4} width={8} height={8} className="fx-ink fx-hollow" />
              <path d="M8 0 H24" className="fx-ink" strokeWidth="2.4" strokeDasharray="1.6 1.4" />
            </g>
          ))}
        </g>
      </g>

      {/* coded callouts with pulsing anchor points */}
      {callouts.map(([bx, by, tx, ty, label], k) => (
        <g key={label} className="callout" style={{ "--i": k } as CSSProperties}>
          <path d={`M${bx} ${by + 7} L${tx} ${ty}`} className="fx-lead" />
          <circle cx={tx} cy={ty} r={2} className="fx-fill" />
          <circle cx={tx} cy={ty} r={5} className="fx-pulse" style={{ animationDelay: `${k * 0.4}s` }} />
          <rect x={bx - 10} y={by - 7} width={label.length * 6.2 + 26} height={14} rx={2} className="fx-tag" />
          <text x={bx - 5} y={by + 3} className="fx-label fx-strong">{String(k + 1).padStart(2, "0")}</text>
          <text x={bx + 11} y={by + 3} className="fx-label">{label}</text>
        </g>
      ))}

      {/* dimension line */}
      <path d="M66 304 L494 304" className="fx-dim" markerStart={`url(#${id}-arrow)`} markerEnd={`url(#${id}-arrow)`} />
      <path d="M66 296 V312 M494 296 V312" className="fx-dim" />
      <rect x="236" y="290" width="88" height="12" rx="2" className="fx-tag" />
      <text x="280" y="299" textAnchor="middle" className="fx-label">ASSEMBLY AXIS</text>

      {/* step 2: which regulations apply to which parts (illustrative, labelled as an example) */}
      <g className="reg-tag"><text x="516" y="36" textAnchor="end" className="fx-label example-tag">EXAMPLE TAGS · NOT A RESULT</text></g>
      <RegTag x={214} y={222} label="UN R10" to={[[268, 196], [118, 246]]} />
      <RegTag x={322} y={204} label="UN R150" to={[[356, 178]]} />
      <RegTag x={436} y={186} label="UN R148" to={[[452, 164]]} />
      <RegTag x={352} y={276} label="UN R48 · whole assembly" to={[]} />

      {/* step 3: one example cited requirement (real text and page from UN R148, labelled as an example) */}
      <g className="example-card">
        <path d="M478 168 L470 206" className="fx-lead" />
        <rect x="262" y="206" width="246" height="66" rx="6" className="example-rect" />
        <text x="274" y="221" className="fx-label example-tag">EXAMPLE · NOT A RESULT</text>
        <text x="274" y="237" className="fx-label example-text">Stop lamp S1 (steady): 60–260 cd</text>
        <text x="274" y="250" className="fx-label example-text">luminous intensity, single lamp</text>
        <text x="274" y="264" className="fx-label">UN R148 1.6.0 · ¶ 5.5.1 Table 7 · p. 19</text>
      </g>

      {/* slow scan sweep */}
      <rect x="0" y="30" width="70" height="262" fill={`url(#${id}-scan)`} className="fx-scan" />
      <rect width="540" height="330" fill={`url(#${id}-vignette)`} pointerEvents="none" />
    </svg>
  );
}

/** A regulation page with the cited paragraph picked out by a scanning highlight, for waiting states. */
export function SourceSheet() {
  const id = useSvgId();
  return (
    <svg className="drawing-art fx" viewBox="0 0 240 150" role="img" aria-label="Drawing of a regulation page with the cited paragraph highlighted">
      <Defs id={id} />
      <rect width="240" height="150" fill={`url(#${id}-dots)`} />
      <g filter={`url(#${id}-glow)`}>
        <g transform={`translate(62 28) ${SKEW}`}>
          <rect x={10} y={-8} width={96} height={118} rx={4} className="fx-back" />
          <rect width={96} height={118} rx={4} fill={`url(#${id}-panel)`} className="fx-ink" />
          {[14, 24, 34, 44].map((y) => <path key={y} d={`M12 ${y} h${y === 44 ? 44 : 70}`} className="fx-thin" />)}
          <rect x={8} y={54} width={80} height={24} rx={3} className="fx-ink fx-hollow fx-cite" />
          {[61, 70].map((y) => <path key={y} d={`M14 ${y} h64`} className="fx-ink" />)}
          {[90, 100].map((y) => <path key={y} d={`M12 ${y} h60`} className="fx-thin" />)}
          <rect x={8} y={54} width={80} height={6} className="fx-cite-scan" fill={`url(#${id}-scan)`} />
        </g>
      </g>
      <path d="M160 64 L196 46" className="fx-lead" />
      <circle cx={160} cy={64} r={4.5} className="fx-pulse" />
      <rect x={190} y={36} width={34} height={16} rx={2} className="fx-tag" />
      <text x={207} y={47.5} textAnchor="middle" className="fx-label fx-strong">¶ REF</text>
    </svg>
  );
}
