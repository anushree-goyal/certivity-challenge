import { useMemo } from "react";
import { motion, useReducedMotion } from "framer-motion";

// Floating white line field behind the whole app (adapted from a 21st.dev "Background Paths" template).
// Fixed to the viewport, so it stays continuous while the page scrolls; decorative only.

function FloatingPaths({ position }: { position: number }) {
  const reduceMotion = useReducedMotion();
  const paths = useMemo(() => Array.from({ length: 36 }, (_, i) => ({
    id: i,
    d: `M-${380 - i * 5 * position} -${189 + i * 6}C-${380 - i * 5 * position} -${189 + i * 6} -${312 - i * 5 * position} ${216 - i * 6} ${152 - i * 5 * position} ${343 - i * 6}C${616 - i * 5 * position} ${470 - i * 6} ${684 - i * 5 * position} ${875 - i * 6} ${684 - i * 5 * position} ${875 - i * 6}`,
    width: 0.5 + i * 0.03,
    duration: 20 + Math.random() * 10,
  })), [position]);

  return (
    <svg className="bg-paths-svg" viewBox="0 0 696 316" fill="none" preserveAspectRatio="xMidYMid slice" aria-hidden>
      {paths.map((path) => reduceMotion ? (
        <path key={path.id} d={path.d} stroke="currentColor" strokeWidth={path.width} strokeOpacity={0.05 + path.id * 0.015} />
      ) : (
        <motion.path
          key={path.id}
          d={path.d}
          stroke="currentColor"
          strokeWidth={path.width}
          strokeOpacity={0.1 + path.id * 0.03}
          initial={{ pathLength: 0.3, opacity: 0.6 }}
          animate={{ pathLength: 1, opacity: [0.3, 0.6, 0.3], pathOffset: [0, 1, 0] }}
          transition={{ duration: path.duration, repeat: Number.POSITIVE_INFINITY, ease: "linear" }}
        />
      ))}
    </svg>
  );
}

export function BackgroundPaths() {
  return (
    <div className="bg-paths" aria-hidden>
      <FloatingPaths position={1} />
      <FloatingPaths position={-1} />
    </div>
  );
}
