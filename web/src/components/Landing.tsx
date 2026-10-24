import type { CSSProperties } from "react";
import { BackgroundPaths } from "./BackgroundPaths";
import { FlowButton } from "./ui/flow-button";

// Front page: one full screen, centred title and call to action over the flowing line field.
// The workspace with the data starts below it; the lines stay on this screen only.
// Text motion (2 s fade, letters springing up one by one) is CSS, so the content always ends up visible
// even where JavaScript animation frames are throttled; it is skipped under prefers-reduced-motion.

const TITLE = "ProofTech";

export function Landing({ onStart, onScroll }: { onStart: () => void; onScroll: () => void }) {
  const words = TITLE.split(" ");

  return (
    <section className="landing" aria-labelledby="landing-title">
      <BackgroundPaths />
      <div className="landing-inner">
        <h1 id="landing-title" className="glass-title" aria-label={TITLE}>
          {words.map((word, wordIndex) => (
            <span key={wordIndex} className="glass-word" aria-hidden>
              {word.split("").map((letter, letterIndex) => (
                <span key={`${wordIndex}-${letterIndex}`} className="glass-letter"
                      style={{ "--delay": `${wordIndex * 0.1 + letterIndex * 0.03}s` } as CSSProperties}>
                  {letter}
                </span>
              ))}
            </span>
          ))}
        </h1>
        <p className="landing-sub">Every engineering requirement your product must meet, traced to the regulation page.</p>
        <FlowButton text="New Analysis" onClick={onStart} size="lg" />
      </div>
      <button type="button" className="landing-scroll" onClick={onScroll} aria-label="Scroll to the workspace">
        <span>Workspace</span>
        <span className="landing-chevron" aria-hidden>↓</span>
      </button>
    </section>
  );
}
