/** The Lycosa lockup: spider mark, then the wordmark. */
export function Brand() {
  return (
    <span className="brand">
      <img src="/lycosa_logo.png" alt="" />
      Lycosa
    </span>
  );
}

/** Centered brand header for the setup and sign-in screens. */
export function BrandHero({ subtitle }: { subtitle?: string }) {
  return (
    <div className="hero">
      <img src="/lycosa_logo.png" alt="" />
      <div className="word">Lycosa</div>
      {subtitle && <div className="muted small">{subtitle}</div>}
    </div>
  );
}
