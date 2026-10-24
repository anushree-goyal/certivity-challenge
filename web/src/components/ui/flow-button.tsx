// Flow button (adapted from a 21st.dev component). Outlined pill at rest; on hover a circle grows from the
// centre to fill it, the left arrow slides in while the right one slides out, the text shifts across and the
// corners soften. The original uses Tailwind utilities; this app uses plain CSS, so the same values live in
// styles.css (.flow-btn …) with the colours inverted for the dark theme.
import { ArrowRight } from "lucide-react";

export function FlowButton({ text = "Modern Button", onClick, block = false, size = "md", current }: {
  text?: string; onClick?: () => void; block?: boolean; size?: "md" | "lg"; current?: boolean;
}) {
  return (
    <button type="button" onClick={onClick} aria-current={current ? "page" : undefined}
            className={`flow-btn flow-${size}${block ? " flow-block" : ""}`}>
      <ArrowRight className="flow-arrow flow-arrow-left" aria-hidden />
      <span className="flow-text">{text}</span>
      <span className="flow-circle" aria-hidden />
      <ArrowRight className="flow-arrow flow-arrow-right" aria-hidden />
    </button>
  );
}
