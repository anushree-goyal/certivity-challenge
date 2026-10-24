// Glass pill button (from the 21st.dev "Background Paths" template): a 1px gradient border around a frosted
// near-black button that lifts on hover while its arrow slides right.

export function GlassButton({ label, onClick, size = "lg", current }: {
  label: string; onClick: () => void; size?: "lg" | "sm"; current?: boolean;
}) {
  return (
    <div className={`glass-wrap glass-${size}`}>
      <button type="button" className="glass-btn" onClick={onClick} aria-current={current ? "page" : undefined}>
        <span className="glass-label">{label}</span>
        <span className="glass-arrow" aria-hidden>→</span>
      </button>
    </div>
  );
}
