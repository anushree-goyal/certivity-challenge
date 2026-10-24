// Decorative technical line drawings (generic hardware, not real product data). Strokes use CSS tokens
// via currentColor/.ink so they follow the theme.

const SKEW = "skewY(-16)";

function Plate({ x, y, w, h, depth = 6, r = 10, children, dashed }: {
  x: number; y: number; w: number; h: number; depth?: number; r?: number; children?: React.ReactNode; dashed?: boolean;
}) {
  return (
    <g transform={`translate(${x} ${y}) ${SKEW}`}>
      <rect x={depth} y={-depth} width={w} height={h} rx={r} fill="none" stroke="currentColor" strokeWidth="1" />
      <rect x={0} y={0} width={w} height={h} rx={r} fill="#fff" className="ink" strokeWidth="1.4"
            strokeDasharray={dashed ? "5 4" : undefined} />
      {children}
    </g>
  );
}

/** Exploded view of a lamp-style assembly along a dashed axis, with numbered callouts and a dimension line. */
export function ExplodedAssembly({ title = "EXPLODED VIEW · REF. ASSEMBLY" }: { title?: string }) {
  const callouts: [number, number, number, number, string][] = [
    // balloon x, y, leader target x, y, label
    [70, 34, 92, 96, "HOUSING"],
    [170, 22, 178, 92, "GASKET"],
    [262, 18, 262, 82, "LED BOARD"],
    [356, 22, 344, 70, "REFLECTOR"],
    [452, 34, 430, 66, "OUTER LENS"],
  ];
  return (
    <svg className="drawing-art" viewBox="0 0 540 330" role="img" aria-label="Technical exploded-view drawing of a generic lamp assembly">
      <defs>
        <pattern id="grid" width="18" height="18" patternUnits="userSpaceOnUse">
          <path d="M18 0H0V18" fill="none" className="grid" strokeWidth="1" />
        </pattern>
        <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0L8 4L0 8z" fill="currentColor" />
        </marker>
      </defs>
      <rect width="540" height="330" fill="url(#grid)" />

      {/* assembly axis */}
      <path d="M40 262 L512 126" stroke="currentColor" strokeWidth="1" strokeDasharray="10 4 2 4" />

      {/* 1 housing: deep shell with mounting bosses */}
      <Plate x={58} y={130} w={86} h={124} depth={16} r={14}>
        <circle cx={18} cy={18} r={5} fill="none" className="ink" />
        <circle cx={68} cy={18} r={5} fill="none" className="ink" />
        <circle cx={18} cy={106} r={5} fill="none" className="ink" />
        <circle cx={68} cy={106} r={5} fill="none" className="ink" />
        <rect x={30} y={44} width={26} height={36} rx={4} fill="none" stroke="currentColor" />
      </Plate>

      {/* 2 gasket */}
      <Plate x={160} y={112} w={78} h={112} depth={3} r={12} dashed />

      {/* 3 LED board with emitter grid and connector */}
      <Plate x={236} y={96} w={70} h={98} depth={4} r={4}>
        {[0, 1, 2].map((c) => [0, 1, 2, 3].map((r) => (
          <rect key={`${c}-${r}`} x={12 + c * 18} y={12 + r * 20} width={9} height={9} rx={1.5} fill="none" className="ink" />
        )))}
        <rect x={24} y={92} width={22} height={10} fill="#fff" className="ink" />
      </Plate>

      {/* 4 reflector: nested cavities */}
      <Plate x={318} y={78} w={74} h={104} depth={8} r={10}>
        <rect x={9} y={9} width={56} height={86} rx={8} fill="none" stroke="currentColor" />
        <rect x={18} y={18} width={38} height={68} rx={6} fill="none" stroke="currentColor" />
      </Plate>

      {/* 5 outer lens with prism optics */}
      <Plate x={410} y={60} w={80} h={112} depth={10} r={16}>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <path key={i} d={`M10 ${16 + i * 15} h60`} stroke="currentColor" strokeWidth="0.8" />
        ))}
      </Plate>

      {/* fasteners */}
      {[[30, 268], [44, 290]].map(([x, y], i) => (
        <g key={i} transform={`translate(${x} ${y}) rotate(-16)`}>
          <rect x={0} y={-4} width={8} height={8} fill="#fff" className="ink" />
          <path d="M8 0 H24" className="ink" strokeWidth="2.4" strokeDasharray="1.6 1.4" />
        </g>
      ))}

      {/* callouts */}
      {callouts.map(([bx, by, tx, ty, label], i) => (
        <g key={label}>
          <path d={`M${bx} ${by + 9} L${tx} ${ty}`} className="lead" strokeWidth="0.8" fill="none" />
          <circle cx={tx} cy={ty} r={1.8} fill="currentColor" />
          <circle cx={bx} cy={by} r={9} className="balloon" strokeWidth="1.2" />
          <text x={bx} y={by + 3.5} textAnchor="middle" className="balloon-n">{i + 1}</text>
          <text x={bx + 13} y={by + 3.5}>{label}</text>
        </g>
      ))}

      {/* dimension line */}
      <path d="M66 300 L494 300" stroke="currentColor" strokeWidth="1" markerStart="url(#arrow)" markerEnd="url(#arrow)" />
      <path d="M66 292 V308 M494 292 V308" stroke="currentColor" strokeWidth="1" />
      <text x="280" y="294" textAnchor="middle">ASSEMBLY AXIS</text>

      {/* title block */}
      <text x="530" y="324" textAnchor="end">{title}</text>
    </svg>
  );
}

/** Small isometric document-and-page drawing for empty and waiting states. */
export function SourceSheet() {
  return (
    <svg className="drawing-art" viewBox="0 0 220 140" role="img" aria-label="Line drawing of a regulation page with a highlighted paragraph">
      <g transform={`translate(52 26) ${SKEW}`}>
        <rect x={10} y={-8} width={96} height={118} rx={4} fill="none" stroke="currentColor" />
        <rect width={96} height={118} rx={4} fill="#fff" className="ink" strokeWidth="1.3" />
        {[14, 24, 34, 44].map((y) => <path key={y} d={`M12 ${y} h${y === 44 ? 44 : 70}`} stroke="currentColor" />)}
        <rect x={8} y={56} width={80} height={22} rx={3} fill="none" className="ink" strokeDasharray="4 3" />
        {[62, 70].map((y) => <path key={y} d={`M14 ${y} h64`} className="ink" />)}
        {[88, 98].map((y) => <path key={y} d={`M12 ${y} h60`} stroke="currentColor" />)}
      </g>
      <path d="M150 54 L190 40" className="lead" strokeWidth="0.8" />
      <circle cx={196} cy={38} r={8} className="balloon" strokeWidth="1.2" />
      <text x={196} y={41.5} textAnchor="middle" className="balloon-n">¶</text>
    </svg>
  );
}
