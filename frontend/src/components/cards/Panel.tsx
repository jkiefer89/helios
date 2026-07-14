import { useId, type ReactNode } from "react";

interface PanelProps {
  title?: string;
  eyebrow?: string;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Panel({ title, eyebrow, meta, children, className = "" }: PanelProps) {
  const headingId = useId();
  return (
    <section className={`panel-card ${className}`} aria-labelledby={title ? headingId : undefined}>
      {(title || meta || eyebrow) && (
        <header className="panel-card__head">
          <div>
            {eyebrow && <div className="section-label">{eyebrow}</div>}
            {title && <h2 id={headingId}>{title}</h2>}
          </div>
          {meta && <div className="panel-card__meta">{meta}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function StatTile({ label, value, tone = "neutral" }: { label: string; value: ReactNode; tone?: string }) {
  return (
    <div className={`stat-tile tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
